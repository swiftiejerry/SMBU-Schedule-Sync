"""
WebVPN (browser-assisted Srun tunnel) test suite.

Architecture under test (see WEBVPN.md):
    browser = login only  (patched here — real-browser behaviour is verified
              with a live account via _probe/webvpn_browser_handoff.py)
    httpx   = data plane  (real code path against a cookie-driven MockTransport)

The tunnel mock is COOKIE-driven like the real gateway: session validity is
read from the request's Cookie header, never from test-internal flags.

Run:  PYTHONPATH=. python tests/test_webvpn.py
"""

import asyncio
import sys
import traceback
import urllib.parse
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx

import app.jw as jw
import app.main as main
import app.webvpn as webvpn
from app.config import settings

TUNNEL = "jw-smbu-edu-cn-s.webvpn.smbu.edu.cn:8118"
TUNNEL_HOST = "jw-smbu-edu-cn-s.webvpn.smbu.edu.cn"  # cookie domains NEVER carry ports
TUNNEL_ROOT = f"https://{TUNNEL}/jwapp/sys/homeapp"

FAKE_COOKIES = [
    {"name": "TWFID", "value": "web-session-1", "domain": "webvpn.smbu.edu.cn", "path": "/"},
    {"name": "CASTGC", "value": "TGT-1", "domain": "authserver.smbu.edu.cn", "path": "/"},
    {"name": "TUNNELSID", "value": "jw-session-1", "domain": TUNNEL_HOST, "path": "/"},
]

SAMPLE_USER = {"xh": "20240001", "xm": "左典典"}
SAMPLE_TERMS = {
    "datas": [
        {"xnxqdm": "2025-2026-2", "xnxqmc": "2025-2026学年第2学期", "dqxnxq": "1"},
        {"xnxqdm": "2025-2026-1", "xnxqmc": "2025-2026学年第1学期"},
    ]
}
SAMPLE_WEEKS = {"datas": [{"zc": 1, "ksrq": "2026-02-23", "jsrq": "2026-03-01"}]}
SAMPLE_SECTIONS = {
    "datas": [
        {"jc": 1, "kssj": "08:00", "jssj": "08:45"},
        {"jc": 2, "kssj": "08:55", "jssj": "09:40"},
    ]
}
SAMPLE_ARRANGED = {
    "datas": [
        {
            "courseName": "高等数学",
            "dayOfWeek": 1,
            "beginSection": 1,
            "endSection": 2,
            "weeksAndTeachers": "1-16周[讲授]/张三[主讲]",
            "placeName": "主楼A101",
            "courseCode": "MATH1001",
            "teachingTarget": "2024级1班",
        }
    ]
}


def make_state(**over) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "requests": [],  # (method, url, cookie-header)
    }
    state.update(over)
    return state


def data_plane_transport(state: Dict[str, Any]) -> httpx.MockTransport:
    """Cookie-driven tunnel mock: session validity lives in the Cookie header."""

    def has_tunnel_sid(request: httpx.Request) -> bool:
        return "TUNNELSID=" in (request.headers.get("cookie") or "")

    async def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        state["requests"].append((request.method, url, request.headers.get("cookie", "")))
        parsed = urllib.parse.urlsplit(url)

        if parsed.netloc != TUNNEL:
            return httpx.Response(404, text=f"unexpected host {parsed.netloc}")

        if not has_tunnel_sid(request):
            # the real gateway bounces to the portal for browsers; for this
            # suite a plain 302 marker is enough — httpx would follow it into
            # the portal domain, which the data plane never does when warm
            return httpx.Response(
                302,
                headers={
                    "Location": "https://webvpn.smbu.edu.cn/?redirect_uri="
                    + urllib.parse.quote(url, safe="")
                },
            )

        path = parsed.path
        if path.endswith("currentUser.do"):
            return httpx.Response(200, json=SAMPLE_USER)
        if path.endswith("xnxq.do"):
            return httpx.Response(200, json=SAMPLE_TERMS)
        if path.endswith("getTermWeeks.do"):
            return httpx.Response(200, json=SAMPLE_WEEKS)
        if path.endswith("getSections.do"):
            return httpx.Response(200, json=SAMPLE_SECTIONS)
        if path.endswith("getMyScheduleDetail.do"):
            return httpx.Response(200, json=SAMPLE_ARRANGED)
        if path.endswith("courses.do"):
            return httpx.Response(200, json={"datas": []})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle)


def mock_client(state: Dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=data_plane_transport(state),
        follow_redirects=True,
        timeout=httpx.Timeout(5.0),
    )


# ---------------------------------------------------------------------------
# Patch points
# ---------------------------------------------------------------------------


