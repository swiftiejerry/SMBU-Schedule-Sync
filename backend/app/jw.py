"""
jwapp (强智) REST client.

Field names differ between campuses and even between versions of the same
deployment, so every normaliser accepts a list of candidate keys and picks
the first one present. That keeps ingestion working through upstream
renames instead of failing closed on the whole export.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from .config import settings
from .schedule import (
    Meeting,
    Section,
    TermSchedule,
    WeekInfo,
    parse_date,
    parse_time,
    parse_weeks,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class UpstreamError(Exception):
    def __init__(self, message: str, retryable: bool = True, status: int = 502):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class SessionExpired(UpstreamError):
    def __init__(self, message: str = "session expired"):
        super().__init__(message, retryable=False, status=401)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _pick(data: Dict[str, Any], *names: str) -> Any:
    """Case-insensitive first-match lookup across candidate key spellings."""
    lowered = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name.lower() in lowered:
            value = lowered[name.lower()]
            if value is not None and value != "":
                return value
    return None


def _rows(payload: Any) -> List[Dict[str, Any]]:
    """Unwrap the several envelope shapes jwapp uses."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("datas", "data", "rows", "list", "items", "result", "records", "arrangedList"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = _rows(value)
                if nested:
                    return nested
        # A bare object is itself the row.
        return [payload]
    return []


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------


def normalize_terms(payload: Any) -> List[Dict[str, Any]]:
    terms: List[Dict[str, Any]] = []
    for row in _rows(payload):
        code = _pick(row, "itemCode", "xnxqdm", "xnxq", "dm", "code", "termCode", "value", "id")
        name = _pick(row, "itemName", "xnxqmc", "mc", "name", "termName", "label", "text")
        if code is None and name is None:
            continue
        terms.append(
            {
                "code": str(code),
                "name": str(name or code),
                "current": bool(
                    _pick(row, "selected", "dqxnxq", "current", "isCurrent", "default") in
                    (True, 1, "1", "true", "True")
                ),
                "raw": row,
            }
        )
    # Newest first; upstream order is not guaranteed.
    terms.sort(key=lambda t: t["code"], reverse=True)
    return terms


def normalize_weeks(payload: Any) -> List[WeekInfo]:
    weeks: List[WeekInfo] = []
    for row in _rows(payload):
        index = _as_int(
            _pick(row, "serialNumber", "zc", "week", "weekIndex", "weekNo", "index", "zcm")
        )
        start = parse_date(
            str(_pick(row, "startDate", "ksrq", "start", "beginDate", "qsrq", "monday") or "")
        )
        end = parse_date(
            str(_pick(row, "endDate", "jsrq", "end", "finishDate", "zzrq", "sunday") or "")
        )
        if index is None or start is None:
            continue
        if end is None:
            end = start
        weeks.append(WeekInfo(index=index, start=start, end=end))
    weeks.sort(key=lambda w: w.index)
    return weeks


def normalize_sections(payload: Any) -> List[Section]:
    sections: List[Section] = []
    for row in _rows(payload):
        index = _as_int(
            _pick(row, "code", "sort", "jc", "jcm", "sectionCode", "section", "index", "order", "jxjc")
        )
        start = parse_time(
            str(_pick(row, "startTime", "kssj", "start", "beginTime", "qssj") or "")
        )
        end = parse_time(
            str(_pick(row, "endTime", "jssj", "end", "finishTime", "zzsj") or "")
        )
        if index is None or start is None:
            continue
        if end is None:
            end = start
        sections.append(Section(index=index, start=start, end=end))
    sections.sort(key=lambda s: s.index)
    return sections


