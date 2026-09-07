"""
Canonical schedule model plus the RFC 5545 (iCalendar) emitter.

The upstream jwapp payload is messy and varies between campuses, so ingestion
is normalised into these dataclasses first. Everything downstream — preview,
ICS export, de-duplication — works against this model only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One teaching period (节次), e.g. period 1 = 08:00-08:45."""

    index: int
    start: time
    end: time


@dataclass(frozen=True)
class WeekInfo:
    """Calendar week of a term. `start` is the Monday of that week."""

    index: int
    start: date
    end: date


@dataclass(frozen=True)
class Meeting:
    """A recurring course slot inside one term."""

    name: str
    day_of_week: int  # 1 = Monday .. 7 = Sunday
    start_section: int
    end_section: int
    weeks: Sequence[int]
    teacher: Optional[str] = None
    location: Optional[str] = None
    code: Optional[str] = None
    class_name: Optional[str] = None
    credit: Optional[str] = None
    raw_weeks: Optional[str] = None

    @property
    def week_label(self) -> str:
        # Prefer the clean parsed week range (e.g. "1-14") over the raw
        # combined weeksAndTeachers string, which also embeds teacher names.
        return format_weeks(self.weeks) or (self.raw_weeks or "")

    @property
    def section_label(self) -> str:
        if self.start_section == self.end_section:
            return str(self.start_section)
        return f"{self.start_section}-{self.end_section}"


@dataclass
class TermSchedule:
    term_code: str
    term_name: str
    weeks: List[WeekInfo] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)
    meetings: List[Meeting] = field(default_factory=list)

    def section(self, index: int) -> Optional[Section]:
        for s in self.sections:
            if s.index == index:
                return s
        return None

    def week(self, index: int) -> Optional[WeekInfo]:
        for w in self.weeks:
            if w.index == index:
                return w
        return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_WEEK_RANGE = re.compile(r"(\d+)\s*[-~－—]\s*(\d+)")
_WEEK_SINGLE = re.compile(r"\d+")
_ODD = ("单", "odd", "单周")
_EVEN = ("双", "even", "双周")


def parse_weeks(raw: str) -> List[int]:
    """
    Parse the many shapes a Chinese timetable uses for 周次.

    >>> parse_weeks("1-16")
    [1, 2, ..., 16]
    >>> parse_weeks("1-15(单)")
    [1, 3, 5, ..., 15]
    >>> parse_weeks("1-8,10-16")
    [1..8, 10..16]

    Unknown input degrades to an empty list rather than raising, so a single
    malformed row can never take down a whole export.
    """
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        return [int(raw)]
    if isinstance(raw, (list, tuple)):
        out: List[int] = []
        for item in raw:
            out.extend(parse_weeks(str(item)))
        return sorted(set(out))

    text = str(raw).strip()
    if not text:
        return []

    odd_only = any(k in text for k in _ODD)
    even_only = any(k in text for k in _EVEN)

    weeks: set[int] = set()
    consumed: List[Tuple[int, int]] = []
    for m in _WEEK_RANGE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        weeks.update(range(lo, hi + 1))
        consumed.append(m.span())

    remainder = text
    for start, end in sorted(consumed, reverse=True):
        remainder = remainder[:start] + " " + remainder[end:]

    for m in _WEEK_SINGLE.finditer(remainder):
        weeks.add(int(m.group(0)))

    if odd_only and not even_only:
        weeks = {w for w in weeks if w % 2 == 1}
    elif even_only and not odd_only:
        weeks = {w for w in weeks if w % 2 == 0}

    return sorted(w for w in weeks if 1 <= w <= 60)


