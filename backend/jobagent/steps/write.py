"""CV Writer, Checker and Cover Letter agents.

Real mode: Claude with the write_cv / check_cv / write_letter prompts.
Offline mode: a careful selector that only reuses text from your main CV
(reorders and trims, never invents), so the output is honest by construction.
"""
from __future__ import annotations

from ..textutil import strip_gender

import re
from typing import Optional

from .. import llm
from ..schemas import (CheckResult, CVContent, CVProfile, ExperienceItem, JobForWriting, LetterContent, ProjectItem,
                       SkillGroup)
from ..vocab import ALL_TERMS, find_terms, term_regex

MAX_SKILL_GROUPS = 6
MAX_ITEMS_PER_GROUP = 9


def job_terms(job: JobForWriting) -> list[str]:
    wanted = list(job.keywords) + list(job.required_skills)
    for mw in job.missing_wording:
        if "->" in mw:
            wanted.append(mw.split("->", 1)[1].strip())
    wanted += find_terms(f"{job.title}\n{job.description}", ALL_TERMS)
    return list(dict.fromkeys(w for w in wanted if w))


def _relevance(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if term_regex(t).search(text))


def _first_sentence_limit(s: str, words: int) -> str:
    """Shorten to whole sentences (or ';' clauses) within `words`. Never cuts mid-sentence."""
    s = (s or "").strip()
    if len(s.split()) <= words:
        return s
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", s)
    out = []
    for sent in sentences:
        if out and len(" ".join(out + [sent]).split()) > words:
            break
        out.append(sent)
    text = " ".join(out)
    if len(text.split()) > words and ";" in text:
        clauses = text.split(";")
        keep = [clauses[0]]
        for c in clauses[1:]:
            if len(";".join(keep + [c]).split()) > words:
                break
            keep.append(c)
        text = ";".join(keep).rstrip(" ,") + "."
        text = text.replace("..", ".")
    return text


def limit_em_dashes(cv: CVContent, allowed: int = 1) -> CVContent:
    """Keep at most `allowed` em dashes in the writing (others become commas)."""
    count = 0

    def fix(t: str) -> str:
        nonlocal count
        def rep(m):
            nonlocal count
            count += 1
            return m.group(0) if count <= allowed else ", "
        return re.sub(r"\s*—\s*", rep, t)

    cv.summary = fix(cv.summary)
    cv.tagline = fix(cv.tagline)
    for e in cv.experience:
        e.bullets = [fix(b) for b in e.bullets]
    for p in cv.projects:
        p.bullets = [fix(b) for b in p.bullets]
    for ed in cv.education:
        ed.bullets = [fix(b) for b in ed.bullets]
    return cv


def _swap_wording(text: str, job: JobForWriting) -> str:
    """Use the posting's exact word where you already have the skill (Missing Wording)."""
    for mw in job.missing_wording:
        if "->" not in mw:
            continue
        mine, theirs = [x.strip() for x in mw.split("->", 1)]
        if mine and theirs and mine.lower() != theirs.lower() and len(theirs) <= 40:
            text = term_regex(mine).sub(theirs, text, count=1)
    return text