def normalize_meetings(payload: Any) -> List[Meeting]:
    meetings: List[Meeting] = []
    for row in _rows(payload):
        name = _pick(
            row,
            "kcmc",
            "courseName",
            "kcxxmc",
            "course_name",
            "kcm",
            "name",
            "title",
        )
        if not name:
            continue

        day = _as_int(
            _pick(row, "xqj", "dayOfWeek", "weekday", "day", "xq", "skxq", "weekDay")
        )
        start_section = _as_int(
            _pick(row, "ksjc", "startSection", "jc", "beginSection", "jcs", "jcx")
        )
        end_section = _as_int(
            _pick(row, "jsjc", "endSection", "jc2", "finishSection", "jcxx")
        )
        raw_weeks = _pick(row, "zc", "zcs", "weeks", "weekList", "skzc", "zcsm")
        weeks = parse_weeks(raw_weeks if raw_weeks is not None else "")

        if day is None or start_section is None or not weeks:
            continue

        # 星期几 may arrive as 0-indexed (0=Mon) on some deployments.
        if day == 0:
            day = 7
        day = max(1, min(7, day))

        meetings.append(
            Meeting(
                name=str(name),
                day_of_week=day,
                start_section=start_section,
                end_section=end_section or start_section,
                weeks=weeks,
                teacher=_as_str(_pick(row, "jsxm", "teacherName", "teacher", "jsxm", "xm", "skjs")),
                location=_as_str(
                    _pick(
                        row,
                        "jsmc",
                        "classroomName",
                        "skdd",
                        "location",
                        "place",
                        "jxdd",
                        "dd",
                    )
                ),
                code=_as_str(_pick(row, "kcdm", "courseCode", "kch", "code", "courseId")),
                class_name=_as_str(_pick(row, "bjmc", "className", "bj", "class")),
                credit=_as_str(_pick(row, "xf", "credit", "xuefen")),
                raw_weeks=str(raw_weeks) if raw_weeks is not None else None,
            )
        )
    return meetings


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_weeks(text: Any) -> List[int]:
    """Pull the week range out of a `weeksAndTeachers` string such as
    ``"1-14周[讲授]/刘滨[主讲],贾云得[主讲]"``. Returns ``[]`` if none found.
    """
    if not text:
        return []
    seg = str(text).split("/")[0]            # "1-14周[讲授]"
    seg = re.sub(r"[\[\]（）()]", " ", seg)  # drop annotation brackets
    seg = re.sub(r"[讲授实践实验]", "", seg)  # drop teaching-type annotations
    seg = seg.replace("周", "").strip()
    return parse_weeks(seg)


def _extract_teacher(text: Any) -> Optional[str]:
    """Pull the teacher list out of a `weeksAndTeachers` string. The teacher
    segment follows the week segment, e.g. ``"刘滨[主讲],贾云得[主讲]"``."""
    if not text:
        return None
    parts = str(text).split("/")
    if len(parts) < 2:
        return None
    t = parts[1]
    t = re.sub(r"[\[\]（）()]", "", t)        # drop brackets
    t = re.sub(r"主讲|辅导|教师", "", t)       # drop role annotations
    t = t.replace("、", ",").strip().strip(",")
    return t or None


