"""
Srun WebVPN client — the off-campus access path (browser-assisted).

Why a browser is involved
-------------------------
Measured on the real gateway (2026-09-06, see WEBVPN.md):

* The gateway separates clients by TLS fingerprint. A bare python (httpx)
  request to the tunnel subdomain gets the RAW jwapp CAS flow — whose ticket
  redirect points at the DIRECT host `jw.smbu.edu.cn`, which the campus edge
  rejects off-campus with `SSLV3_ALERT_HANDSHAKE_FAILURE`. Dead end.
* A real browser gets the PORTAL bounce chain instead, and that chain is
  driven by JavaScript + Sangfor's `window.name` relay (`sf_ssl_ms_...`) —
  no portable way to replay it with an HTTP library.
* Once a browser has completed the SSO walk, the tunnel session cookies it
  drops are honoured for ANY client: plain httpx with those cookies gets
  200 JSON from the jwapp API.

Product design (verified end-to-end with a real account):

    browser (system Edge/Chrome, headless, ~5 s)
        tunnel entry → portal bounce → CAS (AES password, Enter submit)
        → cas_validate → tunnel session established
    cookie hand-off
        export ctx.cookies() → inject into a fresh httpx client
    data plane (httpx, keep-alive)
        same JwClient / normalisers / ICS pipeline as direct mode

Pitfall ledger (WEBVPN.md):
1. handshake budget per source IP — a browser login costs ~3 handshakes and
   the data plane ONE per session; a process-wide gate caps concurrent
   logins so the shared egress IP never floods the gateway.
2. host-only cookies / portal SSO chain — handled BY the browser natively.
3. httpx pool pollution after login — the login never touches the data
   client, so this class of failure cannot occur by construction.
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import settings
from .infra import build_http_client


class WebVpnError(Exception):
    """Transport/protocol level WebVPN failure (retryable at a higher level)."""


@dataclass
class WebVpnSession:
    """A warmed, tunnel-authenticated data-plane client."""

    client: httpx.AsyncClient
    home_root: str  # e.g. https://jw-....webvpn.smbu.edu.cn:8118/jwapp/sys/homeapp


@dataclass
class WebVpnOutcome:
    ok: bool
    message: Optional[str] = None
    needs_captcha: bool = False
    captcha_url: Optional[str] = None
    session: Optional[WebVpnSession] = None


# ---------------------------------------------------------------------------
# Concurrency gate (pitfall 1): cap concurrent browser logins process-wide.
# ---------------------------------------------------------------------------


class _LoginGate:
    """Loop-safe lazy semaphore (tests build several short-lived loops)."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._sem: Optional[asyncio.Semaphore] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self) -> "_LoginGate":
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._sem = asyncio.Semaphore(self._limit)
            self._loop = loop
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._sem is not None
        self._sem.release()


login_gate = _LoginGate(settings.webvpn_handshake_limit)


# ---------------------------------------------------------------------------
# Tunnel URL mapping
# ---------------------------------------------------------------------------


def tunnel_authority(host: str) -> Optional[str]:
    """jw.smbu.edu.cn → jw-smbu-edu-cn-s.webvpn.smbu.edu.cn:8118"""
    return settings.webvpn_tunnel_map.get(host.strip().lower())


def tunnel_home_root() -> str:
    """jwapp REST root as seen through the tunnel."""
    src = httpx.URL(settings.jw_base_url).host
    dst = tunnel_authority(src)
    if not dst:
        raise WebVpnError(
            f"no WebVPN tunnel mapping for {src!r} (set SMBU_WEBVPN_TUNNEL_HOSTS)"
        )
    return f"https://{dst}{settings.jw_context_path}/sys/homeapp"


def tunnel_entry_url() -> str:
    """The page a student would open; triggers the portal bounce when cold."""
    return f"{tunnel_home_root()}/home/index.html"


def _tunnel_host() -> str:
    return httpx.URL(tunnel_entry_url()).host


# ---------------------------------------------------------------------------
# Cookie hand-off
# ---------------------------------------------------------------------------


