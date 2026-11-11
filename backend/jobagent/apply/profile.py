"""'My details': everything an application form asks, stored once. Starts from your main CV.

Stored as JSON in the app_setting table (key "applicant_profile"); nothing here leaves your PC
except into the forms you watch being filled.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlmodel import select

from .. import db, models as m

KEY = "applicant_profile"


class Salary(BaseModel):
    """Expected pay per job type. Empty = the app leaves salary questions to you."""
    working_student_hourly: str = ""      # € per hour, e.g. "18"
    internship_monthly: str = ""          # € per month, e.g. "1800"
    part_time_hourly: str = ""            # € per hour
    full_time_yearly: str = ""            # € per year (rarely needed)
    note: str = ""                         # e.g. "negotiable"


class ApplicantProfile(BaseModel):
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = "Germany"
    linkedin: str = ""
    github: str = ""
    website: str = ""
    date_of_birth: str = ""               # only filled when a form asks; optional
    nationality: str = ""
    gender: str = ""                      # optional; empty = "prefer not to say" where offered
    pronouns: str = ""

    university: str = ""
    degree: str = ""                       # e.g. "M.Sc. Data Science"
    field_of_study: str = ""
    study_start: str = ""
    graduation: str = ""                   # expected graduation, e.g. "09/2027"
    enrolled_student: str = "Yes"         # Yes / No - matters for Werkstudent jobs

    work_permit: str = ""                 # e.g. "Residence permit for study (§16b) - working student allowed"
    work_authorized: str = ""             # "Are you authorised to work in Germany / the EU?"  Yes / No
    needs_sponsorship: str = ""           # Yes / No
    german_level: str = ""                # A1..C2 / native
    english_level: str = ""
    other_languages: str = ""

    earliest_start: str = ""              # e.g. "immediately" or "01.12.2026"
    hours_per_week: str = "20"           # working student / part-time
    internship_duration: str = ""         # e.g. "6 months"
    notice_period: str = ""
    willing_to_relocate: str = ""         # Yes / No / within Germany
    remote_preference: str = ""           # Remote / Hybrid / On-site / any
    drivers_license: str = ""
    how_heard: str = "Job board"          # "How did you hear about us?"
    salary: Salary = Field(default_factory=Salary)

    @property
    def full_name(self) -> str:
        return " ".join(x for x in (self.first_name, self.last_name) if x)


def _from_cv() -> ApplicantProfile:
    """First version of your details, taken from the main CV."""
    from ..cv.service import get_main
    p = ApplicantProfile()
    got = get_main()
    if not got:
        return p
    cv, _ = got
    parts = (cv.name or "").split()
    p.first_name = parts[0] if parts else ""          # "Maria del Carmen Lopez" -> Maria / del Carmen Lopez
    p.last_name = " ".join(parts[1:])
    c = cv.contact
    p.email, p.phone, p.linkedin, p.github = c.email, c.phone, c.linkedin, c.github
    if c.location:
        p.city = c.location.split(",")[0].strip()
    if cv.education:
        e = cv.education[0]
        p.degree, p.university = e.degree, e.school
        years = re.findall(r"(?:\d{2}/)?\d{4}", e.dates or "")
        if years:
            p.study_start, p.graduation = years[0], years[-1] if len(years) > 1 else ""
    for lang in cv.languages:
        name, level = lang.language.lower(), lang.level
        if name.startswith(("german", "deutsch")):
            p.german_level = level
        elif name.startswith(("english", "englisch")):
            p.english_level = level
        else:
            p.other_languages = ", ".join(x for x in (p.other_languages, f"{lang.language} ({level})") if x)
    return p


def load() -> ApplicantProfile:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, KEY)
        if row and row.value:
            try:
                return ApplicantProfile.model_validate(json.loads(row.value))
            except ValueError:
                pass
    return _from_cv()


def save(p: ApplicantProfile) -> ApplicantProfile:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, KEY) or m.AppSetting(key=KEY)
        row.value = p.model_dump_json()
        s.add(row)
    return p


def missing_important(p: ApplicantProfile) -> list[str]:
    """Fields most forms ask for that are still empty."""
    need = {"first_name": "First name", "last_name": "Last name", "email": "Email", "phone": "Phone",
            "city": "City", "earliest_start": "Earliest start", "work_permit": "Work permit",
            "german_level": "German level", "english_level": "English level"}
    out = [label for k, label in need.items() if not getattr(p, k)]
    if not (p.salary.working_student_hourly or p.salary.internship_monthly):
        out.append("Expected salary")
    return out


# ------------------------------------------------------------ answer memory ---
_STOP = {"the", "a", "an", "you", "your", "do", "please", "bitte", "sie", "ihr", "ihre", "haben", "is", "are",
         "to", "of", "in", "for", "with", "and", "oder", "or", "und", "der", "die", "das", "mit", "zu", "?"}


def qkey(question: str) -> str:
    words = re.findall(r"[a-zäöüß0-9+#.]+", (question or "").lower())
    return " ".join(w for w in words if w not in _STOP)[:300]


def _similar(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x and y else 0.0


def find_answer(question: str, min_sim: float = 0.8) -> Optional[m.SavedAnswer]:
    k = qkey(question)
    if not k:
        return None
    with db.session_scope() as s:
        exact = s.exec(select(m.SavedAnswer).where(m.SavedAnswer.key == k)).first()
        if exact:
            return exact
        best, score = None, 0.0
        for a in s.exec(select(m.SavedAnswer)).all():
            sc = _similar(k, a.key)
            if sc > score:
                best, score = a, sc
        return best if score >= min_sim else None


def remember(question: str, answer: str, source: str = "you") -> None:
    k = qkey(question)
    if not k or not str(answer).strip():
        return
    with db.session_scope() as s:
        row = s.exec(select(m.SavedAnswer).where(m.SavedAnswer.key == k)).first() or m.SavedAnswer(key=k)
        row.question, row.answer, row.source = question[:500], str(answer)[:4000], source
        row.uses += 1
        row.updated_at = datetime.now().astimezone()
        s.add(row)
