"""
Local mock of the SMBU CAS + jwapp upstream.

Serves the REAL captured payload shapes (transformed back into the campus
envelope) so the production server can be load-tested without ever touching the
real portal — which would risk captcha-locking the student account.

Run:  python mock_upstream.py   (listens on :8799)
"""
from __future__ import annotations

import json
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
CAS_HTML = """<!doctype html><html><body>
<form id="casForm">
  <input type="hidden" id="pwdEncryptSalt" value="0123456789abcdef"/>
  <input type="hidden" id="lt" name="lt" value="LT-MOCK"/>
  <input type="hidden" id="execution" name="execution" value="e1s1"/>
  <input type="hidden" id="saltPassword" name="password" value=""/>
</form>
</body></html>"""


def _load(name: str):
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def build_envelopes():
    current = _load("current_user.json")
    terms_norm = _load("terms.json")  # normalized list
    sched = _load("schedule_2026-2027-1.json")

    terms_env = {
        "code": "0",
        "datas": [
            {
                "itemCode": t["code"],
                "itemName": t["name"],
                "selected": t.get("current", False),
            }
            for t in terms_norm
        ],
    }

    weeks_env = {
        "code": "0",
        "datas": [
            {"serialNumber": w["index"], "startDate": w["start"], "endDate": w["end"]}
            for w in sched["term_weeks"]
        ],
    }

    sections_env = {
        "code": "0",
        "datas": [
            {"code": s["index"], "startTime": s["start"], "endTime": s["end"]}
            for s in sched["sections"]
        ],
    }

    arranged = []
    for m in sched["schedule_detail (normalized)"]:
        arranged.append(
            {
                "courseName": m["name"],
                "dayOfWeek": m["day_of_week"],
                "beginSection": m["start_section"],
                "endSection": m["end_section"],
                "weeksAndTeachers": m.get("raw_weeks") or f"{m['weeks']}周",
                "placeName": m.get("location"),
                "courseCode": m.get("code"),
                "credit": m.get("credit"),
            }
        )
    detail_env = {"code": "0", "datas": {"arrangedList": arranged}}

    return {
        "current": current,
        "terms": terms_env,
        "weeks": weeks_env,
        "sections": sections_env,
        "detail": detail_env,
    }


ENVS = build_envelopes()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/authserver/login":
            self._send(200, CAS_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/jwapp/sys/homeapp/index.do":
            self._send(200, b'{"code":"0","datas":{"ok":true}}')
            return
        if path.endswith("/api/home/currentUser.do"):
            return self._send(200, json.dumps(ENVS["current"]).encode("utf-8"))
        if path.endswith("/api/home/kb/xnxq.do"):
            return self._send(200, json.dumps(ENVS["terms"]).encode("utf-8"))
        if path.endswith("/api/home/getTermWeeks.do"):
            return self._send(200, json.dumps(ENVS["weeks"]).encode("utf-8"))
        if path.endswith("/api/home/student/getSections.do"):
            return self._send(200, json.dumps(ENVS["sections"]).encode("utf-8"))
        if path.endswith("/api/home/student/getMyScheduleDetail.do"):
            return self._send(200, json.dumps(ENVS["detail"]).encode("utf-8"))
        if path.endswith("/api/home/student/courses.do"):
            return self._send(200, json.dumps({"code": "0", "datas": []}).encode("utf-8"))
        self._send(404, b'{"code":"#E404","msg":"not found"}')

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/authserver/login":
            # Mimic CAS: redirect to the service URL carrying a ticket.
            loc = "/jwapp/sys/homeapp/index.do?ticket=ST-MOCK-" + str(id(self))
            self.send_response(302)
            self.send_header("Location", loc)
            self.end_headers()
            return
        self._send(404, b'{"code":"#E404","msg":"not found"}')


def main() -> None:
    # 0.0.0.0 (not 127.0.0.1) so the app container can reach it over the
    # Docker bridge network by service name.
    server = ThreadingHTTPServer(("0.0.0.0", 8799), Handler)
    print("mock upstream on http://0.0.0.0:8799", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
