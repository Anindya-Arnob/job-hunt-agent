"""Turning pages into jobs: dates, generic description extraction and the Claude fallback."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import llm
from ..browser.reader import Snapshot
from ..schemas import ExtractedJob, PageExtraction, RawJob

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "einem": 1, "einer": 1, "ein": 1}


def parse_posted(text: str, today: Optional[date] = None) -> Optional[date]:
    """'2 days ago', 'vor 3 Tagen', 'today', 'Gestern', '2026-11-02', '30+ days ago' -> date."""
    if not text:
        return None
    today = today or date.today()
    t = text.strip().lower()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    if any(w in t for w in ("just posted", "today", "heute", "just now", "gerade", "new", "neu")) \
            and not re.search(r"\d", t):
        return today
    if any(w in t for w in ("yesterday", "gestern")):
        return today - timedelta(days=1)
    m = re.search(r"(\d+|an?|one|einem|einer|ein)\+?\s*(minute|min|hour|hr|stunde|std|day|tag|week|woche|month|monat)", t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else _NUM_WORDS.get(raw, 1)
        unit = m.group(2)
        if unit.startswith(("min", "hour", "hr", "stunde", "std")):
            return today
        if unit.startswith(("day", "tag")):
            return today - timedelta(days=n)
        if unit.startswith(("week", "woche")):
            return today - timedelta(days=7 * n)
        if unit.startswith(("month", "monat")):
            return today - timedelta(days=30 * n)
    return None


def iso_to_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts).date()
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return parse_posted(s)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for blk in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol", "section"]):
        blk.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def main_text(snap: Snapshot, selectors: tuple[str, ...] = ()) -> str:
    """Best-effort job description from a detail page."""
    soup = BeautifulSoup(snap.html or "", "lxml")
    for sel in selectors:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 150:
            return html_to_text(str(el))
    best, best_len = None, 0
    for el in soup.find_all(["article", "main", "section", "div"]):
        txt = el.get_text(" ", strip=True)
        n_children = len(el.find_all(["div", "section"], recursive=False))
        score = len(txt) - 400 * n_children
        if score > best_len and len(txt) < 40000:
            best, best_len = el, score
    if best is not None and best_len > 200:
        return html_to_text(str(best))
    return (snap.text or "").strip()


# --------------------------------------------------------- Claude fallback ---
_JOB_LINK = re.compile(r"/(jobs?|stellen|stellenangebote|career|careers|position|positions|vacanc|job-?detail|viewjob|o/)", re.I)


# A single posting on these sites (menus and category pages also contain "/jobs/")
_SITE_JOB_LINK = {
    "xing": re.compile(r"/jobs/[a-z0-9-]+-\d{3,}(?:$|[?#/])", re.I),
    "wellfound": re.compile(r"/(jobs/\d{3,}|company/[^/]+/jobs/\d{3,})", re.I),
    "glassdoor": re.compile(r"glassdoor\.[a-z.]+/(job-listing/|partner/jobListing|.*jobListingId=)", re.I),
    "indeed": re.compile(r"indeed\.com/(viewjob|rc/clk|pagead/clk)|[?&]jk=", re.I),
    "stepstone": re.compile(r"stepstone\.de/stellenangebote--", re.I),
    "linkedin": re.compile(r"linkedin\.com/jobs/view/", re.I),
}


def _fake_extract(snap: Snapshot, site: str = "") -> PageExtraction:
    """Offline heuristic: job-looking links become jobs."""
    from ..browser.reader import detect_block
    blk = detect_block(snap)
    if blk in ("login_wall", "captcha"):
        return PageExtraction(page_kind=blk)
    jobs, seen = [], set()
    for text, href in snap.links:
        t = " ".join(text.split())
        pattern = _SITE_JOB_LINK.get(site, _JOB_LINK)
        if not (8 <= len(t) <= 140) or not pattern.search(href) or href in seen:
            continue
        if t.lower() in ("jobs", "careers", "all jobs", "see all jobs", "next", "previous"):
            continue
        seen.add(href)
        jobs.append(ExtractedJob(title=t.split("\n")[0], url=href))
    return PageExtraction(page_kind="results" if jobs else "no_results", jobs=jobs)


def llm_extract(snap: Snapshot, site: str) -> PageExtraction:
    user = (f"SITE: {site}\nURL: {snap.final_url}\nTITLE: {snap.title}\n\nPAGE TEXT:\n{snap.text[:24000]}\n\n"
            f"LINKS:\n{snap.links_block()}")
    res = llm.structured("extract_page", PageExtraction, llm.prompt("extract_page"), user,
                         fake=lambda: _fake_extract(snap, site), max_tokens=6000)
    for j in res.jobs:
        if j.url and not j.url.startswith("http"):
            j.url = urljoin(snap.final_url, j.url)
    return res


def extracted_to_raw(site: str, jobs: list[ExtractedJob], query: str) -> list[RawJob]:
    out = []
    for j in jobs:
        out.append(RawJob(source=site, title=j.title, company=j.company, location=j.location,
                          url=j.url, posted_text=j.posted_text, posted_date=parse_posted(j.posted_text),
                          job_type_hint=j.job_type_hint, query=query))
    return out
