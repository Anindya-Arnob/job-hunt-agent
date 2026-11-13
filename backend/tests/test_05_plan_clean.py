from datetime import date, timedelta

from sqlmodel import select

from jobagent import db, models as m
from jobagent.schemas import Query, RawJob
from jobagent.steps import planner as P
from jobagent.steps.clean import ingest, location_matches, prefilter_reason, known_url_keys

ROLES = ["Data Science", "Machine Learning"]
JT = ["Internship", "Working Student", "Part-time"]


def fp(**kw):
    base = dict(name="t", roles=ROLES, job_types=JT, locations=["Berlin", "Germany", "Remote", "United States"],
                days=5, sources=["linkedin"], exclude_companies=["BadCo"], keywords_out=["Sales"])
    base.update(kw)
    return m.FilterProfile(**base)


def test_linkedin_queries_use_url_filter_and_add_werkstudent():
    qs = P.plan_site_queries("linkedin", ROLES, JT, ["Berlin", "United States"], 5)
    kws = {(q.keywords, q.location) for q in qs}
    assert ("Data Science", "Berlin, Germany") in kws and ("Werkstudent Data Science", "Berlin, Germany") in kws
    assert ("Werkstudent Data Science", "United States") not in kws     # German word only for Germany
    assert all(q.job_type == "Internship,Working Student,Part-time" for q in qs if not q.keywords.startswith("Werkstudent"))
    assert qs[0].location == "Berlin, Germany"                          # your first place first (LinkedIn: city, country)


def test_other_sites_use_bilingual_type_words():
    # Indeed: one OR search per place, which splits down to the single searches only when needed
    qs = P.plan_site_queries("indeed", ["Data Science"], JT, ["Berlin", "United Kingdom"], 5)
    assert [q.location for q in qs] == ["Berlin", "United Kingdom"]
    berlin, uk = qs
    assert set(berlin.type_words) == {"Werkstudent", "Praktikum", "Teilzeit", "Working Student", "Intern", "Part-time"}
    assert "Praktikum" not in uk.type_words and "Intern" in uk.type_words
    assert berlin.keywords.startswith("(Praktikum OR Intern OR Werkstudent") and '("Data Science" OR "Data Scientist")' in berlin.keywords
    singles = {x.keywords for t in P.split_query(berlin) for x in P.split_query(t)}
    for k in ["Werkstudent Data Science", "Praktikum Data Science", "Teilzeit Data Science",
              "Working Student Data Science", "Intern Data Science"]:
        assert k in singles, (k, singles)
    ss = P.plan_site_queries("stepstone", ["Data Science"], JT, ["Berlin", "United Kingdom"], 5)
    assert {q.location for q in ss} == {"Berlin"} and all(q.keywords.split()[0] in ("Werkstudent", "Praktikum", "Teilzeit") for q in ss)


def test_remote_and_europe_on_linkedin():
    qs = P.plan_site_queries("linkedin", ["Gen AI"], JT, ["Remote", "Europe"], 5)
    assert {(q.location, q.work_mode) for q in qs} == {("Worldwide", "Remote"), ("European Union", "")}


def test_max_queries_cap():
    qs = P.plan_site_queries("glassdoor", list(P.ROLE_TERMS), JT, ["Berlin", "Germany", "Remote"], 5, max_queries=30)
    assert len(qs) == 30 and len({q.key for q in qs}) == 30


def test_split_query_levels():
    q = Query(site="linkedin", keywords="Data Science", location="Germany", job_type="Internship,Part-time")
    a = P.split_query(q)
    from jobagent import places
    assert len(a) == len(places.narrower("Germany")) and a[0].location == "Berlin, Germany" and a[0].split_level == 1
    b = P.split_query(a[0])
    assert {x.job_type for x in b} == {"Internship", "Part-time"}
    c = P.split_query(b[0])
    assert {x.work_mode for x in c} == {"Remote", "Hybrid", "On-site"}
    assert P.split_query(c[0]) == []
    eu = P.split_query(Query(site="linkedin", keywords="x", location="European Union"))
    assert "Germany" in {x.location for x in eu}


def test_feed_queries_germany_only():
    assert P.plan_feed_queries(["Data Science"], JT, ["United States"]) == []
    f = P.plan_feed_queries(["Data Science"], JT, ["Berlin"])
    assert {"keywords": "Werkstudent Data Science", "location": "Berlin"} in f
    assert any(x.get("angebotsart") == 34 for x in f) and any(x.get("part_time") for x in f)


