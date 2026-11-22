"""Reach your ATS target: after a build, the words from the ad that are still missing, how many points each is
worth, and where your main CV or GitHub already shows it ("proof"). What you tick is written into your main CV
(once, so you are never asked again) and the CV is built again.

Nothing is added without your tick. Language levels and other knock-outs are never offered: no wording fixes them.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field
from sqlmodel import select

from .. import db, llm, models as m
from ..config import settings
from ..cv import service as cvs
from ..docs import ats
from ..schemas import CVContent, CVProfile
from ..vocab import concept_of


class BoostError(ValueError):
    pass


# ------------------------------------------------------------------ the list ---
def _sentences(text: str) -> list[str]:
    text = re.sub(r"[#*`>|_\[\]]+", " ", text or "")
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 20 <= len(s.strip()) <= 400]


def _sources(prof: CVProfile) -> list[tuple[str, str]]:
    """(where, sentence) from your main CV and, when GitHub is on, your repositories' READMEs."""
    out = [("Summary", prof.summary)]
    for e in prof.experience:
        out += [(f"{e.title} · {e.org}".strip(" ·"), b) for b in e.bullets]
        if e.stack:
            out.append((f"{e.title} · {e.org}".strip(" ·"), "Stack: " + ", ".join(e.stack)))
    for p in prof.projects:
        out += [(p.name, b) for b in p.bullets]
        if p.stack:
            out.append((p.name, "Stack: " + ", ".join(p.stack)))
    for g in prof.skills:
        out.append(("Skills", f"{g.category}: " + ", ".join(g.items)))
    if settings.github_projects:
        try:
            from ..cv.projects import candidates
            cands, info = candidates(prof, True)
            for name, readme in (info.get("readmes") or {}).items():
                out += [(f"{name} (GitHub)", s) for s in _sentences(readme)]
        except Exception:
            pass
    return [(w, s) for w, s in out if s]


def proof_for(term: str, sources: list[tuple[str, str]]) -> Optional[dict]:
    for where, sent in sources:
        if ats.hit(term, sent):
            return {"where": where, "text": sent[:220]}
    return None


def _cv_doc(job_id: int) -> m.Document:
    with db.session_scope() as s:
        d = s.exec(select(m.Document).where(m.Document.job_id == job_id, m.Document.kind == "cv")
                   .order_by(m.Document.id.desc())).first()
        if not d:
            raise BoostError("Build the CV for this job first.")
        s.expunge(d)
        return d


def _job_for(job_id: int):
    from ..graphs.build_graph import _load, job_for_writing
    from .write import doc_language
    job, sc = _load(job_id)
    if not job:
        raise BoostError("Job not found")
    jw = job_for_writing(job, sc)
    jw.lang = doc_language(jw)[0]
    return job, jw


def plan(job_id: int, target: Optional[int] = None) -> dict:
    """What would lift this job's CV to the target: the missing words, fewest first, with points and proof."""
    _, jw = _job_for(job_id)
    doc = _cv_doc(job_id)
    content = {k: v for k, v in (doc.content or {}).items() if not k.startswith("_")}
    cv = CVContent.model_validate(content)
    got = cvs.get_main()
    if not got:
        raise BoostError("Upload your main CV first.")
    prof, _ = got
    target = int(target or settings.ats_target)
    now = ats.score(jw, cv, prof)
    kw = ats.keywords(jw)
    total = sum(kw.values()) or 1
    sources = _sources(prof)
    items = []
    for t in now["missing"]:
        items.append({"term": t, "points": round(60 * kw.get(t, 1) / total, 1), "proof": proof_for(t, sources)})
    items.sort(key=lambda x: (-x["points"], x["proof"] is None))
    need = max(0, target - now["score"])
    chosen, got_pts = [], 0.0
    for it in items:
        if got_pts >= need:
            break
        chosen.append(it)
        got_pts += it["points"]
    return {"score": now["score"], "target": target, "need": need, "items": chosen,
            "others": [it for it in items if it not in chosen],
            "reachable": sum(it["points"] for it in items) >= need,
            "knockouts": now.get("knockouts", []), "few_keywords": now.get("few_keywords", False)}


# ------------------------------------------------------------- writing it in ---
class Placement(BaseModel):
    term: str
    skill_group: str = Field("", description="An existing skill group of the main CV to add the term to")
    place: str = Field("", description="Exact name of the project or job the bullet belongs to, or ''")
    bullet: str = Field("", description="One CV bullet, past tense, no 'I', at most 30 words, or ''")


class Placements(BaseModel):
    items: list[Placement] = Field(default_factory=list)


def _names(prof: CVProfile) -> list[str]:
    return [p.name for p in prof.projects] + [f"{e.title} · {e.org}".strip(" ·") for e in prof.experience]


