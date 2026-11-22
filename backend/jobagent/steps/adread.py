"""Claude reads one ad, once, when you click Build CV: what it requires (and what it marks as a must), what is
nice to have, and the ways of working it asks for. Cheap on purpose: the company text is cut first, only the
ad goes (never your CV), the fast model answers at low effort with a short list, and the answer is kept for
the job, so a rebuild does not read it again. Comparing the list with your CV is done here in Python.

The skill and tool names it finds are taught to the free word matcher (vocab.LEARNED)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .. import db, llm
from .. import models as m
from .. import vocab

log = logging.getLogger(__name__)
VERSION = [0]          # bumped when an ad is read or a word is learned: cached knock-outs are recomputed


class Group(BaseModel):
    any_of: list[str] = Field(description="One skill or tool, or the names the ad offers as alternatives "
                                          "('at least one of Gurobi, CPLEX, Xpress or OR-Tools')")
    must: bool = Field(False, description="true only when the ad itself says this is required / a must / "
                                          "mandatory / Voraussetzung / zwingend for it")


class Spelling(BaseModel):
    term: str
    also: list[str] = Field(default_factory=list, description="Other ways ads and CVs write it")


class AdRead(BaseModel):
    required: list[Group] = Field(default_factory=list, description="Skills and tools the ad asks for (not nice to have)")
    nice_to_have: list[str] = Field(default_factory=list)
    ways_of_working: list[str] = Field(default_factory=list,
                                       description="How to work, not tools: reproducible experiments, documentation ...")
    spellings: list[Spelling] = Field(default_factory=list,
                                      description="Other spellings of the skills and tools above, only where they exist")


# ------------------------------------------------------------ cutting it down ---
_HEAD_OFF = re.compile(r"^(about (us|the company|[\w&.\- ]{2,30})|über uns|ueber uns|wer wir sind|who we are|"
                       r"additional information|we win with inclusion|ai usage.*|equal opportunit.*|diversity.*|"
                       r"why join us|what we offer|our offer|wir bieten|das bieten wir|benefits|perks|"
                       r"(data )?privacy|datenschutz|contact|kontakt|how to apply)\s*:?$", re.I)
_HEAD_ON = re.compile(r"about the (job|role|position|team|opportunity)|what you('ll)? (bring|build|do)|your (profile|tasks|role|skills)|requirements|qualifications|"
                      r"must have|nice to have|responsibilit|dein profil|ihr profil|deine aufgaben|ihre aufgaben|"
                      r"anforderungen|das bringst du mit|was du mitbringst|where you belong|the role", re.I)
_BOILER = re.compile(r"regardless of|without regard to|equal employment|accommodation|disabilit|referral program|"
                     r"inclusion|diversity|gender identity|sexual orientation|chancengleichheit|schwerbehindert|"
                     r"datenschutz|privacy (policy|notice)|background check|e-?mail .{0,40}request", re.I)


def _heading(p: str) -> bool:
    return len(p) <= 60 and "\n" not in p.strip() and not p.strip().endswith((".", ",", ";"))


def trim(text: str) -> str:
    """The ad without the company text: 'About SAP', the equal-opportunity and AI-usage notes, benefits,
    privacy. What the job needs stays."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    out, skip = [], False
    for p in paras:
        if _heading(p):
            if _HEAD_ON.search(p):
                skip = False
            elif _HEAD_OFF.match(p.strip()):
                skip = True
                continue
        if skip or (_BOILER.search(p) and not _HEAD_ON.search(p)):
            continue
        out.append(p)
    return "\n\n".join(out)[:8000]


# ----------------------------------------------------------------- reading ---
def _key(job_id: int) -> str:
    return f"ad_read:{job_id}"


def cached(job_id: int) -> Optional[AdRead]:
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, _key(job_id))
            return AdRead.model_validate(json.loads(row.value)) if row and row.value else None
    except Exception:
        return None


def _save(job_id: int, ad: AdRead) -> None:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, _key(job_id)) or m.AppSetting(key=_key(job_id))
        row.value = ad.model_dump_json()
        s.add(row)
    VERSION[0] += 1


