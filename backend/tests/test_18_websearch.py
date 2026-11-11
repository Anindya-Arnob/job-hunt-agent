"""Web-search agent: postings found through Claude's web search (no browser, no bot checks)."""
import asyncio
import json
from datetime import date, timedelta

import httpx
import openpyxl
from sqlmodel import select

from jobagent import db, models as m
from jobagent.config import settings
from jobagent.cv import service as CV
from jobagent.feeds import websearch as W
from jobagent.graphs import search_graph as G

TODAY = date.today()
LINKS = {
    "https://www.arbeitnow.com/jobs/companies/knime-ag/working-student-data-analytics-berlin-463031",
    "https://de.indeed.com/viewjob?jk=abc123def4567890&from=serp",
    "https://www.stepstone.de/stellenangebote--Werkstudent-Data-Science-Berlin-Acme--12345-inline.html",
    "https://de.indeed.com/Data-Analyst-Jobs-in-Berlin",
    "https://jobs.example.com/old-posting",
}


def answer(items):
    return "Here you go:\n```json\n" + json.dumps(items) + "\n```"


ITEMS = [
    {"title": "Working Student Data Analytics", "company": "KNIME AG", "location": "Berlin",
     "url": "https://arbeitnow.com/jobs/companies/knime-ag/working-student-data-analytics-berlin-463031",
     "posted": "2 days ago", "job_type": "Working Student", "summary": "Python, SQL, KNIME. 20h/week. English."},
    {"title": "Werkstudent Data Science (m/w/d)", "company": "Indico GmbH", "location": "Berlin",
     "url": "https://de.indeed.com/viewjob?jk=abc123def4567890", "posted": "unknown (date not visible)",
     "summary": "Python, pandas, scikit-learn; Deutsch B2."},
    {"title": "Werkstudent Data Science", "company": "Acme", "location": "unknown (not visible)",
     "url": "https://www.stepstone.de/stellenangebote--Werkstudent-Data-Science-Berlin-Acme--12345-inline.html",
     "posted": TODAY.isoformat(), "summary": "Machine learning, Python."},
    {"title": "Data Analyst Intern", "company": "Seen on a list page", "url": "",
     "source_page": "https://de.indeed.com/Data-Analyst-Jobs-in-Berlin", "posted": "1 day ago", "summary": "SQL"},
    {"title": "Invented Job", "company": "Nowhere", "url": "https://de.indeed.com/viewjob?jk=ffffffffffffffff"},
    {"title": "Data Analyst Jobs in Berlin", "company": "", "url": "https://de.indeed.com/Data-Analyst-Jobs-in-Berlin"},
    {"title": "Data Intern (closed)", "company": "Old GmbH", "url": "https://jobs.example.com/old-posting", "posted": ""},
]


def test_only_postings_from_real_search_results_are_kept():
    q = W.WebQuery(site="web", location="Berlin", group="Data", terms=["Data Scientist"], type_words=["Werkstudent"], days=5)
    jobs, dropped = W.to_jobs(q, W._extract_json(answer(ITEMS)), LINKS)
    titles = {j.title for j in jobs}
    assert "Invented Job" not in titles and "Data Analyst Jobs in Berlin" not in titles
    assert dropped == 2
    by = {j.title: j for j in jobs}
    assert by["Working Student Data Analytics"].source == "web"          # arbeitnow = other site
    assert by["Werkstudent Data Science (m/w/d)"].source == "indeed"      # credited to Indeed
    assert by["Werkstudent Data Science"].source == "stepstone"
    assert by["Werkstudent Data Science (m/w/d)"].posted_text == ""        # "unknown (...)" cleaned
    assert by["Werkstudent Data Science"].location == "Berlin"             # falls back to the searched place
    assert by["Data Analyst Intern"].url == "https://de.indeed.com/Data-Analyst-Jobs-in-Berlin"
    assert by["Working Student Data Analytics"].posted_date == TODAY - timedelta(days=2)
    assert all(j.mode == "web" for j in jobs)


def test_site_search_keeps_only_that_site():
    q = W.WebQuery(site="indeed", location="Berlin", group="Data", terms=["Data Scientist"], type_words=["Werkstudent"], days=5)
    jobs, _ = W.to_jobs(q, W._extract_json(answer(ITEMS)), LINKS)
    assert all("indeed.com" in j.url for j in jobs) and len(jobs) == 2


