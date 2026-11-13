import asyncio

import pytest

from jobagent.agents import sites as S
from jobagent.agents.extract import parse_posted
from jobagent.agents.site_agent import SiteAgent
from jobagent.config import settings as CFG
from jobagent.browser.reader import PageReader, Snapshot, detect_block
from jobagent.schemas import Query
from jobagent.textutil import normalize_url, dedupe_key

from fake_sites import FakeReader, FakeSites, TODAY


@pytest.fixture
def server(monkeypatch):
    with FakeSites() as fs:
        for site, prefix in [("linkedin", "/li"), ("glassdoor", "/gd"), ("indeed", "/in"),
                             ("xing", "/xg"), ("stepstone", "/ss")]:
            monkeypatch.setattr(S.SITES[site], "base", fs.base + prefix)
        yield fs


def agent(site, server, **kw):
    return SiteAgent(site, reader_factory=lambda s, mode: FakeReader(s, mode, server.base), **kw)


def q(site, kw="data science", loc="Berlin", jt="Internship,Working Student"):
    return Query(site=site, keywords=kw, location=loc, job_type=jt, days=5)


# --------------------------------------------------------------- unit ---
def test_parse_posted():
    assert parse_posted("2 days ago", TODAY).toordinal() == TODAY.toordinal() - 2
    assert parse_posted("vor 3 Tagen", TODAY).toordinal() == TODAY.toordinal() - 3
    assert parse_posted("Gestern", TODAY).toordinal() == TODAY.toordinal() - 1
    assert parse_posted("Just posted", TODAY) == TODAY
    assert parse_posted("5 hours ago", TODAY) == TODAY
    assert parse_posted("2026-11-02") .isoformat() == "2026-11-02"
    assert parse_posted("03.11.2026").isoformat() == "2026-11-03"
    assert parse_posted("1 week ago", TODAY).toordinal() == TODAY.toordinal() - 7
    assert parse_posted("") is None


def test_normalize_url_and_dedupe_key():
    a = normalize_url("https://de.linkedin.com/jobs/view/werkstudent-data-4100000001?refId=1&trackingId=2")
    b = normalize_url("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4100000001")
    assert a == b == "linkedin.com/jobs/view/4100000001"
    assert normalize_url("https://de.indeed.com/viewjob?jk=abc123&from=serp") == "indeed.com/viewjob?jk=abc123"
    assert dedupe_key("Acme Analytics GmbH", "Werkstudent Data Science (m/w/d)", "Berlin, Germany") == \
        dedupe_key("ACME Analytics", "Werkstudent Data Science", "Berlin")


def test_detect_block():
    assert detect_block(Snapshot("u", "https://x/authwall?x", 200, text="hi")) == "login_wall"
    assert detect_block(Snapshot("u", "u", 403, title="Just a moment...", text="")) == "captcha"
    assert detect_block(Snapshot("u", "u", 429, text="")) == "blocked"
    assert detect_block(Snapshot("u", "u", 0, error="timeout")) == "error"
    assert detect_block(Snapshot("u", "u", 200, text="jobs " * 100)) is None


def test_search_urls_put_filters_in_url():
    li = S.li_search(S.SITES["linkedin"], q("linkedin"), 1, "guest")
    assert "f_TPR=r432000" in li and "f_JT=I%2CP" in li and "start=10" in li and "jobs-guest" in li
    assert "start=25" in S.li_search(S.SITES["linkedin"], q("linkedin"), 1, "login")      # 25 per page when logged in
    assert "/jobs/search/" in S.li_search(S.SITES["linkedin"], q("linkedin"), 0, "login")
    ind = S.indeed_search(S.SITES["indeed"], q("indeed", loc="Germany"), 0, "guest")
    assert ind.startswith("https://de.indeed.com/jobs?") and "fromage=5" in ind
    ss = S.ss_search(S.SITES["stepstone"], q("stepstone", kw="Werkstudent Data Science"), 0, "guest")
    assert ss == "https://www.stepstone.de/jobs/werkstudent-data-science/in-berlin?ag=age_7"


# -------------------------------------------------------- browser e2e ---
def test_linkedin_guest_pagination_and_details(server):
    a = agent("linkedin", server, max_pages=5)
    res = asyncio.run(a.run([q("linkedin")]))
    o = res.outcomes[0]
    assert o.mode == "guest" and o.status == "ok" and o.pages == 4      # 10 + 10 + 10, then an empty page
    assert o.collected == 30 == len(res.jobs)
    j = [x for x in res.jobs if "4100000001" in x.url][0]
    assert j.company == "Acme Analytics GmbH" and j.posted_date.toordinal() == TODAY.toordinal() - 1
    assert "20 hours per week" in j.description and j.job_type_hint == "Part-time"
    # tracking params removed from links
    assert "trackingId" not in j.url