def _rules(job: m.Job) -> AdRead:
    """Without Claude: the built-in word list, as before (nothing marked as a must)."""
    terms = list(dict.fromkeys((job.required_skills or []) + vocab.find_terms(f"{job.title}\n{job.full_description}")))
    return AdRead(required=[Group(any_of=[t]) for t in terms])


def can_read() -> bool:
    from ..config import settings
    return settings.effective_llm_mode != "fake" or llm.cli_available()


def read(job: m.Job) -> tuple[AdRead, bool]:
    """(the ad's lists, read by Claude?). Read once per job; without Claude the word list stands in (not kept,
    so the next build tries Claude again)."""
    have = cached(job.id)
    if have is not None:
        return have, True
    used_rules = []

    def rules():
        used_rules.append(1)
        return _rules(job)
    user = f"JOB TITLE: {job.title}\n\nAD:\n{trim(job.full_description or '')}"
    try:
        ad = llm.structured("read_ad", AdRead, llm.prompt("read_ad"), user, fake=rules, max_tokens=1500)
    except Exception as e:          # never let reading the ad stop a build
        log.warning("read_ad failed (%s) - using the word list", str(e)[:200])
        return _rules(job), False
    if used_rules:
        return ad, False
    ad = clean(ad)
    _save(job.id, ad)
    before = _score_of(job.id)
    learn(ad, f"{job.company} – {job.title}")        # the new words first, so the score after counts them
    apply_to_job(job.id, ad, before)
    return ad, True


def clean(ad: AdRead) -> AdRead:
    def ok(t: str) -> bool:
        return bool(t) and len(t) <= 40 and len(t.split()) <= 5
    req = []
    for g in ad.required:
        names = list(dict.fromkeys(n.strip() for n in g.any_of if ok(n.strip())))
        if names:
            req.append(Group(any_of=names, must=g.must))
    nice = [t.strip() for t in ad.nice_to_have if ok(t.strip())]
    ways = [t.strip() for t in ad.ways_of_working if t.strip() and len(t) <= 60]
    return AdRead(required=req, nice_to_have=nice, ways_of_working=ways, spellings=ad.spellings)


def group_term(g: Group) -> str:
    return " or ".join(g.any_of)


def required_terms(ad: AdRead) -> list[str]:
    return [group_term(g) for g in ad.required]


def must_groups(ad: Optional[AdRead]) -> list[list[str]]:
    return [g.any_of for g in ad.required if g.must] if ad else []


def _score_of(job_id: int) -> Optional[int]:
    from sqlmodel import select
    with db.session_scope() as s:
        sc = s.exec(select(m.Score).where(m.Score.job_id == job_id)).first()
        return sc.total if sc else None


def checked(job_id: int) -> Optional[dict]:
    """{"before", "after", "at"}: the job's score before and after Claude read the ad."""
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, f"claude_check:{job_id}")
            return json.loads(row.value) if row and row.value else None
    except Exception:
        return None


def check(job_id: int, again: bool = False) -> dict:
    """The Claude check button: read the ad (or read it again), update the score, nothing built."""
    with db.session_scope() as s:
        job = s.get(m.Job, job_id)
        if job is None:
            raise KeyError(job_id)
        s.expunge(job)
    if again and cached(job_id) is not None:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, _key(job_id))
            if row:
                s.delete(row)
    ad, by_claude = read(job)
    return {"read_by_claude": by_claude, "score": _score_of(job_id), "check": checked(job_id),
            "must_have": must_groups(ad) if by_claude else []}


def apply_to_job(job_id: int, ad: AdRead, before: Optional[int] = None) -> None:
    """The job card shows Claude's list; the free score of this job is redone with it (before -> after kept)."""
    before = _score_of(job_id) if before is None else before
    with db.session_scope() as s:
        j = s.get(m.Job, job_id)
        if not j:
            return
        j.required_skills = required_terms(ad)[:25]
        j.keywords = list(dict.fromkeys(required_terms(ad) + ad.nice_to_have))[:30]
        s.add(j)
    _rescore([job_id])
    prev = checked(job_id)
    with db.session_scope() as s:
        key = f"claude_check:{job_id}"
        row = s.get(m.AppSetting, key) or m.AppSetting(key=key)
        row.value = json.dumps({"before": prev["before"] if prev else before, "after": _score_of(job_id),
                                "at": datetime.now().astimezone().isoformat(timespec="seconds")})
        s.add(row)


