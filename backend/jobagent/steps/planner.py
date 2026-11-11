"""Query Planner: turns a filter profile into many small, site-specific searches.

* every role x job-type word x location, in English and (for Germany) German
* site filters go into the URL where the site supports them (LinkedIn job type)
* a search that hits the site's result limit is split by city or narrower term
"""
from __future__ import annotations

from ..schemas import Query

ROLE_TERMS = {
    "AI Engineering": ["AI Engineer"],
    "AI/ML Engineering": ["Machine Learning Engineer", "AI ML"],
    "Data Science": ["Data Science", "Data Scientist"],
    "Data Engineering": ["Data Engineer"],
    "Data Analytics Engineering": ["Analytics Engineer", "Data Analyst"],
    "Gen AI": ["Generative AI", "LLM"],
    "MLOps / LLMOps": ["MLOps", "LLMOps"],
    "Machine Learning": ["Machine Learning"],
}
JT_EN = {"Internship": ["Intern"], "Working Student": ["Working Student"], "Part-time": ["Part-time"]}
JT_DE = {"Internship": ["Praktikum"], "Working Student": ["Werkstudent"], "Part-time": ["Teilzeit"]}

GERMAN_LOCATIONS = {"Berlin", "Germany"}
LOCATION_SPLITS = {
    "Germany": ["Berlin", "Munich", "Hamburg", "Frankfurt", "Cologne", "Stuttgart", "Düsseldorf", "Leipzig",
                "Dresden", "Karlsruhe", "Hannover", "Nuremberg"],
    "Europe": ["Germany", "Netherlands", "France", "Spain", "Austria", "Switzerland", "Ireland", "Poland",
               "Sweden", "Denmark", "Belgium", "Italy", "Portugal", "Czechia", "Finland", "Luxembourg"],
    "United States": ["California", "New York", "Massachusetts", "Washington", "Texas", "Illinois",
                      "Pennsylvania", "Georgia", "Colorado", "North Carolina", "New Jersey", "Virginia"],
    "United Kingdom": ["London", "Manchester", "Cambridge", "Oxford", "Edinburgh", "Bristol", "Birmingham"],
    "Canada": ["Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary", "Waterloo"],
    "Australia": ["Sydney", "Melbourne", "Brisbane", "Perth", "Canberra"],
    "New Zealand": ["Auckland", "Wellington", "Christchurch"],
}
LINKEDIN_LOCATION = {"Europe": "European Union", "Remote": "Worldwide"}

# Which sites cover which locations
SITE_LOCATIONS = {
    "linkedin": None,     # all
    "indeed": None,
    "glassdoor": None,
    "stepstone": GERMAN_LOCATIONS | {"Remote"},
    "xing": GERMAN_LOCATIONS | {"Remote"},
    "wellfound": None,
}
# Sites whose URL accepts a job-type filter (so the type word is not needed in keywords)
JT_FILTER_SITES = {"linkedin"}
# Sites that understand (a OR b) searches: far fewer page loads, so far fewer bot checks
OR_SITES = {"indeed"}


def _q(w: str) -> str:
    return f'"{w}"' if (" " in w or "-" in w) else w


def or_keywords(terms: list[str], words: list[str]) -> str:
    """("Werkstudent" OR "Working Student") ("Data Engineer" OR "MLOps") - Indeed's own syntax."""
    t = terms[0] if len(terms) == 1 else "(" + " OR ".join(_q(x) for x in terms) + ")"
    if not words:
        return t
    w = words[0] if len(words) == 1 else "(" + " OR ".join(_q(x) for x in words) + ")"
    return f"{w} {t}"


def _role_of(term: str) -> str:
    return next((r for r, ts in ROLE_TERMS.items() if term in ts), "")
# Search the most important places first, in case the per-site cap is reached
LOCATION_PRIORITY = ["Berlin", "Remote", "Germany", "Europe", "United Kingdom", "United States",
                     "Canada", "Australia", "New Zealand"]


def _terms(roles: list[str]) -> list[tuple[str, str]]:
    out = []
    for r in roles:
        for t in ROLE_TERMS.get(r, [r]):
            out.append((r, t))
    return out


