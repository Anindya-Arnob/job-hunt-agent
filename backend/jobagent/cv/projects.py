"""Pick the projects for one job: from your main CV and your GitHub, the ones closest to the job ad.

Each project is scored by the job terms it really mentions (required skills count three times,
the posting's keywords twice, other skills from the ad once). A GitHub repo that is the same
project as one on your CV adds its README facts to it; a repo that is not on your CV can be used
on its own, with only what its README says.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import settings
from ..schemas import CVProfile, JobForWriting, ProjectItem
from ..vocab import ALL_TERMS, find_terms_strict
from . import github as gh

log = logging.getLogger(__name__)


@dataclass
class Picked:
    item: ProjectItem
    source: str                    # "cv" / "github" / "cv+github"
    score: float = 0.0
    matched: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {"name": self.item.name, "source": self.source, "score": round(self.score, 1),
             "matched": self.matched[:8], "link": self.item.link}
        if self.source != "cv":
            d["item"] = self.item.model_dump()          # its GitHub facts, so later edits are checked against them
        return d


def weighted_terms(job: JobForWriting) -> dict[str, float]:
    w: dict[str, float] = {}
    for t in find_terms_strict(f"{job.title}\n{job.description}", ALL_TERMS):
        w[t] = max(w.get(t, 0), 1.0)
    from ..docs.ats import title_words
    for t in title_words(job.title):                   # the role itself: "AI", "Automation"
        w[t] = max(w.get(t, 0), 2.0)
    for t in job.keywords:
        w[t] = max(w.get(t, 0), 2.0)
    for t in job.required_skills:
        w[t] = max(w.get(t, 0), 3.0)
    for mw in job.missing_wording:
        if "->" in mw:
            w[mw.split("->", 1)[0].strip()] = 2.0      # your word for a term the ad uses
    return {k: v for k, v in w.items() if k and len(k) > 1}


def _text(p: ProjectItem) -> str:
    return "\n".join([p.name, " ".join(p.stack)] + p.bullets)


def relevance(p: ProjectItem, terms: dict[str, float], extra: str = "") -> tuple[float, list[str]]:
    """Weighted job terms the project shows. `extra` (a README) counts at half weight."""
    from ..docs.ats import hit
    text = _text(p)
    found = [t for t in terms if hit(t, text)]
    more = [t for t in terms if t not in found and extra and hit(t, extra)]
    score = sum(terms[t] for t in found) + 0.5 * sum(terms[t] for t in more)
    return score, sorted(found, key=lambda t: -terms[t]) + more


def candidates(prof: CVProfile, use_github: bool) -> tuple[list[tuple[ProjectItem, str]], dict]:
    info: dict = {"github": None, "readmes": {}}
    cands = [(p.model_copy(deep=True), "cv") for p in prof.projects]
    if not use_github:
        return cands, info
    try:
        repos, meta = gh.repos(prof)
        info["github"] = {"user": meta["user"], "repos": len(repos), "cached": meta.get("cached", False)}
    except gh.GitHubError as e:
        info["github"] = {"error": str(e)}
        return cands, info
    for r in repos:
        twin = next((i for i, (p, _) in enumerate(cands) if same_source_cv(cands[i]) and gh.same_project(p, r)), None)
        if twin is not None:
            p, _ = cands[twin]
            extra_stack = [s for s in gh.repo_stack(r) if s.lower() not in {x.lower() for x in p.stack}]
            extra_facts = [f for f in gh.repo_facts(r, 3) if f not in p.bullets][:2]
            p.stack = p.stack + extra_stack[:4]
            p.bullets = p.bullets + extra_facts
            if not p.link:
                p.link = r.url
            cands[twin] = (p, "cv+github")
            info["readmes"][p.name] = r.readme
        else:
            item = gh.as_project(r)
            if item.bullets:                      # a repo without a description or README has nothing to say
                cands.append((item, "github"))
                info["readmes"][item.name] = r.readme
    return cands, info


def same_source_cv(c: tuple[ProjectItem, str]) -> bool:
    return c[1] in ("cv", "cv+github")


def pick(job: JobForWriting, prof: CVProfile, n: int | None = None,
         use_github: bool | None = None) -> tuple[list[Picked], dict]:
    n = max(1, min(int(n or settings.cv_projects or 3), 8))
    use_github = settings.github_projects if use_github is None else use_github
    cands, info = candidates(prof, use_github)
    terms = weighted_terms(job)
    scored = []
    readmes = info.pop("readmes", {})
    for i, (p, src) in enumerate(cands):
        sc, hit = relevance(p, terms, readmes.get(p.name, ""))
        bonus = 0.5 if src != "github" else 0.0          # your CV is curated: it wins a tie
        scored.append(Picked(p, src, sc + bonus, hit))
    scored.sort(key=lambda x: (-x.score, x.source == "github"))
    info["available"] = len(cands)
    info["asked"] = n
    info["want"] = min(n, len(cands))
    return scored[:n + SPARE], info


SPARE = 2          # the writer gets two more than you asked for and keeps the best ones


def writer_profile(prof: CVProfile, picked: list[Picked]) -> CVProfile:
    """What the writer sees: your CV with only the picked projects (fewer tokens, no wrong picks)."""
    p = prof.model_copy(deep=True)
    p.projects = [x.item for x in picked]
    return p


def truth_profile(prof: CVProfile, picked: list[Picked]) -> CVProfile:
    """What the checks accept: your CV plus the README facts of the picked GitHub projects."""
    p = prof.model_copy(deep=True)
    by_name = {x.item.name: x for x in picked}
    merged = []
    for cvp in p.projects:
        x = by_name.pop(cvp.name, None)
        merged.append(x.item if x else cvp)
    merged += [x.item for x in by_name.values()]
    p.projects = merged
    return p


def truth_from_meta(prof: CVProfile, meta: dict) -> CVProfile:
    """Rebuild what a build's checks accepted from the document's saved notes (for quick edits)."""
    p = prof.model_copy(deep=True)
    extra = [ProjectItem.model_validate(x["item"]) for x in (meta.get("_projects") or []) if x.get("item")]
    names = {x.name for x in extra}
    p.projects = [q for q in p.projects if q.name not in names] + extra
    if meta.get("_fact"):
        p.summary = (p.summary + " " + meta["_fact"]).strip()
    return p