def heuristic_cv(job: JobForWriting, prof: CVProfile, level: int = 0, picked: int = 0, fact: str = "") -> CVContent:
    """level 0..4: higher = shorter (used to fit one page)."""
    terms = job_terms(job)
    # skills: most relevant groups first, relevant items first
    groups = []
    for g in prof.skills:
        items = sorted(g.items, key=lambda i: -_relevance(i, terms) if terms else 0)
        score = sum(_relevance(i, terms) for i in g.items)
        groups.append((score, SkillGroup(category=g.category, items=items[:MAX_ITEMS_PER_GROUP - (1 if level >= 3 else 0)])))
    groups.sort(key=lambda t: -t[0])
    n_groups = MAX_SKILL_GROUPS - (1 if level >= 2 else 0) - (1 if level >= 4 else 0)
    skills = [g for _, g in groups[:n_groups]]
    # projects: the picked ones (already in order), or the most relevant 3-4 of the main CV
    if picked:
        scored = list(prof.projects)
        n_proj = int(picked) - (1 if level >= 4 and int(picked) > 3 else 0)
    else:
        scored = sorted(prof.projects, key=lambda p: -_relevance(" ".join([p.name] + p.stack + p.bullets), terms))
        n_proj = 4 if level <= 1 else 3
    per_proj = 2 if level < 3 else (2 if level == 3 else 1)
    if n_proj >= 5 and level >= 1:
        per_proj = 1
    projects = []
    for p in scored[:n_proj]:
        ranked = sorted(p.bullets, key=lambda b: (-_relevance(b, terms), len(b.split())))
        if level >= 2:   # prefer shorter bullets when space is tight
            ranked = sorted(ranked[:3], key=lambda b: len(b.split()))
        bullets = [_first_sentence_limit(_swap_wording(b, job), 34 - 4 * min(level, 3)) for b in ranked[:per_proj]]
        projects.append(ProjectItem(name=p.name, link=p.link, note=p.note, dates=p.dates,
                                    stack=sorted(p.stack, key=lambda s: -_relevance(s, terms))[:8 - min(level, 3)],
                                    bullets=bullets))
    exp = []
    for e in prof.experience:
        nb = 3 if level < 2 else 2
        bullets = sorted(e.bullets, key=lambda b: -_relevance(b, terms))[:nb]
        exp.append(ExperienceItem(title=e.title, org=e.org, meta=e.meta, dates=e.dates,
                                  stack=e.stack[:8], bullets=[_first_sentence_limit(b, 32 - 3 * min(level, 3)) for b in bullets]))
    summary = _first_sentence_limit(_swap_wording(prof.summary.replace(" — ", ", "), job), 62 - 6 * min(level, 3))
    summary = re.sub(r"(?<![A-Za-z])(I am|I'm|I have|I)\s+", "", summary)
    if fact:
        summary = (summary.rstrip(".") + ". " + fact).strip()
    focus = [t for t in terms if t in ("Machine Learning", "Deep Learning", "NLP", "LLM", "RAG", "Generative AI",
                                       "Data Engineering", "MLOps", "Computer Vision", "Data Science", "Python", "SQL")][:3]
    base = prof.tagline.split("·")[0].strip() if prof.tagline else "Data Science M.Sc. Student"
    from ..docs.ats import core_title
    role = core_title(job.title)
    if role and len(role) <= 45:
        tagline = " · ".join([base, role] + [f for f in focus if f.lower() not in role.lower()][:1])
    else:
        tagline = " · ".join([base] + focus) if focus else prof.tagline
    edu = []
    for ed in prof.education:
        e2 = ed.model_copy()
        e2.bullets = ed.bullets[:1] if level < 3 else []
        edu.append(e2)
    certs = [c.model_copy(update={"bullets": []}) for c in prof.certifications][: (1 if level < 4 else 0)]
    return limit_em_dashes(CVContent(tagline=tagline, summary=summary, skills=skills, experience=exp,
                                     projects=projects, education=edu, certifications=certs,
                                     languages=prof.languages))


def _job_block(job: JobForWriting) -> str:
    return (f"JOB:\nCompany: {job.company}\nTitle: {job.title}\nType: {job.job_type}\nLocation: {job.location}\n"
            f"Keywords: {', '.join(job.keywords)}\nRequired skills: {', '.join(job.required_skills)}\n"
            f"Missing wording (resume term -> posting term): {'; '.join(job.missing_wording)}\n"
            f"Missing skills (do NOT claim): {', '.join(job.missing_skills)}\n"
            f"Deal-breakers: {'; '.join(job.deal_breakers)}\n\nDESCRIPTION:\n{job.description[:15000]}")


def system_prompt(name: str) -> str:
    """The node's prompt, plus the human-writing rules when that switch is on (Settings → CV building)."""
    from ..config import settings
    text = llm.prompt(name)
    if settings.humanize:
        text += "\n\n" + llm.prompt("humanize")
    return text


def write_cv(job: JobForWriting, prof: CVProfile, issues: Optional[list[str]] = None, level: int = 0,
             picked: int = 0, fact: str = "") -> CVContent:
    """picked = how many projects go on the CV; the profile then holds a ranked shortlist to choose from."""
    fix = ("\n\nREVIEWER ISSUES TO FIX (all of them):\n- " + "\n- ".join(issues)) if issues else ""
    extra = ""
    if picked:
        extra += (f"\n\nPROJECTS TO USE: exactly {int(picked)} projects, chosen from this ranked shortlist "
                  f"(best match first; projects from GitHub are described only by their README facts): "
                  + "; ".join(p.name for p in prof.projects) + ". Pick the ones a recruiter for THIS job cares "
                  "about most, most relevant first.")
    if fact:
        extra += f"\n\nAVAILABILITY (end the summary with this, in your words, keep the numbers): {fact}"
    from ..docs.ats import core_title
    extra += f"\n\nTARGET ROLE WORDS for the tagline: {core_title(job.title) or job.title}"
    user = f"MAIN CV (only truth):\n{prof.model_dump_json(exclude={'uncertain_fields'})}\n\n{_job_block(job)}{extra}{fix}"
    return llm.structured("write_cv", CVContent, system_prompt("write_cv"), user,
                          fake=lambda: heuristic_cv(job, prof, level, picked, fact), max_tokens=6000)


