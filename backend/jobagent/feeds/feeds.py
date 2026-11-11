"""Official, public job feeds. No login, no scraping, no account risk.

* Company applicant-tracking systems (Greenhouse, Lever, Ashby, SmartRecruiters,
  Personio) for your list of target companies
* Bundesagentur für Arbeit (Germany's official job board)
* Arbeitnow and RemoteOK (public job APIs)
"""
from __future__ import annotations

import base64
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Optional

import httpx

from ..agents.extract import html_to_text, iso_to_date
from ..schemas import RawJob

log = logging.getLogger(__name__)

UA = {"User-Agent": "JobHuntAgent/1.0 (personal job search)"}

BASES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards",
    "lever": "https://api.lever.co/v0/postings",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies",
    "personio": "https://{board}.jobs.personio.de",
    "arbeitsagentur": "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service",
    "arbeitnow": "https://www.arbeitnow.com/api/job-board-api",
    "remoteok": "https://remoteok.com/api",
}

ROLE_WORDS = re.compile(
    r"(data|machine learning|\bml\b|\bai\b|\bki\b|artificial intelligence|künstliche intelligenz|llm|genai|"
    r"generative|deep learning|nlp|computer vision|mlops|analytics|analyst|scientist|engineer.*(data|ml|ai))", re.I)


def relevant(title: str) -> bool:
    return bool(ROLE_WORDS.search(title or ""))


def _within(d: Optional[date], days: int) -> bool:
    return d is None or d >= date.today() - timedelta(days=days)


def _client(transport=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True, transport=transport)


