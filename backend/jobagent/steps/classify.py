"""Classify: one structured Claude call per job, then fixed Python rules.

Full-time -> Excluded, unclear job type -> Needs review (never dropped),
role outside scope -> Excluded, German level above your filter -> Excluded.
"""
from __future__ import annotations

import re
from typing import Optional

from .. import llm, models as m
from ..schemas import Classification
from ..textutil import SENIOR_TITLE
from ..vocab import find_terms

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_WS = re.compile(r"werkstudent|working student|studentische|student assistant|student job|hiwi", re.I)
_INTERN = re.compile(r"\bintern\b|internship|praktik|praktikant|\bstage\b|stagiaire|trainee|thesis|abschlussarbeit|pflichtpraktikum", re.I)
_PART = re.compile(r"part[- ]?time|teilzeit|minijob|\b(1[0-9]|20)\s*(hours|h|std|stunden)\s*(per|/|pro|a)\s*(week|woche)", re.I)
_FULL = re.compile(r"full[- ]?time|vollzeit|permanent|unbefristet|festanstellung|40\s*(hours|h|stunden)", re.I)

ROLE_RULES = [
    ("MLOps / LLMOps", r"mlops|llmops|ml platform|model deployment|ml infrastructure"),
    ("Gen AI", r"genai|gen ai|generative ai|\bllm|large language|prompt engineer|\brag\b|diffusion"),
    ("Data Engineering", r"data engineer|etl|data pipeline|dateningenieur|airflow|dbt"),
    ("Data Analytics Engineering", r"analytics engineer|data analyst|datenanalyst|business intelligence|\bbi\b|reporting analyst"),
    ("AI/ML Engineering", r"machine learning engineer|ml engineer|ai/ml|ai ml"),
    ("AI Engineering", r"ai engineer|ki[- ]entwick|ai developer|künstliche intelligenz|ai[- ]agent"),
    ("Data Science", r"data scien|data science|datenwissenschaft"),
    ("Machine Learning", r"machine learning|deep learning|\bml\b|maschinelles lernen|computer vision|nlp"),
    ("Data Science", r"\bdata\b|daten"),
]
_GERMAN_REQ = [
    (r"(fließend|fliessend|verhandlungssicher)e?s?\s*deutsch|deutsch\s*(fließend|verhandlungssicher)|fluent (in )?german|german.{0,20}fluent|native german|deutsch.{0,10}(muttersprach|c1|c2)|german.{0,12}\(?c1|german.{0,12}c2", "C1"),
    (r"sehr gute deutschkenntnisse|very good (command of )?german|excellent german|german.{0,15}b2|deutsch.{0,12}b2", "B2"),
    (r"gute deutschkenntnisse|good (knowledge of )?german|german.{0,12}b1|deutsch.{0,12}b1", "B1"),
]


def _role_for(title: str, text: str, roles: list[str] | None) -> str:
    """Which of your job titles a job is (from its title first, then its text). Without a profile: the AI /
    data groups of earlier versions."""
    from .. import roles as R
    tl, xl = title.lower(), text.lower()
    # a profile with the AI / data groups of earlier versions accepts all of them (as before)
    legacy = not roles or any(r in R.LEGACY for r in roles)
    allowed = lambda name: legacy or name in roles        # noqa: E731
    for name, pat in ROLE_RULES:                          # the AI / data groups: precise patterns
        if allowed(name) and re.search(pat, tl):
            return name
    for r in roles or []:                                 # your title (or its other name) in the job title
        if any(t.lower() in tl for t in R.terms_for(r)):
            return r
    for r in roles or []:                                 # a word of your title in the job title
        rx = R._title_rx((r,))
        if r not in R.LEGACY and rx and rx.search(R.fold(title)):
            return r
    for name, pat in ROLE_RULES:
        if allowed(name) and re.search(pat, xl):
            return name
    for r in roles or []:
        if r not in R.LEGACY and any(t.lower() in xl for t in R.terms_for(r)):
            return r
    return "Other"


