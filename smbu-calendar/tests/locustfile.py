"""
Stress test for SMBU Schedule Sync.

The goal is not to break the portal but to prove the *service* stays upright
under load: bounded concurrency, graceful 429s, no upstream fan-out.

Run against a local instance:

    locust -f tests/locustfile.py --host http://127.0.0.1:8770 -u 200 -r 40 -t 3m

A pre-authenticated token can be supplied so the authed mix runs without
credentials:

    SMBU_TOKEN=xxxx locust -f tests/locustfile.py --host http://127.0.0.1:8770

Or supply credentials to exercise the full login path (expect CAS-side rate
limits / captcha, so keep user count low for that):

    SMBU_USER=2024xxx SMBU_PASS=xxxx locust -f tests/locustfile.py ... -u 8 -r 2
"""

import os

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

TOKEN = os.getenv("SMBU_TOKEN")
USER = os.getenv("SMBU_USER")
PASSWORD = os.getenv("SMBU_PASS")
TERM = os.getenv("SMBU_TERM")


@events.test_start.add_listener
def on_start(environment, **_):
    if isinstance(environment.runner, MasterRunner):
        return
    print("\n[locust] mode:",
          "pre-authed token" if TOKEN else
          ("full login" if USER and PASSWORD else "health-only (no creds)"))
    if not (TOKEN or (USER and PASSWORD)):
        print("[locust] no SMBU_TOKEN/SMBU_USER set — only /api/health will run\n")


class CalendarUser(HttpUser):
    wait_time = between(1.0, 3.0)
    abstract = True

    def on_start(self):
        self.session_token = None
        if TOKEN:
            self.session_token = TOKEN
        elif USER and PASSWORD:
            with self.client.post(
                "/api/auth/login",
                json={"username": USER, "password": PASSWORD},
                catch_response=True,
                name="auth.login",
            ) as resp:
                if resp.status_code == 200:
                    self.session_token = resp.json().get("token")

    def _headers(self):
        h = {"X-Session-Token": self.session_token} if self.session_token else {}
        return h


class AuthedUser(CalendarUser):
    """Full flow against an authenticated session."""

    @task(5)
    def health(self):
        self.client.get("/api/health", name="health")

    @task(3)
    def terms(self):
        if not self.session_token:
            return
        self.client.get("/api/terms", headers=self._headers(), name="terms")

    @task(6)
    def schedule(self):
        if not self.session_token:
            return
        params = {"term": TERM} if TERM else {}
        self.client.get(
            "/api/schedule", params=params, headers=self._headers(), name="schedule"
        )

    @task(2)
    def export_ics(self):
        if not self.session_token:
            return
        params = {"term": TERM, "lang": "zh", "reminder": "15"} if TERM else {}
        self.client.get(
            "/api/export.ics",
            params=params,
            headers=self._headers(),
            name="export.ics",
        )


class HealthOnlyUser(HttpUser):
    """Used when no credentials are available — proves the gateway survives."""

    wait_time = between(0.5, 1.5)

    @task
    def health(self):
        self.client.get("/api/health", name="health")