# ------------------------------------------------------------------ ATS feeds ---
async def greenhouse(board: str, company: str, days: int, client: httpx.AsyncClient) -> list[RawJob]:
    r = await client.get(f"{BASES['greenhouse']}/{board}/jobs", params={"content": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        d = iso_to_date(j.get("first_published") or j.get("updated_at"))
        if not relevant(j.get("title", "")) or not _within(d, days):
            continue
        import html as _html
        out.append(RawJob(source="ats_feeds", title=j["title"], company=company,
                          location=(j.get("location") or {}).get("name", ""), url=j.get("absolute_url", ""),
                          posted_date=d, description=html_to_text(_html.unescape(j.get("content", ""))),
                          mode="feed"))
    return out


async def lever(board: str, company: str, days: int, client: httpx.AsyncClient) -> list[RawJob]:
    r = await client.get(f"{BASES['lever']}/{board}", params={"mode": "json"})
    r.raise_for_status()
    out = []
    for j in r.json():
        d = iso_to_date(j.get("createdAt"))
        if not relevant(j.get("text", "")) or not _within(d, days):
            continue
        cats = j.get("categories") or {}
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []) or []:
            parts.append(lst.get("text", "") + "\n" + html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        out.append(RawJob(source="ats_feeds", title=j["text"], company=company, location=cats.get("location", ""),
                          url=j.get("hostedUrl", ""), posted_date=d, description="\n\n".join(p for p in parts if p),
                          job_type_hint=cats.get("commitment", ""),
                          work_mode_hint=j.get("workplaceType", ""), mode="feed"))
    return out


async def ashby(board: str, company: str, days: int, client: httpx.AsyncClient) -> list[RawJob]:
    r = await client.get(f"{BASES['ashby']}/{board}", params={"includeCompensation": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        d = iso_to_date(j.get("publishedAt"))
        if not relevant(j.get("title", "")) or not _within(d, days):
            continue
        out.append(RawJob(source="ats_feeds", title=j["title"], company=company, location=j.get("location", ""),
                          url=j.get("jobUrl", ""), posted_date=d,
                          description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                          job_type_hint=j.get("employmentType", ""),
                          work_mode_hint="Remote" if j.get("isRemote") else "", mode="feed",
                          salary=((j.get("compensation") or {}).get("compensationTierSummary") or "")))
    return out


async def smartrecruiters(board: str, company: str, days: int, client: httpx.AsyncClient) -> list[RawJob]:
    r = await client.get(f"{BASES['smartrecruiters']}/{board}/postings", params={"limit": 100})
    r.raise_for_status()
    out = []
    for j in r.json().get("content", []):
        d = iso_to_date(j.get("releasedDate"))
        if not relevant(j.get("name", "")) or not _within(d, days):
            continue
        loc = j.get("location") or {}
        detail = await client.get(f"{BASES['smartrecruiters']}/{board}/postings/{j['id']}")
        desc = ""
        if detail.status_code == 200:
            secs = ((detail.json().get("jobAd") or {}).get("sections") or {})
            desc = "\n\n".join(html_to_text((secs.get(k) or {}).get("text", ""))
                               for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
        out.append(RawJob(source="ats_feeds", title=j["name"], company=company,
                          location=", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                          url=f"https://jobs.smartrecruiters.com/{board}/{j['id']}", posted_date=d,
                          description=desc.strip(), job_type_hint=(j.get("typeOfEmployment") or {}).get("label", ""),
                          work_mode_hint="Remote" if loc.get("remote") else "", mode="feed"))
    return out


async def personio(board: str, company: str, days: int, client: httpx.AsyncClient) -> list[RawJob]:
    base = BASES["personio"].format(board=board)
    r = await client.get(f"{base}/xml", params={"language": "en"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for pos in root.iter("position"):
        title = (pos.findtext("name") or "").strip()
        d = iso_to_date(pos.findtext("createdAt"))
        if not relevant(title) or not _within(d, days):
            continue
        desc = []
        for jd in pos.iter("jobDescription"):
            desc.append(f"{jd.findtext('name') or ''}\n{html_to_text(jd.findtext('value') or '')}")
        out.append(RawJob(source="ats_feeds", title=title, company=company, location=pos.findtext("office") or "",
                          url=f"{base}/job/{pos.findtext('id')}", posted_date=d, description="\n\n".join(desc),
                          job_type_hint=f"{pos.findtext('employmentType') or ''} {pos.findtext('schedule') or ''}".strip(),
                          mode="feed"))
    return out


ATS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
       "smartrecruiters": smartrecruiters, "personio": personio}


# ----------------------------------------------------- Bundesagentur für Arbeit ---
BA_HEADERS = {"X-API-Key": "jobboerse-jobsuche"}   # public key used by the official web app


async def arbeitsagentur(keywords: str, location: str, days: int, client: httpx.AsyncClient,
                         angebotsart: Optional[int] = None, part_time: bool = False,
                         max_pages: int = 3) -> tuple[list[RawJob], Optional[int]]:
    out, total = [], None
    for page in range(1, max_pages + 1):
        params = {"was": keywords, "veroeffentlichtseit": str(days), "page": str(page), "size": "50"}
        if location and location not in ("Germany", "Remote", "Europe"):
            params["wo"] = location
        if angebotsart:
            params["angebotsart"] = str(angebotsart)
        if part_time:
            params["arbeitszeit"] = "tz;snw;mj"   # Teilzeit; Schicht/Nacht/Wochenende; Minijob
        r = await client.get(f"{BASES['arbeitsagentur']}/pc/v4/jobs", params=params, headers=BA_HEADERS)
        if r.status_code != 200:
            break
        data = r.json()
        total = data.get("maxErgebnisse", total)
        items = data.get("stellenangebote") or []
        for s in items:
            ort = s.get("arbeitsort") or {}
            refnr = s.get("refnr", "")
            out.append(RawJob(
                source="arbeitsagentur", title=s.get("titel") or s.get("beruf", ""), company=s.get("arbeitgeber", ""),
                location=", ".join(x for x in (ort.get("ort"), ort.get("land")) if x),
                url=s.get("externeUrl") or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
                posted_date=iso_to_date(s.get("aktuelleVeroeffentlichungsdatum")), mode="feed",
                job_type_hint="Praktikum" if angebotsart == 34 else ("Teilzeit" if part_time else ""),
                query=keywords, external_id=refnr))
        if len(items) < 50:
            break
    return out, (int(total) if total is not None else None)


async def arbeitsagentur_details(jobs: list[RawJob], client: httpx.AsyncClient) -> None:
    for j in jobs:
        refnr = j.external_id
        if not refnr or j.description:
            continue
        enc = base64.b64encode(refnr.encode()).decode()
        r = await client.get(f"{BASES['arbeitsagentur']}/pc/v4/jobdetails/{enc}", headers=BA_HEADERS)
        if r.status_code == 200:
            d = r.json()
            j.description = d.get("stellenangebotsBeschreibung") or d.get("stellenbeschreibung") or ""


# -------------------------------------------------------------- public boards ---
async def arbeitnow(days: int, client: httpx.AsyncClient, max_pages: int = 5) -> list[RawJob]:
    out = []
    for page in range(1, max_pages + 1):
        r = await client.get(BASES["arbeitnow"], params={"page": page})
        if r.status_code != 200:
            break
        data = r.json().get("data", [])
        stop = False
        for j in data:
            d = iso_to_date(j.get("created_at"))
            if d and not _within(d, days):
                stop = True
                continue
            if not relevant(j.get("title", "")):
                continue
            out.append(RawJob(source="arbeitnow", title=j["title"], company=j.get("company_name", ""),
                              location=j.get("location", ""), url=j.get("url", ""), posted_date=d,
                              description=html_to_text(j.get("description", "")),
                              job_type_hint=" ".join(j.get("job_types") or []),
                              work_mode_hint="Remote" if j.get("remote") else "", mode="feed"))
        if stop or not data:
            break
    return out


async def remoteok(days: int, client: httpx.AsyncClient) -> list[RawJob]:
    r = await client.get(BASES["remoteok"])
    if r.status_code != 200:
        return []
    out = []
    for j in r.json():
        if not isinstance(j, dict) or "position" not in j:
            continue
        d = iso_to_date(j.get("date"))
        if not relevant(j.get("position", "")) or not _within(d, days):
            continue
        out.append(RawJob(source="remoteok", title=j["position"], company=j.get("company", ""),
                          location=j.get("location") or "Remote", url=j.get("url", ""), posted_date=d,
                          description=html_to_text(j.get("description", "")),
                          job_type_hint=" ".join(j.get("tags") or []), work_mode_hint="Remote", mode="feed"))
    return out