def normalize_arranged(payload: Any) -> List[Meeting]:
    """Normalise the `getMyScheduleDetail.do` `arrangedList` — SMBU's rich,
    structured schedule source (course name, period indices, day of week,
    location, and a combined weeks+teachers string)."""
    meetings: List[Meeting] = []
    for row in _rows(payload):
        name = _pick(row, "courseName", "kcmc", "course_name", "name", "title")
        if not name:
            continue

        day = _as_int(_pick(row, "dayOfWeek", "xqj", "weekday", "day"))
        if day is None:
            continue
        if day == 0:
            day = 7
        day = max(1, min(7, day))

        start_section = _as_int(_pick(row, "beginSection", "ksjc", "startSection", "jc"))
        end_section = _as_int(_pick(row, "endSection", "jsjc", "endSection", "jc2"))
        if start_section is None:
            continue

        wt = _pick(row, "weeksAndTeachers", "zcsm", "weeks", "skzc") or ""
        meetings.append(
            Meeting(
                name=str(name),
                day_of_week=day,
                start_section=start_section,
                end_section=end_section or start_section,
                weeks=_extract_weeks(wt),
                teacher=_extract_teacher(wt),
                location=_as_str(_pick(row, "placeName", "jsmc", "jxdd", "location", "place")),
                code=_as_str(_pick(row, "courseCode", "kcdm", "kch", "code", "courseId")),
                class_name=_as_str(_pick(row, "teachingTarget", "teachClassName", "bjmc", "className")),
                credit=_as_str(_pick(row, "credit", "xf")),
                raw_weeks=str(wt) if wt else None,
            )
        )
    return meetings


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JwClient:
    """Thin async wrapper over the homeapp JSON surface."""

    def __init__(self, client: httpx.AsyncClient, home_root: Optional[str] = None):
        self._client = client
        # Direct mode rides the configured campus root; a WebVPN session passes
        # its tunnel root instead — same paths, same payloads, different
        # authority, so every normaliser below is shared verbatim.
        self._home_root = home_root or settings.jw_home_root

    # -- low level ---------------------------------------------------------

    async def _call(
        self, path: str, params: Optional[Dict[str, Any]] = None, method: str = "POST"
    ) -> Any:
        url = f"{self._home_root}{path}"
        headers = {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self._home_root}/home/index.html",
            "Accept": "application/json, text/plain, */*",
        }
        try:
            if method == "GET":
                resp = await self._client.get(url, params=params, headers=headers)
            else:
                resp = await self._client.post(url, data=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise UpstreamError(f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"transport error calling {path}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise SessionExpired(f"upstream rejected {path} with {resp.status_code}")
        if resp.status_code >= 500:
            raise UpstreamError(f"upstream {path} returned {resp.status_code}")
        if resp.status_code >= 400:
            raise UpstreamError(
                f"upstream {path} returned {resp.status_code}", retryable=False, status=400
            )

        text = resp.text.strip()
        if not text:
            raise UpstreamError(f"empty response from {path}")
        if text.lstrip().startswith("<"):
            # Almost always the CAS interstitial -> the session is gone.
            raise SessionExpired(f"{path} returned HTML instead of JSON")

        import orjson

        try:
            payload = orjson.loads(text)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clean 502
            raise UpstreamError(f"malformed JSON from {path}") from exc

        # Surface application-level errors (the campus envelope uses
        # code "0" for success and "#E..." for failures) instead of
        # silently returning an empty result set.
        if isinstance(payload, dict):
            code = payload.get("code")
            if isinstance(code, str) and code.startswith("#"):
                raise UpstreamError(
                    f"upstream {path} failed: {payload.get('msg') or code}",
                    retryable=False,
                    status=502,
                )
        return payload

    # -- endpoints ---------------------------------------------------------

    async def current_user(self) -> Dict[str, Any]:
        return await self._call("/api/home/currentUser.do", method="GET")

    async def terms(self) -> List[Dict[str, Any]]:
        payload = await self._call("/api/home/kb/xnxq.do", method="GET")
        return normalize_terms(payload)

    async def term_weeks(self, term_code: str) -> List[WeekInfo]:
        payload = await self._call(
            "/api/home/getTermWeeks.do", {"termCode": term_code}, method="GET"
        )
        return normalize_weeks(payload)

    async def sections(self, term_code: Optional[str]) -> List[Section]:
        params = {"termCode": term_code} if term_code else None
        payload = await self._call(
            "/api/home/student/getSections.do", params, method="GET"
        )
        return normalize_sections(payload)

    async def courses(self, term_code: str) -> List[Meeting]:
        payload = await self._call(
            "/api/home/student/courses.do", {"termCode": term_code}, method="GET"
        )
        return normalize_meetings(payload)

    async def schedule_detail(self, term_code: str) -> List[Meeting]:
        payload = await self._call(
            "/api/home/student/getMyScheduleDetail.do",
            {"termCode": term_code},
            method="GET",
        )
        return normalize_arranged(payload)

    # -- composite ---------------------------------------------------------

    async def build_term_schedule(self, term_code: str, term_name: str) -> TermSchedule:
        weeks = await self.term_weeks(term_code)
        sections = await self.sections(term_code)

        # The arranged list is the rich, structured source; the course list is
        # a free-text fallback for campuses that don't expose it.
        meetings = await self.schedule_detail(term_code)
        if not meetings:
            meetings = await self.courses(term_code)

        return TermSchedule(
            term_code=term_code,
            term_name=term_name,
            weeks=weeks,
            sections=sections,
            meetings=meetings,
        )


# ---------------------------------------------------------------------------
# Fallback calendar: when the campus does not publish week dates we derive the
# term grid from the term code and a configurable Monday.
# ---------------------------------------------------------------------------


def synthesize_weeks(first_monday: str, count: int = 20) -> List[WeekInfo]:
    from datetime import date, timedelta

    start = parse_date(first_monday)
    if start is None:
        return []
    return [
        WeekInfo(
            index=i + 1,
            start=start + timedelta(days=7 * i),
            end=start + timedelta(days=7 * i + 6),
        )
        for i in range(count)
    ]


def _t(value: str):
    return parse_time(value)  # helper keeps the literal readable


# Used only when the campus refuses to publish 节次 times. Never preferred.
DEFAULT_SECTIONS: List[Section] = [
    Section(1, _t("08:00"), _t("08:45")),
    Section(2, _t("08:55"), _t("09:40")),
    Section(3, _t("10:00"), _t("10:45")),
    Section(4, _t("10:55"), _t("11:40")),
    Section(5, _t("14:00"), _t("14:45")),
    Section(6, _t("14:55"), _t("15:40")),
    Section(7, _t("16:00"), _t("16:45")),
    Section(8, _t("16:55"), _t("17:40")),
    Section(9, _t("19:00"), _t("19:45")),
    Section(10, _t("19:55"), _t("20:40")),
    Section(11, _t("20:50"), _t("21:35")),
]
