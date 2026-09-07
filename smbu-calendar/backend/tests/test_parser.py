"""
Offline validation of the ingestion layer.

We cannot reach the campus portal from CI, so we exercise the normalisers with
realistic payloads that mirror the field shapes 强智 jwapp uses across
deployments. This locks the mapping logic in place; the live /api/debug/raw
endpoint only needs to confirm which variant SMBU actually emits.
"""

from datetime import date, time

from app import schedule as S
from app.jw import (
    normalize_meetings,
    normalize_sections,
    normalize_terms,
    normalize_weeks,
)


def test_parse_weeks_shapes():
    assert normalize_weeks is not None  # imported
    assert S.parse_weeks("1-16") == list(range(1, 17))
    assert S.parse_weeks("1-15(单)") == list(range(1, 16, 2))
    assert S.parse_weeks("2-16(双)") == list(range(2, 17, 2))
    assert S.parse_weeks("1-8,10-16") == list(range(1, 9)) + list(range(10, 17))
    assert S.parse_weeks("3,5,7") == [3, 5, 7]
    assert S.parse_weeks("") == []


def test_normalize_terms_handles_envelopes():
    payload = {
        "datas": [
            {"xnxqdm": "2025-2026-2", "xnxqmc": "2025-2026学年第2学期", "dqxnxq": "1"},
            {"xnxqdm": "2025-2026-1", "xnxqmc": "2025-2026学年第1学期"},
        ]
    }
    terms = normalize_terms(payload)
    assert terms[0]["code"] == "2025-2026-2"
    assert terms[0]["current"] is True
    # newest first
    assert terms[0]["code"] >= terms[1]["code"]


def test_normalize_weeks_with_dates():
    payload = [
        {"zc": 1, "ksrq": "2026-02-23", "jsrq": "2026-03-01"},
        {"zc": 2, "ksrq": "2026-03-02", "jsrq": "2026-03-08"},
    ]
    weeks = normalize_weeks(payload)
    assert len(weeks) == 2
    assert weeks[0].start == date(2026, 2, 23)
    assert weeks[1].end == date(2026, 3, 8)


def test_normalize_sections():
    payload = [
        {"jc": 1, "kssj": "08:00", "jssj": "08:45"},
        {"jc": 2, "kssj": "08:55", "jssj": "09:40"},
    ]
    sections = normalize_sections(payload)
    assert sections[0].start == time(8, 0)
    assert sections[1].end == time(9, 40)


def test_normalize_meetings_typical_qiangzhi():
    payload = {
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
    meetings = normalize_meetings(payload)
    assert len(meetings) == 1
    m = meetings[0]
    assert m.name == "高等数学"
    assert m.teacher == "张三"
    assert m.location == "主楼A101"
    assert m.day_of_week == 1
    assert m.start_section == 1 and m.end_section == 2
    assert m.weeks == list(range(1, 17))
    assert m.week_label == "1-16"
    assert m.section_label == "1-2"


def test_normalize_meetings_zero_indexed_dow_and_odd_weeks():
    payload = [
        {"kcmc": "线性代数", "xqj": 0, "jc": 3, "zc": "1-15(单)"},
    ]
    meetings = normalize_meetings(payload)
    assert meetings[0].day_of_week == 7  # 0 mapped to Sunday per normaliser
    assert meetings[0].weeks == list(range(1, 16, 2))


def test_build_ics_emits_one_event_per_session():
    sched = S.TermSchedule(
        term_code="2025-2026-2",
        term_name="2025-2026学年第2学期",
        weeks=[
            S.WeekInfo(1, date(2026, 2, 23), date(2026, 3, 1)),
            S.WeekInfo(2, date(2026, 3, 2), date(2026, 3, 8)),
        ],
        sections=[
            S.Section(1, time(8, 0), time(8, 45)),
            S.Section(2, time(8, 55), time(9, 40)),
        ],
        meetings=[
            S.Meeting(
                name="高等数学",
                day_of_week=1,
                start_section=1,
                end_section=2,
                weeks=[1, 2],
                teacher="张三",
                location="主楼A101",
                code="MATH1001",
                raw_weeks="1-2",
            )
        ],
    )
    ics = S.build_ics(sched, student_id="20240001", options=S.IcsOptions(language="zh"))
    # 2 sessions -> 2 VEVENTs (+1 calendar wrapper)
    assert ics.count("BEGIN:VEVENT") == 2
    assert "DTSTART;TZID=Asia/Shanghai:20260223T080000" in ics
    assert "DTEND;TZID=Asia/Shanghai:20260302T094000" in ics
    assert "SUMMARY:高等数学" in ics
    # UID is deterministic so re-import updates rather than duplicates.
    assert "UID:" in ics and "@smbu-schedule.local" in ics
    # RFC5545 CRLF line endings and VTIMEZONE present.
    assert "\r\n" in ics
    assert "BEGIN:VTIMEZONE" in ics
    # Russian label path works without raising.
    ru = S.build_ics(sched, student_id="x", options=S.IcsOptions(language="ru"))
    assert "BEGIN:VEVENT" in ru


if __name__ == "__main__":
    import sys

    fn = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for f in fn:
        try:
            f()
            print(f"ok  {f.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {f.__name__}: {e}")
    print(f"\n{len(fn) - failed}/{len(fn)} passed")
    sys.exit(1 if failed else 0)
