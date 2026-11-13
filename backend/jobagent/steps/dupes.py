"""The same job found twice: on two sites, on two days, or in German and in English.

Exact matches (same link, or same company + title + place) are caught when a job is stored. This adds the
near ones the exact check misses, seen in real data:
  "MAHLE" vs "MAHLE International"                         (company written differently)
  no place vs "Stuttgart", "München" vs "Munich"            (place missing or in another language)
  "Praktikum Supplier Quality" vs "Internship Supplier Quality"   (German vs English title)
It stays careful: different places, or Junior vs Senior, are never the same job.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import select

from .. import db, models as m, places
from ..textutil import norm_city, norm_company, norm_title

_COMPANY_EXTRA = re.compile(r"\b(international|group|germany|deutschland|holding|global|europe|emea|de|gmbh|ag|se|"
                            r"inc|ltd|llc|corp|corporation|company|co|kg|technologies|technology)\b")
_DE_EN = {"praktikum": "internship", "praktikant": "intern", "praktikantin": "intern", "werkstudent": "working student",
          "werkstudentin": "working student", "werkstudierende": "working student", "werkstudierender": "working student",
          "fur": "for", "und": "and", "im": "in", "mit": "with", "bereich": "area", "schwerpunkt": "focus", "ki": "ai",
          "teilzeit": "part time", "abschlussarbeit": "thesis", "entwicklung": "development", "entwickler": "developer",
          "ingenieur": "engineer", "daten": "data", "datenanalyse": "data analytics", "vollzeit": "full time"}
# words that make two otherwise similar titles different jobs
_LEVEL = {"junior", "senior", "lead", "principal", "head", "staff", "intern", "internship", "working", "student",
          "part", "thesis", "trainee", "director", "manager", "werkstudent", "full", "mid"}
_WORK = re.compile(r"^(hybrid( work)?|remote|on ?site|onsite|homeoffice|home office)( in)?\s+")
DONE_STATUSES = {m.JobStatus.BUILT, m.JobStatus.APPLIED, m.JobStatus.INTERVIEW, m.JobStatus.OFFER, m.JobStatus.REJECTED}
_RANK = {m.JobStatus.OFFER: 9, m.JobStatus.INTERVIEW: 8, m.JobStatus.APPLIED: 7, m.JobStatus.REJECTED: 6,
         m.JobStatus.BUILT: 5, m.JobStatus.SCORED: 4, m.JobStatus.NEEDS_REVIEW: 3, m.JobStatus.NEW: 2,
         m.JobStatus.EXCLUDED: 1}


def company_core(name: str) -> str:
    s = norm_company(name)
    core = " ".join(_COMPANY_EXTRA.sub(" ", s).split())
    return core or s


def title_core(title: str) -> str:
    words = []
    for w in norm_title(title).split():
        words += _DE_EN.get(w, w).split()
    return " ".join(words)


def place_core(location: str) -> str:
    c = _WORK.sub("", norm_city(location or "")).strip()
    if not c or c in ("remote", "germany", "deutschland"):
        return ""
    return places.fold(places.find(c).name)


class _Item:
    __slots__ = ("id", "company", "title", "tokens", "place")

    def __init__(self, id_: int, company: str, title: str, location: str):
        self.id, self.company, self.title = id_, company_core(company), title_core(title)
        self.tokens, self.place = set(self.title.split()), place_core(location)


def same_job(a: _Item, b: _Item) -> bool:
    if not a.company or a.company != b.company or not a.title:
        return False
    if a.place and b.place and a.place != b.place:
        return False
    if a.title == b.title:
        return True
    if (a.tokens & _LEVEL) != (b.tokens & _LEVEL):
        return False
    return len(a.tokens ^ b.tokens) <= 2 and SequenceMatcher(None, a.title, b.title).ratio() >= 0.9


class DupIndex:
    """The stored jobs by company, to find a near duplicate of a new one quickly."""

    def __init__(self, s):
        self.by: dict[str, list[_Item]] = {}
        for id_, co, ti, loc in s.exec(select(m.Job.id, m.Job.company, m.Job.title, m.Job.location)).all():
            self.add(id_, co, ti, loc)

    def add(self, id_: int, company: str, title: str, location: str) -> None:
        it = _Item(id_, company, title, location)
        if it.company:
            self.by.setdefault(it.company, []).append(it)

    def find(self, company: str, title: str, location: str) -> Optional[int]:
        it = _Item(0, company, title, location)
        for other in self.by.get(it.company, []):
            if same_job(it, other):
                return other.id
        return None


def merge_existing() -> int:
    """Copies already on your board: the one you worked on (or the oldest) stays, the others are hidden as
    "Duplicate of #id" and give it their sites and links. Jobs you built documents for or applied to are
    never hidden. Returns how many copies were hidden."""
    hidden = 0
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job)).all()
        items = {j.id: _Item(j.id, j.company, j.title, j.location) for j in jobs}
        by_id = {j.id: j for j in jobs}
        groups: dict[str, list[_Item]] = {}
        for it in items.values():
            if it.company:
                groups.setdefault(it.company, []).append(it)
        for members in groups.values():
            if len(members) < 2:
                continue
            parent = {it.id: it.id for it in members}

            def root(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if same_job(a, b):
                        parent[root(a.id)] = root(b.id)
            clusters: dict[int, list[m.Job]] = {}
            for it in members:
                clusters.setdefault(root(it.id), []).append(by_id[it.id])
            for cl in clusters.values():
                if len(cl) < 2:
                    continue
                keep = max(cl, key=lambda j: (_RANK.get(j.status, 0), len(j.full_description or ""), -j.id))
                for j in cl:
                    if j.id == keep.id or j.status not in (m.JobStatus.NEW, m.JobStatus.SCORED, m.JobStatus.NEEDS_REVIEW):
                        continue
                    keep.sources = list(dict.fromkeys((keep.sources or []) + (j.sources or [])))
                    keep.links = list(dict.fromkeys((keep.links or []) + (j.links or [])))
                    if not keep.location and j.location:
                        keep.location = j.location
                    if len(j.full_description or "") > len(keep.full_description or ""):
                        keep.full_description = j.full_description
                    j.status, j.exclude_reason = m.JobStatus.EXCLUDED, f"Duplicate of #{keep.id}"
                    s.add(j)
                    hidden += 1
                s.add(keep)
    return hidden
