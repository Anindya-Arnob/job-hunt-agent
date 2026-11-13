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


# ------------------------------------------------------------- search plan ---
# How many searches each site gets per run, how many of them are narrower (city) searches, and a rough
# number above which the site tends to refuse pages or block for a while. You change the first two on the
# Search screen; the third is only used for the warning there.
SITE_PLAN = {
    "linkedin":  {"searches": 80,  "narrow": 15, "safe": 80,
                  "risk": "LinkedIn starts answering 'too many requests' after about 80 searches in one run. Above "
                          "that the run gets much slower (it waits after every refusal), and LinkedIn may block "
                          "searches from this computer for a few hours."},
    "stepstone": {"searches": 120, "narrow": 30, "safe": 150,
                  "risk": "StepStone may show 'access denied' pages after about 150 searches in one run."},
    "xing":      {"searches": 120, "narrow": 30, "safe": 150,
                  "risk": "XING may refuse pages after about 150 searches in one run."},
    "wellfound": {"searches": 60,  "narrow": 0,  "safe": 80,
                  "risk": "Wellfound shows its human check more often after about 80 searches in one run."},
    "glassdoor": {"searches": 20,  "narrow": 0,  "safe": 20,
                  "risk": "Glassdoor is collected in your own Chrome: more than about 20 searches means more "
                          "human checks for you, and Glassdoor may lock the search for a while."},
    "indeed":    {"searches": 20,  "narrow": 0,  "safe": 20,
                  "risk": "Indeed is collected in your own Chrome: more than about 20 searches means more "
                          "human checks for you, and Indeed may block for a while."},
}
MAX_SEARCHES = 400            # per site and run, whatever you type
WEIGHT_LABELS = {1: "Normal", 2: "More", 3: "Most"}
CUSTOM_FIELD = "Your keywords"


def site_plan(plan: dict | None, site: str) -> dict:
    """{'searches', 'narrow', 'safe', 'risk'} for a site: your numbers on top of the defaults."""
    d = dict(SITE_PLAN.get(site, {"searches": 80, "narrow": 0, "safe": 80, "risk": ""}))
    mine = ((plan or {}).get("sites") or {}).get(site) or {}
    try:
        if "searches" in mine:
            d["searches"] = max(0, min(MAX_SEARCHES, int(mine["searches"])))
        if "narrow" in mine:
            d["narrow"] = max(0, int(mine["narrow"]))
    except (TypeError, ValueError):
        pass
    d["narrow"] = min(d["narrow"], d["searches"])
    return d


def weight(plan: dict | None, kind: str, name: str) -> int:
    """kind 'places' or 'fields': 1 normal (equal share), 2 more, 3 most."""
    try:
        w = int(((plan or {}).get(kind) or {}).get(name, 1))
    except (TypeError, ValueError):
        w = 1
    return max(1, min(3, w))


def _interleave(lists: list[list]) -> list:
    """First item of each list, then the second of each, ...: a few searches still cover every term."""
    out = []
    for i in range(max((len(x) for x in lists), default=0)):
        out += [x[i] for x in lists if i < len(x)]
    return out