def test_known_jobs_and_prefilter_skip_detail_fetch(server):
    known = {normalize_url(f"{server.base}/li/jobs/view/machine-learning-intern-4100000002")}
    a = agent("linkedin", server, max_pages=1, known_url_keys=known,
              prefilter=lambda j: "seniority" if "Senior" in j.title else None)
    res = asyncio.run(a.run([q("linkedin")]))
    by = {j.url.rsplit("-", 1)[1]: j for j in res.jobs}
    assert by["4100000002"].description == ""      # known: not refetched
    assert by["4100000003"].prefilter_reason == "seniority"
    assert by["4100000003"].description == ""
    assert by["4100000001"].description != ""
    detail_hits = [h for h in server.hits if "jobPosting/" in h]
    assert not any("4100000002" in h or "4100000003" in h for h in detail_hits)


def test_glassdoor_login_wall_falls_back_to_login(server):
    a = agent("glassdoor", server, connection_status="connected")
    res = asyncio.run(a.run([q("glassdoor")]))
    o = res.outcomes[0]
    assert o.mode == "login" and o.status == "ok" and o.collected == 2
    assert all(len(j.description) > 100 for j in res.jobs)
    assert res.jobs[0].company in ("Glass Co", "Door GmbH")


def test_glassdoor_without_login_records_login_needed(server):
    a = agent("glassdoor", server, connection_status="not_connected")
    res = asyncio.run(a.run([q("glassdoor")]))
    assert res.outcomes[0].status == "login_needed" and res.jobs == []


def test_expired_login_is_reported(server, monkeypatch):
    monkeypatch.setattr(S.SITES["glassdoor"], "base", server.base + "/gdexpired")
    a = agent("glassdoor", server, connection_status="connected")
    res = asyncio.run(a.run([q("glassdoor"), q("glassdoor", kw="ml")]))
    assert res.outcomes[0].status == "login_expired" and res.login_expired
    assert res.outcomes[1].status == "login_needed"   # stops using the dead session


def test_captcha_without_login_is_recorded(server):
    a = agent("indeed", server)
    res = asyncio.run(a.run([q("indeed")]))
    assert res.outcomes[0].status == "captcha"


def test_human_check_opens_window_and_continues_once_passed(server, monkeypatch):
    """Indeed-style bot check: switch to the visible window, notice when you passed it, carry on there."""
    monkeypatch.setattr(CFG, "headless_guest", True)
    asked = []

    async def on_captcha(site, url):
        asked.append(url)
        r = a._readers["window"]                     # you tick "I am human" in the window ...
        await r._context.add_cookies([{"name": "human", "value": "ok", "url": server.base}])
        await r._page.goto(url)                     # ... and the site shows the results
        await asyncio.sleep(30)                     # you never press "Solved": the agent notices by itself
        return False

    a = agent("indeed", server, on_captcha=on_captcha)
    res = asyncio.run(a.run([q("indeed"), q("indeed", kw="machine learning"), q("indeed", kw="ai")]))
    assert len(asked) == 1                                       # asked once, not for every search
    assert [o.status for o in res.outcomes] == ["ok", "ok", "ok"]
    assert all(o.mode == "window" for o in res.outcomes) and a.start_mode == "window"
    assert sorted(j.title for j in res.jobs) == ["Praktikum Machine Learning", "Werkstudent Data Science (m/w/d)"]
    assert all("Python" in j.description for j in res.jobs)


def test_unsolved_human_check_skips_rest_of_site(server, monkeypatch):
    monkeypatch.setattr(CFG, "headless_guest", True)
    asked = []

    async def on_captcha(site, url):                 # nobody there (e.g. the morning run)
        asked.append(url)
        return False

    a = agent("indeed", server, on_captcha=on_captcha)
    before = len([h for h in server.hits if h.startswith("/in/")])
    res = asyncio.run(a.run([q("indeed", kw=f"kw{i}") for i in range(10)]))
    loads = len([h for h in server.hits if h.startswith("/in/")]) - before
    assert len(asked) == 1 and loads <= 3                        # guest try + window try, then it stops
    assert [o.status for o in res.outcomes] == ["captcha"] * 10
    assert "skipped" in res.outcomes[5].error


