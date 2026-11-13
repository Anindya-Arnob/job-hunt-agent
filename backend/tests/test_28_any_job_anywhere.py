"""Any job title, any place: someone in Canada looking for full-time IT jobs in Canada and the US, in the cities
they named, gets searches, filters, sorting and scoring for exactly that (nothing about Berlin or data roles)."""
from collections import Counter

from fastapi.testclient import TestClient

from jobagent import models as m, places, roles
from jobagent.agents.sites import SITES
from jobagent.schemas import RawJob
from jobagent.steps import planner as P, search_plan as SP
from jobagent.steps.classify import apply_classification, heuristic_classification
from jobagent.steps.clean import location_matches, prefilter_reason

TITLES = ["Software Engineer", "DevOps Engineer", "IT Support Specialist"]
PLACES = ["Canada", "United States", "Toronto", "Vancouver", "Seattle"]
FP = dict(name="IT in North America", roles=TITLES, job_types=["Full-time"], locations=PLACES, days=7,
          sources=["linkedin", "indeed", "stepstone", "xing", "wellfound"], work_modes=["Remote", "Hybrid", "On-site"])


def test_searches_are_for_your_titles_and_places():
    qs = P.plan_site_queries("linkedin", TITLES, ["Full-time"], PLACES, 7, max_queries=80)
    assert {q.place for q in qs} == set(PLACES)
    assert set(Counter(q.place for q in qs).values()) == {6}            # 3 titles x 2 names, every place equal
    assert {q.role for q in qs} == set(TITLES)
    locs = {q.location for q in qs}
    assert "Toronto, Canada" in locs and "Seattle, United States" in locs and not any("Berlin" in l for l in locs)
    assert all(not q.keywords.startswith(("Werkstudent", "Intern", "Part-time")) for q in qs)   # full-time: the title alone
    url = SITES["linkedin"].search_url(SITES["linkedin"], qs[0], 0, "guest")
    assert "f_JT=F" in url
    # Indeed: the country's own site
    iq = P.plan_site_queries("indeed", TITLES, ["Full-time"], ["Toronto", "Seattle"], 7)
    urls = [SITES["indeed"].search_url(SITES["indeed"], q, 0, "guest") for q in iq]
    assert urls[0].startswith("https://ca.indeed.com/") and urls[1].startswith("https://www.indeed.com/")
    # German job boards have nothing to search here
    assert P.plan_site_queries("stepstone", TITLES, ["Full-time"], PLACES, 7) == []


def test_narrower_searches_use_that_country_s_cities():
    q = P.plan_site_queries("linkedin", ["Software Engineer"], ["Full-time"], ["Canada"], 7)[0]
    kids = [k.location for k in P.split_query(q)]
    assert kids[:3] == ["Toronto, Canada", "Vancouver, Canada", "Montreal, Canada"]


def test_plan_preview_explains_the_german_only_sites():
    pv = SP.preview(FP)
    st = next(s for s in pv["sites"] if s["site"] == "stepstone")
    assert st["places"] == {} and set(st["not_here"]) == set(PLACES)
    li = next(s for s in pv["sites"] if s["site"] == "linkedin")
    assert set(li["places"]) == set(PLACES) and li["fields"] == {t: 10 for t in TITLES}


def test_location_check_for_any_country():
    assert location_matches("Mississauga, Ontario, Canada", PLACES) is True
    assert location_matches("Toronto, ON", ["Canada"]) is True
    assert location_matches("Austin, TX", ["United States"]) is True
    assert location_matches("Berlin, Germany", PLACES) is False
    assert location_matches("Bengaluru, Karnataka", ["India"]) is True
    assert location_matches("Remote", ["Remote"]) is True and location_matches("", PLACES) is None


def test_full_time_and_senior_are_kept_when_you_want_full_time():
    fp = m.FilterProfile(**FP)
    job = RawJob(source="linkedin", title="Senior Software Engineer", location="Toronto, ON", url="https://x/1")
    assert prefilter_reason(job, fp) is None
    student = m.FilterProfile(**{**FP, "job_types": ["Internship"]})
    assert "Seniority" in prefilter_reason(job, student)
    # unrelated titles are skipped before their page is read
    assert prefilter_reason(RawJob(source="linkedin", title="Registered Nurse", location="Toronto"), fp) == \
        "Not one of your job titles (title)"
    c = heuristic_classification("Senior Backend Software Engineer", "Full-time, permanent role. Java, AWS, Kubernetes.",
                                 roles=TITLES)
    assert c.job_type == "Full-time" and c.role_category == "Software Engineer"
    j = apply_classification(m.Job(title="x", company="y"), c, fp)
    assert j.status != "excluded"
    j2 = apply_classification(m.Job(title="x", company="y"), c, student)
    assert j2.status == "excluded" and j2.exclude_reason == "Full-time"


def test_scoring_knows_your_places():
    from jobagent.schemas import CVProfile, SkillGroup
    from jobagent.steps.score import heuristic_score
    fp = m.FilterProfile(**FP)
    prof = CVProfile(name="A", skills=[SkillGroup(category="Tech", items=["Java", "AWS"])])
    near = heuristic_score(m.Job(title="Software Engineer", company="c", location="Toronto, ON",
                                 full_description="Java and AWS. Full-time."), prof, fp)
    far = heuristic_score(m.Job(title="Software Engineer", company="c", location="Berlin, Germany",
                                full_description="Java and AWS. Full-time."), prof, fp)
    assert near.type_location == 5 and not near.deal_breakers
    assert far.type_location < near.type_location and any("Germany" in d.text and d.hard for d in far.deal_breakers)
    assert near.title_fit == 9


def test_suggestions_while_typing():
    from jobagent.api.main import app
    with TestClient(app) as c:
        r = c.get("/api/suggest/places", params={"q": "toro"}).json()
        assert r[0]["value"] == "Toronto" and r[0]["label"] == "Toronto, Canada"
        assert c.get("/api/suggest/places", params={"q": "can"}).json()[0]["label"] == "Canada (country)"
        assert any(x["value"] == "Registered Nurse" for x in c.get("/api/suggest/roles", params={"q": "nur"}).json())
        assert c.get("/api/suggest/roles", params={"q": "devo"}).json()[0]["value"] == "DevOps Engineer"
        assert c.get("/api/suggest/skills", params={"q": "pyt"}).json()[0]["value"].lower().startswith("pyt")
        assert c.get("/api/suggest/nope").status_code == 404


def test_titles_and_places_you_type_yourself_still_work():
    assert roles.terms_for("Underwater Welder") == ["Underwater Welder"]
    assert places.find("Springfield").kind == "other"
    qs = P.plan_site_queries("linkedin", ["Underwater Welder"], ["Full-time"], ["Springfield"], 7)
    assert [(q.keywords, q.location) for q in qs] == [("Underwater Welder", "Springfield")]
    assert location_matches("Springfield, IL", ["Springfield"]) is True
