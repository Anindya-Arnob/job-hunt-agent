"""Database tables (SQLite via SQLModel)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now().astimezone()


class JobStatus:
    NEW = "new"                    # found, not yet classified
    NEEDS_REVIEW = "needs_review"  # job type unclear, kept for you to check
    EXCLUDED = "excluded"          # filtered out, reason in exclude_reason
    SCORED = "scored"
    BUILT = "built"                # CV + letter built
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"

    ALL = (NEW, NEEDS_REVIEW, EXCLUDED, SCORED, BUILT, APPLIED, INTERVIEW, OFFER, REJECTED)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company: str = ""
    title: str = ""
    job_type: str = ""             # Internship / Working Student / Part-time / Full-time / Unclear
    role_category: str = ""
    location: str = ""
    work_mode: str = ""            # Remote / Hybrid / On-site
    posted_date: Optional[date] = None
    sources: list = Field(default_factory=list, sa_column=Column(JSON))
    links: list = Field(default_factory=list, sa_column=Column(JSON))
    full_description: str = ""
    required_skills: list = Field(default_factory=list, sa_column=Column(JSON))
    keywords: list = Field(default_factory=list, sa_column=Column(JSON))
    language_req: str = ""
    salary: str = ""
    deadline: str = ""
    dedupe_key: str = Field(default="", index=True)
    url_key: str = Field(default="", index=True)
    status: str = Field(default=JobStatus.NEW, index=True)
    exclude_reason: str = ""
    notes: str = ""
    first_seen_run: Optional[int] = None
    last_seen_run: Optional[int] = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    @property
    def link(self) -> str:
        return self.links[0] if self.links else ""


class Score(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    cv_version_id: Optional[int] = None
    total: int = 0
    skills: int = 0
    experience: int = 0
    title_fit: int = 0
    education: int = 0
    language: int = 0
    type_location: int = 0
    missing_wording: list = Field(default_factory=list, sa_column=Column(JSON))
    missing_skills: list = Field(default_factory=list, sa_column=Column(JSON))
    deal_breakers: list = Field(default_factory=list, sa_column=Column(JSON))
    verdict: str = ""
    rationale: str = ""
    is_outdated: bool = False
    created_at: datetime = Field(default_factory=now)


class CVProfileRow(SQLModel, table=True):
    __tablename__ = "cv_profile"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "Main CV"
    is_main: bool = False
    archived_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now)


class CVVersion(SQLModel, table=True):
    __tablename__ = "cv_version"
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    version: int = 1
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))  # structured profile
    source_file: str = ""
    note: str = ""
    created_at: datetime = Field(default_factory=now)


class Template(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "Default"
    file_path: str = ""
    style: dict = Field(default_factory=dict, sa_column=Column(JSON))  # colours, fonts
    is_active: bool = True
    created_at: datetime = Field(default_factory=now)


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    cv_version_id: Optional[int] = None
    kind: str = "cv"               # cv / letter
    docx_path: str = ""
    pdf_path: str = ""
    content: dict = Field(default_factory=dict, sa_column=Column(JSON))
    qa_results: list = Field(default_factory=list, sa_column=Column(JSON))
    qa_passed: bool = False
    blockers_stated: list = Field(default_factory=list, sa_column=Column(JSON))
    attempts: int = 1
    built_at: datetime = Field(default_factory=now)


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, unique=True)
    applied_at: Optional[date] = None
    notes: str = ""
    follow_up_at: Optional[date] = None
    outcome: str = ""


class FilterProfile(SQLModel, table=True):
    __tablename__ = "filter_profile"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    roles: list = Field(default_factory=list, sa_column=Column(JSON))
    keywords_in: list = Field(default_factory=list, sa_column=Column(JSON))
    keywords_out: list = Field(default_factory=list, sa_column=Column(JSON))
    job_types: list = Field(default_factory=list, sa_column=Column(JSON))
    locations: list = Field(default_factory=list, sa_column=Column(JSON))
    work_modes: list = Field(default_factory=list, sa_column=Column(JSON))
    days: int = 5
    sources: list = Field(default_factory=list, sa_column=Column(JSON))
    language_max: str = "any"      # any / none / A1..C2 (highest German level you accept)
    exclude_companies: list = Field(default_factory=list, sa_column=Column(JSON))
    min_salary: int = 0
    is_default: bool = False
    # search plan: {"sites": {site: {"searches", "narrow"}}, "places": {place: 1-3}, "fields": {role: 1-3}}
    plan: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Connection(SQLModel, table=True):
    site: str = Field(primary_key=True)
    status: str = "not_connected"  # not_connected / connected / expired / not_needed
    last_verified_at: Optional[datetime] = None
    note: str = ""


class Run(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = "search"           # search / build / rescore
    status: str = "running"        # running / paused / done / failed / cancelled
    filter_profile_id: Optional[int] = None
    thread_id: str = ""
    started_at: datetime = Field(default_factory=now)
    finished_at: Optional[datetime] = None
    cost_usd: float = 0.0
    counts: dict = Field(default_factory=dict, sa_column=Column(JSON))
    errors: list = Field(default_factory=list, sa_column=Column(JSON))
    log: list = Field(default_factory=list, sa_column=Column(JSON))


class SearchStat(SQLModel, table=True):
    __tablename__ = "search_stat"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    site: str
    query: str
    location: str = ""
    mode: str = "guest"            # guest / login / feed
    reported: Optional[int] = None
    collected: int = 0
    pages: int = 0
    status: str = "ok"             # ok / login_needed / failed / captcha / split / limit
    error: str = ""


class TargetCompany(SQLModel, table=True):
    __tablename__ = "target_company"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    ats: str = ""                  # greenhouse / lever / ashby / smartrecruiters / personio / careers
    board_id: str = ""
    careers_url: str = ""
    active: bool = True


class DeletedJob(SQLModel, table=True):
    """Jobs you deleted. Kept as a tombstone so later searches don't bring them back."""
    __tablename__ = "deleted_job"
    id: Optional[int] = Field(default=None, primary_key=True)
    url_key: str = Field(default="", index=True)
    dedupe_key: str = Field(default="", index=True)
    company: str = ""
    title: str = ""
    deleted_at: datetime = Field(default_factory=now)


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_setting"
    key: str = Field(primary_key=True)
    value: str = ""


