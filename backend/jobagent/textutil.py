"""Normalisation helpers shared by agents, cleaning and dedupe."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qs, urlparse

# Query parameters that identify a job; everything else (tracking) is dropped.
_ID_PARAMS = ("jk", "jobid", "job_id", "id", "gh_jid", "refnr", "jl", "jobListingId", "currentJobId")

_COMPANY_SUFFIX = re.compile(
    r"\b(gmbh|ag|se|kg|kgaa|ug|mbh|co|inc|ltd|llc|plc|bv|b\.v\.|sa|s\.a\.|sas|srl|corp|corporation|company|group|holding|&)\b\.?",
    re.I)
SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head of|director|manager|vp|chief|architect)\b", re.I)
STUDENT_TITLE = re.compile(
    r"(werkstudent|working student|studentische|intern\b|internship|praktik|praktikant|trainee|thesis|abschlussarbeit|"
    r"part[- ]?time|teilzeit|student assistant|student job|stage\b|stagiaire|mini[- ]?job)", re.I)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_url(url: str) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    host = re.sub(r"^[a-z]{2}\.(linkedin\.com|indeed\.com)$", r"\1", host)  # de.linkedin.com -> linkedin.com
    path = re.sub(r"/+$", "", p.path)
    m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d{6,})", path)
    if "linkedin.com" in host and m:
        return f"linkedin.com/jobs/view/{m.group(1)}"
    m = re.search(r"/jobPosting/(\d{6,})", path)
    if "linkedin.com" in host and m:
        return f"linkedin.com/jobs/view/{m.group(1)}"
    q = parse_qs(p.query)
    keep = [f"{k}={q[k][0]}" for k in _ID_PARAMS if k in q]
    if "indeed.com" in host and "jk" in q:
        return f"indeed.com/viewjob?jk={q['jk'][0]}"
    return f"{host}{path}" + ("?" + "&".join(keep) if keep else "")


def norm_company(name: str) -> str:
    s = strip_accents(name or "").lower()
    s = _COMPANY_SUFFIX.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_G = r"(?:m|w|d|f|x|i|h|div\.?|divers|diverse|nb|all genders?|alle geschlechter|gn\*?|\*)"
GENDER_TAG = re.compile(r"\s*[(\[]\s*" + _G + r"(?:\s*[/|,*]\s*" + _G + r")*\s*[)\]]\**", re.I)
GENDER_BARE = re.compile(r"\s*\b(?:m|w|d|f|h)\s*/\s*(?:m|w|d|f|h)(?:\s*/\s*(?:m|w|d|f|x|div\.?|i))?\b\.?", re.I)


def strip_gender(text: str) -> str:
    """'Praktikant (m/w/d) im Bereich KI' -> 'Praktikant im Bereich KI'. Every bracketed or bare form job ads
    use: (m/w/d), (M/F/D), (w/m/div.), (gn*), (all genders), [m/w/d], m/w/d, (H/F)."""
    t = GENDER_TAG.sub("", text or "")
    t = GENDER_BARE.sub("", t)
    t = re.sub(r"\s+([,.;:!?)])", r"\1", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def norm_title(title: str) -> str:
    s = strip_accents(title or "").lower()
    s = re.sub(r"\((m|w|d|f|x|div|all genders?|gn)[/|,\s]*(m|w|d|f|x)?[/|,\s]*(m|w|d|f|x)?\)", " ", s)
    s = re.sub(r"\b(m/w/d|w/m/d|m/f/d|f/m/d|all genders?|d/f/m)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def norm_city(location: str) -> str:
    s = strip_accents(location or "").lower()
    first = re.split(r"[,(\-/|]", s)[0]
    return re.sub(r"[^a-z]+", " ", first).strip()


def dedupe_key(company: str, title: str, location: str) -> str:
    return f"{norm_company(company)}|{norm_title(title)}|{norm_city(location)}"


def safe_filename_part(s: str, max_len: int = 60) -> str:
    """Keep accents (é, ü, –) but remove characters Windows forbids."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s or "")
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("._ ")
    return s[:max_len].rstrip("._ ") or "Unknown"


def safe_folder_name(s: str, max_len: int = 80) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    return s[:max_len].rstrip(". ") or "Unknown"


# ------------------------------------------------------------------ company from the job page ---
_ABOUT_CO = re.compile(r"(?:About the company|Über das Unternehmen)\s*\n+\s*([^\n]{2,80}?)\s*\n", re.I)
_NOT_A_COMPANY = re.compile(
    r"^(xing|linkedin|stepstone|indeed|wellfound|show more|mehr anzeigen|similar jobs|ähnliche jobs|all job offers|"
    r"alle stellenangebote|easy apply|save job|log ?in|anmelden|find jobs|jobs finden|search|suche|\d+)$", re.I)


def _company_ok(name: str, title: str) -> bool:
    n = name.strip()
    return (2 <= len(n) <= 80 and not _NOT_A_COMPANY.match(n) and n.lower() != (title or "").strip().lower()
            and not re.search(r"(\+ \d+ (more|weitere)|posted|veröffentlicht|employees|mitarbeitende|€)", n, re.I))


def guess_company(title: str, description: str) -> str:
    """Company name from a stored job page when the search result had none.
    XING pages read '<title>\\n<title>\\n\\n<company>\\n<industry>' and later 'About the company\\n\\n<company>'."""
    text = description or ""
    m = _ABOUT_CO.search(text)
    if m and _company_ok(m.group(1), title):
        return m.group(1).strip()
    t = (title or "").strip().lower()
    if not t:
        return ""
    lines = [ln.strip() for ln in text.split("\n")]
    for i, ln in enumerate(lines):
        if ln.lower() != t:
            continue
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j >= len(lines) or j == i + 1 or lines[j].lower() == t:
            continue                    # needs a blank line after the title block
        if _company_ok(lines[j], title) and j + 1 < len(lines) and lines[j + 1]:
            return lines[j]
    return ""
