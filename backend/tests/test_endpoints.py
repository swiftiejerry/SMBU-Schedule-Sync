"""
End-to-end HTTP pipeline test with a stubbed upstream.

No campus credentials, no browser. We replace the CAS login and the jwapp REST
calls with canned responses so the REAL FastAPI routes, session store, rate
limiter and ICS serialisation are exercised exactly as they would be live.

Collected by pytest:

    pytest -q

(The file used to be a hand-rolled `run()` script executed via
`python tests/test_endpoints.py`; pytest saw zero tests in it, so the most
important suite — the one exercising the real routes — was invisible to CI.)
"""

import pytest
from fastapi.testclient import TestClient

import app.cas as cas
import app.jw as jw
import app.main as main
from app.cas import LoginOutcome
from app.infra import SlidingWindowLimiter

SAMPLE_USER = {"xh": "20240001", "xm": "左典典 Test"}
SAMPLE_TERMS = {
    "datas": [
        {"xnxqdm": "2025-2026-2", "xnxqmc": "2025-2026学年第2学期", "dqxnxq": "1"},
        {"xnxqdm": "2025-2026-1", "xnxqmc": "2025-2026学年第1学期"},
    ]
}
SAMPLE_WEEKS = [{"zc": 1, "ksrq": "2026-02-23", "jsrq": "2026-03-01"}]
SAMPLE_SECTIONS = [
    {"jc": 1, "kssj": "08:00", "jssj": "08:45"},
    {"jc": 2, "kssj": "08:55", "jssj": "09:40"},
]
SAMPLE_COURSES = {
    "datas": [
        {
            "kcmc": "高等数学",
            "jsxm": "张三",
            "jsmc": "主楼A101",
            "xqj": 1,
            "ksjc": 1,
            "jsjc": 2,
            "zc": "1-16",
            "kcdm": "MATH1001",
        }
    ]
}


async def fake_login(self, username, password, captcha=""):  # noqa: ANN001
    return LoginOutcome(ok=True, ticket_url="http://x", cookies={"JSESSIONID": "stub"})


async def fake_current_user(self):  # noqa: ANN001
    return SAMPLE_USER


async def fake_terms(self):  # noqa: ANN001
    return jw.normalize_terms(SAMPLE_TERMS)


async def fake_term_weeks(self, term_code):  # noqa: ANN001
    return jw.normalize_weeks(SAMPLE_WEEKS)


async def fake_sections(self, term_code):  # noqa: ANN001
    return jw.normalize_sections(SAMPLE_SECTIONS)


async def fake_courses(self, term_code):  # noqa: ANN001
    return jw.normalize_meetings(SAMPLE_COURSES)


async def fake_schedule_detail(self, term_code):  # noqa: ANN001
    return jw.normalize_meetings(SAMPLE_COURSES)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(cas.CasClient, "login", fake_login)
    monkeypatch.setattr(jw.JwClient, "current_user", fake_current_user)
    monkeypatch.setattr(jw.JwClient, "terms", fake_terms)
    monkeypatch.setattr(jw.JwClient, "term_weeks", fake_term_weeks)
    monkeypatch.setattr(jw.JwClient, "sections", fake_sections)
    monkeypatch.setattr(jw.JwClient, "courses", fake_courses)
    monkeypatch.setattr(jw.JwClient, "schedule_detail", fake_schedule_detail)
    # A fresh limiter per test keeps the suite order-independent no matter how
    # many logins a rerun or `-x` retry performs.
    monkeypatch.setattr(main, "limiter", SlidingWindowLimiter())
    with TestClient(main.app) as c:
        yield c


def _login(client) -> dict:
    r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token
    return {"X-Session-Token": token}


def test_login_issues_token(client):
    r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["student_id"] == SAMPLE_USER["xh"]
    assert body["display_name"] == SAMPLE_USER["xm"]
    assert body["expires_in"] > 0


def test_terms_ordered_and_current(client):
    headers = _login(client)
    r = client.get("/api/terms", headers=headers)
    assert r.status_code == 200
    terms = r.json()["terms"]
    assert [t["code"] for t in terms] == ["2025-2026-2", "2025-2026-1"]
    assert terms[0]["current"] is True


def test_schedule_meetings_and_weeks(client):
    headers = _login(client)
    r = client.get("/api/schedule", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["meetings"]) == 1
    assert body["event_count"] > 0
    assert body["weeks"] and "start" in body["weeks"][0]


def test_ics_export_matches_event_count(client):
    headers = _login(client)
    event_count = client.get("/api/schedule", headers=headers).json()["event_count"]
    r = client.get(
        "/api/export.ics", headers=headers, params={"lang": "en", "reminder": "15"}
    )
    assert r.status_code == 200
    assert "text/calendar" in r.headers.get("content-type", "")
    ics = r.text
    assert ics.count("BEGIN:VEVENT") == event_count
    assert ics.count("BEGIN:VALARM") == event_count
    assert "SUMMARY:" in ics
    assert "Weeks" in ics or "DESCRIPTION" in ics


def test_missing_token_is_401(client):
    assert client.get("/api/terms").status_code == 401


def test_logout_destroys_session(client):
    headers = _login(client)
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/terms", headers=headers).status_code == 401