def client_from_cookies(cookies: List[Dict[str, Any]]) -> httpx.AsyncClient:
    """
    Build the data-plane client from browser cookies.

    Host-only/domain/path attributes are preserved so the tunnel session and
    the CAS TGT land in exactly the scopes the gateway expects.
    """
    client = build_http_client()
    for ck in cookies:
        name = ck.get("name") or ""
        if not name:
            continue
        domain = ck.get("domain") or ""
        path = ck.get("path") or "/"
        if domain:
            client.cookies.set(name, ck.get("value") or "", domain=domain, path=path)
        else:
            client.cookies.set(name, ck.get("value") or "", path=path)
    return client


def cookies_from_client(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Inverse hand-off (used by refresh): jar → playwright-style dicts."""
    out: List[Dict[str, Any]] = []
    for morsel in list(client.cookies.jar):
        name = (morsel.name or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "value": morsel.value or "",
                "domain": morsel.domain or "",
                "path": morsel.path or "/",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Browser driving
# ---------------------------------------------------------------------------


async def _tunnel_serving(page: Any, timeout_ms: int = 15000) -> bool:
    """True when the tunnel actually serves jwapp JSON through the browser."""
    try:
        resp = await page.request.get(
            f"{tunnel_home_root()}/api/home/currentUser.do", timeout=timeout_ms
        )
        return resp.status == 200
    except Exception:  # noqa: BLE001 - any navigation/JS hiccup = not ready
        return False


async def _read_cas_error(page: Any) -> str:
    for sel in ("#showErrorTip", "#msg", "#showWarnTip"):
        try:
            text = (await page.text_content(sel, timeout=800) or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            continue
    return ""


async def _captcha_data_uri(page: Any) -> Optional[str]:
    """Screenshot the captcha image — its URL is session-scoped, so a data
    URI is the only representation the frontend <img> can display."""
    for sel in ("img[id*='aptcha']", "img[src*='captcha']"):
        try:
            el = page.query_selector(sel)
            if el is None:
                continue
            shot = await el.screenshot()
            return "data:image/png;base64," + base64.b64encode(shot).decode("ascii")
        except Exception:  # noqa: BLE001
            continue
    return None


async def _browser_cookies(
    username: str, password: str, captcha: str
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[WebVpnOutcome]]:
    """
    Drive a REAL headless browser through the student flow.

    Returns (cookies, None) on success or (None, failure-outcome).
    Raises WebVpnError on transport-level breakage.
    """
    from playwright.async_api import async_playwright

    deadline = time.monotonic() + settings.webvpn_login_timeout_s
    channel = settings.webvpn_browser_channel.strip() or None

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel=channel, headless=True)
        except Exception as exc:  # noqa: BLE001 - browser not installed etc.
            raise WebVpnError(f"cannot launch browser ({channel or 'bundled'}): {exc}") from exc

        try:
            ctx = await browser.new_context(ignore_https_errors=True, locale="zh-CN")
            page = await ctx.new_page()

            try:
                await page.goto(
                    tunnel_entry_url(), wait_until="domcontentloaded",
                    timeout=settings.webvpn_login_timeout_s * 1000,
                )
            except Exception as exc:  # noqa: BLE001
                raise WebVpnError(f"tunnel unreachable: {exc}") from exc

            # Ride the bounces until CAS asks for credentials (or the tunnel
            # is somehow already serving).
            while time.monotonic() < deadline:
                if "authserver" in page.url:
                    break
                if _tunnel_host() in page.url and await _tunnel_serving(page):
                    return await ctx.cookies(), None
                await page.wait_for_timeout(800)
            if "authserver" not in page.url:
                raise WebVpnError("CAS login form not reached (bounce chain stuck)")

            # Credential loop: fill → Enter (the form submits itself), then
            # classify the outcome. Captcha is surfaced, never guessed.
            last_error = ""
            for attempt in range(3):
                await page.wait_for_selector("#username", timeout=10000)
                await page.fill("#username", username)
                await page.fill("#password", password)
                if captcha:
                    for sel in ("#captchaResponse", "input[name='captcha']"):
                        try:
                            await page.fill(sel, captcha, timeout=1200)
                            break
                        except Exception:  # noqa: BLE001
                            continue
                await page.press("#password", "Enter")

                while time.monotonic() < deadline and "authserver" in page.url:
                    await page.wait_for_timeout(1000)
                if "authserver" not in page.url:
                    break

                last_error = await _read_cas_error(page)
                cap_uri = await _captcha_data_uri(page)
                if cap_uri or "验证码" in last_error:
                    return None, WebVpnOutcome(
                        ok=False,
                        message=last_error or "captcha required",
                        needs_captcha=True,
                        captcha_url=cap_uri,
                    )
                if last_error:
                    return None, WebVpnOutcome(ok=False, message=last_error)
                # No message and still on CAS: retry once, then give up.
            else:
                return None, WebVpnOutcome(
                    ok=False, message=last_error or "CAS did not accept the login"
                )

            # SSO chain is running; wait until the tunnel SERVES jwapp.
            while time.monotonic() < deadline:
                if _tunnel_host() in page.url and await _tunnel_serving(page):
                    return await ctx.cookies(), None
                await page.wait_for_timeout(1000)
            raise WebVpnError("tunnel did not become ready before the timeout")
        finally:
            await browser.close()


async def _reauth_cookies(client: httpx.AsyncClient) -> Optional[List[Dict[str, Any]]]:
    """
    Re-walk the SSO chain WITHOUT credentials: the CAS TGT cookie usually
    outlives the tunnel session, so a browser seeded with the current jar
    gets re-ticketed automatically.
    """
    from playwright.async_api import async_playwright

    deadline = time.monotonic() + settings.webvpn_login_timeout_s
    channel = settings.webvpn_browser_channel.strip() or None

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel=channel, headless=True)
        except Exception as exc:  # noqa: BLE001
            raise WebVpnError(f"cannot launch browser ({channel or 'bundled'}): {exc}") from exc
        try:
            ctx = await browser.new_context(ignore_https_errors=True, locale="zh-CN")
            await ctx.add_cookies(cookies_from_client(client))
            page = await ctx.new_page()
            try:
                await page.goto(
                    tunnel_entry_url(), wait_until="domcontentloaded",
                    timeout=settings.webvpn_login_timeout_s * 1000,
                )
            except Exception:  # noqa: BLE001 - keep polling; JS may still settle
                pass
            while time.monotonic() < deadline:
                if "authserver" in page.url and "login" in page.url:
                    return None  # TGT gone — needs full credentials
                if _tunnel_host() in page.url and await _tunnel_serving(page):
                    return await ctx.cookies()
                await page.wait_for_timeout(1000)
            return None
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class WebVpnClient:
    """
    Browser-assisted Srun WebVPN login.

    Contract (unchanged from the httpx design): `login()` returns a
    WebVpnOutcome; on success `outcome.session.client` is the data-plane
    httpx client the Session keeps. The caller's per-login httpx client is
    NOT used for the webvpn path (the browser replaces it), so `_login_webvpn`
    in main.py closes it.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = client  # accepted for interface compatibility

    async def login(
        self, username: str, password: str, captcha: str = ""
    ) -> WebVpnOutcome:
        async with login_gate:
            try:
                cookies, failure = await _browser_cookies(username, password, captcha)
            except WebVpnError:
                raise
            except Exception as exc:  # noqa: BLE001 - any playwright blowup
                raise WebVpnError(f"WebVPN browser login failed: {exc}") from exc

        if failure is not None:
            return failure
        assert cookies is not None
        return WebVpnOutcome(
            ok=True,
            session=WebVpnSession(
                client=client_from_cookies(cookies), home_root=tunnel_home_root()
            ),
        )


async def refresh_tunnel_session(client: httpx.AsyncClient) -> bool:
    """
    Best-effort re-authentication WITHOUT credentials for an existing data
    client (the CAS TGT cookie often outlives the tunnel session). On success
    the SAME client's jar is refreshed in place.
    """
    try:
        cookies = await _reauth_cookies(client)
    except (WebVpnError, Exception):  # noqa: BLE001 - refresh is best-effort
        return False
    if not cookies:
        return False
    fresh = client_from_cookies(cookies)
    # Swap jar contents in place so closures holding `client` keep working.
    for morsel in list(client.cookies.jar):
        client.cookies.jar.clear(morsel.domain, morsel.path, morsel.name)
    for ck in cookies:
        client.cookies.set(ck["name"], ck["value"], domain=ck["domain"], path=ck["path"])
    await fresh.aclose()
    return True