def format_weeks(weeks: Iterable[int]) -> str:
    """Render a week list back into a compact human string."""
    nums = sorted(set(weeks))
    if not nums:
        return ""
    parts: List[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def parse_time(value: str) -> Optional[time]:
    """Accept `08:00`, `8:00`, `0800`, `08:00:00`."""
    if not value:
        return None
    text = str(value).strip()
    m = re.match(r"^(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?$", text)
    if m:
        return time(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    m = re.match(r"^(\d{2})(\d{2})$", text)
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    return None


def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


# ---------------------------------------------------------------------------
# iCalendar emitter
# ---------------------------------------------------------------------------

_TZID = "Asia/Shanghai"

_VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Asia/Shanghai
X-LIC-LOCATION:Asia/Shanghai
BEGIN:STANDARD
DTSTART:19910915T000000
TZOFFSETFROM:+0900
TZOFFSETTO:+0800
TZNAME:CST
END:STANDARD
END:VTIMEZONE"""


def _escape(text: str) -> str:
    out = (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return out.replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 §3.1: fold lines at 75 octets, continuation prefixed by space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks: List[bytes] = []
    limit = 75
    pos = 0
    while pos < len(raw):
        take = limit if not chunks else limit - 1
        # never split a UTF-8 codepoint
        end = pos + take
        while end > pos and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[pos:end])
        pos = end
    out = chunks[0].decode("utf-8")
    for chunk in chunks[1:]:
        out += "\r\n " + chunk.decode("utf-8")
    return out


@dataclass
class IcsOptions:
    reminder_minutes: int = 15
    include_teacher: bool = True
    include_location: bool = True
    language: str = "zh"
    calendar_name: str = "SMBU 课表"


_DESCRIPTION_LABELS = {
    "zh": {"teacher": "教师", "weeks": "周次", "sections": "节次", "code": "课程号"},
    "en": {"teacher": "Teacher", "weeks": "Weeks", "sections": "Periods", "code": "Code"},
    "ru": {
        "teacher": "Преподаватель",
        "weeks": "Недели",
        "sections": "Пары",
        "code": "Код",
    },
}


def build_ics(
    schedule: TermSchedule,
    student_id: str,
    options: Optional[IcsOptions] = None,
    now: Optional[datetime] = None,
) -> str:
    """
    Emit a complete iCalendar document.

    Every individual session becomes its own VEVENT. Recurrence rules were
    considered and rejected: campus timetables routinely use irregular week
    sets (1-8,10-16 / 单周 / 实践周), which RRULE cannot express without
    generating EXDATE noise. Explicit events are always correct and every
    phone calendar handles them.
    """
    opts = options or IcsOptions()
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    labels = _DESCRIPTION_LABELS.get(opts.language, _DESCRIPTION_LABELS["zh"])

    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{opts.ics_product_id if hasattr(opts, 'ics_product_id') else '-//SMBU//Schedule Sync//CN'}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(opts.calendar_name)}",
        f"X-WR-TIMEZONE:{_TZID}",
        "X-WR-CALDESC:SMBU course schedule",
    ]
    lines.extend(_VTIMEZONE.split("\n"))

    emitted = 0
    for meeting in schedule.meetings:
        start_sec = schedule.section(meeting.start_section)
        end_sec = schedule.section(meeting.end_section)
        if start_sec is None or end_sec is None:
            continue
        if end_sec.end <= start_sec.start:
            continue

        for week_index in meeting.weeks:
            week = schedule.week(week_index)
            if week is None:
                continue
            day = week.start + timedelta(days=meeting.day_of_week - 1)
            dt_start = datetime.combine(day, start_sec.start)
            dt_end = datetime.combine(day, end_sec.end)

            summary = meeting.name
            parts: List[str] = []
            if opts.include_teacher and meeting.teacher:
                parts.append(f"{labels['teacher']}: {meeting.teacher}")
            parts.append(f"{labels['weeks']}: {meeting.week_label}")
            parts.append(f"{labels['sections']}: {meeting.section_label}")
            if meeting.code:
                parts.append(f"{labels['code']}: {meeting.code}")

            identity = "|".join(
                [
                    student_id,
                    schedule.term_code,
                    meeting.code or meeting.name,
                    str(meeting.day_of_week),
                    str(meeting.start_section),
                    str(week_index),
                ]
            )
            uid = hashlib.md5(identity.encode("utf-8")).hexdigest()

            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{uid}@smbu-schedule.local",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART;TZID={_TZID}:{dt_start.strftime('%Y%m%dT%H%M%S')}",
                    f"DTEND;TZID={_TZID}:{dt_end.strftime('%Y%m%dT%H%M%S')}",
                    f"SUMMARY:{_escape(summary)}",
                    f"DESCRIPTION:{_escape(' / '.join(parts))}",
                ]
            )
            if opts.include_location and meeting.location:
                lines.append(f"LOCATION:{_escape(meeting.location)}")
            lines.append("CATEGORIES:COURSE")
            lines.append("TRANSP:OPAQUE")
            if opts.reminder_minutes > 0:
                lines.extend(
                    [
                        "BEGIN:VALARM",
                        "ACTION:DISPLAY",
                        "TRIGGER;RELATED=START:"
                        f"-PT{int(opts.reminder_minutes)}M",
                        "DESCRIPTION:Reminder",
                        "END:VALARM",
                    ]
                )
            lines.append("END:VEVENT")
            emitted += 1

    lines.append("END:VCALENDAR")
    body = "\r\n".join(_fold(line) for line in lines)
    return body


def count_events(schedule: TermSchedule) -> int:
    total = 0
    for m in schedule.meetings:
        if schedule.section(m.start_section) and schedule.section(m.end_section):
            total += len([w for w in m.weeks if schedule.week(w)])
    return total
