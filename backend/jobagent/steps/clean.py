"""Clean & dedupe (plain Python, no AI).

Applies the filters a second time (site filters are unreliable), removes
duplicates against today's results and every earlier run, and stores jobs.
Nothing is dropped silently: excluded jobs are stored with their reason.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from sqlmodel import or_, select

from .. import db, models as m
from ..schemas import RawJob
from ..textutil import SENIOR_TITLE, STUDENT_TITLE, dedupe_key, normalize_url, strip_accents

# ----------------------------------------------------------------- regions ---
GERMAN_CITIES = (
    "berlin", "munich", "munchen", "hamburg", "frankfurt", "cologne", "koln", "stuttgart", "dusseldorf",
    "leipzig", "dresden", "karlsruhe", "hannover", "nuremberg", "nurnberg", "bonn", "essen", "dortmund",
    "mannheim", "heidelberg", "potsdam", "aachen", "bremen", "freiburg", "darmstadt", "wiesbaden", "mainz",
    "jena", "ulm", "augsburg", "regensburg", "kiel", "lubeck", "munster", "erlangen", "walldorf", "ingolstadt",
    "wolfsburg", "braunschweig", "gottingen", "kassel", "bochum", "duisburg", "bielefeld", "saarbrucken",
    "paderborn", "rostock", "magdeburg", "halle", "chemnitz", "konstanz", "tubingen", "wurzburg", "garching",
)
GERMANY = ("germany", "deutschland", ", de", " de)") + GERMAN_CITIES
EUROPE = (
    "germany", "deutschland", "netherlands", "nederland", "amsterdam", "rotterdam", "eindhoven", "france", "paris",
    "lyon", "bordeaux", "toulouse", "spain", "madrid", "barcelona", "austria", "osterreich", "vienna", "wien",
    "switzerland", "schweiz", "zurich", "geneva", "basel", "lausanne", "ireland", "dublin", "poland", "warsaw",
    "krakow", "wroclaw", "sweden", "stockholm", "gothenburg", "denmark", "copenhagen", "belgium", "brussels",
    "antwerp", "italy", "milan", "rome", "turin", "portugal", "lisbon", "porto", "czech", "prague", "brno",
    "finland", "helsinki", "norway", "oslo", "luxembourg", "esch-sur-alzette", "hungary", "budapest", "romania",
    "bucharest", "greece", "athens", "estonia", "tallinn", "latvia", "riga", "lithuania", "vilnius", "croatia",
    "slovenia", "slovakia", "bulgaria", "european union", "europe", "emea", "united kingdom", "london",
) + GERMAN_CITIES
UK = ("united kingdom", "england", "scotland", "wales", "london", "manchester", "cambridge", "oxford",
      "edinburgh", "bristol", "birmingham", "glasgow", "leeds", ", uk", " uk")
US_STATES = ("alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
             "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
             "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
             "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
             "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
             "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming")
US = ("united states", "usa", "u.s.", "san francisco", "seattle", "boston", "austin", "chicago") + US_STATES
US_ABBR = re.compile(r",\s*(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b")
CANADA = ("canada", "toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo", "ontario", "quebec",
          "british columbia", "alberta", ", on", ", bc", ", qc")
AUSTRALIA = ("australia", "sydney", "melbourne", "brisbane", "perth", "canberra", "adelaide", ", nsw", ", vic")
NZ = ("new zealand", "auckland", "wellington", "christchurch")

REGION_WORDS = {
    "Berlin": ("berlin",),
    "Germany": GERMANY,
    "Europe": EUROPE,
    "United Kingdom": UK,
    "United States": US,
    "Canada": CANADA,
    "Australia": AUSTRALIA,
    "New Zealand": NZ,
}


def is_remote(location: str, work_mode: str = "", text: str = "") -> bool:
    s = f"{location} {work_mode}".lower()
    return "remote" in s or "home office" in s or "homeoffice" in s or "anywhere" in s


def location_matches(location: str, allowed: list[str], work_mode: str = "") -> Optional[bool]:
    """True/False, or None when the location is unknown (kept, never dropped silently)."""
    loc = " " + strip_accents(location or "").lower() + " "
    if not location.strip():
        return None
    if "Remote" in allowed and is_remote(location, work_mode):
        return True
    for region in allowed:
        words = REGION_WORDS.get(region)
        if words and any(w in loc for w in words):
            return True
        if region == "United States" and US_ABBR.search(location or ""):
            return True
    return False


# --------------------------------------------------------------- filtering ---
# Words that make a title an AI / ML / data role. Deliberately broad: a job is only skipped before
# reading its description when its title contains none of them (e.g. "Werkstudent Vertrieb").
FIELD_TITLE = re.compile(
    r"(\b(ai|ki|ml|bi|nlp|llms?|rag|etl|sql|cv|gen ?ai|mlops|llmops|dataops)\b|\ba\.i\.|data|daten|"
    r"artificial|künstlich|kuenstlich|intelligen|machine|maschinell|learning|deep|neural|genai|generativ|"
    r"analy|scien|wissenschaft|statisti|python|algorith|quant|vision|robot|agent|chatbot|prompt|"
    r"automati|business intelligence|power ?bi|tableau|dashboard|reporting|warehouse|pipeline|cloud|"
    r"forecast|predict|insight|research|forschung|informatik|computer|software|developer|entwickl|"
    r"engineer|ingenieur|tech|digital|it[- ]|mathemati|optimi)", re.I)


NAV_TITLE = re.compile(r"^\s*(skip (navigation|to (main )?content)|zum inhalt springen|letzte (woche|24 stunden|7 tage)|"
                       r"heute|gestern|privatsphäre|datenschutz|impressum|cookies?|cookie-einstellungen|anmelden|login|"
                       r"log in|sign in|registrieren|mehr anzeigen|alle jobs|filter|sortieren|menü|menu|home|start|"
                       r"jobs|stellenangebote|karriere|careers?|weiter|zurück|next|previous)\s*$", re.I)
ROLE_WORD = re.compile(r"werkstudent|student|intern|praktik|thesis|abschluss|trainee|junior|engineer|ingenieur|scientist|"
                       r"wissenschaft|analyst|analytics|developer|entwickler|manager|consultant|berater|specialist|spezialist|"
                       r"assist|architect|research|forsch|associate|designer|programm|software|data|daten|machine|learning|"
                       r"\bki\b|\bai\b|\bml\b|devops|mitarbeiter|referent|koordinator|techni|position|stelle|job|"
                       r"\((m|w|f|d|x|gn|all)[^)]{0,12}\)", re.I)


def not_a_job(title: str, url: str = "") -> bool:
    """Links from a search page ("Skip navigation", "Letzte Woche", "Norddeutschland") are not jobs."""
    t = (title or "").strip()
    if not t or NAV_TITLE.match(t):
        return True
    if re.search(r"/jobs/search[/?]|/suche\?|/search\?", url or "", re.I) and not ROLE_WORD.search(t):
        return True
    return len(t.split()) <= 3 and not ROLE_WORD.search(t)


def prefilter_reason(job: RawJob, fp: m.FilterProfile, today: Optional[date] = None) -> Optional[str]:
    """Quick checks before fetching a description. Returns a reason to exclude, or None."""
    today = today or date.today()
    title = job.title or ""
    if not_a_job(title, job.url):
        return "Not a job (a link from a search page)"
    if SENIOR_TITLE.search(title) and not STUDENT_TITLE.search(title):
        return "Seniority in title (full-time role)"
    if title and not FIELD_TITLE.search(title) and not any(w and w.lower() in title.lower() for w in fp.keywords_in or []):
        return "Not an AI / ML / data role (title)"
    if job.posted_date and job.posted_date < today - timedelta(days=fp.days):
        return f"Posted more than {fp.days} days ago ({job.posted_date.isoformat()})"
    comp = (job.company or "").lower()
    for c in fp.exclude_companies or []:
        if c and c.lower() in comp:
            return f"Excluded company ({c})"
    tl = title.lower()
    for w in fp.keywords_out or []:
        if w and w.lower() in tl:
            return f"Excluded keyword in title ({w})"
    lm = location_matches(job.location, fp.locations, job.work_mode_hint)
    if lm is False:
        return f"Location not in filter ({job.location})"
    return None


def full_filter_reason(job: RawJob, fp: m.FilterProfile) -> Optional[str]:
    r = prefilter_reason(job, fp)
    if r:
        return r
    text = f"{job.title}\n{job.description}".lower()
    for w in fp.keywords_out or []:
        if w and w.lower() in text:
            return f"Excluded keyword ({w})"
    if fp.keywords_in:
        if not any(w.lower() in text for w in fp.keywords_in if w):
            return "None of the must-include keywords"
    return None


# ------------------------------------------------------------ dedupe + store ---
def _find_existing(s, url_key: str, dkey: str) -> Optional[m.Job]:
    conds = []
    if url_key:
        conds.append(m.Job.url_key == url_key)
    if dkey and not dkey.startswith("|"):
        conds.append(m.Job.dedupe_key == dkey)
    if not conds:
        return None
    return s.exec(select(m.Job).where(or_(*conds))).first()


def ingest(raw_jobs: list[RawJob], run_id: Optional[int], fp: m.FilterProfile) -> dict:
    """Store new jobs, merge duplicates. Returns counts and the ids of new jobs to classify."""
    counts = {"found": len(raw_jobs), "new": 0, "duplicates": 0, "excluded": 0, "no_description": 0,
              "previously_deleted": 0}
    new_ids: list[int] = []
    with db.session_scope() as s:
        dead_urls, dead_keys = _tombstones(s)
        for rj in raw_jobs:
            url_key = normalize_url(rj.url)
            dkey = dedupe_key(rj.company, rj.title, rj.location)
            if (url_key and url_key in dead_urls) or dkey in dead_keys:
                counts["previously_deleted"] += 1
                continue
            existing = _find_existing(s, url_key, dkey)
            if existing:
                counts["duplicates"] += 1
                if rj.source not in existing.sources:
                    existing.sources = existing.sources + [rj.source]
                if rj.url and rj.url not in existing.links:
                    existing.links = existing.links + [rj.url]
                if rj.description and len(rj.description) > len(existing.full_description or ""):
                    existing.full_description = rj.description
                existing.last_seen_run = run_id
                s.add(existing)
                continue
            reason = rj.prefilter_reason or full_filter_reason(rj, fp)
            job = m.Job(
                company=rj.company.strip(), title=rj.title.strip(), location=rj.location.strip(),
                work_mode=rj.work_mode_hint or ("Remote" if is_remote(rj.location) else ""),
                posted_date=rj.posted_date, sources=[rj.source], links=[rj.url] if rj.url else [],
                full_description=rj.description.strip(), salary=rj.salary, dedupe_key=dkey, url_key=url_key,
                job_type=rj.job_type_hint, first_seen_run=run_id, last_seen_run=run_id,
                status=m.JobStatus.EXCLUDED if reason else m.JobStatus.NEW, exclude_reason=reason or "")
            if not job.full_description and not reason:
                counts["no_description"] += 1
                job.notes = "Description incomplete (could not be retrieved)"
            s.add(job)
            s.flush()
            if reason:
                counts["excluded"] += 1
            else:
                counts["new"] += 1
                new_ids.append(job.id)
    counts["new_ids"] = new_ids
    return counts


def _tombstones(s) -> tuple[set[str], set[str]]:
    rows = s.exec(select(m.DeletedJob)).all()
    return {r.url_key for r in rows if r.url_key}, {r.dedupe_key for r in rows if r.dedupe_key and not r.dedupe_key.startswith("|")}


def known_url_keys() -> set[str]:
    """Jobs already in the database or deleted by you: their descriptions are not fetched again."""
    with db.session_scope() as s:
        keys = {k for k in s.exec(select(m.Job.url_key)).all() if k}
        keys |= _tombstones(s)[0]
        return keys


def delete_jobs(job_ids: list[int], delete_files: bool = False, forget: bool = False) -> dict:
    """Delete jobs with their scores, documents and tracker entries.

    forget=False keeps a tombstone so the job never comes back in later searches.
    delete_files=True also removes the built CV / cover letter files (and the folder if it is then empty).
    """
    from pathlib import Path
    removed, files_removed = 0, 0
    with db.session_scope() as s:
        for jid in job_ids:
            job = s.get(m.Job, jid)
            if not job:
                continue
            for model in (m.Score, m.Application):
                for row in s.exec(select(model).where(model.job_id == jid)).all():
                    s.delete(row)
            for d in s.exec(select(m.Document).where(m.Document.job_id == jid)).all():
                if delete_files:
                    for f in (d.docx_path, d.pdf_path):
                        if f and Path(f).exists():
                            try:
                                Path(f).unlink()
                                files_removed += 1
                            except OSError:
                                pass
                    folder = Path(d.docx_path or d.pdf_path).parent if (d.docx_path or d.pdf_path) else None
                    try:
                        if folder and folder.exists() and not any(folder.iterdir()):
                            folder.rmdir()
                    except OSError:
                        pass
                s.delete(d)
            if not forget:
                s.add(m.DeletedJob(url_key=job.url_key, dedupe_key=job.dedupe_key, company=job.company, title=job.title))
            s.delete(job)
            removed += 1
    return {"deleted": removed, "files_deleted": files_removed}



def hide_junk_jobs() -> int:
    """Older searches saved page links as jobs. Move them to Excluded (jobs with a CV or applied stay untouched)."""
    n = 0
    with db.session_scope() as s:
        for j in s.exec(select(m.Job).where(m.Job.status.in_([m.JobStatus.NEW, m.JobStatus.SCORED,
                                                             m.JobStatus.NEEDS_REVIEW]))).all():
            if not_a_job(j.title, j.link):
                j.status, j.exclude_reason = m.JobStatus.EXCLUDED, "Not a job (a link from a search page)"
                s.add(j)
                n += 1
    return n
