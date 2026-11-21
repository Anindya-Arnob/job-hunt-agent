"""Knock-outs: requirements an ad states that no CV wording can fix. Checked only when the ad states them, before a
CV is built (a knock-out stops the build: no tokens spent), on the job board and on Documents.

* language level   "Deutsch C1", "fluent English"           vs the languages on your main CV
* experience       "mehrjährige Erfahrung", "3+ years"      vs the dates of your jobs and internships
* work permit      "Arbeitserlaubnis erforderlich"          vs My details
* degree           "abgeschlossenes Studium", "PhD"         vs the education on your main CV
* student status   "eingeschriebene/r Student/in"           vs My details (or a degree still in progress)

"von Vorteil", "a plus", "wünschenswert" never make a knock-out.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..schemas import CVContent, CVProfile

NICE = re.compile(r"\bplus\b|nice to have|von vorteil|wünschenswert|wuenschenswert|advantage|bonus|desirable|ideally"
                  r"|preferred|gerne|idealerweise|optional", re.I)

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "mär": 3, "maer": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12}
_NOW = re.compile(r"present|current|heute|jetzt|aktuell|now|laufend|ongoing|today", re.I)


def _segments(text: str) -> list[str]:
    return [s for s in re.split(r"[\n;•·*]+|(?<=[.!?])\s+", text or "") if s.strip()]


# ------------------------------------------------------------------ experience ---
_YEARS_NUM = [
    r"(?:mindestens|at least|min\.?|minimum(?: of)?)\s*(\d{1,2})\s*\+?\s*(?:years?|jahre?n?)",
    r"(\d{1,2})\s*\+\s*(?:years?|jahre?n?)",
    r"(\d{1,2})\s*(?:years?|jahre?n?)\s+(?:of\s+)?(?:\w+\s+){0,3}?(?:experience|erfahrung|berufserfahrung)",
    r"(?:experience|erfahrung|berufserfahrung)\s+(?:von|of)\s+(?:mindestens\s+|at least\s+)?(\d{1,2})\s*\+?\s*(?:years?|jahre?n?)",
]
_YEARS_WORD = [
    (r"langjährig\w*,?\s+(?:[\w-]+,?\s+){0,3}?(?:erfahrung|berufserfahrung)|extensive experience"
     r"|many years of experience", 4),
    (r"mehrjährig\w*,?\s+(?:[\w-]+,?\s+){0,3}?(?:erfahrung|berufserfahrung)|several years of\s+(?:[\w-]+\s+){0,2}?"
     r"experience|multiple years of\s+(?:[\w-]+\s+){0,2}?experience|years of (?:professional |relevant |industry )?"
     r"experience", 2),
]


def required_years(text: str) -> Optional[float]:
    """The most work experience the ad requires, in years (None when it asks for none)."""
    need = None
    for seg in _segments(text):
        if NICE.search(seg) or re.search(r"\berste\w*\s+(?:praktische\s+)?erfahrung|first experience", seg, re.I):
            continue
        if not re.search(r"experience|erfahrung|years|jahre", seg, re.I):
            continue
        found = None
        seg = re.sub(r"(\d{1,2})\s*(?:-|–|bis|to)\s*\d{1,2}(\s*(?:years?|jahre))", r"\1\2", seg, flags=re.I)  # 2-4 -> 2
        for rx in _YEARS_NUM:
            for m in re.finditer(rx, seg, re.I):
                n = int(m.group(1))
                if 0 < n <= 20 and re.search(r"experience|erfahrung", seg, re.I):
                    found = max(found or 0, n)
        if found is None:
            for rx, n in _YEARS_WORD:
                if re.search(rx, seg, re.I):
                    found = max(found or 0, n)
        if found:
            need = max(need or 0, found)
    return need


def _month(s: str) -> Optional[tuple[int, int]]:
    s = s.strip().lower()
    if not s:
        return None
    if _NOW.search(s):
        t = date.today()
        return t.year, t.month
    m = re.search(r"(\d{1,2})\s*[./]\s*(\d{4})", s)
    if m:
        return int(m.group(2)), max(1, min(12, int(m.group(1))))
    m = re.search(r"([a-zäö]{3})[a-zäö]*\.?\s*(\d{4})", s)
    if m and m.group(1) in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1)]
    m = re.search(r"(\d{4})", s)
    if m:
        return int(m.group(1)), 1
    return None


def work_months(prof) -> int:
    """Months of work (jobs and internships) from the dates on the CV; overlapping ones count once."""
    spans = []
    for e in prof.experience:
        parts = re.split(r"\s*(?:–|-|—|to|bis)\s*", e.dates or "", maxsplit=1)
        a = _month(parts[0]) if parts else None
        b = _month(parts[1]) if len(parts) > 1 else a
        if not a or not b:
            continue
        sa, sb = a[0] * 12 + a[1], b[0] * 12 + b[1]
        if sb >= sa:
            spans.append((sa, sb + 1))              # both months count: Dec-Feb = 3 months
    spans.sort()
    total, cur = 0, None
    for s, e in spans:
        if cur and s <= cur[1]:
            cur = (cur[0], max(cur[1], e))
        else:
            if cur:
                total += cur[1] - cur[0]
            cur = (s, e)
    if cur:
        total += cur[1] - cur[0]
    return total


def _fmt_months(n: int) -> str:
    if n < 12:
        return f"{n} month{'s' if n != 1 else ''} of work"
    y = n / 12
    return f"{y:.1f}".rstrip("0").rstrip(".") + " years of work"


# --------------------------------------------------------------------- degree ---
_LEVEL = {"any": 1, "bachelor": 1, "master": 2, "phd": 3}


def required_degree(text: str) -> Optional[str]:
    need = None
    for seg in _segments(text):
        if NICE.search(seg) or re.search(r"student|studierend|enrolled|pursuing|thesis|arbeit\b|masterarbeit|"
                                         r"bachelorarbeit|werkstudent|praktikum|internship|im studium", seg, re.I):
            continue
        lvl = None
        if re.search(r"\bph\.?\s?d\b|promotion\b|doctorate|doktor", seg, re.I) and not re.search(r"master|or equivalent", seg, re.I):
            lvl = "phd"
        elif re.search(r"(abgeschlossen\w*|completed|finished)\s+(\w+\s+){0,2}?(master|diplom)|master'?s?\s+degree\s+(is\s+)?"
                       r"(required|mandatory)|master(abschluss|studium)\s+(\w+\s+){0,2}?(erforderlich|vorausgesetzt)", seg, re.I):
            lvl = "master"
        elif re.search(r"abgeschlossene[sn]?\s+(\w+\s+){0,2}?(studium|hochschulstudium|bachelor|ausbildung)|"
                       r"completed\s+(\w+\s+){0,2}?(degree|studies|bachelor)|(university\s+)?degree\s+(is\s+)?(required|"
                       r"mandatory)|(bachelor'?s?|university)\s+degree\s+in\b", seg, re.I):
            lvl = "bachelor"
        if lvl and (need is None or _LEVEL[lvl] > _LEVEL[need]):
            need = lvl
    return need


def finished_degree(prof) -> Optional[str]:
    """The highest degree on the CV that is finished (not 'laufend' / 'present')."""
    best = None
    for ed in prof.education:
        if _NOW.search(ed.dates or "") or _NOW.search(ed.degree or ""):
            continue
        d = (ed.degree or "").lower()
        lvl = "phd" if re.search(r"ph\.?d|doctor|promotion|dr\.", d) else \
            "master" if re.search(r"master|m\.?\s?sc|m\.?\s?a\b|m\.?\s?eng|m\.?\s?tech|mba|diplom", d) else "bachelor"
        if best is None or _LEVEL[lvl] > _LEVEL[best]:
            best = lvl
    return best


# ---------------------------------------------------------- permit / student ---
_PERMIT = re.compile(r"arbeitserlaubnis|aufenthaltstitel|work permit|right to work|work authori[sz]ation|"
                     r"authori[sz]ed to work|eligible to work|no visa sponsorship|keine visa|visa sponsorship is not|"
                     r"cannot sponsor|unable to sponsor|eu[- ]citizens? only|eu[- ]bürger|eu citizenship", re.I)
_STUDENT = re.compile(r"eingeschrieben|immatrikuliert|enrolled (as a )?student|enrolled at a|active student status|"
                      r"studierendenstatus|ordentlich\w* student|must be (a )?(current )?student|"
                      r"currently enrolled|valid student (status|id)", re.I)


def _yes(v: str) -> Optional[bool]:
    v = (v or "").strip().lower()
    if not v:
        return None
    return v.startswith(("y", "j", "true"))


def _asks(rx: re.Pattern, text: str) -> bool:
    return any(rx.search(seg) and not NICE.search(seg) for seg in _segments(text))


# ----------------------------------------------------------------------- all ---
def check(description: str, prof, details=None) -> list[dict]:
    """Every requirement the ad states that this check knows, met or not:
    {"key", "label", "need", "have", "ok", "text"}. Not met = a knock-out."""
    from ..docs.ats import CEFR, LANGS, level_of, required_levels
    out = []
    for k, need in required_levels(description).items():
        mine = [l for l in prof.languages if any(n in l.language.lower() for n in LANGS[k])]
        have = level_of(mine[0].level) if mine else None
        if not have and details is not None:            # My details as a fallback
            have = level_of(getattr(details, f"{k}_level", "") or "")
        ok = bool(have) and CEFR.index(have) >= CEFR.index(need)
        out.append({"key": f"language:{k}", "label": k.title(), "need": need, "have": have or "not listed", "ok": ok,
                    "text": f"{k.title()}: the ad asks for {need}, your CV " +
                            (f"says {have}" if have else "does not list it")})
    yrs = required_years(description)
    if yrs:
        months = work_months(prof)
        ok = months >= yrs * 12 * 0.9
        out.append({"key": "experience", "label": "Experience", "need": f"{yrs:g}+ years", "have": _fmt_months(months),
                    "ok": ok, "text": f"Experience: the ad asks for {yrs:g}+ years, your CV shows {_fmt_months(months)}"})
    deg = required_degree(description)
    if deg:
        have = finished_degree(prof)
        ok = have is not None and _LEVEL[have] >= _LEVEL[deg]
        name = {"bachelor": "a finished degree", "master": "a finished Master's", "phd": "a PhD", "any": "a degree"}[deg]
        out.append({"key": "degree", "label": "Degree", "need": name,
                    "have": {"bachelor": "Bachelor's", "master": "Master's", "phd": "PhD"}.get(have, "none finished"),
                    "ok": ok, "text": f"Degree: the ad asks for {name}, your CV shows " +
                                      ({"bachelor": "a finished Bachelor's", "master": "a finished Master's",
                                        "phd": "a PhD"}.get(have, "no finished degree"))})
    if _asks(_PERMIT, description):
        auth = _yes(getattr(details, "work_authorized", "")) if details is not None else None
        sponsor = _yes(getattr(details, "needs_sponsorship", "")) if details is not None else None
        ok = None if auth is None else (auth and not sponsor)
        out.append({"key": "permit", "label": "Work permit", "need": "work permit", "ok": ok is not False,
                    "unknown": ok is None,
                    "have": "yes" if ok else ("not filled in on My details" if ok is None else "no"),
                    "text": "Work permit: required" + (", you have one" if ok else
                                                       " - fill in 'work permit' on My details" if ok is None else
                                                       ", My details says you need one")})
    if _asks(_STUDENT, description):
        st = _yes(getattr(details, "enrolled_student", "")) if details is not None else None
        if st is None:
            st = any(_NOW.search(ed.dates or "") for ed in prof.education)
        out.append({"key": "student", "label": "Student status", "need": "enrolled student", "ok": bool(st),
                    "have": "enrolled" if st else "not enrolled",
                    "text": "Student status: the ad asks for an enrolled student" + (", you are one" if st else
                                                                                     ", you are not enrolled")})
    return out


def knockouts(description: str, prof, details=None) -> list[str]:
    return [r["text"] for r in check(description, prof, details) if not r["ok"]]


def details_or_none():
    try:
        from ..apply import profile as ap
        return ap.load()
    except Exception:
        return None
