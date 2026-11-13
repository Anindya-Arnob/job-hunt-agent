"""How each job site is searched and read.

Each site has: a search-URL builder that puts your filters into the URL, a
selector-based parser for result cards and job pages (fast and free), and the
Claude page reader as fallback when the site's layout changes.

`base` can be overridden (tests point it at a local server).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote, quote_plus, urlencode, urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

from ..browser.reader import Snapshot
from ..schemas import Query, RawJob
from .extract import main_text, parse_posted

# ------------------------------------------------------------------ helpers ---


def _txt(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el is not None else ""


def slug(s: str) -> str:
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _count(text: str) -> Optional[int]:
    m = re.search(r"([\d.,]+)\+?\s*(results|jobs|stellen|ergebnisse|treffer|job)", text, re.I)
    if not m:
        return None
    try:
        return int(re.sub(r"[.,]", "", m.group(1)))
    except ValueError:
        return None


@dataclass
class SiteSpec:
    name: str
    label: str
    base: str
    supports_login: bool
    login_url: str = ""
    check_url: str = ""           # page that shows a login wall when logged out
    logged_in_markers: tuple = ()
    page_size: int = 25
    result_limit: int = 1000
    max_pages: Optional[int] = None       # overrides the setting for this site
    use_chrome: bool = False              # open login / window profiles in your installed Chrome
    guest_page_size: Optional[int] = None # results per page without login, when it differs

    def size(self, mode: str) -> int:
        return self.guest_page_size if (mode == "guest" and self.guest_page_size) else self.page_size
    search_url: Callable[["SiteSpec", Query, int, str], str] = None  # (spec, query, page, mode)
    parse_results: Callable[["SiteSpec", Snapshot, Query], tuple[list[RawJob], Optional[int]]] = None
    detail_url: Callable[["SiteSpec", RawJob, str], str] = None
    detail_selectors: tuple = ()
    parse_detail_extra: Callable[[Snapshot], dict] = None
    job_type_param: dict = field(default_factory=dict)


# ----------------------------------------------------------------- LinkedIn ---
LI_JT = {"Internship": "I", "Part-time": "P", "Working Student": "P", "Full-time": "F"}
LI_WT = {"On-site": "1", "Remote": "2", "Hybrid": "3"}


def li_search(spec: SiteSpec, q: Query, page: int, mode: str) -> str:
    params = {"keywords": q.keywords, "location": q.location, "f_TPR": f"r{q.days * 86400}"}
    jt = ",".join(sorted({LI_JT[t] for t in q.job_type.split(",") if t in LI_JT}))
    if jt:
        params["f_JT"] = jt
    if q.work_mode in LI_WT:
        params["f_WT"] = LI_WT[q.work_mode]
    params["start"] = str(page * spec.size(mode))
    path = "/jobs-guest/jobs/api/seeMoreJobPostings/search" if mode == "guest" else "/jobs/search/"
    return f"{spec.base}{path}?{urlencode(params)}"


def li_parse(spec: SiteSpec, snap: Snapshot, q: Query):
    soup = BeautifulSoup(snap.html, "lxml")
    jobs = []
    for card in soup.select("div.base-card, div.job-search-card, li.jobs-search-results__list-item, div.job-card-container"):
        a = card.select_one("a.base-card__full-link, a.job-card-list__title, a.job-card-container__link, a[href*='/jobs/view/']")
        title = _txt(card.select_one(".base-search-card__title, .job-card-list__title, .job-card-container__link"))
        if not a or not title:
            continue
        href = urljoin(snap.final_url, a.get("href", "")).split("?")[0]
        t = card.select_one("time")
        posted = None
        if t is not None and t.get("datetime"):
            posted = parse_posted(t["datetime"])
        posted = posted or parse_posted(_txt(t))
        jobs.append(RawJob(
            source="linkedin", title=title,
            company=_txt(card.select_one(".base-search-card__subtitle, .job-card-container__primary-description, .artdeco-entity-lockup__subtitle")),
            location=_txt(card.select_one(".job-search-card__location, .job-card-container__metadata-item, .artdeco-entity-lockup__caption")),
            url=href, posted_date=posted, posted_text=_txt(t), query=q.keywords))
    total = _count(_txt(soup.select_one(".results-context-header__job-count, .jobs-search-results-list__subtitle")) + " results") \
        if soup.select_one(".results-context-header__job-count, .jobs-search-results-list__subtitle") else None
    return jobs, total


def li_detail(spec: SiteSpec, job: RawJob, mode: str) -> str:
    m = re.search(r"(\d{6,})", job.url)
    if mode == "guest" and m:
        return f"{spec.base}/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
    return job.url


def li_detail_extra(snap: Snapshot) -> dict:
    soup = BeautifulSoup(snap.html, "lxml")
    extra = {}
    for item in soup.select(".description__job-criteria-item"):
        k = _txt(item.select_one(".description__job-criteria-subheader")).lower()
        v = _txt(item.select_one(".description__job-criteria-text"))
        if "employment" in k:
            extra["job_type_hint"] = v
    return extra


# ------------------------------------------------------------------- Indeed ---
def indeed_base(spec: SiteSpec, location: str) -> str:
    """Indeed's site for the country of the place (ca.indeed.com for Toronto, ...)."""
    if spec.base != "https://www.indeed.com":   # overridden (tests)
        return spec.base
    from .. import places
    return f"https://{places.indeed_host(location)}"


