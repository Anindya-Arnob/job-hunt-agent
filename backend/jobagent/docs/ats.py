"""ATS score and the checks that keep a CV out of the "no" pile.

The score copies what applicant-tracking systems and a recruiter's first scan look at:

* keywords (45): the posting's required skills (x3), its keywords (x2) and other skills it names (x1),
  matched as exact words in the CV text
* up front (15): the same keywords in the headline, summary or skills (what a recruiter reads first)
* job title (10): the words of the job title found in the CV
* sections (10): Summary, Skills, Experience, Projects, Education, Languages present
* contact (10): email, phone, location, LinkedIn
* results (5): bullets that show a number
* languages (5): languages the posting asks for are listed

"Before" scores your main CV as it is, "after" scores the tailored CV, both against the same job.
"""
from __future__ import annotations

import re
from typing import Optional, Union

from ..schemas import CVContent, CVProfile, JobForWriting
from ..steps.score import profile_text
from ..vocab import ALL_TERMS, concept_key, find_concepts, find_terms_strict, term_regex, variants

TITLE_STOP = {
    "werkstudent", "werkstudentin", "werkstudent:in", "working", "student", "studentin", "studentische", "hilfskraft",
    "intern", "internship", "praktikum", "praktikant", "praktikantin", "pflichtpraktikum", "mandatory", "junior",
    "senior", "m", "w", "d", "f", "x", "in", "und", "and", "the", "of", "for", "im", "mit", "bereich", "als", "a",
    "an", "to", "with", "or", "oder", "all", "genders", "remote", "hybrid", "berlin", "hamburg", "munich", "münchen",
    "part", "time", "teilzeit", "job", "position", "stelle", "thesis", "abschlussarbeit", "masterarbeit", "duales",
    "studium", "trainee", "–", "-", "&", "/", "bachelorarbeit", "bachelor", "master", "von", "für", "zur", "zum",
    "der", "die", "das", "project", "based", "internal",
}
WEAK_START = re.compile(r"^(responsible for|worked on|helped|assisted|participated in|involved in|tasked with|"
                        r"duties included|was part of)\b", re.I)
FIRST_PERSON = re.compile(r"(?<![A-Za-zäöüß/.-])(I|I'm|I've|my|My|me|myself|[Ii]ch|[Mm]ein|[Mm]eine[mnrs]?|mich|mir)"
                          r"(?![A-Za-zäöüß'/.-])")
LANGS = {"german": ("german", "deutsch"), "english": ("english", "englisch"), "french": ("french", "französisch"),
         "spanish": ("spanish", "spanisch")}

Doc = Union[CVContent, CVProfile]

# ------------------------------------------------------------------ language levels ---
CEFR = ["A1", "A2", "B1", "B2", "C1", "C2"]
_LEVEL_WORDS = [
    (r"muttersprach|native|mother tongue", "C2"),
    (r"verhandlungssicher|fließend|fliessend|fluent|proficient|sehr gute?", "C1"),
    (r"fortgeschritten|advanced|upper[- ]intermediate|\bgute?\b|\bgood\b", "B2"),
    (r"konversationssicher|conversational|intermediate|mittelstufe", "B1"),
    (r"grundkenntnisse|basic|elementary|beginner|anfänger", "A2"),
]
_NICE = re.compile(r"\bplus\b|nice to have|von vorteil|wünschenswert|wuenschenswert|advantage|bonus|desirable|ideally"
                   r"|preferred", re.I)


def level_of(text: str) -> Optional[str]:
    """CEFR level written in a text ('C1', 'B1-B2' -> B1, 'fließend' -> C1, 'Grundkenntnisse' -> A2)."""
    m = re.search(r"(?<![A-Za-z])([ABCabc][12])(?![0-9])", text or "")
    if m:
        return m.group(1).upper()
    for rx, lvl in _LEVEL_WORDS:
        if re.search(rx, text or "", re.I):
            return lvl
    return None


def required_levels(description: str) -> dict[str, str]:
    """language -> lowest level the ad requires ('German is a plus' is not a requirement)."""
    out: dict[str, str] = {}
    for seg in re.split(r"[\n;,•·*]+|\s-\s(?=[A-ZÄÖÜ][a-zäöü]+\s*[-:–])", description or ""):
        if _NICE.search(seg):
            continue
        pos = {}
        for k, names in LANGS.items():
            m = min((x.start() for n in names for x in re.finditer(rf"\b{n}", seg, re.I)), default=None)
            if m is not None:
                pos[k] = m
        order = sorted(pos.items(), key=lambda kv: kv[1])
        for i, (k, start) in enumerate(order):       # "German B2 and English C1": each its own level
            end = order[i + 1][1] if i + 1 < len(order) else len(seg)
            lvl = level_of(seg[start:end]) or level_of(seg)
            if lvl and (k not in out or CEFR.index(lvl) > CEFR.index(out[k])):
                out[k] = lvl
    return out