def plan_site_queries(site: str, roles: list[str], job_types: list[str], locations: list[str],
                      days: int, work_modes: list[str] | None = None, keywords_in: list[str] | None = None,
                      max_queries: int = 80) -> list[Query]:
    allowed = SITE_LOCATIONS.get(site)
    locs = [l for l in sorted(locations, key=lambda x: LOCATION_PRIORITY.index(x) if x in LOCATION_PRIORITY else 99)
            if allowed is None or l in allowed]
    terms = _terms(roles) + [("Custom", k) for k in (keywords_in or [])]
    queries: list[Query] = []
    seen = set()

    def add(q: Query):
        if q.key not in seen:
            seen.add(q.key)
            queries.append(q)

    for loc in locs:
        wm = "Remote" if loc == "Remote" else ""
        site_loc = LINKEDIN_LOCATION.get(loc, loc) if site == "linkedin" else loc
        german = loc in GERMAN_LOCATIONS or (loc == "Remote" and site in ("stepstone", "xing"))
        if site in OR_SITES and terms:
            words = []
            for jt in job_types:
                ws = list(JT_EN.get(jt, []))
                if german:
                    ws = JT_DE.get(jt, []) + ws
                words += [w for w in ws if w not in words]
            all_terms = [t for _, t in terms]
            add(Query(site=site, keywords=or_keywords(all_terms, words), location=site_loc, work_mode=wm,
                      days=days, role=terms[0][0] if len({r for r, _ in terms}) == 1 else "",
                      any_terms=all_terms, type_words=words))
            continue
        for role, term in terms:
            if site in JT_FILTER_SITES:
                add(Query(site=site, keywords=term, location=site_loc, job_type=",".join(job_types),
                          work_mode=wm, days=days, role=role))
                if german and "Working Student" in job_types:
                    # LinkedIn often tags Werkstudent jobs as full-time, so search the word too
                    add(Query(site=site, keywords=f"Werkstudent {term}", location=site_loc, work_mode=wm,
                              days=days, role=role))
                continue
            for jt in job_types:
                words = list(JT_EN.get(jt, []))
                if german:
                    words = JT_DE.get(jt, []) + ([] if site in ("stepstone", "xing") else words)
                for w in words:
                    add(Query(site=site, keywords=f"{w} {term}", location=site_loc, work_mode=wm,
                              days=days, role=role))
    return queries[:max_queries]


def split_query(q: Query) -> list[Query]:
    """A search that hit the site's limit is split by place, then by narrower wording."""
    if len(q.any_terms) > 1:          # OR search: one search per role term first
        return [q.model_copy(update={"keywords": or_keywords([t], q.type_words), "any_terms": [t],
                                     "role": _role_of(t) or q.role, "split_level": q.split_level + 1})
                for t in q.any_terms]
    if len(q.type_words) > 1:         # then one per job-type word
        return [q.model_copy(update={"keywords": or_keywords(q.any_terms, [w]), "type_words": [w],
                                     "split_level": q.split_level + 1})
                for w in q.type_words]
    base_loc = next((k for k, v in LINKEDIN_LOCATION.items() if v == q.location), q.location)
    if base_loc in LOCATION_SPLITS and q.split_level == 0:
        loc_map = (lambda c: c) if q.site != "linkedin" else (lambda c: LINKEDIN_LOCATION.get(c, c))
        return [q.model_copy(update={"location": loc_map(c), "split_level": q.split_level + 1})
                for c in LOCATION_SPLITS[base_loc]]
    if "," in q.job_type:
        return [q.model_copy(update={"job_type": jt, "split_level": q.split_level + 1})
                for jt in q.job_type.split(",")]
    if q.split_level < 3 and q.work_mode == "":
        return [q.model_copy(update={"work_mode": wm, "split_level": q.split_level + 1})
                for wm in ("Remote", "Hybrid", "On-site")]
    return []


def plan_feed_queries(roles: list[str], job_types: list[str], locations: list[str]) -> list[dict]:
    """Bundesagentur searches (Germany only)."""
    if not ({"Berlin", "Germany"} & set(locations)):
        return []
    loc = "Berlin" if "Germany" not in locations else ""
    out = []
    for _, term in _terms(roles):
        if "Working Student" in job_types:
            out.append({"keywords": f"Werkstudent {term}", "location": loc})
        if "Internship" in job_types:
            out.append({"keywords": term, "location": loc, "angebotsart": 34})
        if "Part-time" in job_types:
            out.append({"keywords": term, "location": loc, "part_time": True})
    return out
