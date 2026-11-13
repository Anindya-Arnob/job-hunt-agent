"""The same job found twice (two sites, two days, German and English) is stored once; copies already on the board
are hidden, keeping the one you worked on. Cases taken from a real job board."""
from sqlmodel import select

from jobagent import db, models as m
from jobagent.schemas import RawJob
from jobagent.steps import dupes as D
from jobagent.steps.clean import ingest


def same(a, b):
    return D.same_job(D._Item(1, *a), D._Item(2, *b))


def test_near_duplicates_from_real_boards():
    assert same(("MAHLE", "Praktikum Machine Learning und Data Science (m/w/d)", "Stuttgart"),
                ("MAHLE International", "Internship Machine Learning and Data Science (m/f/d)", "Stuttgart"))
    assert same(("BMW Group", "Werkstudent Motorrad Datenanalysen", "Munich, Bavaria, Germany"),
                ("BMW AG", "Werkstudent Motorrad Datenanalysen", ""))
    assert same(("Cadence Design Systems", "Intern Agentic AI Workflow Development", "München"),
                ("Cadence Design Systems", "Intern Agentic AI Workflow Development", "Munich"))
    assert same(("TDK Electronics AG", "Werkstudent Corporate Strategic Planning (m/w/d)", ""),
                ("TDK Electronics AG", "Working student Corporate Strategic Planning (m/f/d)", ""))
    assert same(("PERGOLUX", "Working Student Analytics Engineer (m/w/d)", ""),
                ("Pergolux", "Working Student Analytics Engineer (m/w/d)", "Berlin, Germany"))


def test_different_jobs_stay_apart():
    assert not same(("BDO Germany", "Praktikant Management Advisory", "Düsseldorf"),
                    ("BDO Germany", "Praktikant Management Advisory", "Stuttgart"))           # other city
    assert not same(("Reply", "Junior Data Engineer (m/w/d)", "Berlin"), ("Reply", "Senior Data Engineer (m/w/d)", "Berlin"))
    assert not same(("Reply", "AI Engineer (m/w/d)", "Berlin"), ("Reply", "GenAI Engineer (m/w/d)", "Berlin"))
    assert not same(("MAHLE", "Praktikum Supplier Quality", "Stuttgart"), ("Bosch", "Praktikum Supplier Quality", "Stuttgart"))
    assert not same(("Reply", "Werkstudent Data Engineer", "Berlin"), ("Reply", "Data Engineer", "Berlin"))   # job type


def test_a_second_site_adds_to_the_job_instead_of_a_copy():
    fp = m.FilterProfile(name="t", roles=["Data Science"], job_types=["Internship"], locations=["Germany"], days=30)
    a = RawJob(source="xing", title="Praktikum Machine Learning und Data Science (m/w/d)", company="MAHLE",
               location="", url="https://www.xing.com/jobs/stuttgart-praktikum-ml-ds-1")
    b = RawJob(source="glassdoor", title="Internship Machine Learning and Data Science (m/f/d)",
               company="MAHLE International", location="Stuttgart", url="https://www.glassdoor.de/job-listing/x-1")
    assert ingest([a], 1, fp)["new"] == 1
    c = ingest([b], 2, fp)
    assert c["new"] == 0 and c["duplicates"] == 1
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job).where(m.Job.company.like("MAHLE%"))).all()
    assert len(jobs) == 1 and set(jobs[0].sources) == {"xing", "glassdoor"} and len(jobs[0].links) == 2
    assert jobs[0].location == "Stuttgart"                     # the missing place filled in


def test_copies_on_the_board_are_hidden_keeping_your_work():
    with db.session_scope() as s:
        built = m.Job(company="BMW Group", title="Werkstudent Motorrad Datenanalysen", location="Munich, Bavaria",
                      status=m.JobStatus.BUILT, sources=["linkedin"], links=["https://l/1"])
        copy = m.Job(company="BMW AG", title="Werkstudent Motorrad Datenanalysen", location="",
                     status=m.JobStatus.SCORED, sources=["xing"], links=["https://x/1"])
        applied2 = m.Job(company="BMW AG", title="Werkstudent Motorrad Datenanalysen", location="Munich",
                         status=m.JobStatus.APPLIED, sources=["stepstone"], links=["https://s/1"])
        other = m.Job(company="BMW AG", title="Werkstudent Batterie", location="Munich", status=m.JobStatus.SCORED)
        for j in (built, copy, applied2, other):
            s.add(j)
        s.flush()
        ids = built.id, copy.id, applied2.id, other.id
    assert D.merge_existing() == 1
    with db.session_scope() as s:
        b, c, a, o = (s.get(m.Job, i) for i in ids)
        assert c.status == "excluded" and c.exclude_reason == f"Duplicate of #{a.id}"    # kept: the one you applied to
        assert "https://x/1" in a.links and "xing" in a.sources
        assert b.status == "built" and a.status == "applied" and o.status == "scored"     # your work is never hidden
    assert D.merge_existing() == 0                                  # nothing left to do the second time
