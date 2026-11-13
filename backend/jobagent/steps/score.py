"""Score: one structured Claude call per job (recruiter rubric), then Python
adds up the parts and sets the verdict by fixed rules."""
from __future__ import annotations

import re
from typing import Optional

from sqlmodel import select

from .. import db, llm, models as m
from ..config import settings
from ..schemas import CVProfile, DealBreaker, ScoreResult
from ..vocab import ALL_TERMS, SYNONYMS, find_terms, norm_tool, term_regex
from .classify import LEVELS
from .clean import location_matches

LIMITS = {"skills": 40, "experience": 25, "title_fit": 10, "education": 10, "language": 10, "type_location": 5}


# ------------------------------------------------------------ profile facts ---
def profile_text(p: CVProfile) -> str:
    parts = [p.tagline, p.summary]
    for g in p.skills:
        parts.append(g.category + ": " + ", ".join(g.items))
    for e in p.experience:
        parts += [e.title, e.org, e.meta, ", ".join(e.stack)] + e.bullets
    for pr in p.projects:
        parts += [pr.name, ", ".join(pr.stack)] + pr.bullets
    for ed in p.education:
        parts += [ed.degree, ed.school] + ed.bullets
    for c in p.certifications:
        parts += [c.name] + c.bullets
    return "\n".join(x for x in parts if x)


def profile_tools(p: CVProfile) -> list[str]:
    tools = []
    for g in p.skills:
        tools += g.items
    for e in p.experience:
        tools += e.stack
    for pr in p.projects:
        tools += pr.stack
    seen, out = set(), []
    for t in tools:
        k = norm_tool(t)
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out


def candidate_german_level(p: CVProfile) -> Optional[str]:
    for l in p.languages:
        if l.language.lower().startswith(("german", "deutsch")):
            m_ = re.search(r"(A1|A2|B1|B2|C1|C2)", l.level.upper())
            if m_:
                return m_.group(1)
            low = l.level.lower()
            if "native" in low or "mutter" in low:
                return "C2"
            if "fluent" in low:
                return "C1"
            if "basic" in low or "elementary" in low:
                return "A2"
    return None


# ---------------------------------------------------------- offline heuristic ---
def heuristic_score(job: m.Job, p: CVProfile, fp: Optional[m.FilterProfile] = None) -> ScoreResult:
    ptext = profile_text(p)
    jtext = f"{job.title}\n{job.full_description}"
    wanted = list(dict.fromkeys((job.required_skills or []) + (job.keywords or []) + find_terms(jtext, ALL_TERMS)))[:30]
    have, missing, wording = [], [], []
    for term in wanted:
        if term_regex(term).search(ptext):
            have.append(term)
            continue
        alt = None
        for resume_term, alts in SYNONYMS.items():
            names = [resume_term] + alts
            if any(norm_tool(term) == norm_tool(a) for a in names):
                alt = next((n for n in names if term_regex(n).search(ptext)), None)
                if alt:
                    break
        if alt:
            wording.append(f"{alt} -> {term}")
            have.append(term)
        else:
            missing.append(term)
    ratio = len(have) / max(1, len(wanted))
    skills = round(40 * min(1.0, ratio * 1.05)) if wanted else 20
    proj_hits = 0
    for pr in p.projects:
        ptxt = " ".join([pr.name] + pr.stack + pr.bullets)
        if sum(1 for t in wanted if term_regex(t).search(ptxt)) >= 2:
            proj_hits += 1
    exp_hit = any(sum(1 for t in wanted if term_regex(t).search(" ".join(e.stack + e.bullets))) >= 1 for e in p.experience)
    experience = min(25, 6 + proj_hits * 4 + (5 if exp_hit else 0))
    title_l = job.title.lower()
    from .. import roles as R
    wanted_roles = list(fp.roles) if fp and fp.roles else []
    if wanted_roles and not all(R.is_data(r) for r in wanted_roles):
        title_fit = 9 if any(t.lower() in title_l for r in wanted_roles for t in R.terms_for(r)) else (
            6 if R.title_fits(job.title, wanted_roles) else 3)
        edu_text = " ".join(e.degree for e in p.education).lower()
        education = 9 if any(w in edu_text for r in wanted_roles for w in R._tokens(r)) else 7
    else:
        title_fit = 9 if any(w in title_l for w in ("data scien", "machine learning", "ml", "ai", "ki", "nlp", "llm", "genai")) else (
            6 if any(w in title_l for w in ("data", "analyt")) else 3)
        edu_text = " ".join(e.degree for e in p.education).lower()
        education = 9 if "data science" in edu_text or "computer" in edu_text else 7
    if re.search(r"phd|doctoral|promotion", jtext.lower()):
        education = 4
    deal: list[DealBreaker] = []
    cand = candidate_german_level(p)
    language = 10
    m_req = None
    from .classify import heuristic_classification
    req = heuristic_classification(job.title, job.full_description).german_required_level
    if req:
        m_req = req
        gap = LEVELS.index(req) - (LEVELS.index(cand) if cand in LEVELS else -1)
        if gap > 0:
            language = max(0, 10 - 3 * gap)
            deal.append(DealBreaker(text=f"German {req} required (CV: {cand or 'none'})", hard=gap >= 2))
    from .. import places as PL
    mine = list(fp.locations) if fp and fp.locations else ["Berlin", "Remote"]
    loc_ok = location_matches(job.location, mine, job.work_mode)
    mine_p = [PL.find(x) for x in mine if x != "Remote"]
    countries = {x.country for x in mine_p if x.country} | {c for x in mine_p if x.kind == "region" for c in x.cities}
    regions = {x.region for x in mine_p if x.region}
    job_country = PL.country_of(job.location)
    same_country = bool(job_country and job_country.name in countries)
    same_region = bool(job_country and job_country.region in regions)
    type_location = 5 if loc_ok in (True, None) else (4 if same_country else (3 if same_region else 1))
    if loc_ok is False:
        where = (job_country.name if job_country else "elsewhere")
        far = not same_country and not same_region
        deal.append(DealBreaker(text=f"On-site in {job.location} ({where}) - not one of your places, relocation needed",
                                hard=far and job_country is not None))
        if far and job_country is not None:
            deal.append(DealBreaker(text=f"Work authorisation for {job_country.name} likely required", hard=True))
    if re.search(r"must be enrolled|eingeschrieben|immatrikul", jtext.lower()):
        deal.append(DealBreaker(text="Must be enrolled as a student (you are: M.Sc. student - check university requirements)", hard=False))
    return ScoreResult(skills=skills, experience=experience, title_fit=title_fit, education=education,
                       language=language, type_location=type_location, missing_wording=wording[:12],
                       missing_skills=missing[:15], deal_breakers=deal,
                       rationale=f"Matches {len(have)} of {len(wanted)} requested skills."
                                 + (f" German {m_req} is required." if m_req else ""))