def check_cv(job: JobForWriting, prof: CVProfile, cv: CVContent) -> CheckResult:
    def fake() -> CheckResult:
        bad = re.compile(r"\b(passionate|results-driven|cutting-edge|synergy|synergies|dynamic|rockstar|ninja)\b", re.I)
        text = cv.summary + " " + " ".join(b for p in cv.projects for b in p.bullets)
        issues = [f"Remove buzzword '{m.group(0)}'" for m in bad.finditer(text)]
        if len(cv.projects) < 3:
            issues.append("Keep at least 3 projects.")
        return CheckResult(passed=not issues, issues=issues)
    user = (f"MAIN CV (only truth):\n{prof.model_dump_json(exclude={'uncertain_fields'})}\n\n{_job_block(job)}\n\n"
            f"TAILORED CV:\n{cv.model_dump_json()}")
    return llm.structured("check_cv", CheckResult, llm.prompt("check_cv"), user, fake=fake, max_tokens=2000)


# ------------------------------------------------------------------ recruiter review ---
def review_cv(job: JobForWriting, truth: CVProfile, cv: CVContent, ats_after: dict) -> "ReviewResult":
    from ..schemas import ReviewResult

    def fake() -> ReviewResult:
        ko = [d.replace(" (hard)", "") for d in job.deal_breakers if "(hard)" in d]
        return ReviewResult(verdict="maybe" if ko else "interview", knockouts=ko, fixes=[],
                            reason="Offline check: no recruiter review without Claude.")
    user = (f"MAIN CV + PROJECT FACTS (only truth):\n{truth.model_dump_json(exclude={'uncertain_fields'})}\n\n"
            f"{_job_block(job)}\n\nATS RESULT: score {ats_after.get('score')}, missing keywords: "
            f"{', '.join(ats_after.get('missing', [])[:15])}\n\nTAILORED CV:\n{cv.model_dump_json()}")
    return llm.structured("review_cv", ReviewResult, llm.prompt("review_cv"), user, fake=fake, max_tokens=1500)


# ------------------------------------------------------------------ letter ---
def availability_line(job_type: str) -> str:
    if job_type == "Internship":
        return "I am happy to discuss start date and duration."
    if job_type == "Working Student":
        return "I am available as a working student for up to 20 hours a week."
    if job_type == "Part-time":
        return "I am available part-time and happy to discuss the hours."
    return "I am happy to discuss a start date."


LETTER_LANGUAGE = {
    "english": "English. Write in English even when the job ad is in German or another language.",
    "german": "German.",
    "ad": "the language of the job ad (German ad -> German letter, English ad -> English letter).",
}


def letter_language_line() -> str:
    from ..config import settings
    return "LETTER LANGUAGE: " + LETTER_LANGUAGE.get((settings.letter_language or "english").lower(), LETTER_LANGUAGE["english"])


def _human_blocker(d: str) -> str:
    d = d.replace(" (hard)", "").strip()
    m = re.match(r"German (\w\d) required \(CV: (\w+)\)", d)
    if m:
        return f"the role asks for German {m.group(1)}, and my German is currently at {m.group(2)} level"
    return d[0].lower() + d[1:] if d else d


def _as_i(sentence: str) -> str:
    s = sentence.strip()
    return "I " + s[0].lower() + s[1:] if s and s[0].isupper() and not s.startswith("I ") else s


def blockers_for(job: JobForWriting) -> list[str]:
    out = []
    for d in job.deal_breakers:
        out.append(_human_blocker(d))
    for s in job.missing_skills[:3]:
        out.append(f"no hands-on experience with {s} yet")
    return out