def test_plan_covers_every_place_and_role_group(monkeypatch):
    monkeypatch.setattr(W, "engine", lambda: "cli")
    qs = W.plan_web_queries(["Data Science", "Data Engineering", "Gen AI"], ["Internship", "Working Student"],
                            ["Berlin", "Germany", "Remote"], 5, ["indeed", "linkedin"], max_calls=0)
    assert {(q.location, q.group) for q in qs} == {(l, g) for l in ("Berlin", "Germany", "Remote")
                                                   for g in ("AI / ML / GenAI", "Data Science / Analytics", "Data Engineering / MLOps")}
    assert all(q.site == "web" for q in qs)                                # Claude Code ignores site: filters
    assert "Werkstudent" in qs[0].type_words and "Werkstudent" not in [q for q in qs if q.location == "Remote"][0].type_words
    monkeypatch.setattr(W, "engine", lambda: "api")
    qs = W.plan_web_queries(["Data Science"], ["Working Student"], ["Berlin"], 5, ["indeed", "glassdoor", "linkedin"], max_calls=0)
    assert [q.site for q in qs] == ["indeed", "glassdoor", "web"]          # API: blocked sites get their own search
    assert len(W.plan_web_queries(["Data Science"], ["Working Student"], ["Berlin", "Germany"], 5, [], max_calls=1)) == 1