def language_gaps(job: JobForWriting, c: Doc) -> list[str]:
    """'German: the ad asks for C1, your CV says A2': a knock-out no wording can fix."""
    out = []
    for k, need in required_levels(job.description).items():
        mine = [l for l in c.languages if any(n in l.language.lower() for n in LANGS[k])]
        have = level_of(mine[0].level) if mine else None
        if not mine:
            out.append(f"{k.title()}: the ad asks for {need}, your CV does not list it")
        elif have and CEFR.index(have) < CEFR.index(need):
            out.append(f"{k.title()}: the ad asks for {need}, your CV says {have}")
    return out


def hit(term: str, text: str) -> bool:
    """Exact word match, singular or plural (most ATS stem 'AI Agents' / 'AI agent'), in any of the term's
    forms ('Error Handling' = 'Fehlerbehandlung' = 'retries' ...)."""
    forms = variants(term)
    if len(forms) > 1:
        return any(_hit1(f, text) for f in forms)
    return _hit1(term, text)


def _hit1(term: str, text: str) -> bool:
    if len(term) <= 3 and term.isupper():          # AI, KI, ML, API: only with their capitals
        return bool(re.search(rf"(?<![A-Za-z0-9+#]){re.escape(term)}(?![A-Za-z0-9+#])", text))
    if term_regex(term).search(text):
        return True
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
        return bool(term_regex(term[:-1]).search(text))
    if len(term) > 3 and term[-1].isalpha() and term[-1] != "s":
        return bool(term_regex(term + "s").search(text))
    return False


def title_words(title: str) -> list[str]:
    t = re.sub(r"\(.*?\)", " ", title or "")
    words = re.findall(r"[A-Za-zÄÖÜäöüß0-9+#.]+", t)
    out = []
    for w in words:
        lw = w.lower().strip(".")
        if lw and lw not in TITLE_STOP and len(lw) > 1:
            out.append(w.strip("."))
    return list(dict.fromkeys(out))


def keywords(job: JobForWriting) -> dict[str, int]:
    """term -> weight; the strongest weight wins, at most 30 terms."""
    w: dict[str, int] = {}
    key = {}

    def add(t: str, wt: int):
        t = (t or "").strip()
        if not t or len(t) > 40:
            return
        k = concept_key(t)
        if k not in key or wt > w[key[k]]:
            if k in key:
                w.pop(key[k])
            key[k] = t
            w[t] = wt
    for t in find_terms_strict(f"{job.title}\n{job.description}", ALL_TERMS):
        add(t, 1)
    for t in find_concepts(f"{job.title}\n{job.description}"):    # what the ad asks for in words (German too)
        add(t, 1)
    for t in job.keywords:
        add(t, 2)
    for t in job.required_skills:
        add(t, 3)
    ranked = sorted(w.items(), key=lambda kv: -kv[1])[:30]
    return dict(ranked)


def doc_text(c: Doc) -> str:
    parts = [c.tagline, c.summary]
    for g in c.skills:
        parts.append(g.category + ": " + ", ".join(g.items))
    for e in c.experience:
        parts += [e.title, e.org, e.meta, ", ".join(e.stack)] + e.bullets
    for p in c.projects:
        parts += [p.name, ", ".join(p.stack)] + p.bullets
    for ed in c.education:
        parts += [ed.degree, ed.school] + ed.bullets
    for ce in c.certifications:
        parts += [ce.name] + ce.bullets
    parts += [f"{l.language} {l.level}" for l in c.languages]
    return "\n".join(x for x in parts if x)


def bullets(c: Doc) -> list[str]:
    return [b for e in c.experience for b in e.bullets] + [b for p in c.projects for b in p.bullets]