def test_german_automatic_check_is_recognised_and_waited_for(server, monkeypatch):
    """Last run: the window got a German check page (HTTP 403) that wasn't recognised -> 'failed' x80."""
    monkeypatch.setattr(CFG, "headless_guest", True)
    monkeypatch.setattr(S.SITES["indeed"], "base", server.base + "/inde")
    snap = Snapshot("u", "u", 403, title="Einen Moment…", text="Überprüfung, ob Sie ein Mensch sind.",
                    html="<div id='challenge-platform'>")
    assert detect_block(snap) == "captcha"
    asked = []

    async def on_captcha(site, url):
        asked.append(url)
        return False

    a = agent("indeed", server, on_captcha=on_captcha)
    res = asyncio.run(a.run([q("indeed"), q("indeed", kw="ml")]))
    assert asked == []                                  # passed by itself, you were not bothered
    assert [o.status for o in res.outcomes] == ["ok", "ok"] and len(res.jobs) == 2
    from pathlib import Path
    dbg = Path(CFG.data_dir) / "debug" / "indeed"
    assert (dbg / "log.txt").exists() and any(p.suffix == ".html" for p in dbg.iterdir())


def test_refused_pages_back_off_then_stop_the_site(server, monkeypatch):
    monkeypatch.setattr(S.SITES["indeed"], "base", server.base + "/inblock")
    a = agent("indeed", server)
    before = len(server.hits)
    res = asyncio.run(a.run([q("indeed", kw=f"kw{i}") for i in range(6)]))
    hits = len(server.hits) - before
    assert [o.status for o in res.outcomes] == ["blocked"] * 6
    assert hits == 6                                    # 2 searches x (1 try + 2 retries), then it stops
    assert "skipped" in res.outcomes[2].error and "skipped" not in res.outcomes[1].error


def test_ok_cell_not_mistaken_for_check():
    page = Snapshot("u", "u", 200, title="Werkstudent Jobs in Berlin", text="Einen Moment bitte, wir laden. " + "Job " * 600,
                    html="<script src='https://www.google.com/recaptcha/api.js'></script>")
    assert detect_block(page) is None


def test_unknown_layout_uses_page_reader_fallback(server):
    a = agent("xing", server)
    res = asyncio.run(a.run([q("xing")]))
    titles = sorted(j.title for j in res.jobs)
    assert titles == ["Praktikum Data Engineering", "Werkstudent KI-Entwicklung (m/w/d)"]
    assert all("Python" in j.description or "Werkstudent" in j.description for j in res.jobs)


def test_result_count_at_site_limit_marks_split_needed(server):
    a = agent("stepstone", server)
    res = asyncio.run(a.run([q("stepstone", kw="Werkstudent Data")]))
    o = res.outcomes[0]
    assert o.reported == 2345 and o.status == "limit" and o.collected == 1


def test_agent_has_no_click_or_type_tools():
    assert not any(hasattr(PageReader, n) for n in ("click", "type", "fill", "press", "apply"))


def test_connection_verify_detects_logged_in_and_expired(server, monkeypatch):
    from jobagent.browser import connections as C
    pdir = PageReader.profile_dir("glassdoor")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "Cookies").write_text("x")
    monkeypatch.setattr(S.SITES["glassdoor"], "check_url", server.base + "/gd/Job/jobs.htm")
    st = asyncio.run(C.verify("glassdoor", reader_factory=lambda s, m: FakeReader(s, "login", server.base)))
    assert st == "connected" and C.get_status("glassdoor") == "connected"
    st = asyncio.run(C.verify("glassdoor", reader_factory=lambda s, m: FakeReader(s, "guest", server.base)))
    assert st == "expired"
    C.disconnect("glassdoor")
    assert C.get_status("glassdoor") == "not_connected" and not C.has_profile("glassdoor")
    rows = {r["site"]: r for r in C.list_connections()}
    assert rows["stepstone"]["status"] == "not_needed" and rows["indeed"]["supports_login"] and rows["linkedin"]["supports_login"]


def test_diagnose_endpoint_runs_one_search(server):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        r = c.get("/api/diagnose/stepstone", params={"keywords": "Werkstudent Data", "location": "Berlin"}).json()
    assert r["status"] in ("ok", "limit") and r["collected"] == 1 and r["jobs"][0]["company"] == "Stepco GmbH"
    assert "stepstone" in r["url"] or "/ss/" in r["url"]