def heuristic_letter(job: JobForWriting, prof: CVProfile, cv: CVContent) -> LetterContent:
    """Offline fallback (used only when Claude Code / the API is not available). Fuller and gap-free."""
    company = (job.company or "").strip()
    at = f"at {company}" if company else "in your team"
    team = company or "your team"
    terms = [t for t in job_terms(job) if len(t) > 2][:4]
    deg = prof.education[0].degree.split('(')[0].strip() if prof.education else ""
    school = prof.education[0].school if prof.education else ""
    art = "an" if deg[:1].upper() in "AEIOUM" else "a"
    who = f"{art} {deg} student" + (f" at {school}" if school else "") + " in Berlin" if deg else "a student in Berlin"
    p1 = (f"I am applying for the {job.title} position {at}. I am {who}. "
          + (f"Your posting asks for {', '.join(terms[:-1]) + ' and ' + terms[-1] if len(terms) > 1 else terms[0]}, "
             "which is the kind of work my recent projects are built on." if terms else
             "The role is close to the data and machine learning work I do in my studies and projects."))
    proj_lines = []
    for p in cv.projects[:3]:
        b = [x.rstrip(".") for x in p.bullets[:2]]
        if not b:
            continue
        stack = f" using {', '.join(p.stack[:4])}" if getattr(p, "stack", None) else ""
        proj_lines.append(f"In \"{p.name}\"{stack}, {_as_i(b[0])}." + (f" {_as_i(b[1]).capitalize()}." if len(b) > 1 else ""))
    p2 = " ".join(proj_lines[:2]) or "My projects cover data pipelines, model training and evaluation."
    p3 = proj_lines[2] if len(proj_lines) > 2 else ""
    exp = prof.experience[0] if prof.experience else None
    if exp and exp.bullets:
        p3 = (p3 + " " if p3 else "") + f"As {exp.title} at {exp.org}, {_as_i(exp.bullets[0]).rstrip('.')}."
    p4 = (f"In the first weeks {at.replace('at ', 'at ') if company else 'here'}, I would like to take on concrete tasks from the posting"
          + (f" around {terms[0]}" if terms else "") + ", learn your stack quickly and document what I build so others can use it.")
    blockers = blockers_for(job)
    honest = ("To be transparent: " + "; ".join(blockers[:3]) + ". I would rather say this up front, and I am glad to "
              "discuss how I would close these gaps. ") if blockers else ""
    p5 = honest + availability_line(job.job_type) + f" I would welcome a conversation about how I can contribute to {team}."
    paras = [p1, p2] + ([p3] if p3 else []) + [p4, p5.strip()]
    return LetterContent(recipient=f"Hiring Team, {company}" if company else "Hiring Team",
                         subject=f"Application: {job.title}", paragraphs=paras[:5], blockers_stated=blockers[:3])


_GENDER_TAG = re.compile(r"\s*\(\s*(m|w|d|f|x|div|all genders?)(\s*[/|,]\s*(m|w|d|f|x|div|i))*\s*\)", re.I)


def tidy_letter(L: LetterContent, job: JobForWriting) -> LetterContent:
    """Final clean-up for every letter: no em dashes, no gender tags, no empty company gaps."""
    def fix(t: str) -> str:
        t = re.sub(r"\s*—\s*", ", ", t or "")
        t = strip_gender(t)
        if not (job.company or "").strip():
            t = re.sub(r"\b(at|bei)\s+([.,;])", r"in your team\2", t)
        return re.sub(r"\s{2,}", " ", re.sub(r",\s*,", ",", t)).strip()
    L.paragraphs = [fix(p) for p in L.paragraphs]
    L.subject, L.recipient = fix(L.subject), fix(L.recipient).rstrip(", ")
    return L


def write_letter(job: JobForWriting, prof: CVProfile, cv: CVContent, issues: Optional[list[str]] = None) -> LetterContent:
    fix = ("\n\nISSUES TO FIX:\n- " + "\n- ".join(issues)) if issues else ""
    user = (f"MAIN CV (only truth):\n{prof.model_dump_json(exclude={'uncertain_fields'})}\n\n"
            f"TAILORED CV:\n{cv.model_dump_json()}\n\n{_job_block(job)}\n\n"
            f"Availability sentence to use: \"{availability_line(job.job_type)}\"\n\n{letter_language_line()}{fix}")
    if not (job.company or "").strip():
        user += "\n\nThe company name is unknown: write 'your team' instead of a company name."
    out = llm.structured("write_letter", LetterContent, system_prompt("write_letter"), user,
                         fake=lambda: heuristic_letter(job, prof, cv), max_tokens=3000)
    return tidy_letter(out, job)