def test_location_matching():
    assert location_matches("Berlin, Germany", ["Berlin"])
    assert location_matches("München, Bayern, Deutschland", ["Germany"])
    assert location_matches("Folsom, CA", ["United States"])
    assert location_matches("Amsterdam, Netherlands", ["Europe"])
    assert location_matches("Remote (EU)", ["Remote"])
    assert location_matches("Paris, France", ["Germany", "Remote"]) is False
    assert location_matches("", ["Berlin"]) is None
    assert location_matches("Toronto, ON", ["Canada"]) and location_matches("Auckland", ["New Zealand"])


def test_prefilter_reasons():
    f = fp()
    t = date.today()
    mk = lambda **kw: RawJob(**{"source": "linkedin", "title": "Data Intern", "company": "A", "location": "Berlin", **kw})
    assert "Seniority" in prefilter_reason(mk(title="Senior Data Scientist"), f)
    assert prefilter_reason(mk(title="Werkstudent Senior Management Support Data"), f) is None
    assert "more than 5 days" in prefilter_reason(mk(posted_date=t - timedelta(days=9)), f)
    assert "Excluded company" in prefilter_reason(mk(company="BadCo GmbH"), f)
    assert "Excluded keyword" in prefilter_reason(mk(title="Sales Data Intern"), f)
    assert "Location" in prefilter_reason(mk(location="Lyon, France"), f)
    assert prefilter_reason(mk(posted_date=t - timedelta(days=2)), f) is None


def test_ingest_dedupes_against_today_and_earlier_runs():
    f = fp()
    t = date.today()
    batch1 = [
        RawJob(source="linkedin", title="Werkstudent Data Science (m/w/d)", company="Acme GmbH", location="Berlin, Germany",
               url="https://de.linkedin.com/jobs/view/x-4100000001?trk=1", description="desc A", posted_date=t),
        RawJob(source="indeed", title="Werkstudent Data Science", company="ACME", location="Berlin",
               url="https://de.indeed.com/viewjob?jk=aa1", description="longer description A from indeed", posted_date=t),
        RawJob(source="linkedin", title="Senior ML Engineer", company="B", location="Berlin", url="https://linkedin.com/jobs/view/4100000009",
               prefilter_reason="Seniority in title (full-time role)"),
        RawJob(source="stepstone", title="Praktikum ML", company="C", location="Berlin", url="https://stepstone.de/j/1"),
    ]
    c1 = ingest(batch1, 1, f)
    assert c1["new"] == 2 and c1["duplicates"] == 1 and c1["excluded"] == 1 and c1["no_description"] == 1
    with db.session_scope() as s:
        acme = s.exec(select(m.Job).where(m.Job.company == "Acme GmbH")).one()
        assert acme.sources == ["linkedin", "indeed"] and len(acme.links) == 2
        assert acme.full_description == "longer description A from indeed"
        sen = s.exec(select(m.Job).where(m.Job.company == "B")).one()
        assert sen.status == "excluded" and "Seniority" in sen.exclude_reason
        c = s.exec(select(m.Job).where(m.Job.company == "C")).one()
        assert "incomplete" in c.notes and c.status == "new"
    # next day: same LinkedIn job again (different tracking params) -> duplicate, not new
    c2 = ingest([RawJob(source="linkedin", title="Werkstudent Data Science", company="Acme GmbH", location="Berlin",
                        url="https://www.linkedin.com/jobs/view/4100000001/")], 2, f)
    assert c2["new"] == 0 and c2["duplicates"] == 1
    assert "linkedin.com/jobs/view/4100000001" in known_url_keys()


def test_title_filter_skips_unrelated_roles_only():
    from jobagent.steps.clean import prefilter_reason
    from jobagent.schemas import RawJob as R
    fp = m.FilterProfile(name="t", roles=["Data Science", "Data Engineering"], locations=["Berlin"], days=5)
    keep = ["Werkstudent Data Engineer (m/w/d)", "Praktikum KI-Entwicklung", "Working Student GenAI / LLM",
            "Werkstudent Business Intelligence", "AI/ML Development Intern", "Werkstudent Prozessautomatisierung"]
    skip = ["Werkstudent Vertrieb (m/w/d)", "Praktikum Marketing", "Werkstudent Buchhaltung", "Werkstudent Kundenservice"]
    reason = lambda t: prefilter_reason(R(source="stepstone", title=t, location="Berlin"), fp)
    assert [t for t in keep if reason(t)] == []
    assert all(reason(t) == "Not one of your job titles (title)" for t in skip)
    # any other job titles work the same way: an accountant's search keeps accounting jobs, skips data jobs
    fp2 = m.FilterProfile(name="t", roles=["Accountant"], locations=["Berlin"], days=5)
    reason2 = lambda t: prefilter_reason(R(source="stepstone", title=t, location="Berlin"), fp2)
    assert reason2("Werkstudent Buchhaltung") is None and reason2("Junior Accountant (m/w/d)") is None
    assert reason2("Werkstudent Data Engineer (m/w/d)") == "Not one of your job titles (title)"