def test_site_that_refuses_even_after_the_check_switches_to_manual(server, monkeypatch):
    """What your PC showed: box ticked -> 'Überprüfung erfolgreich' -> new Ray-ID, still blocked."""
    monkeypatch.setattr(CFG, "headless_guest", True)
    monkeypatch.setattr(S.SITES["indeed"], "base", server.base + "/inray")
    asked = []

    async def on_captcha(site, url):
        asked.append(url)
        await a._readers["window"]._page.reload()       # you tick the box, the page loads again ...
        await asyncio.sleep(30)                         # ... and is refused anyway
        return False

    a = agent("indeed", server, on_captcha=on_captcha)
    before = len(server.hits)
    res = asyncio.run(a.run([q("indeed", kw=f"kw{i}") for i in range(5)]))
    assert len(asked) == 1 and a.refuses_automation
    assert [o.status for o in res.outcomes] == ["manual"] * 5
    assert len(server.hits) - before <= 4                 # no hammering

    # next run: manual mode -> no browser, no page loads, links instead
    b = agent("indeed", server, on_captcha=on_captcha, start_mode="manual")
    before = len(server.hits)
    res = asyncio.run(b.run([q("indeed", kw=f"kw{i}") for i in range(5)]))
    assert len(server.hits) == before and all(o.status == "manual" for o in res.outcomes)


def test_search_links_and_mode_endpoints():
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    from jobagent import db, models as m
    with db.session_scope() as s:
        fp = s.exec(m.select(m.FilterProfile)).first() if hasattr(m, "select") else None
    from sqlmodel import select
    with db.session_scope() as s:
        fp = s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first()  # noqa: E712
        fp.roles, fp.locations, fp.job_types = ["Data Science", "Data Engineering"], ["Berlin", "Remote"], ["Working Student"]
        s.add(fp)
    with TestClient(app) as c:
        assert c.get("/api/sites/indeed/mode").json()["mode"] == "guest"
        c.put("/api/sites/indeed/mode", json={"mode": "manual"})
        assert c.get("/api/sites/indeed/mode").json()["mode"] == "manual"
        links = c.get("/api/sites/indeed/search-links").json()
        assert links and all(l["url"].startswith("https://") and "indeed" in l["url"] for l in links)
        assert len(links) < 15                                  # OR searches: a handful of links, not 80



def test_indeed_with_your_login_is_used_when_guest_is_blocked(server, monkeypatch):
    monkeypatch.setattr(S.SITES["indeed"], "base", server.base + "/inlogin")
    a = agent("indeed", server, connection_status="connected", on_captcha=lambda s, u: asyncio.sleep(0, False))
    res = asyncio.run(a.run([q("indeed"), q("indeed", kw="ml")]))
    assert [o.status for o in res.outcomes] == ["ok", "ok"] and {o.mode for o in res.outcomes} == {"login"}
    assert len(res.jobs) == 2 and all("Python" in j.description for j in res.jobs)


def test_job_pages_most_relevant_first_with_a_cap_and_progress(monkeypatch):
    """1,405 job pages took 2 h 20 min in silence: now the best ones first, a cap per run, progress with time left."""
    import asyncio
    from jobagent.agents import site_agent as SA
    from jobagent.config import settings
    from jobagent.schemas import RawJob
    monkeypatch.setattr(settings, "max_details_per_site", 30)
    monkeypatch.setattr(settings, "page_delay_seconds", 0)
    logs, opened = [], []

    class Snap:
        status, final_url, title, text, html = 200, "", "", "a job " * 50, "<div class='d'>" + "Python SQL " * 40 + "</div>"

    class Reader:
        async def goto(self, url):
            opened.append(url)
            return Snap()

        async def pause(self):
            pass
    agent = SA.SiteAgent.__new__(SA.SiteAgent)
    agent.site, agent.spec, agent.start_mode, agent.can_login = "linkedin", SA.SITES["linkedin"], "guest", False
    agent.emit = lambda e: logs.append(e["message"])
    agent.details_read = 0

    async def reader(mode):
        return Reader()
    agent.reader = reader
    monkeypatch.setattr(SA, "detect_block", lambda snap: None)
    jobs = [RawJob(source="linkedin", title=f"Sales Manager {i}", company="X", url=f"https://www.linkedin.com/jobs/view/{1000 + i}")
            for i in range(60)]
    jobs.insert(40, RawJob(source="linkedin", title="Werkstudent Machine Learning Python", company="Y",
                           url="https://www.linkedin.com/jobs/view/999"))
    asyncio.run(agent.fetch_details(jobs))
    assert len(opened) == 30 and "999" in opened[0]                   # the relevant one first, then the cap
    assert any("most relevant of 61" in m for m in logs) and any("read 25 of 30 job pages, about" in m for m in logs)
    assert logs[-1] == "read 30 of 30 job pages"
    # narrower searches find more jobs later in the same run: the limit is for the whole run, not each round
    more = [RawJob(source="linkedin", title=f"Data Analyst {i}", company="Z", url=f"https://www.linkedin.com/jobs/view/{5000 + i}")
            for i in range(20)]
    asyncio.run(agent.fetch_details(more))
    assert len(opened) == 30 and any("30 read already this run" in m for m in logs)