@contextmanager
def patched_browser(result: Any, raises: Optional[Exception] = None, state: Optional[Dict[str, Any]] = None):
    """Replace the browser layer: `_browser_cookies` / `_reauth_cookies`."""

    async def fake_browser(user: str, pw: str, captcha: str):
        if raises is not None:
            raise raises
        assert result is not None
        return list(FAKE_COOKIES), None

    async def fake_reauth(client: httpx.AsyncClient):
        if raises is not None:
            raise raises
        return None if result is None else list(FAKE_COOKIES)

    old_browser, old_reauth = webvpn._browser_cookies, webvpn._reauth_cookies
    old_build = webvpn.build_http_client
    webvpn._browser_cookies = fake_browser  # type: ignore[assignment]
    webvpn._reauth_cookies = fake_reauth  # type: ignore[assignment]
    webvpn.build_http_client = lambda: mock_client(state or make_state())  # type: ignore[assignment]
    try:
        yield
    finally:
        webvpn._browser_cookies, webvpn._reauth_cookies = old_browser, old_reauth  # type: ignore[assignment]
        webvpn.build_http_client = old_build  # type: ignore[assignment]


def patch_main_transport(state: Dict[str, Any]):
    """Point main.build_http_client at the data-plane mock as well."""

    def factory():
        return mock_client(state)

    main.build_http_client = factory  # type: ignore[assignment]


def restore_main_transport():
    from app.infra import build_http_client as real

    main.build_http_client = real  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tunnel_mapping():
    assert webvpn.tunnel_home_root() == TUNNEL_ROOT
    assert webvpn.tunnel_entry_url() == f"{TUNNEL_ROOT}/home/index.html"
    assert webvpn._tunnel_host() == TUNNEL.split(":")[0]


def test_client_from_cookies_preserves_domains():
    client = webvpn.client_from_cookies(FAKE_COOKIES)
    assert client.cookies.get("TUNNELSID") == "jw-session-1"
    assert client.cookies.get("TWFID") == "web-session-1"
    assert client.cookies.get("CASTGC") == "TGT-1"


async def test_data_plane_rides_tunnel():
    state = make_state()
    client = mock_client(state)
    client.cookies.set("TUNNELSID", "jw-session-1", domain=TUNNEL_HOST, path="/")
    jwc = jw.JwClient(client, home_root=TUNNEL_ROOT)
    user = await jwc.current_user()
    assert user["xh"] == "20240001"
    terms = await jwc.terms()
    assert terms[0]["code"] == "2025-2026-2"
    hosts = {urllib.parse.urlsplit(u).netloc for (_, u, _) in state["requests"]}
    assert hosts == {TUNNEL}, hosts


async def test_login_success():
    with patched_browser(FAKE_COOKIES):
        outcome = await webvpn.WebVpnClient().login("20240001", "secret123")
    assert outcome.ok, outcome.message
    assert outcome.session is not None
    assert outcome.session.home_root == TUNNEL_ROOT
    assert outcome.session.client.cookies.get("TUNNELSID") == "jw-session-1"
    await outcome.session.client.aclose()


async def test_login_captcha_surfaced():
    failure = webvpn.WebVpnOutcome(
        ok=False, message="captcha required", needs_captcha=True,
        captcha_url="data:image/png;base64,AAAA",
    )
    with patched_browser(None), patched_browser_failure(failure):
        outcome = await webvpn.WebVpnClient().login("20240001", "secret123")
    assert not outcome.ok and outcome.needs_captcha
    assert outcome.captcha_url.startswith("data:image/png")


# helper context to make the browser layer return a failure outcome
from contextlib import ExitStack  # noqa: E402


def patched_browser_failure(failure: webvpn.WebVpnOutcome):
    class _CM:
        def __enter__(self):
            self._old = webvpn._browser_cookies

            async def fake(user, pw, captcha):
                return None, failure

            webvpn._browser_cookies = fake  # type: ignore[assignment]
            return self

        def __exit__(self, *exc):
            webvpn._browser_cookies = self._old  # type: ignore[assignment]
            return False

    return _CM()


async def test_login_bad_password():
    failure = webvpn.WebVpnOutcome(ok=False, message="Incorrect password")
    with patched_browser_failure(failure):
        outcome = await webvpn.WebVpnClient().login("20240001", "wrong")
    assert not outcome.ok
    assert "Incorrect password" in (outcome.message or "")
    assert not outcome.needs_captcha


