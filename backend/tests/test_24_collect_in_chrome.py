"""Glassdoor and Indeed refuse automated browsers, so they are collected in your own Chrome by the extension:
it waits while you do the human check, sends the job list and the job pages to the app, then goes on."""
import asyncio
import time

import httpx
import pytest
from sqlmodel import select

from jobagent import collect, db, models as m
from jobagent.agents.sites import SITES

from fake_sites import FakeSites, GD_RESULTS, IN_RESULTS
from test_20_extension import EXT, app_server  # noqa: F401  (fixture)


@pytest.fixture
def profile():
    with db.session_scope() as s:
        fp = m.FilterProfile(name="t", roles=["Data Science"], job_types=["Working Student", "Internship"],
                             locations=["Berlin"], work_modes=["Remote", "Hybrid", "On-site"], days=5,
                             sources=["glassdoor", "indeed"], is_default=True)
        s.add(fp)
        s.flush()
        return fp.id


@pytest.fixture
def fake_hosts(monkeypatch):
    with FakeSites() as fs:
        port = fs.base.rsplit(":", 1)[1].split("/")[0]
        monkeypatch.setattr(SITES["indeed"], "base", f"http://de.indeed.test:{port}/in")
        monkeypatch.setattr(SITES["glassdoor"], "base", f"http://www.glassdoor.test:{port}/gd")
        monkeypatch.setattr(collect, "PAGES", 1)
        yield port


def test_plan_and_pages_without_a_browser(profile, fake_hosts):
    q = collect.plan(["indeed", "glassdoor"], profile)
    assert q and {x["site"] for x in q} == {"indeed", "glassdoor"} and all("de.indeed.test" in x["url"] or "glassdoor.test" in x["url"] for x in q)
    st = collect.start(["indeed"], profile)
    assert st["active"] and st["url"].startswith("http://de.indeed.test")
    url = st["url"]
    r = collect.page(url, IN_RESULTS)
    assert r["jobs"] == 2 and len(r["details"]) == 2 and all("/viewjob?jk=" in u for u in r["details"])
    ok = collect.detail(r["details"][0], "<html><body><div id='jobDescriptionText'>" + "Python and SQL for data. " * 20 + "</div></body></html>")
    assert ok["ok"]
    assert collect.detail(r["details"][1], "<html><head><title>Just a moment...</title></head><body>Verify you are human</body></html>")["blocked"]
    d = collect.page_done()
    assert d["counts"]["new"] == 2 and d["counts"]["described"] == 1
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job).where(m.Job.company.in_(["Indico GmbH", "Indico Labs"]))).all()
        assert len(jobs) == 2 and any("Python and SQL" in (j.full_description or "") for j in jobs)
        assert all("indeed" in j.sources for j in jobs)
    collect.stop()
    collect.stop()                                       # Stop pressed again: recorded once
    assert not collect.public()["active"]
    with db.session_scope() as s:
        run = s.get(m.Run, st["run_id"])
        assert len(run.errors) <= 1                     # never one line per press


def test_collect_in_your_own_chrome_with_a_human_check(app_server, profile, fake_hosts, tmp_path):  # noqa: F811
    port = fake_hosts
    st = httpx.post("http://127.0.0.1:8780/api/collect/start", json={"sites": ["indeed", "glassdoor"],
                                                                     "filter_profile_id": profile}).json()
    total = st["total"]
    assert total >= 2

    async def go():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"), headless=True, channel="chromium",
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                      "--host-resolver-rules=MAP de.indeed.test 127.0.0.1, MAP www.glassdoor.test 127.0.0.1"])
            if not ctx.service_workers:
                await ctx.wait_for_event("serviceworker")
            # Glassdoor: you are logged in already (its results need a session)
            await ctx.add_cookies([{"name": "session", "value": "ok", "domain": "www.glassdoor.test", "path": "/"}])
            app = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await app.goto("http://127.0.0.1:8780/search")
            async with ctx.expect_page() as info:          # what "Run" / "Collect now" does on the Search screen
                await app.evaluate("u => { const w = window.open('about:blank', '_blank'); setTimeout(() => { w.location.href = u }, 300) }", st["url"])
            tab = await info.value
            # Indeed shows its "verify you are human" page: the helper waits for you
            await tab.wait_for_selector("#jhc-panel", timeout=20000)
            await tab.wait_for_function("() => /verify you are human/i.test(document.querySelector('#jhc-status')?.textContent || '')", timeout=20000)
            waiting = httpx.get("http://127.0.0.1:8780/api/collect/status").json()
            # you pass the check: the site sets its cookie and reloads
            await ctx.add_cookies([{"name": "human", "value": "ok", "domain": "de.indeed.test", "path": "/"}])
            await tab.reload()
            for _ in range(240):
                s = httpx.get("http://127.0.0.1:8780/api/collect/status").json()
                if s["status"] != "running":
                    break
                await asyncio.sleep(0.5)
            panel = await tab.inner_text("#jhc-panel")
            await ctx.close()
            return waiting, s, panel

    waiting, final, panel = asyncio.run(go())
    assert waiting["waiting"] == "human check"
    assert final["status"] == "done" and final["done"] == total, final
    assert final["counts"]["found"] >= 4 and final["counts"]["new"] >= 4 and final["counts"]["described"] >= 3
    assert "Done:" in panel
    with db.session_scope() as s:
        titles = {j.title for j in s.exec(select(m.Job)).all()}
        run = s.exec(select(m.Run).where(m.Run.kind == "collect")).all()[-1]
    assert {"Data Science Intern", "ML Working Student", "Praktikum Machine Learning"} <= titles
    assert run.status == "done" and run.counts["new"] >= 4
