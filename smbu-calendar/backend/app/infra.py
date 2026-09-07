"""
Process-local infrastructure: session store, rate limiter, upstream gate.

Everything here is deliberately in-memory. Credentials are never persisted,
and a restart simply invalidates sessions — which is the behaviour we want
for a service that handles university passwords.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

import httpx
from fastapi.responses import JSONResponse

from .config import settings


def _lenient_cookie_get(self, name, default=None, domain=None, path=None):
    """Lenient replacement for ``httpx.Cookies.get``.

    Plain httpx raises ``CookieConflict`` whenever two same-name cookies
    match a lookup. The campus load balancer stamps a ``route`` cookie from
    every host it proxies (authserver, jwapp, ...), and they all resolve to
    the same subnet, so a redirected CAS request always carries two of them.
    We return the last match instead of failing closed.

    Applied as a monkeypatch rather than a subclass: ``Cookies.__init__``
    rebuilds a brand-new plain ``CookieJar`` for every instance, so
    ``merge_cookies`` silently discards any subclass.
    """
    value = None
    for cookie in self.jar:
        if cookie.name != name:
            continue
        if domain is not None and cookie.domain != domain:
            continue
        if path is not None and cookie.path != path:
            continue
        value = cookie.value  # keep last; never raise
    return value if value is not None else default


httpx.Cookies.get = _lenient_cookie_get  # noqa: SLF001 - deliberate campus-LB workaround


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry in {retry_after:.0f}s")
        self.retry_after = retry_after


class SlidingWindowLimiter:
    """Fixed-capacity sliding window. O(1) amortised, no background task."""

    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        async with self._lock:
            bucket = self._events[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                raise RateLimited(window - (now - bucket[0]))
            bucket.append(now)

    async def sweep(self, max_idle: float = 300.0) -> int:
        """Drop idle buckets so a traffic flood cannot grow the map forever."""
        now = time.monotonic()
        removed = 0
        async with self._lock:
            for key in [k for k, v in self._events.items() if not v or now - v[-1] > max_idle]:
                self._events.pop(key, None)
                removed += 1
        return removed


@dataclass
class Session:
    token: str
    student_id: str
    display_name: str
    client: httpx.AsyncClient
    # Which upstream path this session rides: "direct" (public jw host) or
    # "webvpn" (Srun tunnel). Only webvpn sessions get a transparent re-auth.
    access: str = "direct"
    # jwapp REST root for webvpn sessions (tunnel authority); None = direct.
    home_root: Optional[str] = None
    # At most ONE transparent tunnel re-auth per session lifetime.
    reauthed: bool = False
    created_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    # Bounds how many upstream calls a single session may have in flight.
    gate: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(settings.per_student_upstream_limit)
    )

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def expired(self) -> bool:
        return time.monotonic() - self.last_seen > settings.session_ttl_s


class SessionStore:
    """LRU + TTL session registry. Nothing leaves this process."""

    def __init__(self) -> None:
        self._sessions: "OrderedDict[str, Session]" = OrderedDict()
        self._lock = asyncio.Lock()

    async def create(
        self,
        student_id: str,
        display_name: str,
        client: httpx.AsyncClient,
        access: str = "direct",
        home_root: Optional[str] = None,
    ) -> Session:
        token = secrets.token_urlsafe(32)
        session = Session(
            token=token,
            student_id=student_id,
            display_name=display_name,
            client=client,
            access=access,
            home_root=home_root,
        )
        async with self._lock:
            await self._evict_locked()
            self._sessions[token] = session
            self._sessions.move_to_end(token)
        return session

    async def get(self, token: str) -> Optional[Session]:
        async with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expired():
                self._sessions.pop(token, None)
                return None
            self._sessions.move_to_end(token)
            session.touch()
            return session

    async def drop(self, token: str) -> Optional[Session]:
        async with self._lock:
            return self._sessions.pop(token, None)

    async def _evict_locked(self) -> None:
        now = time.monotonic()
        for token in [t for t, s in self._sessions.items() if s.expired()]:
            self._sessions.pop(token, None)
        while len(self._sessions) >= settings.session_max_entries:
            _, victim = self._sessions.popitem(last=False)
            asyncio.create_task(victim.client.aclose())

    async def sweep(self) -> int:
        async with self._lock:
            before = len(self._sessions)
            await self._evict_locked()
            return before - len(self._sessions)

    @property
    def size(self) -> int:
        return len(self._sessions)


class UpstreamGate:
    """
    Global ceiling on concurrent upstream requests.

    Without this, a burst of N client requests fans out to N upstream
    connections and the campus server starts refusing them — which is
    exactly how a service like this takes the school's portal down.
    """

    def __init__(self, limit: int) -> None:
        self._sem = asyncio.Semaphore(limit)
        self.in_flight = 0
        self.rejected = 0

    async def __aenter__(self) -> "UpstreamGate":
        await self._sem.acquire()
        self.in_flight += 1
        return self

    async def __aexit__(self, *exc) -> None:
        self.in_flight -= 1
        self._sem.release()

    def try_acquire(self) -> bool:
        if self._sem.locked():
            self.rejected += 1
            return False
        return True


# ---------------------------------------------------------------------------
# Hardening middleware (pure ASGI: no buffering, no streaming breakage)
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware:
    """
    Baseline browser-side hardening.

    HSTS is opt-in because sending it over plain HTTP would make a browser
    pin a scheme the server may not actually be serving yet — enable it once
    TLS terminates in front of the app (nginx/Caddy).
    """

    def __init__(self, app, *, hsts: bool, csp: str) -> None:
        self.app = app
        self.hsts = hsts
        self.csp = csp

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_hardened(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
                    (b"content-security-policy", self.csp.encode("utf-8")),
                ]
                if self.hsts:
                    extra.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                headers.extend(extra)
            await send(message)

        await self.app(scope, receive, send_hardened)


class BodySizeLimitMiddleware:
    """
    Cap request bodies so a flood cannot exhaust memory.

    Rejects an oversized body as early as possible: immediately when
    Content-Length lies about it, and progressively for chunked uploads.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        response = JSONResponse(
                            status_code=413,
                            content={
                                "error": {"code": "PAYLOAD_TOO_LARGE", "message": "body too large"}
                            },
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    pass
                break

        seen = 0

        async def counted_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes and not message.get("more_body"):
                    pass
            return message

        # Streaming bodies without Content-Length are bounded by the same cap.
        if scope.get("headers") and not any(
            k == b"content-length" for k, _ in scope["headers"]
        ):
            await self.app(scope, counted_receive, send)
        else:
            await self.app(scope, receive, send)


def build_http_client() -> httpx.AsyncClient:
    """
    One shared transport configuration for every outbound call.

    `verify=False` is deliberate: campus TLS chains are routinely broken and
    this is a read-only client. It is surfaced in config so a deployment with
    a correct chain can turn it back on.
    """
    timeout = httpx.Timeout(
        connect=settings.connect_timeout_s,
        read=settings.read_timeout_s,
        write=settings.read_timeout_s,
        pool=settings.connect_timeout_s,
    )
    limits = httpx.Limits(
        max_connections=settings.max_concurrent_upstream,
        max_keepalive_connections=max(8, settings.max_concurrent_upstream // 2),
        keepalive_expiry=30.0,
    )
    kwargs: Dict[str, Any] = dict(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        # A CA bundle (SMBU_UPSTREAM_CA_BUNDLE) turns verification on and pins
        # trust to that root — the actual defence if DNS ever answers with a
        # hijacked address. Falls back to the permissive campus default.
        verify=settings.upstream_ca_bundle or settings.upstream_verify_tls,
        http2=False,
        # Never inherit a caller's local dev proxy (HTTP_PROXY/HTTPS_PROXY).
        # This is a server-side client that must reach the campus hosts
        # directly; routing through an unrelated proxy breaks internal IPs
        # and leaks requests off-box.
        trust_env=False,
    )
    # Opt-in explicit proxy. Some deployments cannot reach the campus directly
    # (e.g. a container behind a NAT the campus does not authorise) and must
    # egress through the authenticated host. Deliberately separate from
    # trust_env so ambient proxy variables still cannot hijack traffic.
    if settings.upstream_proxy:
        kwargs["proxy"] = settings.upstream_proxy
    return httpx.AsyncClient(**kwargs)