class SavedAnswer(SQLModel, table=True):
    """An answer you gave to an application-form question, reused on later forms."""
    __tablename__ = "saved_answer"
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True)            # normalised question text
    question: str = ""
    answer: str = ""
    source: str = "you"                     # you / claude (a draft you kept)
    uses: int = 0
    updated_at: datetime = Field(default_factory=now)


class ApplyAttempt(SQLModel, table=True):
    """One application in the apply assistant."""
    __tablename__ = "apply_attempt"
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    started_at: datetime = Field(default_factory=now)
    finished_at: Optional[datetime] = None
    status: str = "running"                 # running / applied / skipped / failed
    form_url: str = ""
    filled: int = 0
    open_questions: list = Field(default_factory=list, sa_column=Column(JSON))
    screenshot: str = ""
    note: str = ""


class MailEvent(SQLModel, table=True):
    """One job-related email: a confirmation, rejection, interview invitation, job alert ..."""
    __tablename__ = "mail_event"
    id: Optional[int] = Field(default=None, primary_key=True)
    msg_id: str = Field(default="", index=True)
    uid: int = 0
    received_at: Optional[datetime] = None
    sender: str = ""
    sender_name: str = ""
    subject: str = ""
    kind: str = Field(default="other", index=True)   # confirmation/rejection/interview/assessment/offer/update/recruiter/job_alert/account/other
    company: str = ""
    role: str = ""
    summary: str = ""
    action: str = ""                 # what you need to do (interview slot, test ...)
    when_text: str = ""              # date/time mentioned for an interview or deadline
    excerpt: str = ""                # start of the mail, passwords and codes removed
    job_id: Optional[int] = Field(default=None, index=True)
    effect: str = ""                 # what the app changed ("marked Rejected", "3 new jobs")
    job_ids: list = Field(default_factory=list, sa_column=Column(JSON))   # jobs found in an alert
    done: bool = False               # you handled the action
    created_at: datetime = Field(default_factory=now)