def indeed_search(spec: SiteSpec, q: Query, page: int, mode: str) -> str:
    loc = "" if q.location == "Remote" else q.location
    params = {"q": q.keywords, "l": loc, "fromage": str(min(q.days, 14)), "sort": "date",
              "start": str(page * spec.page_size)}
    if q.location == "Remote" or q.work_mode == "Remote":
        params["sc"] = "0kf:attr(DSQF7);"
    return f"{indeed_base(spec, q.location)}/jobs?{urlencode(params)}"


def indeed_parse(spec: SiteSpec, snap: Snapshot, q: Query):
    soup = BeautifulSoup(snap.html, "lxml")
    jobs = []
    for card in soup.select("div.job_seen_beacon, td.resultContent, div.cardOutline"):
        a = card.select_one("a.jcs-JobTitle, h2.jobTitle a")
        if not a:
            continue
        jk = a.get("data-jk") or (parse_qs(urlparse(a.get("href", "")).query).get("jk") or [""])[0]
        base = f"{urlparse(snap.final_url).scheme}://{urlparse(snap.final_url).netloc}"
        url = f"{base}/viewjob?jk={jk}" if jk else urljoin(snap.final_url, a.get("href", ""))
        date_el = card.select_one("span.date, [data-testid='myJobsStateDate']")
        jobs.append(RawJob(
            source="indeed", title=_txt(a), url=url,
            company=_txt(card.select_one("[data-testid='company-name'], span.companyName")),
            location=_txt(card.select_one("[data-testid='text-location'], div.companyLocation")),
            posted_text=_txt(date_el), posted_date=parse_posted(_txt(date_el)),
            job_type_hint=_txt(card.select_one("[data-testid='attribute_snippet_testid'], .metadata")),
            query=q.keywords))
    total = _count(_txt(soup.select_one(".jobsearch-JobCountAndSortPane-jobCount")))
    return jobs, total


# ---------------------------------------------------------------- Glassdoor ---
def gd_base(spec: SiteSpec, location: str) -> str:
    if spec.base != "https://www.glassdoor.com":
        return spec.base
    from .. import places
    return f"https://{places.glassdoor_host(location)}"


def gd_search(spec: SiteSpec, q: Query, page: int, mode: str) -> str:
    params = {"sc.keyword": q.keywords, "locKeyword": "" if q.location == "Remote" else q.location,
              "fromAge": str(q.days), "sortBy": "date_desc"}
    if q.location == "Remote":
        params["remoteWorkType"] = "1"
    if page:
        params["p"] = str(page + 1)
    return f"{gd_base(spec, q.location)}/Job/jobs.htm?{urlencode(params)}"


def gd_parse(spec: SiteSpec, snap: Snapshot, q: Query):
    soup = BeautifulSoup(snap.html, "lxml")
    jobs = []
    for card in soup.select("li[data-test='jobListing'], li.react-job-listing"):
        a = card.select_one("a[data-test='job-title'], a.jobLink, a[href*='job-listing']")
        if not a:
            continue
        age = card.select_one("[data-test='job-age'], .listing-age")
        jobs.append(RawJob(
            source="glassdoor", title=_txt(a), url=urljoin(snap.final_url, a.get("href", "")),
            company=_txt(card.select_one("[class*='EmployerProfile_compactEmployerName'], .employer-name, [data-test='employer-name']")),
            location=_txt(card.select_one("[data-test='emp-location'], .location")),
            posted_text=_txt(age), posted_date=parse_posted(_txt(age)), query=q.keywords))
    total = _count(_txt(soup.select_one("[data-test='search-title'], h1")))
    return jobs, total


# ---------------------------------------------------------------- StepStone ---
def ss_search(spec: SiteSpec, q: Query, page: int, mode: str) -> str:
    loc = "" if q.location in ("Germany", "Remote", "Europe") else f"/in-{slug(q.location)}"
    params = {"ag": "age_1" if q.days <= 1 else "age_7"}
    if q.location == "Remote" or q.work_mode == "Remote":
        params["wfh"] = "1"
    if page:
        params["page"] = str(page + 1)
    return f"{spec.base}/jobs/{slug(q.keywords)}{loc}?{urlencode(params)}"


