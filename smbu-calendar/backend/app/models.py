from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    captcha: Optional[str] = Field(default="", max_length=16)


class LoginResponse(BaseModel):
    token: str
    student_id: str
    display_name: str
    expires_in: int


class Term(BaseModel):
    code: str
    name: str
    current: bool = False


class TermsResponse(BaseModel):
    terms: List[Term]
    default_code: Optional[str] = None


class SectionOut(BaseModel):
    index: int
    start: str
    end: str


class MeetingOut(BaseModel):
    name: str
    day_of_week: int
    start_section: int
    end_section: int
    weeks: List[int]
    week_label: str
    section_label: str
    teacher: Optional[str] = None
    location: Optional[str] = None
    code: Optional[str] = None
    class_name: Optional[str] = None


class ScheduleResponse(BaseModel):
    term_code: str
    term_name: str
    weeks: List[Dict[str, str]]
    sections: List[SectionOut]
    meetings: List[MeetingOut]
    event_count: int
    warnings: List[str] = []
