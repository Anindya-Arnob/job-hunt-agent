"""The search plan as the Search screen shows it: for each job site, how many searches go to each place and job
field, how many are kept for narrower (city) searches, about how long that takes, and warnings."""
from __future__ import annotations

from sqlmodel import select

from . import planner as P
from .. import db, models as m
from ..config import settings

BROWSER_SITES = ["linkedin", "stepstone", "xing", "wellfound", "glassdoor", "indeed"]
IN_CHROME = {"glassdoor", "indeed"}           # collected in your own Chrome when the site blocks the app's browser


def _pages_per_search() -> dict[str, float]:
    """Result pages an average search reads on each site (from your earlier runs)."""
    out: dict[str, float] = {}
    try:
        with db.session_scope() as s:
            rows = s.exec(select(m.SearchStat).order_by(m.SearchStat.id.desc()).limit(3000)).all()
        by: dict[str, list[int]] = {}
        for r in rows:
            if r.mode != "feed" and r.pages:
                by.setdefault(r.site, []).append(r.pages)
        out = {k: sum(v) / len(v) for k, v in by.items() if len(v) >= 10}
    except Exception:
        pass
    return out


def rough_minutes(site: str, searches: int, pages: dict | None = None) -> int:
    """Searching plus reading the new job pages, at the page speed set in Settings."""
    page_s = settings.page_delay_seconds * 1.15 + 2.5
    per = (pages or {}).get(site, 3.0)
    detail = min(max(0, settings.max_details_per_site) or 10 ** 6, searches * 4)
    secs = (searches * per + detail) * page_s
    if site == "linkedin" and searches > P.SITE_PLAN["linkedin"]["safe"]:
        secs *= 1.5                                 # waiting after "too many requests"
    return max(1, round(secs / 60))


def preview(form: dict) -> dict:
    plan = form.get("plan") or {}
    roles, places = form.get("roles") or [], form.get("locations") or []
    fields = list(dict.fromkeys(roles))
    pages = _pages_per_search()
    sites = []
    for site in [x for x in BROWSER_SITES if x in (form.get("sources") or [])]:
        sp = P.site_plan(plan, site)
        cells = P.site_cells(site, roles, form.get("job_types") or [], places, form.get("days") or 5,
                             form.get("work_modes") or [], form.get("keywords_in") or [])
        base = sp["searches"] - sp["narrow"]
        n = P.allocate(cells, base, plan)
        possible = sum(len(c["queries"]) for c in cells)
        used = sum(n)
        by_place = {p: 0 for p in places if P.covers(site, p)}
        by_field = {f: 0 for f in fields}
        for c, k in zip(cells, n):
            by_place[c["place"]] = by_place.get(c["place"], 0) + k
            if c["field"]:
                by_field[c["field"]] = by_field.get(c["field"], 0) + k
        warnings = []
        if sp["searches"] > sp["safe"]:
            warnings.append({"level": "risk", "text": sp["risk"]})
        missed_places = [p for p, k in by_place.items() if k == 0]
        if missed_places and possible:
            warnings.append({"level": "gap", "text": f"{', '.join(missed_places)} {'gets' if len(missed_places) == 1 else 'get'} "
                             f"no searches on {_label(site)}: raise its searches, or give other places less weight."})
        if site in P.OR_SITES:                       # one search covers every job field at once
            by_field = {f: used for f in fields}
        missed_fields = [f for f, k in by_field.items() if k == 0]
        if missed_fields and possible:
            warnings.append({"level": "gap", "text": f"{', '.join(missed_fields)} {'gets' if len(missed_fields) == 1 else 'get'} "
                             f"no searches on {_label(site)}."})
        cap_place: dict[str, int] = {}
        for c in cells:
            cap_place[c["place"]] = cap_place.get(c["place"], 0) + len(c["queries"])
        full = [p for p in places if P.weight(plan, "places", p) > 1 and p in by_place and by_place[p] >= cap_place.get(p, 0) > 0]
        if full:
            warnings.append({"level": "info", "text": f"{', '.join(full)} already {'gets' if len(full) == 1 else 'get'} every "
                             f"different search there is on {_label(site)} ({', '.join(str(cap_place[p]) for p in full)}), so More / "
                             "Most adds nothing here. Add more job titles to search it more."})
        if base > possible:
            warnings.append({"level": "info", "text": f"Your choices make only {possible} different searches on "
                             f"{_label(site)}, so {base - possible} of the {base} are not needed."})
        not_here = [p for p in places if not P.covers(site, p)]
        sites.append({
            "site": site, "label": _label(site), "searches": sp["searches"], "narrow": sp["narrow"], "base": base,
            "used": used, "possible": possible, "safe": sp["safe"], "risky": sp["searches"] > sp["safe"],
            "minutes": rough_minutes(site, used + sp["narrow"], pages), "places": by_place, "fields": by_field,
            "not_here": not_here, "in_chrome": site in IN_CHROME, "warnings": warnings,
            "defaults": {"searches": P.SITE_PLAN.get(site, {}).get("searches", 80),
                         "narrow": P.SITE_PLAN.get(site, {}).get("narrow", 0)},
        })
    return {"sites": sites, "places": places, "fields": fields, "max_searches": P.MAX_SEARCHES,
            "weights": {"places": {p: P.weight(plan, "places", p) for p in places},
                        "fields": {f: P.weight(plan, "fields", f) for f in fields}},
            "weight_labels": P.WEIGHT_LABELS}


def _label(site: str) -> str:
    from ..agents.sites import SITES
    return SITES[site].label if site in SITES else site