def ss_parse(spec: SiteSpec, snap: Snapshot, q: Query):
    soup = BeautifulSoup(snap.html, "lxml")
    jobs = []
    for card in soup.select("article[data-testid='job-item'], article[data-at='job-item']"):
        a = card.select_one("a[data-testid='job-item-title'], a[data-at='job-item-title']")
        if not a:
            continue
        t = card.select_one("time")
        posted = parse_posted(t.get("datetime", "")) if t is not None else None
        jobs.append(RawJob(
            source="stepstone", title=_txt(a), url=urljoin(snap.final_url, a.get("href", "")),
            company=_txt(card.select_one("[data-at='job-item-company-name']")),
            location=_txt(card.select_one("[data-at='job-item-location']")),
            posted_text=_txt(t), posted_date=posted or parse_posted(_txt(t)), query=q.keywords))
    total = _count(_txt(soup.select_one("[data-at='search-results-count'], h1")))
    return jobs, total


# ---------------------------------------------------------- XING / Wellfound ---
def xing_search(spec: SiteSpec, q: Query, page: int, mode: str) -> str:
    params = {"keywords": q.keywords, "location": "" if q.location == "Remote" else q.location,
              "sort": "date"}
    if q.location == "Remote":
        params["remoteOption"] = "FULLY_REMOTE"
    if page:
        params["page"] = str(page + 1)
    return f"{spec.base}/jobs/search?{urlencode(params)}"


def wf_search(spec: SiteSpec, q: Query, page: int, mode: str) -> str:
    loc = "remote" if q.location == "Remote" else slug(q.location)
    url = f"{spec.base}/role/l/{slug(q.keywords)}/{loc}"
    return url + (f"?page={page + 1}" if page else "")


def no_parse(spec: SiteSpec, snap: Snapshot, q: Query):
    return [], None


def same_url(spec: SiteSpec, job: RawJob, mode: str) -> str:
    return job.url


SITES: dict[str, SiteSpec] = {
    "linkedin": SiteSpec(
        name="linkedin", label="LinkedIn", base="https://www.linkedin.com", supports_login=True,
        login_url="https://www.linkedin.com/login", check_url="https://www.linkedin.com/feed/",
        logged_in_markers=("global-nav", "feed-identity"), page_size=25, guest_page_size=10, max_pages=10,
        result_limit=1000,
        search_url=li_search, parse_results=li_parse, detail_url=li_detail,
        detail_selectors=(".show-more-less-html__markup", ".description__text", "#job-details",
                          ".jobs-description__content"),
        parse_detail_extra=li_detail_extra),
    "indeed": SiteSpec(
        name="indeed", label="Indeed", base="https://www.indeed.com", supports_login=True,
        login_url="https://secure.indeed.com/auth?hl=de_DE&co=DE", check_url="https://myjobs.indeed.com/saved",
        logged_in_markers=("myjobs", "gespeichert", "saved"), use_chrome=True,
        page_size=10, result_limit=1000, max_pages=5, search_url=indeed_search, parse_results=indeed_parse,
        detail_url=same_url, detail_selectors=("#jobDescriptionText",)),
    "glassdoor": SiteSpec(
        name="glassdoor", label="Glassdoor", base="https://www.glassdoor.com", supports_login=True,
        login_url="https://www.glassdoor.com/profile/login_input.htm",
        check_url="https://www.glassdoor.com/member/profile/index.htm",
        page_size=30, result_limit=900, search_url=gd_search, parse_results=gd_parse,
        detail_url=same_url, detail_selectors=("div[class*='JobDetails_jobDescription']", ".jobDescriptionContent")),
    "stepstone": SiteSpec(
        name="stepstone", label="StepStone", base="https://www.stepstone.de", supports_login=False,
        page_size=25, result_limit=1000, search_url=ss_search, parse_results=ss_parse,
        detail_url=same_url, detail_selectors=("[data-at='section-text-description-content']",
                                                "[data-at='job-ad-content']", "article")),
    "xing": SiteSpec(
        name="xing", label="XING", base="https://www.xing.com", supports_login=True,
        login_url="https://login.xing.com/", check_url="https://www.xing.com/jobs/my-jobs",
        page_size=20, result_limit=1000, search_url=xing_search, parse_results=no_parse,
        detail_url=same_url, detail_selectors=("[data-testid='job-details-description']",)),
    "wellfound": SiteSpec(
        name="wellfound", label="Wellfound", base="https://wellfound.com", supports_login=True,
        login_url="https://wellfound.com/login", check_url="https://wellfound.com/jobs/home",
        page_size=20, result_limit=500, search_url=wf_search, parse_results=no_parse,
        detail_url=same_url),
}

DEFAULT_BASES = {k: v.base for k, v in SITES.items()}