def score(job: JobForWriting, c: Doc, prof: CVProfile, text: Optional[str] = None) -> dict:
    text = text if text is not None else doc_text(c)
    kw = keywords(job)
    matched = [t for t in kw if hit(t, text)]
    missing = [t for t in kw if t not in matched]
    total_w = sum(kw.values()) or 1
    kw_pts = 45 * sum(kw[t] for t in matched) / total_w if kw else 34
    top = "\n".join([c.tagline, c.summary] + [g.category + ": " + ", ".join(g.items) for g in c.skills])
    upfront = [t for t in matched if hit(t, top)]
    top_pts = 15 * sum(kw[t] for t in upfront) / total_w if kw else 11
    tw = title_words(job.title)
    t_hit = [w for w in tw if hit(w, text)]
    title_pts = 10 * len(t_hit) / len(tw) if tw else 10
    sections = {"Summary": bool(c.summary), "Skills": bool(c.skills), "Experience": bool(c.experience),
                "Projects": bool(c.projects), "Education": bool(c.education), "Languages": bool(c.languages)}
    sec_pts = 10 * sum(sections.values()) / len(sections)
    ct = prof.contact
    contact = {"email": bool(ct.email), "phone": bool(ct.phone), "location": bool(ct.location), "LinkedIn": bool(ct.linkedin)}
    con_pts = 10 * sum(contact.values()) / len(contact)
    bl = bullets(c)
    with_num = [b for b in bl if re.search(r"\d", b)]
    res_pts = 5 * min(1.0, (len(with_num) / len(bl)) / 0.3) if bl else 0
    asked = [k for k, names in LANGS.items() if any(re.search(rf"\b{n}\b", job.description or "", re.I) for n in names)]
    listed = " ".join(f"{l.language}" for l in c.languages).lower()
    gaps = language_gaps(job, c)
    short = {g.split(":")[0].lower() for g in gaps}          # listed, but below the level the ad asks for
    lang_hit = [k for k in asked if any(n in listed for n in LANGS[k]) and k not in short]
    lang_pts = 5 * len(lang_hit) / len(asked) if asked else 5
    total = round(kw_pts + top_pts + title_pts + sec_pts + con_pts + res_pts + lang_pts)
    return {"score": max(0, min(100, total)),
            "parts": {"keywords": round(kw_pts), "upfront": round(top_pts), "title": round(title_pts), "sections": round(sec_pts),
                      "contact": round(con_pts), "results": round(res_pts), "languages": round(lang_pts)},
            "matched": matched, "missing": missing,
            "missing_sections": [k for k, v in sections.items() if not v],
            "knockouts": gaps, "n_keywords": len(kw), "few_keywords": len(kw) < 10,
            "missing_contact": [k for k, v in contact.items() if not v],
            "title_words": tw, "title_missing": [w for w in tw if w not in t_hit]}


def addressable(missing: list[str], truth: CVProfile, job: JobForWriting) -> list[str]:
    """Missing keywords you really have (they are somewhere in your CV or GitHub facts)."""
    have = profile_text(truth)
    mine_for = {}
    for mw in job.missing_wording:
        if "->" in mw:
            a, b = [x.strip() for x in mw.split("->", 1)]
            mine_for[b.lower()] = a
    out = []
    for t in missing:
        if hit(t, have):
            out.append(t)
        elif t.lower() in mine_for and term_regex(mine_for[t.lower()]).search(have):
            out.append(t)
    return out


# ------------------------------------------------------------------ availability ---
def availability_fact(job_type: str) -> str:
    """One line recruiters filter on, from My details (empty when the details are not filled in)."""
    try:
        from ..apply import profile as ap
        p = ap.load()
    except Exception:
        return ""
    bits = []
    jt = (job_type or "").lower()
    if "working" in jt or "werk" in jt or "part" in jt:
        if (p.hours_per_week or "").strip():
            bits.append(f"available {p.hours_per_week.strip()} hours per week")
        if (p.enrolled_student or "").lower().startswith("y") and (p.graduation or "").strip():
            bits.append(f"enrolled until {p.graduation.strip()}")
    elif "intern" in jt:
        if (p.internship_duration or "").strip():
            bits.append(f"available for a {p.internship_duration.strip()} internship")
    if (p.earliest_start or "").strip() and bits:
        st = p.earliest_start.strip()
        bits.append(f"can start {st}" if st.lower() in ("immediately", "sofort", "now") else f"can start from {st}")
    if (p.needs_sponsorship or "").lower().startswith("n") and (p.work_authorized or "").lower().startswith("y"):
        bits.append("work permit in Germany, no visa sponsorship needed")
    if not bits:
        return ""
    s = "; ".join(bits)
    return s[0].upper() + s[1:] + "."