def _heuristic(prof: CVProfile, picks: list[dict]) -> Placements:
    out = []
    names = _names(prof)
    groups = [g.category for g in prof.skills]
    for it in picks:
        where = (it.get("where") or "").strip()
        proof = it.get("proof") or {}
        place = next((n for n in names if n.split(" ·")[0].lower() in (where + " " + proof.get("where", "")).lower()), "")
        group = next((g for g in groups if re.search(r"tool|mlops|engineering|framework|deploy", g, re.I)),
                     groups[-1] if groups else "Skills")
        bullet = ""
        if where and place:
            w = re.sub(rf"^\s*(in\s+)?{re.escape(place.split(' ·')[0])}\s*[:,-]?\s*", "", where, flags=re.I).strip()
            if w and not re.match(r"[A-Za-z]+ed\b", w):          # "pytest unit tests" -> a bullet that says what you did
                w = f"Applied {it['term'].lower()}: {w}"
            bullet = (w[:1].upper() + w[1:]).rstrip(".") + "." if w else ""
        out.append(Placement(term=it["term"], skill_group=group, place=place, bullet=bullet))
    return Placements(items=out)


def placements(prof: CVProfile, picks: list[dict]) -> Placements:
    """Claude decides where each ticked word goes and writes one plain bullet from your words or the proof."""
    lines = []
    for it in picks:
        proof = it.get("proof") or {}
        lines.append(f"- {it['term']} | your words: {it.get('where') or '(none)'} | "
                     f"proof in your CV/GitHub: {proof.get('where', '')}: {proof.get('text', '') or '(none)'}")
    user = ("MAIN CV skill groups: " + "; ".join(g.category for g in prof.skills) + "\n"
            "MAIN CV projects and jobs: " + "; ".join(_names(prof)) + "\n\nTICKED BY THE PERSON:\n" + "\n".join(lines))
    try:
        out = llm.structured("place_facts", Placements, llm.prompt("place_facts"), user,
                             fake=lambda: _heuristic(prof, picks), max_tokens=1500)
    except Exception:
        out = _heuristic(prof, picks)
    by = {p.term.lower(): p for p in out.items}
    fixed = []
    for it in picks:                       # every ticked word gets a place, even if the answer skipped one
        p = by.get(it["term"].lower()) or _heuristic(prof, [it]).items[0]
        p.term = it["term"]
        fixed.append(p)
    return Placements(items=fixed)


def add_to_main(picks: list[dict], company: str = "") -> dict:
    """Write the ticked words into your main CV as a new version: the term into a skill group, the bullet into
    the project or job it belongs to."""
    got = cvs.get_main()
    if not got:
        raise BoostError("Upload your main CV first.")
    prof, vid = got
    from ..vocab import alternatives
    for pk in picks:              # 'Gurobi or CPLEX or OR-Tools': the one you used (named in "where"), else the first
        alts = alternatives(pk.get("term", ""))
        if len(alts) > 1:
            pk["term"] = next((a for a in alts if a.lower() in (pk.get("where") or "").lower()), alts[0])
    plan_ = placements(prof, picks)
    p = prof.model_copy(deep=True)
    added = []
    from ..vocab import is_practice
    for pl in plan_.items:
        have = " ".join(i for g in p.skills for i in g.items)
        practice = is_practice(pl.term)
        if practice:          # a way of working: shown in a bullet, not listed as a skill
            from ..steps.score import profile_text
            if not pl.bullet and not ats.hit(pl.term, profile_text(p)):
                place = pl.place or (p.projects[0].name if p.projects else "")
                pl.place, pl.bullet = place, f"Applied {pl.term.lower()} throughout the work."
        elif not ats.hit(pl.term, have):
            g = next((g for g in p.skills if g.category.lower() == pl.skill_group.lower()), None)
            if g is None:
                g = next((g for g in p.skills if g.category == "Additional Skills"), None)
                if g is None:
                    from ..schemas import SkillGroup
                    g = SkillGroup(category="Additional Skills", items=[])
                    p.skills.append(g)
            g.items.append(concept_of(pl.term) or pl.term)
        bullet = re.sub(r"\s+", " ", pl.bullet or "").strip()
        if bullet:
            target = next((x for x in p.projects if x.name.lower() == pl.place.lower()), None) or \
                next((e for e in p.experience if f"{e.title} · {e.org}".strip(" ·").lower() == pl.place.lower()), None)
            if target is not None and bullet not in target.bullets:
                target.bullets.append(bullet)
        added.append({"term": pl.term, "skill_group": pl.skill_group, "place": pl.place, "bullet": bullet})
    with db.session_scope() as s:
        row = s.exec(select(m.CVVersion).where(m.CVVersion.id == vid)).first()
        pid = row.profile_id
    note = "Confirmed for " + (company or "a job") + ": " + ", ".join(a["term"] for a in added)
    saved = cvs.save_version(pid, p.model_dump(), note[:200])
    return {"added": added, "version_id": saved.get("version_id")}