def site_cells(site: str, roles: list[str], job_types: list[str], locations: list[str], days: int,
               work_modes: list[str] | None = None, keywords_in: list[str] | None = None) -> list[dict]:
    """Every search the site could run, grouped by (place, job field), most useful first inside a group."""
    allowed = SITE_LOCATIONS.get(site)
    locs = [l for l in sorted(locations, key=lambda x: LOCATION_PRIORITY.index(x) if x in LOCATION_PRIORITY else 99)
            if allowed is None or l in allowed]
    terms = _terms(roles) + [(CUSTOM_FIELD, k) for k in (keywords_in or [])]
    cells: list[dict] = []
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
            cells.append({"place": loc, "field": "", "queries": [Query(
                site=site, keywords=or_keywords(all_terms, words), location=site_loc, work_mode=wm, days=days,
                role=terms[0][0] if len({r for r, _ in terms}) == 1 else "", any_terms=all_terms, type_words=words,
                place=loc)]})
            continue
        by_field: dict[str, list[list[Query]]] = {}
        for role, term in terms:
            per_term: list[Query] = []
            if site in JT_FILTER_SITES:
                per_term.append(Query(site=site, keywords=term, location=site_loc, job_type=",".join(job_types),
                                      work_mode=wm, days=days, role=role, place=loc))
                if german and "Working Student" in job_types:
                    # LinkedIn often tags Werkstudent jobs as full-time, so search the word too
                    per_term.append(Query(site=site, keywords=f"Werkstudent {term}", location=site_loc,
                                          work_mode=wm, days=days, role=role, place=loc))
            else:
                for jt in job_types:
                    words = list(JT_EN.get(jt, []))
                    if german:
                        words = JT_DE.get(jt, []) + ([] if site in ("stepstone", "xing") else words)
                    for w in words:
                        per_term.append(Query(site=site, keywords=f"{w} {term}", location=site_loc, work_mode=wm,
                                              days=days, role=role, place=loc))
            by_field.setdefault(role, []).append(per_term)
        for field, lists in by_field.items():
            cells.append({"place": loc, "field": field, "queries": _interleave(lists)})
    # the same search can come from two fields (shared terms): keep it once
    seen: set = set()
    for c in cells:
        c["queries"] = [q for q in c["queries"] if not (q.key in seen or seen.add(q.key))]
    return [c for c in cells if c["queries"]]


def _share(weights: list[int], caps: list[int], budget: int) -> list[int]:
    """Hand out searches one at a time to whoever is furthest behind their share (weight 2 = double share);
    someone who has no searches left gives their share to the others."""
    n = [0] * len(weights)
    for _ in range(max(0, budget)):
        best = None
        for i in range(len(weights)):
            if n[i] < caps[i] and (best is None or (n[i] + 1) / weights[i] < (n[best] + 1) / weights[best]):
                best = i
        if best is None:
            break
        n[best] += 1
    return n


def allocate(cells: list[dict], budget: int, plan: dict | None = None) -> list[int]:
    """How many searches each (place, job field) group gets: first the places share the site's searches
    (equal by default; More = double, Most = triple), then inside each place the job fields share them."""
    places = list(dict.fromkeys(c["place"] for c in cells))
    idx = {p: [i for i, c in enumerate(cells) if c["place"] == p] for p in places}
    per_place = _share([weight(plan, "places", p) for p in places],
                       [sum(len(cells[i]["queries"]) for i in idx[p]) for p in places], budget)
    n = [0] * len(cells)
    total: dict[str, int] = {}                 # searches per job field over all places so far: ties go to the
    for p, k in zip(places, per_place):        # field with the fewest, so small numbers still rotate fairly
        ids = idx[p]
        w = {i: weight(plan, "fields", cells[i]["field"]) if cells[i]["field"] else 1 for i in ids}
        for _ in range(k):
            free = [i for i in ids if n[i] < len(cells[i]["queries"])]
            if not free:
                break
            i = min(free, key=lambda i: ((n[i] + 1) / w[i], (total.get(cells[i]["field"], 0) + 1) / w[i]))
            n[i] += 1
            total[cells[i]["field"]] = total.get(cells[i]["field"], 0) + 1
    return n


def plan_site_queries(site: str, roles: list[str], job_types: list[str], locations: list[str],
                      days: int, work_modes: list[str] | None = None, keywords_in: list[str] | None = None,
                      max_queries: int = 80, plan: dict | None = None) -> list[Query]:
    """The searches for one site: max_queries of them, shared fairly between places and job fields."""
    cells = site_cells(site, roles, job_types, locations, days, work_modes, keywords_in)
    n = allocate(cells, max_queries, plan)
    # round-robin over the groups, so a run that stops early has still searched every place and field
    return _interleave([c["queries"][:k] for c, k in zip(cells, n)])


def plan_for_profile(site: str, fp, plan: dict | None = None) -> list[Query]:
    """The first-round searches for a site from a search profile (its plan decides the numbers)."""
    plan = getattr(fp, "plan", None) if plan is None else plan
    sp = site_plan(plan, site)
    return plan_site_queries(site, fp.roles, fp.job_types, fp.locations, fp.days, fp.work_modes, fp.keywords_in,
                             max_queries=sp["searches"] - sp["narrow"], plan=plan)


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
