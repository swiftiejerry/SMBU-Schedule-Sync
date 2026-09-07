"""
SMBU Schedule Sync — API surface.

Design rules enforced here:
  * Credentials are used once during login and then dropped. Never logged,
    never written to disk, never returned to a caller.
  * Every outbound call passes the global upstream gate so a traffic spike
    degrades latency instead of knocking over the campus portal.
  * Errors carry a stable machine `code` so the UI can localise them.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from . import jw as jwmod
from .cas import CasClient, CasError
from .config import settings
from .infra import (
    BodySizeLimitMiddleware,
    RateLimited,
    Session,
    SessionStore,
    SlidingWindowLimiter,
    UpstreamGate,
    SecurityHeadersMiddleware,
    build_http_client,
)
from .models import (
    LoginRequest,
    LoginResponse,
    MeetingOut,
    ScheduleResponse,
    SectionOut,
    Term,
    TermsResponse,
)
from .payments import (
    Channel,
    OrderStatus,
    PaymentGateway,
    PaymentError,
    gateway,
    iso,
    ledger,
)
from .schedule import IcsOptions, TermSchedule, build_ics, count_events
from .webvpn import WebVpnClient, WebVpnError, refresh_tunnel_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("smbu")

app = FastAPI(title="SMBU Schedule Sync", version="0.1.0")

origins = [o.strip() for o in settings.allow_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    # Browsers reject `Access-Control-Allow-Origin: *` on credentialed
    # requests, so only advertise credentials when explicit origins are set.
    allow_origins=origins,
    allow_credentials="*" not in origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last == outermost, so an oversized body is rejected before anything
# else touches it, and every response (including CORS/errors) gets the headers.
app.add_middleware(
    SecurityHeadersMiddleware,
    hsts=settings.security_hsts_enabled,
    csp=settings.security_csp,
)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)

sessions = SessionStore()
limiter = SlidingWindowLimiter()
gate = UpstreamGate(settings.max_concurrent_upstream)

START_TIME = time.time()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def api_error(status: int, code: str, message: str, **extra) -> JSONResponse:
    payload = {"error": {"code": code, "message": message}}
    payload["error"].update(extra)
    return JSONResponse(status_code=status, content=payload)


# Parsed once at import; empty config -> empty list -> XFF never trusted.
_TRUSTED_PROXY_NETS = [
    ipaddress.ip_network(net.strip())
    for net in settings.trusted_proxies.split(",")
    if net.strip()
]


def client_ip(request: Request) -> str:
    """
    Client IP for rate-limiting, safe against header spoofing.

    X-Forwarded-For is honoured ONLY when the immediate peer is one of the
    configured trusted proxies (SMBU_TRUSTED_PROXIES, e.g. your nginx). A
    client connecting directly must not be able to rotate fake XFF values to
    dodge the per-IP limiter, so without a trusted proxy we fall back to the
    raw peer address.
    """
    peer = request.client.host if request.client else "unknown"
    if _TRUSTED_PROXY_NETS:
        try:
            addr = ipaddress.ip_address(peer)
        except ValueError:
            return peer
        if any(addr in net for net in _TRUSTED_PROXY_NETS):
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
    return peer


async def require_session(token: Optional[str]) -> Session:
    if not token:
        raise PermissionError("SESSION_EXPIRED")
    session = await sessions.get(token)
    if session is None:
        raise PermissionError("SESSION_EXPIRED")
    return session


async def call_upstream(session: Session, coro_factory, attempts: int | None = None):
    """
    Run an upstream call under both the global gate and the per-session gate,
    with bounded retry on retryable failures.

    A WebVPN session gets ONE transparent tunnel re-auth when the upstream
    reports the session gone: the CAS TGT cookie often outlives the tunnel
    session, so re-walking the SSO chain restores access without the user
    re-entering credentials. A second expiry really is expired.
    """
    attempts = attempts or (settings.max_retries + 1)
    try:
        return await _upstream_with_retries(session, coro_factory, attempts)
    except jwmod.SessionExpired:
        if session.access == "webvpn" and not session.reauthed:
            session.reauthed = True
            if await refresh_tunnel_session(session.client):
                logger.info("tunnel session restored for student %s", session.student_id)
                return await _upstream_with_retries(session, coro_factory, attempts)
        raise


async def _upstream_with_retries(session: Session, coro_factory, attempts: int):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            async with gate, session.gate:
                return await coro_factory()
        except (jwmod.UpstreamError, httpx.HTTPError) as exc:
            last = exc
            retryable = getattr(exc, "retryable", True)
            if not retryable or attempt == attempts - 1:
                break
            await asyncio.sleep(settings.retry_backoff_s * (2**attempt))
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Login strategies (direct / WebVPN, selected by SMBU_ACCESS_MODE)
# ---------------------------------------------------------------------------


@dataclass
class AuthResult:
    """Everything the login endpoint needs to open an app session."""

    client: httpx.AsyncClient  # the client the Session will keep
    user: Dict[str, Any]
    access: str  # "direct" | "webvpn"
    home_root: Optional[str]  # tunnel jwapp root for webvpn sessions


async def _login_direct(
    client: httpx.AsyncClient, username: str, password: str, captcha: str
) -> tuple[Optional[AuthResult], Optional[Dict[str, Any]]]:
    """
    Campus CAS → jwapp over the public hosts. Returns (result, None) or
    (None, failure). Closes `client` on every outcome EXCEPT success, where
    the client becomes the session's data connection.
    """
    try:
        cas = CasClient(client)
        outcome = await cas.login(username, password, captcha)
        if not outcome.ok:
            await client.aclose()
            return None, {
                "needs_captcha": outcome.needs_captcha,
                "message": outcome.message,
                "captcha_url": outcome.captcha_url,
            }
        jw = jwmod.JwClient(client)
        user = await jw.current_user()
    except (CasError, jwmod.UpstreamError, httpx.HTTPError):
        await client.aclose()
        raise
    return AuthResult(client=client, user=user, access="direct", home_root=None), None


async def _login_webvpn(
    client: httpx.AsyncClient, username: str, password: str, captcha: str
) -> tuple[Optional[AuthResult], Optional[Dict[str, Any]]]:
    """
    Srun portal CAS → tunnel, per app/webvpn.py. The login client is closed
    inside WebVpnClient on success (pool pollution); the returned data-plane
    client is the one the Session keeps.
    """
    outcome = None
    try:
        wv = WebVpnClient(client)
        outcome = await wv.login(username, password, captcha)
        if not outcome.ok or outcome.session is None:
            await client.aclose()
            return None, {
                "needs_captcha": outcome.needs_captcha,
                "message": outcome.message,
                "captcha_url": outcome.captcha_url,
            }
        jw = jwmod.JwClient(
            outcome.session.client, home_root=outcome.session.home_root
        )
        user = await jw.current_user()
    except (WebVpnError, jwmod.UpstreamError, httpx.HTTPError):
        # client is already closed by WebVpnClient on success; make sure the
        # data-plane client never leaks either.
        if outcome is not None and outcome.session is not None:
            with contextlib.suppress(Exception):
                await outcome.session.client.aclose()
        raise
    return (
        AuthResult(
            client=outcome.session.client,
            user=user,
            access="webvpn",
            home_root=outcome.session.home_root,
        ),
        None,
    )


def _auth_failed_response(failure: Optional[Dict[str, Any]]) -> JSONResponse:
    failure = failure or {}
    if failure.get("needs_captcha"):
        return api_error(
            401,
            "AUTH_CAPTCHA_REQUIRED",
            failure.get("message") or "captcha required",
            captcha_url=failure.get("captcha_url"),
        )
    return api_error(401, "AUTH_FAILED", failure.get("message") or "invalid credentials")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "uptime_s": round(time.time() - START_TIME, 1),
        "sessions": sessions.size,
        "upstream_in_flight": gate.in_flight,
        "upstream_limit": settings.max_concurrent_upstream,
    }


@app.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request) -> Response:
    ip = client_ip(request)
    try:
        await limiter.check(
            f"login:{ip}", settings.login_rate_limit_per_minute_ip
        )
        # Docker bridge / campus NAT collapse every student onto one source
        # IP, so the IP bucket alone would be the whole school's budget. The
        # per-username bucket keeps one account's retries fair while still
        # capping brute-force on any single account.
        await limiter.check(
            f"login-id:{payload.username}",
            settings.login_rate_limit_per_minute_user,
        )
    except RateLimited as exc:
        return api_error(
            429, "RATE_LIMITED", "too many login attempts", retry_after=round(exc.retry_after)
        )

    mode = settings.access_mode.strip().lower()
    if mode not in ("auto", "direct", "webvpn"):
        mode = "auto"

    try:
        auth: Optional[AuthResult] = None
        auth_failure: Optional[Dict[str, Any]] = None
        transport_error: Optional[Exception] = None

        # --- direct path (and the first leg of auto) ------------------------
        if mode in ("direct", "auto"):
            client = build_http_client()
            try:
                async with gate:
                    auth, auth_failure = await _login_direct(
                        client, payload.username, payload.password, payload.captcha or ""
                    )
            except (CasError, jwmod.UpstreamError, httpx.HTTPError) as exc:
                # Transport-level failure only. Wrong credentials are wrong on
                # every path — falling back would just burn another CAS
                # attempt (and risk locking the account).
                transport_error = exc
                logger.warning("direct login failed for %s: %s", ip, exc)

            if auth is None and mode == "direct":
                if transport_error is not None:
                    if isinstance(transport_error, jwmod.SessionExpired):
                        return api_error(
                            401,
                            "AUTH_FAILED",
                            "CAS accepted but the portal rejected the session",
                        )
                    return api_error(503, "UPSTREAM_ERROR", "academic portal temporarily unavailable")
                return _auth_failed_response(auth_failure)

            if auth is None and auth_failure is not None:
                # auto mode with a credential-level failure: surface as-is.
                return _auth_failed_response(auth_failure)

        # --- WebVPN path (forced, or auto fallback after a transport failure)
        if auth is None:
            if transport_error is not None:
                logger.info("falling back to WebVPN for %s", ip)
            client = build_http_client()
            try:
                async with gate:
                    auth, auth_failure = await _login_webvpn(
                        client, payload.username, payload.password, payload.captcha or ""
                    )
            except WebVpnError as exc:
                transport_error = exc
                logger.warning("webvpn login failed for %s: %s", ip, exc)

            if auth is None:
                if transport_error is not None:
                    return api_error(503, "UPSTREAM_ERROR", "academic portal temporarily unavailable")
                return _auth_failed_response(auth_failure)

        # --- success ---------------------------------------------------------
        user = auth.user
        student_id = str(
            jwmod._pick(user, "xh", "userId", "username", "loginName", "userCode")
            or payload.username
        )
        display_name = str(
            jwmod._pick(user, "xm", "nickName", "name", "userName", "displayName")
            or student_id
        )

        session = await sessions.create(
            student_id,
            display_name,
            auth.client,
            access=auth.access,
            home_root=auth.home_root,
        )
        logger.info("login ok for student %s from %s via %s", student_id, ip, auth.access)
        return JSONResponse(
            LoginResponse(
                token=session.token,
                student_id=student_id,
                display_name=display_name,
                expires_in=settings.session_ttl_s,
            ).model_dump()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled login error")
        return api_error(500, "INTERNAL", "internal server error")
    finally:
        # The plaintext password must not outlive this call.
        payload.password = ""


@app.post("/api/auth/logout")
async def logout(x_session_token: Optional[str] = Header(default=None)) -> Response:
    if x_session_token:
        session = await sessions.drop(x_session_token)
        if session:
            await session.client.aclose()
    return JSONResponse({"ok": True})


@app.get("/api/terms", response_model=TermsResponse)
async def list_terms(
    request: Request, x_session_token: Optional[str] = Header(default=None)
) -> Response:
    try:
        session = await require_session(x_session_token)
    except PermissionError:
        return api_error(401, "SESSION_EXPIRED", "session expired")

    try:
        await limiter.check(f"student:{session.student_id}", settings.rate_limit_per_minute_student)
        jw = jwmod.JwClient(session.client, home_root=session.home_root)
        terms = await call_upstream(session, jw.terms)
    except RateLimited as exc:
        return api_error(429, "RATE_LIMITED", "too many requests", retry_after=round(exc.retry_after))
    except jwmod.SessionExpired:
        return api_error(401, "SESSION_EXPIRED", "session expired upstream")
    except Exception as exc:  # noqa: BLE001
        logger.warning("terms failed: %s", exc)
        return api_error(502, "UPSTREAM_ERROR", "academic portal temporarily unavailable")

    if not terms:
        return api_error(502, "NO_DATA", "portal returned no terms")

    default_code = next((t["code"] for t in terms if t["current"]), terms[0]["code"])
    return JSONResponse(
        TermsResponse(
            terms=[Term(**t) for t in terms],
            default_code=default_code,
        ).model_dump()
    )


@app.get("/api/schedule", response_model=ScheduleResponse)
async def get_schedule(
    request: Request,
    term: Optional[str] = None,
    x_session_token: Optional[str] = Header(default=None),
) -> Response:
    try:
        session = await require_session(x_session_token)
    except PermissionError:
        return api_error(401, "SESSION_EXPIRED", "session expired")

    try:
        await limiter.check(f"student:{session.student_id}", settings.rate_limit_per_minute_student)
        jw = jwmod.JwClient(session.client, home_root=session.home_root)

        terms = await call_upstream(session, jw.terms)
        if not terms:
            return api_error(502, "NO_DATA", "portal returned no terms")
        selected = next((t for t in terms if t["code"] == term), None) or (
            next((t for t in terms if t["current"]), terms[0])
        )
        schedule = await call_upstream(
            session, lambda: jw.build_term_schedule(selected["code"], selected["name"])
        )
    except RateLimited as exc:
        return api_error(429, "RATE_LIMITED", "too many requests", retry_after=round(exc.retry_after))
    except jwmod.SessionExpired:
        return api_error(401, "SESSION_EXPIRED", "session expired upstream")
    except Exception as exc:  # noqa: BLE001
        logger.warning("schedule failed: %s", exc)
        return api_error(502, "UPSTREAM_ERROR", "academic portal temporarily unavailable")

    warnings: List[str] = []
    if not schedule.weeks:
        warnings.append("WEEK_DATES_MISSING")
    if not schedule.sections:
        warnings.append("SECTION_TIMES_MISSING")
        schedule.sections = list(jwmod.DEFAULT_SECTIONS)
    if not schedule.meetings:
        warnings.append("NO_COURSES")

    events = count_events(schedule)
    if events > settings.max_ics_events:
        return api_error(413, "TOO_LARGE", f"schedule has {events} events")

    return JSONResponse(
        ScheduleResponse(
            term_code=schedule.term_code,
            term_name=schedule.term_name,
            weeks=[
                {"index": str(w.index), "start": w.start.isoformat(), "end": w.end.isoformat()}
                for w in schedule.weeks
            ],
            sections=[
                SectionOut(index=s.index, start=s.start.strftime("%H:%M"), end=s.end.strftime("%H:%M"))
                for s in schedule.sections
            ],
            meetings=[
                MeetingOut(
                    name=m.name,
                    day_of_week=m.day_of_week,
                    start_section=m.start_section,
                    end_section=m.end_section,
                    weeks=list(m.weeks),
                    week_label=m.week_label,
                    section_label=m.section_label,
                    teacher=m.teacher,
                    location=m.location,
                    code=m.code,
                    class_name=m.class_name,
                )
                for m in schedule.meetings
            ],
            event_count=events,
            warnings=warnings,
        ).model_dump()
    )


@app.get("/api/export.ics")
async def export_ics(
    request: Request,
    term: Optional[str] = None,
    lang: str = "zh",
    reminder: int = 15,
    x_session_token: Optional[str] = Header(default=None),
) -> Response:
    try:
        session = await require_session(x_session_token)
    except PermissionError:
        return api_error(401, "SESSION_EXPIRED", "session expired")

    if lang not in ("zh", "en", "ru"):
        lang = "zh"
    reminder = max(0, min(180, reminder))

    try:
        await limiter.check(f"student:{session.student_id}", settings.rate_limit_per_minute_student)
        jw = jwmod.JwClient(session.client, home_root=session.home_root)
        terms = await call_upstream(session, jw.terms)
        if not terms:
            return api_error(502, "NO_DATA", "portal returned no terms")
        selected = next((t for t in terms if t["code"] == term), None) or (
            next((t for t in terms if t["current"]), terms[0])
        )
        schedule = await call_upstream(
            session, lambda: jw.build_term_schedule(selected["code"], selected["name"])
        )
    except RateLimited as exc:
        return api_error(429, "RATE_LIMITED", "too many requests", retry_after=round(exc.retry_after))
    except jwmod.SessionExpired:
        return api_error(401, "SESSION_EXPIRED", "session expired upstream")
    except Exception as exc:  # noqa: BLE001
        logger.warning("export failed: %s", exc)
        return api_error(502, "UPSTREAM_ERROR", "academic portal temporarily unavailable")

    if not schedule.sections:
        schedule.sections = list(jwmod.DEFAULT_SECTIONS)

    events = count_events(schedule)
    if events > settings.max_ics_events:
        return api_error(413, "TOO_LARGE", f"schedule has {events} events")

    body = build_ics(
        schedule,
        student_id=session.student_id,
        options=IcsOptions(
            reminder_minutes=reminder,
            language=lang,
            calendar_name=f"{schedule.term_name} 课表" if lang == "zh" else f"{schedule.term_name} Schedule",
        ),
    )

    filename = f"smbu-{schedule.term_code}-{session.student_id}.ics"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


if settings.debug_raw_endpoint:

    @app.get("/api/debug/raw")
    async def debug_raw(
        request: Request,
        term: Optional[str] = None,
        x_session_token: Optional[str] = Header(default=None),
    ) -> Response:
        """
        Dump the untouched upstream payloads.

        Exists purely so field mappings can be re-calibrated when the portal
        changes shape. OFF by default; enable in dev with
        SMBU_DEBUG_RAW_ENDPOINT=true. Never enable in production.
        """
        try:
            session = await require_session(x_session_token)
        except PermissionError:
            return api_error(401, "SESSION_EXPIRED", "session expired")

        jw = jwmod.JwClient(session.client, home_root=session.home_root)
        out: Dict[str, Any] = {}
        for name, factory in (
            ("currentUser", jw.current_user),
            ("terms", jw.terms),
        ):
            try:
                out[name] = await call_upstream(session, factory)
            except Exception as exc:  # noqa: BLE001
                # Only the exception CLASS name reaches the client — the full
                # message goes to the server log. Raw exception text in a
                # response is an information-leak (CodeQL py/stack-trace-exposure).
                logger.warning("debug/raw %s failed: %s", name, exc)
                out[name] = {"error": type(exc).__name__}
        if term:
            for name, factory in (
                ("termWeeks", lambda: jw._call("/api/home/getTermWeeks.do", {"xnxqdm": term}, "GET")),
                ("sections", lambda: jw._call("/api/home/student/getSections.do", {"xnxqdm": term}, "GET")),
                ("courses", lambda: jw._call("/api/home/student/courses.do", {"xnxqdm": term}, "GET")),
                (
                    "scheduleDetail",
                    lambda: jw._call(
                        "/api/home/student/getMyScheduleDetail.do", {"xnxqdm": term}, "GET"
                    ),
                ),
            ):
                try:
                    out[name] = await call_upstream(session, factory)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("debug/raw %s failed: %s", name, exc)
                    out[name] = {"error": type(exc).__name__}
        return JSONResponse(out)


# ---------------------------------------------------------------------------
# Payments (deferred — interface only, behind a flag)
# ---------------------------------------------------------------------------


@app.post("/api/pay/order")
async def create_order(
    request: Request,
    body: Dict[str, str],
    x_session_token: Optional[str] = Header(default=None),
) -> Response:
    if not settings.payments_enabled:
        return api_error(503, "PAYMENTS_DISABLED", "payments not enabled yet")
    try:
        session = await require_session(x_session_token)
    except PermissionError:
        return api_error(401, "SESSION_EXPIRED", "session expired")

    idem = body.get("idempotency_key") or secrets.token_hex(16)
    order = ledger.create(
        student_id=session.student_id,
        term_code=body.get("term", ""),
        idempotency_key=idem,
    )
    code = await gateway.create_aggregate_code(order)
    return JSONResponse(
        {
            "order_id": order.id,
            "amount_cents": order.amount_cents,
            "status": order.status.value,
            "expires_at": iso(order.expires_at),
            "code": code,
        }
    )


@app.get("/api/pay/order/{order_id}")
async def order_status(order_id: str) -> Response:
    if not settings.payments_enabled:
        return api_error(503, "PAYMENTS_DISABLED", "payments not enabled yet")
    order = ledger.get(order_id)
    if not order:
        return api_error(404, "ORDER_NOT_FOUND", "no such order")
    return JSONResponse(
        {
            "order_id": order.id,
            "status": order.status.value,
            "channel": order.channel.value if order.channel else None,
            "paid_at": iso(order.paid_at),
        }
    )


@app.post("/api/pay/callback")
async def pay_callback(request: Request, body: Dict[str, str]) -> Response:
    """
    Gateway webhook. Idempotent: repeated callbacks for the same trade number
    are safe no-ops, so a lost/delayed 漏单 acknowledgement can never double-charge.
    """
    if not settings.payments_enabled:
        return JSONResponse({"ok": False, "disabled": True})
    trade_no = await gateway.verify_callback(body)
    if not trade_no:
        return JSONResponse({"ok": False}, status_code=400)
    order_id = body.get("order_id", "")
    try:
        ledger.mark_paid(order_id, trade_no, Channel.AGGREGATE)
    except PaymentError as exc:
        logger.warning("payment mark_paid conflict for %s: %s", order_id, exc)
        return api_error(409, "ORDER_CONFLICT", "order state conflict")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request: Request, exc: StarletteHTTPException) -> Response:
        """
        Hand route-shaped URLs to the SPA.

        StaticFiles is mounted at "/", so it answers every path first and 404s
        anything it does not hold — which would otherwise make a catch-all route
        unreachable and break deep links / refreshes on a non-root URL. Only
        extension-less, non-API paths fall through to the SPA; a genuinely
        missing asset (e.g. /assets/app.js) still 404s like a real file.
        """
        if (
            exc.status_code == 404
            and not request.url.path.startswith("/api/")
            and not Path(request.url.path).suffix
        ):
            index = FRONTEND_DIST / "index.html"
            if index.exists():
                return FileResponse(index)
        return api_error(exc.status_code, "HTTP_ERROR", exc.detail)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    app.state.sweeper = asyncio.create_task(_sweep_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    task = getattr(app.state, "sweeper", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            removed = await sessions.sweep()
            cleaned = await limiter.sweep()
            expired = ledger.sweep_expired()

            # Anti-漏单 backstop: the gateway's records are authoritative, so
            # settle anything our inbox missed (lost webhook, restart, blip).
            healed = 0
            if settings.payments_enabled and settings.payment_reconcile_enabled:
                healed = await ledger.reconcile(
                    gateway, time.time() - settings.payment_reconcile_window_s
                )
                if healed:
                    logger.warning("reconciliation healed %d dropped order(s)", healed)

            if removed or cleaned or expired or healed:
                logger.info(
                    "swept %d sessions, %d limiter buckets, %d expired orders, "
                    "%d reconciled orders",
                    removed,
                    cleaned,
                    expired,
                    healed,
                )
        except Exception:  # noqa: BLE001
            logger.exception("sweep failed")
