"""Pydantic schemas: what agents return and what every structured Claude call must produce."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

JobType = Literal["Internship", "Working Student", "Part-time", "Full-time", "Unclear"]
RoleCategory = Literal[
    "AI Engineering", "AI/ML Engineering", "Data Science", "Data Engineering",
    "Data Analytics Engineering", "Gen AI", "MLOps / LLMOps", "Machine Learning", "Other",
]
WorkMode = Literal["Remote", "Hybrid", "On-site", "Unknown"]


# ---------------------------------------------------------------- search ---
class RawJob(BaseModel):
    """A job as found by a site agent or feed, before cleaning."""
    source: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    posted_date: Optional[date] = None
    posted_text: str = ""
    description: str = ""
    job_type_hint: str = ""        # e.g. the site's own "Internship" tag
    work_mode_hint: str = ""
    salary: str = ""
    query: str = ""
    place: str = ""                # the place of your search that found it (a city search also returns nearby towns)
    mode: str = "guest"            # guest / login / feed - how it was found
    external_id: str = ""          # e.g. Bundesagentur refnr
    prefilter_reason: str = ""     # set when excluded before fetching the description


class ExtractedJob(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    posted_text: str = Field("", description="Posting age as shown, e.g. '2 days ago'")
    job_type_hint: str = ""


class PageExtraction(BaseModel):
    """Claude reading a search-results page."""
    page_kind: Literal["results", "login_wall", "captcha", "no_results", "error", "other"]
    total_results_shown: Optional[int] = Field(None, description="Total result count the page states, if any")
    jobs: list[ExtractedJob] = Field(default_factory=list)


class Query(BaseModel):
    site: str
    keywords: str
    location: str
    job_type: str = ""             # filter value on the site if supported
    work_mode: str = ""
    days: int = 5
    split_level: int = 0
    role: str = ""
    place: str = ""                # the place from your profile this search covers (Europe, not "European Union")
    # sites with OR search (Indeed): one search covers several role terms / job-type words,
    # and is split into the single searches only when it hits the result limit
    any_terms: list[str] = Field(default_factory=list)
    type_words: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.site}|{self.keywords}|{self.location}|{self.job_type}|{self.work_mode}"


# ------------------------------------------------------------ classify ---
class Classification(BaseModel):
    job_type: JobType = Field(description="Internship, Working Student (Werkstudent), Part-time, Full-time, or Unclear if the posting does not say")
    role_category: str = Field(description="The one of the candidate's job titles this job is (copied exactly from the "
                                           "list given), or Other if it is none of them")
    work_mode: WorkMode = "Unknown"
    language_requirements: str = Field("", description="e.g. 'English C1, German B2 (must)'; empty if none stated")
    german_required_level: Optional[Literal["A1", "A2", "B1", "B2", "C1", "C2"]] = Field(
        None, description="Minimum German level REQUIRED (not 'nice to have'); null if not required")
    required_skills: list[str] = Field(default_factory=list, description="Exact terms as written in the posting")
    keywords: list[str] = Field(default_factory=list, description="Important ATS keywords, exact wording")
    salary: str = ""
    application_deadline: str = ""
    seniority: Literal["intern", "student", "junior", "mid", "senior", "unknown"] = "unknown"


# --------------------------------------------------------------- score ---
class DealBreaker(BaseModel):
    text: str
    hard: bool = Field(description="True only if it clearly rules the candidate out (e.g. fluent German required and candidate has A2)")


class ScoreResult(BaseModel):
    skills: int = Field(ge=0, le=40, description="Technical skills & tools match, 0-40")
    experience: int = Field(ge=0, le=25, description="Relevant experience & projects, 0-25")
    title_fit: int = Field(ge=0, le=10, description="Role / job title fit, 0-10")
    education: int = Field(ge=0, le=10, description="Education fit, 0-10")
    language: int = Field(ge=0, le=10, description="Language requirements, 0-10")
    type_location: int = Field(ge=0, le=5, description="Job type & location fit, 0-5")
    missing_wording: list[str] = Field(default_factory=list, description="'resume term -> exact JD term' for skills the candidate has but phrases differently")
    missing_skills: list[str] = Field(default_factory=list, description="Exact JD terms not anywhere in the resume")
    deal_breakers: list[DealBreaker] = Field(default_factory=list)
    rationale: str = Field("", description="Two sentences, recruiter voice")


# ------------------------------------------------------------- CV data ---
class SkillGroup(BaseModel):
    category: str
    items: list[str]


class ExperienceItem(BaseModel):
    title: str
    org: str = ""
    meta: str = Field("", description="e.g. 'Internship · Berlin, Germany'")
    dates: str = ""
    stack: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str
    link: str = ""
    note: str = Field("", description="e.g. '(Ongoing)'")
    dates: str = ""
    stack: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    degree: str
    school: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)


class CertificationItem(BaseModel):
    name: str
    issuer: str = ""
    dates: str = ""
    link: str = ""
    bullets: list[str] = Field(default_factory=list)


class LanguageItem(BaseModel):
    language: str
    level: str


class Contact(BaseModel):
    location: str = ""
    phone: str = ""
    email: str = ""
    linkedin: str = ""
    github: str = ""


class CVProfile(BaseModel):
    """The structured main CV (content only, no layout)."""
    name: str
    tagline: str = ""
    contact: Contact = Field(default_factory=Contact)
    summary: str = ""
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list, description="Fields the parser was unsure about")


class CVContent(BaseModel):
    """A tailored CV for one job (same shape as the profile, rewritten)."""
    tagline: str
    summary: str
    skills: list[SkillGroup]
    experience: list[ExperienceItem]
    projects: list[ProjectItem] = Field(min_length=1)
    education: list[EducationItem]
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)


class CheckResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list, description="Concrete problems the writer must fix")


class ReviewResult(BaseModel):
    """A recruiter's first look at the tailored CV."""
    verdict: Literal["interview", "maybe", "reject"]
    reason: str = Field("", description="One sentence: why, in a recruiter's words")
    fixes: list[str] = Field(default_factory=list, description="Concrete changes to the CV text that are true to the main CV, max 5")
    knockouts: list[str] = Field(default_factory=list, description="Requirements the candidate does not meet; the CV cannot fix these")


class LetterContent(BaseModel):
    recipient: str = Field("", description="e.g. 'Hiring Team, Company GmbH'")
    subject: str
    greeting: str = "Dear Hiring Team,"
    paragraphs: list[str] = Field(min_length=3, max_length=5)
    closing: str = "Kind regards,"
    blockers_stated: list[str] = Field(default_factory=list, description="Gaps/blockers the letter names honestly")


class JobForWriting(BaseModel):
    company: str
    title: str
    job_type: str = ""
    location: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    missing_wording: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)