def test_cli_stream_is_parsed(monkeypatch):
    """The real `claude -p --output-format stream-json` output: tool results carry the links."""
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": "x"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content":
            'Web search results for query: "x"\n\nLinks: [{"title":"KNIME","url":"https://www.arbeitnow.com/jobs/companies/knime-ag/working-student-data-analytics-berlin-463031"}]'}]}},
        {"type": "result", "result": answer(ITEMS[:1]), "total_cost_usd": 0.02},
    ]

    class Proc:
        returncode = 0

        async def communicate(self, data):
            assert b"Find CURRENT job postings" in data
            return ("\n".join(json.dumps(x) for x in lines)).encode(), b""

    async def fake_exec(*cmd, **kw):
        assert "--allowedTools" in cmd and "WebSearch" in cmd
        return Proc()

    monkeypatch.setattr(W, "claude_cli", lambda: "/usr/bin/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    q = W.WebQuery(site="web", location="Berlin", group="Data", terms=["Data Analyst"], type_words=["Working Student"], days=5)
    text, links, n, cost = asyncio.run(W._call_cli(W.build_prompt(q), q))
    assert n == 1 and len(links) == 1 and cost == 0.02
    jobs, _ = W.to_jobs(q, W._extract_json(text), links)
    assert [j.company for j in jobs] == ["KNIME AG"]


def page(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "old-posting" in u:
        return httpx.Response(200, text="<html><body><h1>Diese Stelle ist nicht mehr verfügbar</h1></body></html>")
    if "arbeitnow" in u:
        return httpx.Response(200, text="<html><body><main>" + ("Working student Data Analytics at KNIME in Berlin. "
                                        "Python, SQL, dashboards, 20 hours per week, English. " * 12) + "</main></body></html>")
    return httpx.Response(404)


def test_web_search_jobs_go_through_the_whole_pipeline(monkeypatch, master_cv_path):
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    with db.session_scope() as s:
        fp = m.FilterProfile(name="web", roles=["Data Science"], job_types=["Working Student", "Internship"],
                             locations=["Berlin"], work_modes=["Remote", "Hybrid", "On-site"], days=5,
                             sources=["indeed"], language_max="any")
        s.add(fp)
        s.flush()
        fid = fp.id
    monkeypatch.setattr(settings, "websearch_enabled", True)
    monkeypatch.setattr(G, "BROWSER_SITES", [])                            # only the web-search agent
    calls = []

    async def caller(prompt, q):
        calls.append(q.label)
        return answer(ITEMS), set(LINKS), 3, 0.0

    monkeypatch.setattr(G, "WEB_CALLER", caller)
    monkeypatch.setattr(G, "WEB_HTTP", httpx.AsyncClient(transport=httpx.MockTransport(page)))
    final = asyncio.run(G.run_search(fid))
    assert calls, "web search did not run"
    with db.session_scope() as s:
        jobs = {j.title: j for j in s.exec(select(m.Job)).all()}
        stats = s.exec(select(m.SearchStat).where(m.SearchStat.run_id == final["run_id"])).all()
    assert "Invented Job" not in jobs and "Data Intern (closed)" not in jobs            # fake + expired dropped
    knime = jobs["Working Student Data Analytics"]
    assert knime.sources == ["web"] and "dashboards" in knime.full_description            # full text fetched
    assert jobs["Werkstudent Data Science (m/w/d)"].sources == ["indeed"]
    assert any(st.mode == "web" and st.collected > 0 for st in stats)
    wb = openpyxl.load_workbook(final["export_path"])                                     # in the Excel file too
    rows = [[c.value for c in r] for r in wb["Jobs"].iter_rows(min_row=2)]
    assert any("KNIME" in " ".join(str(v) for v in r) for r in rows)


def test_without_claude_the_run_says_how_to_set_it_up(monkeypatch, master_cv_path):
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    with db.session_scope() as s:
        fp = m.FilterProfile(name="web", roles=["Data Science"], job_types=["Working Student"], locations=["Berlin"],
                             work_modes=["Remote"], days=5, sources=["indeed"], language_max="any")
        s.add(fp)
        s.flush()
        fid = fp.id
    monkeypatch.setattr(settings, "websearch_enabled", True)
    monkeypatch.setattr(G, "BROWSER_SITES", [])
    monkeypatch.setattr(W, "engine", lambda: "")
    final = asyncio.run(G.run_search(fid))
    with db.session_scope() as s:
        st = s.exec(select(m.SearchStat).where(m.SearchStat.run_id == final["run_id"])).all()
    assert any(x.site == "websearch" and "Claude Code" in x.error for x in st)


def test_one_failed_search_does_not_lose_the_others():
    """Your run #16: one search hit Claude Code's step limit and the others' jobs were thrown away."""
    async def caller(prompt, q):
        if q.location == "Germany":
            raise RuntimeError("error_max_turns")
        return answer(ITEMS[:1]), set(LINKS), 3, 0.0

    qs = [W.WebQuery(site="web", location=l, group="Data", terms=["Data Analyst"], type_words=["Working Student"], days=5)
          for l in ("Berlin", "Germany", "Remote")]
    res = asyncio.run(W.run_all(qs, caller=caller, parallel=2))
    assert [len(r.jobs) for r in res] == [1, 0, 1] and "max_turns" in res[1].error


def test_not_logged_in_stops_only_the_searches_not_started():
    async def caller(prompt, q):
        raise W.WebSearchUnavailable("Not logged in · Please run /login")

    qs = [W.WebQuery(site="web", location=l, group="Data", terms=["x"], type_words=["y"], days=5) for l in ("Berlin", "Germany")]
    try:
        asyncio.run(W.run_all(qs, caller=caller, parallel=1))
        raise AssertionError("should have stopped")
    except W.WebSearchUnavailable as e:
        assert "login" in str(e)


def test_offline_page_reader_ignores_menu_and_category_links():
    from jobagent.agents.extract import _fake_extract
    from jobagent.browser.reader import Snapshot
    links = [("Data Engineer jobs in Berlin", "https://wellfound.com/role/l/data-engineer/berlin"),
             ("Browse all startup jobs", "https://wellfound.com/jobs"),
             ("Working Student Data Engineer", "https://wellfound.com/jobs/3141592-working-student-data-engineer"),
             ("Werkstudent Data Science (m/w/d)", "https://www.xing.com/jobs/berlin-werkstudent-data-science-123456789"),
             ("Jobs in Berlin finden", "https://www.xing.com/jobs/search?keywords=data")]
    snap = Snapshot("u", "u", 200, title="Jobs", text="x" * 500, links=links)
    assert [j.title for j in _fake_extract(snap, "wellfound").jobs] == ["Working Student Data Engineer"]
    assert [j.title for j in _fake_extract(snap, "xing").jobs] == ["Werkstudent Data Science (m/w/d)"]