def heuristic_classification(title: str, description: str, hint: str = "", location: str = "",
                             roles: list[str] | None = None) -> Classification:
    head = f"{title} {hint}"
    text = f"{title}\n{hint}\n{description}"
    if _WS.search(head) or (_WS.search(description) and not _INTERN.search(head)):
        jt = "Working Student"
    elif _INTERN.search(head) or _INTERN.search(description[:600]):
        jt = "Internship"
    elif _PART.search(head) or _PART.search(description):
        jt = "Part-time"
    elif _FULL.search(text):
        jt = "Full-time"
    else:
        jt = "Unclear"
    low = text.lower()
    role = _role_for(title, text, roles)
    german = None
    for pat, lvl in _GERMAN_REQ:
        m_ = re.search(pat, low)
        if m_ and not re.search(r"(nice to have|plus|von vorteil|wünschenswert|preferred|advantage)", low[m_.start():m_.end() + 60]):
            german = lvl
            break
    wm = "Remote" if re.search(r"\bremote\b|home ?office|100% remote", low) else (
        "Hybrid" if "hybrid" in low else ("On-site" if re.search(r"on[- ]site|vor ort|onsite", low) else "Unknown"))
    skills = find_terms(text)
    langs = []
    if "english" in low or "englisch" in low:
        langs.append("English")
    if german:
        langs.append(f"German {german} (required)")
    elif "german" in low or "deutsch" in low:
        langs.append("German (nice to have)")
    sal = re.search(r"(\d{1,3}(?:[.,]\d{3})*\s?(?:€|eur|euro)(?:\s?/\s?(?:h|hour|stunde|month|monat|year|jahr))?|€\s?\d+[.,]?\d*\s?(?:/\s?h)?)", text, re.I)
    seniority = "senior" if SENIOR_TITLE.search(title) else ("student" if jt == "Working Student" else ("intern" if jt == "Internship" else "unknown"))
    return Classification(job_type=jt, role_category=role, work_mode=wm, language_requirements=", ".join(langs),
                          german_required_level=german, required_skills=skills[:20], keywords=skills[:20],
                          salary=sal.group(0) if sal else "", seniority=seniority)


def profile_for(job: m.Job) -> Optional[m.FilterProfile]:
    """The search profile a job was found with (else your default one)."""
    from sqlmodel import select
    from .. import db
    with db.session_scope() as s:
        run = s.get(m.Run, job.first_seen_run) if job.first_seen_run else None
        fp = s.get(m.FilterProfile, run.filter_profile_id) if run and run.filter_profile_id else None
        fp = fp or s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first()  # noqa: E712
        return fp


def search_context(fp: Optional[m.FilterProfile]) -> str:
    """What the candidate is looking for, for the Claude calls."""
    if not fp:
        return ""
    return (f"THE CANDIDATE IS LOOKING FOR\nJob titles: {', '.join(fp.roles or []) or 'any'}\n"
            f"Job types: {', '.join(fp.job_types or []) or 'any'}\nPlaces: {', '.join(fp.locations or []) or 'anywhere'}\n\n")


def classify_job(job: m.Job, fp: Optional[m.FilterProfile] = None) -> Classification:
    fp = fp or profile_for(job)
    user = (f"{search_context(fp)}TITLE: {job.title}\nCOMPANY: {job.company}\nLOCATION: {job.location}\n"
            f"SITE LABEL: {job.job_type}\n\nDESCRIPTION:\n{job.full_description[:20000]}")
    roles = list(fp.roles) if fp and fp.roles else None
    c = llm.structured("classify", Classification, llm.prompt("classify"), user,
                       fake=lambda: heuristic_classification(job.title, job.full_description, job.job_type,
                                                             job.location, roles))
    if roles and c.role_category != "Other":       # Claude's wording -> your exact title
        match = next((r for r in roles if r.lower() == (c.role_category or "").lower()), None)
        c.role_category = match or c.role_category
    return c


def apply_classification(job: m.Job, c: Classification, fp: Optional[m.FilterProfile]) -> m.Job:
    job.job_type = c.job_type
    job.role_category = c.role_category
    if c.work_mode != "Unknown":
        job.work_mode = c.work_mode
    job.language_req = c.language_requirements
    job.required_skills = c.required_skills
    job.keywords = c.keywords
    job.salary = job.salary or c.salary
    job.deadline = c.application_deadline
    reason = None
    allowed_types = set(fp.job_types) if fp and fp.job_types else {"Internship", "Working Student", "Part-time"}
    if c.job_type == "Full-time" and "Full-time" not in allowed_types:
        reason = "Full-time"
    elif c.seniority == "senior" and "Full-time" not in allowed_types:
        reason = "Senior role (full-time)"
    elif c.role_category == "Other":
        reason = "Not one of your job titles"
    elif c.job_type != "Unclear" and c.job_type not in allowed_types:
        reason = f"Job type {c.job_type} not in filter"
    elif fp and fp.language_max not in ("any", "", None) and c.german_required_level:
        limit = "A0" if fp.language_max == "none" else fp.language_max
        if limit == "A0" or LEVELS.index(c.german_required_level) > LEVELS.index(limit):
            reason = f"German {c.german_required_level} required (filter allows up to {fp.language_max})"
    elif fp and fp.work_modes and job.work_mode in ("Remote", "Hybrid", "On-site") and job.work_mode not in fp.work_modes:
        reason = f"Work mode {job.work_mode} not in filter"
    if reason:
        job.status, job.exclude_reason = m.JobStatus.EXCLUDED, reason
    elif c.job_type == "Unclear":
        job.status, job.exclude_reason = m.JobStatus.NEEDS_REVIEW, "Job type unclear - please check"
    return job
