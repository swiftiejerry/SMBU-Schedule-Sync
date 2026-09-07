"""
Headless capture — proves the login path works without any browser.

This reuses the exact same CasClient / JwClient the running service uses, so a
successful run here means the service will work for real users. It only READS
the timetable; it never writes, never stores, never logs credentials.

Usage (credentials via env — never type them into chat):

    SMBU_USER=2024xxxx SMBU_PASS='***' \
        python capture.py --out _probe/raw

For a single term instead of the default:

    SMBU_USER=... SMBU_PASS='...' python capture.py --term 2025-2026-1 --out _probe/raw

The dumped JSON is what /api/debug/raw returns; feed it back to calibrate the
field mappings in app/jw.py if SMBU uses names this code does not yet recognise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from app.cas import CasClient, CasError
from app.config import settings
from app.infra import build_http_client
from app import jw as jwmod


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--term", default=None, help="specific term code to capture")
    parser.add_argument("--out", default="_probe/raw", help="directory for raw dumps")
    args = parser.parse_args()

    user = os.getenv("SMBU_USER")
    password = os.getenv("SMBU_PASS")
    if not user or not password:
        print("ERROR: set SMBU_USER and SMBU_PASS environment variables.", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    client = build_http_client()
    try:
        cas = CasClient(client)
        print("[1/5] CAS login ...")
        outcome = await cas.login(user, password)
        if not outcome.ok:
            print(f"    login failed: {outcome.message}")
            if outcome.captcha_url:
                print(f"    captcha required: {outcome.captcha_url}")
            return 1
        print(f"    ok (student={outcome.cookies.get('JSESSIONID', '')[:8]}…)")

        jw = jwmod.JwClient(client)
        user_info = await jw.current_user()
        with open(f"{args.out}/current_user.json", "w", encoding="utf-8") as f:
            json.dump(user_info, f, ensure_ascii=False, indent=2, default=str)
        print("[2/5] current user captured")

        terms = await jw.terms()
        with open(f"{args.out}/terms.json", "w", encoding="utf-8") as f:
            json.dump(terms, f, ensure_ascii=False, indent=2, default=str)
        print(f"[3/5] {len(terms)} terms captured")

        term = args.term
        if not term:
            # Prefer the campus-flagged current term, else the newest by code.
            current = next((t for t in terms if t.get("current")), None)
            term = (current or terms[0])["code"] if terms else None
        if not term:
            print("    no terms to capture schedules for")
            return 0
        print(f"[4/5] capturing term {term} ...")

        weeks = await jw.term_weeks(term)
        sections = await jw.sections(term)
        courses = await jw.courses(term)
        detail = await jw.schedule_detail(term)

        dump = {
            "term": term,
            "term_weeks": [w.__dict__ for w in weeks],
            "sections": [s.__dict__ for s in sections],
            "courses (normalized)": [m.__dict__ for m in courses],
            "schedule_detail (normalized)": [m.__dict__ for m in detail],
        }
        with open(f"{args.out}/schedule_{term}.json", "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
        print(
            f"    weeks={len(weeks)} sections={len(sections)} "
            f"courses={len(courses)} detail={len(detail)}"
        )

        # Raw, untouched upstream payloads for calibration.
        raw = {
            "currentUser": user_info,
            "termWeeks": await jw._call("/api/home/getTermWeeks.do", {"termCode": term}, "GET"),
            "sections": await jw._call("/api/home/student/getSections.do", {"termCode": term}, "GET"),
            "courses": await jw._call("/api/home/student/courses.do", {"termCode": term}, "GET"),
            "scheduleDetail": await jw._call(
                "/api/home/student/getMyScheduleDetail.do", {"termCode": term}, "GET"
            ),
        }
        with open(f"{args.out}/raw_{term}.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2, default=str)
        print("[5/5] raw upstream payloads saved to", args.out)
        return 0
    except CasError as exc:
        print(f"CAS error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected: {exc!r}", file=sys.stderr)
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