def _rescore(job_ids: Optional[list[int]] = None) -> int:
    """Free re-score with the word matcher (only when scoring is the free one: with an API key a re-score
    would cost tokens, so it is left to the Re-score button)."""
    from ..config import settings
    if settings.effective_llm_mode != "fake":
        return 0
    from ..cv.service import get_main
    from sqlmodel import select
    from .score import heuristic_score, store_score
    got = get_main()
    if not got:
        return 0
    prof, vid = got
    n = 0
    with db.session_scope() as s:
        q = select(m.Job).where(m.Job.status.in_([m.JobStatus.SCORED, m.JobStatus.NEW, m.JobStatus.NEEDS_REVIEW,
                                                   m.JobStatus.BUILT]))
        if job_ids is not None:
            q = q.where(m.Job.id.in_(job_ids))
        jobs = s.exec(q).all()
        for j in jobs:
            s.expunge(j)
        fp = s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first()  # noqa: E712
        if fp:
            s.expunge(fp)
    for j in jobs:
        try:
            store_score(j.id, heuristic_score(j, prof, fp), vid)
            n += 1
        except Exception as e:
            log.warning("re-score %s: %s", j.id, e)
    return n


# ----------------------------------------------------------- learned words ---
_LEARN_KEY = "learned_words"


def learned() -> dict:
    """{word: {"also": [...], "from": "Company – Title", "added": iso}}"""
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, _LEARN_KEY)
            return json.loads(row.value) if row and row.value else {}
    except Exception:
        return {}


def _store(words: dict) -> None:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, _LEARN_KEY) or m.AppSetting(key=_LEARN_KEY)
        row.value = json.dumps(words, ensure_ascii=False)
        s.add(row)
    vocab.set_learned({k: v.get("also", []) for k, v in words.items()})
    VERSION[0] += 1


def load_learned() -> None:
    vocab.set_learned({k: v.get("also", []) for k, v in learned().items()})


def learn(ad: AdRead, source: str = "") -> list[str]:
    """Teach the word matcher the tools and skills of this ad it did not know (not ways of working)."""
    also = {sp.term.lower(): [a.strip() for a in sp.also if a.strip() and len(a) <= 40] for sp in ad.spellings}
    ways = {w.lower() for w in ad.ways_of_working}
    words = learned()
    new = []
    for t in [n for g in ad.required for n in g.any_of] + ad.nice_to_have:
        if t.lower() in ways or vocab.is_practice(t) or vocab.known(t) or t in words or len(t) < 2:
            continue
        words[t] = {"also": also.get(t.lower(), []), "from": source[:120],
                    "added": datetime.now().astimezone().isoformat(timespec="minutes")}
        new.append(t)
    if new:
        _store(words)
        _rescore()
    return new


def forget(word: str) -> bool:
    words = learned()
    if word not in words:
        return False
    words.pop(word)
    _store(words)
    _rescore()
    return True


def checked_all() -> dict[int, dict]:
    """Every job Claude read: id -> {"before", "after", "at"} (empty dict when only the reading is kept)."""
    from sqlmodel import select
    out: dict[int, dict] = {}
    with db.session_scope() as s:
        for row in s.exec(select(m.AppSetting).where(m.AppSetting.key.startswith("ad_read:"))).all():
            try:
                out[int(row.key[8:])] = {}
            except ValueError:
                pass
        for row in s.exec(select(m.AppSetting).where(m.AppSetting.key.startswith("claude_check:"))).all():
            try:
                jid = int(row.key[13:])
                if jid in out:
                    out[jid] = json.loads(row.value)
            except (ValueError, TypeError):
                pass
    return out