async def test_login_browser_error_wrapped():
    with patched_browser(None, raises=RuntimeError("browser crashed")):
        try:
            await webvpn.WebVpnClient().login("20240001", "secret123")
            raise AssertionError("expected WebVpnError")
        except webvpn.WebVpnError as exc:
            assert "browser crashed" in str(exc)


async def test_login_gate_serialises():
    order: List[str] = []
    gate = webvpn._LoginGate(1)

    async def worker(tag: str, hold: float):
        async with gate:
            order.append(f"{tag}:acquired")
            await asyncio.sleep(hold)
            order.append(f"{tag}:released")

    await asyncio.gather(worker("a", 0.05), worker("b", 0.0))
    assert order.index("a:released") < order.index("b:acquired"), order


async def test_refresh_restores_session():
    client = webvpn.client_from_cookies(FAKE_COOKIES)
    stale = {"name": "TUNNELSID", "value": "old", "domain": TUNNEL, "path": "/"}
    client.cookies.set(stale["name"], "old", domain=TUNNEL, path="/")
    with patched_browser(FAKE_COOKIES):
        ok = await webvpn.refresh_tunnel_session(client)
    assert ok is True
    assert client.cookies.get("TUNNELSID") == "jw-session-1"


async def test_refresh_tgt_gone():
    client = webvpn.client_from_cookies(FAKE_COOKIES)
    with patched_browser(None):  # reauth layer reports "TGT gone" via None
        ok = await webvpn.refresh_tunnel_session(client)
    assert ok is False


async def test_main_end_to_end_webvpn():
    state = make_state()
    patch_main_transport(state)
    old_mode = settings.access_mode
    settings.access_mode = "webvpn"
    try:
        from fastapi.testclient import TestClient

        with patched_browser(FAKE_COOKIES, state=state):
            with TestClient(main.app) as client:
                r = client.post(
                    "/api/auth/login",
                    json={"username": "20240001", "password": "secret123"},
                )
                assert r.status_code == 200, r.text
                token = r.json()["token"]
                headers = {"X-Session-Token": token}

                r = client.get("/api/terms", headers=headers)
                assert r.status_code == 200, r.text
                assert r.json()["terms"][0]["code"] == "2025-2026-2"

                r = client.get("/api/schedule", headers=headers)
                assert r.status_code == 200, r.text
                body = r.json()
                assert len(body["meetings"]) == 1 and body["event_count"] > 0

                r = client.get("/api/export.ics", headers=headers)
                assert r.status_code == 200, r.text
                assert "BEGIN:VEVENT" in r.text

        api_hosts = {
            urllib.parse.urlsplit(u).netloc for (_, u, _) in state["requests"]
        }
        assert api_hosts == {TUNNEL}, api_hosts
    finally:
        settings.access_mode = old_mode
        restore_main_transport()


async def test_auto_falls_back_to_webvpn():
    state = make_state()
    patch_main_transport(state)
    old_mode = settings.access_mode
    settings.access_mode = "auto"

    async def boom(client, username, password, captcha):
        raise __import__("app.cas", fromlist=["CasError"]).CasError(
            "CAS unreachable: [Errno 111] Connection refused (jw.smbu.edu.cn)"
        )

    old_direct = main._login_direct
    main._login_direct = boom  # type: ignore[assignment]
    try:
        from fastapi.testclient import TestClient

        with patched_browser(FAKE_COOKIES, state=state):
            with TestClient(main.app) as client:
                r = client.post(
                    "/api/auth/login",
                    json={"username": "20240001", "password": "secret123"},
                )
                assert r.status_code == 200, r.text
                token = r.json()["token"]
                r = client.get("/api/terms", headers={"X-Session-Token": token})
                assert r.status_code == 200, r.text

        api_hosts = {
            urllib.parse.urlsplit(u).netloc for (_, u, _) in state["requests"]
        }
        assert api_hosts == {TUNNEL}, api_hosts
    finally:
        main._login_direct = old_direct  # type: ignore[assignment]
        settings.access_mode = old_mode
        restore_main_transport()


TESTS = [
    test_tunnel_mapping,
    test_client_from_cookies_preserves_domains,
    test_data_plane_rides_tunnel,
    test_login_success,
    test_login_captcha_surfaced,
    test_login_bad_password,
    test_login_browser_error_wrapped,
    test_login_gate_serialises,
    test_refresh_restores_session,
    test_refresh_tgt_gone,
    test_main_end_to_end_webvpn,
    test_auto_falls_back_to_webvpn,
]


def run() -> int:
    failed = 0
    for test in TESTS:
        try:
            if asyncio.iscoroutinefunction(test):
                asyncio.run(test())
            else:
                test()
            print(f"ok  {test.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    total = len(TESTS)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