# ------------------------------------------------------------------ the screen checks ---
def screen_checks(job: JobForWriting, c: CVContent, prof: CVProfile, truth: CVProfile, ats_after: dict,
                  target: int, fact: str, pdf_text: Optional[str], ats_before: Optional[dict] = None) -> list:
    """Checks a recruiter's first scan would fail you on. Soft ones are shown, they never force a rewrite."""
    from .qa import Check
    out = []
    head = f"{c.tagline}\n{c.summary}"
    tw = title_words(job.title)
    t_hit = [w for w in tw if term_regex(w).search(head)]
    ok = not tw or len(t_hit) >= max(1, (len(tw) + 1) // 2)
    # a long title (often German for an English CV) cannot be put in a headline word by word: shown, no rewrite
    out.append(Check("Job title in your headline", ok,
                     "" if ok else f"add the posting's words to the tagline or summary: {', '.join(w for w in tw if w not in t_hit)}",
                     soft=len(tw) > 4))
    fp = [m.group(0) for s in [c.summary] + bullets(c) for m in FIRST_PERSON.finditer(s)]
    out.append(Check("No 'I' or 'my' in the CV", not fp, f"found: {', '.join(sorted(set(fp)))}" if fp else ""))
    weak = [b for b in bullets(c) if WEAK_START.search(b.strip())]
    out.append(Check("Bullets start with what you did", not weak,
                     "rewrite: " + "; ".join(w[:40] for w in weak[:3]) if weak else ""))
    starts = [(b.split() or [""])[0].lower() for b in bullets(c)]
    rep = sorted({s for s in starts if s and starts.count(s) > 2})
    out.append(Check("Varied bullet openings", not rep, f"'{', '.join(rep)}' starts 3+ bullets" if rep else "", soft=True))
    if fact:
        hours = re.search(r"(\d+)\s*hours", fact)
        ok = bool(hours and re.search(rf"\b{hours.group(1)}\b", head)) if hours else True
        out.append(Check("States your availability", ok, "" if ok else f"end the summary with: {fact}"))
    addr = addressable(ats_after.get("missing", []), truth, job)
    sc = ats_after.get("score", 0)
    hard_ats = sc < target and bool(addr)
    out.append(Check(f"ATS score {target}+", sc >= target,
                     "" if sc >= target else (f"{sc}: add these words you really have: {', '.join(addr[:10])}" if addr else
                                              f"{sc}: the missing words are skills you do not have, so they stay out"),
                     soft=not hard_ats))
    gaps = ats_after.get("knockouts") or []
    out.append(Check("Language level the ad asks for", not gaps, "; ".join(gaps), soft=True))   # no wording fixes it
    if ats_before and ats_before.get("matched"):
        lost = [t for t in ats_before["matched"] if t not in ats_after.get("matched", [])]
        out.append(Check("Keeps the keywords your main CV had", not lost,
                         f"put back: {', '.join(lost[:8])}" if lost else ""))
    if pdf_text is not None:
        flat = " ".join(pdf_text.split())
        found = [t for t in ats_after.get("matched", []) if hit(t, flat)]
        n = len(ats_after.get("matched", []))
        ok = bool(flat) and prof.name.split()[0].lower() in flat.lower() and (not n or len(found) >= 0.8 * n)
        out.append(Check("ATS can read the PDF", ok, "" if ok else "the PDF text could not be read back fully", soft=True))
    bl = bullets(c)
    if bl:
        share = sum(1 for b in bl if re.search(r"\d", b)) / len(bl)
        have_nums = bool(re.search(r"\d", " ".join(bullets(truth))))
        out.append(Check("Results with numbers", share >= 0.3 or not have_nums,
                         f"{round(share * 100)}% of bullets show a number" if share < 0.3 and have_nums else "", soft=True))
    hard_db = [d.replace(" (hard)", "") for d in job.deal_breakers if "(hard)" in d]
    out.append(Check("No knock-out requirement", not hard_db, "; ".join(hard_db[:3]), soft=True))
    return out


def core_title(title: str) -> str:
    """'Werkstudent:in AI & Automation - Energy (m/w/d)' -> 'AI & Automation - Energy'."""
    t = re.sub(r"\(.*?\)", " ", title or "")
    t = re.sub(r"^\s*((werkstudent(:?in|\*in)?|working student|studentische hilfskraft|internship|intern|"
               r"praktikum|pflichtpraktikum|praktikant(:?in)?|mandatory internship)\b[\s:–-]*(im bereich|in|als|for|im)?\s*)+",
               "", t, flags=re.I)
    t = re.sub(r"\s*[-–|,]\s*(werkstudent\w*|working student|internship|praktikum)\s*$", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip(" -–,:")