# --------------------------------------------------------------- the step ---
def score_job(job: m.Job, profile: CVProfile, fp: Optional[m.FilterProfile] = None) -> ScoreResult:
    from .classify import profile_for, search_context
    fp = fp or profile_for(job)
    user = (f"{search_context(fp)}RESUME (structured JSON):\n{profile.model_dump_json(exclude={'uncertain_fields'})}\n\n"
            f"JOB:\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location} ({job.work_mode})\n"
            f"Job type: {job.job_type}\nLanguage requirements: {job.language_req}\n\n"
            f"FULL JOB DESCRIPTION:\n{job.full_description[:20000]}")
    return llm.structured("score", ScoreResult, llm.prompt("score"), user,
                          fake=lambda: heuristic_score(job, profile, fp))


def clamp(r: ScoreResult) -> ScoreResult:
    for k, lim in LIMITS.items():
        setattr(r, k, max(0, min(lim, int(getattr(r, k)))))
    return r


def total(r: ScoreResult) -> int:
    return sum(getattr(r, k) for k in LIMITS)


def verdict(score_total: int, deal_breakers: list[DealBreaker]) -> str:
    if any(d.hard for d in deal_breakers):
        return "Skip"
    if score_total < settings.verdict_skip_below:
        return "Skip"
    if score_total >= settings.verdict_apply_from:
        return "Apply"
    return "Apply after CV tailoring"


def breakdown_text(s: m.Score) -> str:
    return (f"Skills {s.skills}/40, Experience {s.experience}/25, Title {s.title_fit}/10, "
            f"Education {s.education}/10, Language {s.language}/10, Type & location {s.type_location}/5")


def store_score(job_id: int, r: ScoreResult, cv_version_id: Optional[int]) -> m.Score:
    r = clamp(r)
    t = total(r)
    with db.session_scope() as s:
        for old in s.exec(select(m.Score).where(m.Score.job_id == job_id)).all():
            s.delete(old)
        row = m.Score(job_id=job_id, cv_version_id=cv_version_id, total=t, skills=r.skills,
                      experience=r.experience, title_fit=r.title_fit, education=r.education, language=r.language,
                      type_location=r.type_location, missing_wording=r.missing_wording,
                      missing_skills=r.missing_skills,
                      deal_breakers=[d.model_dump() for d in r.deal_breakers],
                      verdict=verdict(t, r.deal_breakers), rationale=r.rationale)
        s.add(row)
        job = s.get(m.Job, job_id)
        if job and job.status in (m.JobStatus.NEW, m.JobStatus.SCORED, m.JobStatus.NEEDS_REVIEW):
            if job.status != m.JobStatus.NEEDS_REVIEW:
                job.status = m.JobStatus.SCORED
            s.add(job)
        s.flush()
        return row


def mark_scores_outdated(current_version_id: int) -> int:
    with db.session_scope() as s:
        rows = s.exec(select(m.Score).where(m.Score.cv_version_id != current_version_id)).all()
        for r in rows:
            r.is_outdated = True
            s.add(r)
        return len(rows)
