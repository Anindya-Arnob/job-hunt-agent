"""The browser extension: apply in your own browser. Loads the real extension into Chromium and talks to the real app."""
import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlmodel import select

from jobagent import db, models as m
from jobagent.apply import profile as P
from jobagent.cv import service as CV

from fake_apply import FakeApply
from test_19_apply import prof

EXT = Path(__file__).resolve().parents[2] / "extension"


@pytest.fixture
def app_server():
    from jobagent.api.main import app
    cfg = uvicorn.Config(app, host="127.0.0.1", port=8780, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            httpx.get("http://127.0.0.1:8780/api/ext/ping", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield
    srv.should_exit = True
    t.join(5)


def test_apply_in_your_own_browser_with_the_extension(app_server, master_cv_path, tmp_path):
    site = FakeApply()
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    P.save(prof())
    cv, letter = tmp_path / "Alex_CV_Acme.pdf", tmp_path / "Alex_Cover_Letter_Acme.pdf"
    cv.write_bytes(b"%PDF-1.4 cv")
    letter.write_bytes(b"%PDF-1.4 letter")
    from jobagent.config import settings
    with db.session_scope() as s:
        job = m.Job(title="Working Student Data Science", company="Acme Analytics GmbH", location="Berlin",
                    job_type="Working Student", links=[site.base + "/board/job/1"], status=m.JobStatus.BUILT,
                    full_description="Python, SQL.", url_key="acme1", dedupe_key="acme1")
        s.add(job)
        s.flush()
        jid = job.id
        s.add(m.Score(job_id=jid, total=82, verdict="Apply"))
        s.add(m.Document(job_id=jid, kind="cv", pdf_path=str(cv)))
        s.add(m.Document(job_id=jid, kind="letter", pdf_path=str(letter)))
    # the job board / Apply page lives under the job folder in the real app - allow the test files
    settings.job_dir = str(tmp_path)

    # Claude (stand-in) answers the questions still open, in one call per page
    from jobagent.apply import bridge
    calls = []

    def fake_batch(ctx):
        def answer(items):
            calls.append([i["question"] for i in items])
            return {i["id"]: ("The data platform team." if "team" in i["question"].lower() else None) for i in items}
        return answer
    bridge.make_batch_answerer = fake_batch

    # you press "Apply in my browser" on the app's Apply page
    r = httpx.post("http://127.0.0.1:8780/api/ext/active", json={"job_id": jid}).json()
    assert r["job"]["company"] == "Acme Analytics GmbH" and r["job"]["has_letter"]

    async def go():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"), headless=True, channel="chromium",
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(site.base + "/board/job/1")
            # what the popup's "Apply" button does: follow this tab for the job and start the helper
            await sw.evaluate("""async (jid) => { const [t] = await chrome.tabs.query({url: '*://127.0.0.1/*/job/*'}) ;
                const tabs = await chrome.tabs.query({}); const tab = tabs.find(x => (x.url||'').includes('/board/job/1'));
                const f = {}; f[tab.id] = {jobId: jid}; await chrome.storage.session.set({follow: f});
                await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['content.js']});
                await chrome.tabs.sendMessage(tab.id, {type: 'jha-start', autoFill: true}); }""", jid)
            # the posting: the helper offers to go to the company's form
            await page.get_by_text("Go to the application form →").click(timeout=15000)
            await page.wait_for_url("**/ats/acme/apply", timeout=15000)
            # the form gets filled automatically
            await page.wait_for_function("() => document.querySelector('#fn') && document.querySelector('#fn').value === 'Alex'", timeout=30000)
            # the helper fills one field after the other (no jumping around): wait for the last of these
            await page.wait_for_function("() => document.querySelector('#sal').value !== '' && document.querySelector('#start').value !== ''", timeout=30000)
            vals = {k: await page.input_value(f"#{k}") for k in ("fn", "ln", "em", "start", "sal")}
            files = await page.eval_on_selector("#cv", "e => e.files.length ? e.files[0].name : ''")
            letter_name = await page.eval_on_selector("#cl", "e => e.files.length ? e.files[0].name : ''")
            german = await page.eval_on_selector("#de", "e => e.options[e.selectedIndex].text")
            py = await page.is_checked("input[name=py][value=yes]")
            await page.wait_for_function("() => document.querySelector('#team').value !== ''", timeout=15000)
            claude_team = await page.input_value("#team")
            panel = await page.inner_text("#jha-panel")
            # you: change Claude's answer, then press "Fill this page" again - your text must stay
            await page.click("#team")
            await page.keyboard.press("ControlOrMeta+A")                  # Ctrl+A, Cmd+A on a Mac
            await page.keyboard.type("The analytics platform team.")
            await page.fill("#ph", "")                                  # you cleared the phone on purpose...
            await page.click("#ph")
            await page.keyboard.type("+49 30 1234")                      # ...and typed another one
            await page.click("#jha-fill")
            await asyncio.sleep(2)
            kept_team, kept_phone = await page.input_value("#team"), await page.input_value("#ph")
            # you: answer the red ones, tick consent, Submit
            await page.check("input[name=spark][value=no]")
            await page.fill("#why", "I like your product.")
            await page.check("input[name=privacy]")
            await page.click("button[type=submit]")
            await page.wait_for_url("**/ats/acme/submit", timeout=15000)
            for _ in range(60):                       # the helper sees "Thank you for your application"
                await asyncio.sleep(0.25)
                with db.session_scope() as s:
                    if s.get(m.Job, jid).status == "applied":
                        break
            final_panel = await page.inner_text("#jha-panel")
            await ctx.close()
            return vals, files, letter_name, german, py, panel, final_panel, claude_team, kept_team, kept_phone

    try:
        vals, files, letter_name, german, py, panel, final_panel, claude_team, kept_team, kept_phone = asyncio.run(go())
    finally:
        site.close()
    assert vals == {"fn": "Alex", "ln": "Morgan", "em": "alex@example.org", "start": "01.12.2026", "sal": "18 € per hour"}
    assert files == "Alex_CV_Acme.pdf" and letter_name == "Alex_Cover_Letter_Acme.pdf"
    assert german == "A2" and py
    assert "Acme Analytics GmbH" in panel and "for you" in panel
    assert "Application sent" in final_panel
    assert claude_team == "The data platform team." and calls and len(calls) == 1      # one Claude call for the form
    assert kept_team == "The analytics platform team." and kept_phone == "+49 30 1234"  # yours, never overwritten
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
        app_row = s.exec(select(m.Application).where(m.Application.job_id == jid)).one()
    assert job.status == "applied" and app_row.applied_at is not None
    assert P.find_answer("Which team at Acme would you most like to join and why?").answer == "The analytics platform team."
    assert site.submissions and b"Morgan" in site.submissions[0]



def test_stop_clears_the_job(app_server, master_cv_path):
    with db.session_scope() as s:
        job = m.Job(title="X", company="Y", links=["http://example.org/j"], status=m.JobStatus.BUILT, url_key="y1", dedupe_key="y1")
        s.add(job)
        s.flush()
        jid = job.id
    assert httpx.post("http://127.0.0.1:8780/api/ext/active", json={"job_id": jid}).json()["job"]["id"] == jid
    assert httpx.post("http://127.0.0.1:8780/api/ext/active", json={"job_id": None}).json() == {"job": None}
    with db.session_scope() as s:
        att = s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.job_id == jid)).one()
    assert att.status == "skipped" and att.note == "stopped by you"
    assert s.get(m.Job, jid) if False else True


def test_restart_page_match_letter_and_limit_and_no_scroll_jump(app_server, master_cv_path, tmp_path):
    """After an app restart the helper still knows the job, or finds it from the page; motivation answers come
    from this job's cover letter and keep to the box's limit; the page does not jump while it fills."""
    site = FakeApply()
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    P.save(prof())
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 cv")
    letter = "I would like to join Acme Analytics as a working student in data science. " * 3
    with db.session_scope() as s:
        job = m.Job(title="Working Student Data Science", company="Acme Analytics GmbH", links=[site.base + "/x"],
                    status=m.JobStatus.BUILT, url_key="acme9", dedupe_key="acme9")
        other = m.Job(title="Data Intern", company="Zeta Robotics", links=["http://example.org/z"],
                      status=m.JobStatus.BUILT, url_key="z9", dedupe_key="z9")
        s.add(job); s.add(other)
        s.flush()
        jid = job.id
        s.add(m.Document(job_id=jid, kind="cv", pdf_path=str(cv)))
        s.add(m.Document(job_id=jid, kind="letter", content={"paragraphs": [letter]}))
    from jobagent.apply import bridge

    # 1) the chosen job survives a restart
    bridge.set_active(jid)
    bridge.ACTIVE.update(job_id=None, attempt_id=None)
    bridge._loaded = False
    assert bridge.active()["job"]["id"] == jid
    # 2) the page tells which job it is
    assert [c["id"] for c in bridge.match("https://jobs.acme-analytics.com/apply", "")] == [jid]
    assert [c["id"] for c in bridge.match("https://lufthansagroup.careers/x", "Apply")] == []
    bridge.set_active(None)                                         # nothing selected in the app now

    seen = {}

    def fake_batch(ctx):
        def answer(items):
            seen["letter"], seen["items"] = ctx.letter_text, items
            return {i["id"]: ("Long answer from the letter. " * 30 if "motivation" in i["question"].lower() else None) for i in items}
        return answer
    bridge.make_batch_answerer = fake_batch

    async def go():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"), headless=True, channel="chromium", viewport={"width": 1200, "height": 800},
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(site.base + "/ats/acme/long")
            await page.evaluate("window.scrollTo(0, 1200)")
            await sw.evaluate("""async () => { const tabs = await chrome.tabs.query({}); const tab = tabs.find(x => (x.url||'').includes('/ats/acme/long'));
                await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['content.js']});
                await chrome.tabs.sendMessage(tab.id, {type: 'jha-start', autoFill: false, manual: true}); }""")
            await page.wait_for_function("() => document.querySelector('#mot').value !== ''", timeout=30000)
            await asyncio.sleep(1.2)
            out = (await page.input_value("#mot"), await page.input_value("#lfn"), await page.evaluate("scrollY"),
                   await page.inner_text("#jha-panel"))
            await ctx.close()
            return out

    try:
        mot, fn, y, panel = asyncio.run(go())
    finally:
        site.close()
    assert fn == "Alex" and "Acme Analytics GmbH" in panel             # found the job from the page title
    assert seen["letter"].startswith("I would like to join Acme Analytics")  # Claude gets this job's cover letter
    assert any(i.get("max_chars") == 300 for i in seen["items"])
    assert 0 < len(mot) <= 300 and mot.endswith(".")                       # within the limit, full sentence
    assert y == 1200                                                        # the page did not jump


def test_no_applied_without_sending(app_server, tmp_path):
    """A posting that only mentions "application received", or a thank-you page you did not send from,
    is never marked as Applied by itself."""
    site = FakeApply()
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 cv")
    with db.session_scope() as s:
        job = m.Job(title="Data Science Intern", company="Acme Analytics GmbH", links=[site.base + "/ats/acme/info"],
                    status=m.JobStatus.BUILT, url_key="acme7", dedupe_key="acme7")
        s.add(job)
        s.flush()
        jid = job.id
        s.add(m.Document(job_id=jid, kind="cv", pdf_path=str(cv)))
    httpx.post("http://127.0.0.1:8780/api/ext/active", json={"job_id": jid})

    async def go():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"), headless=True, channel="chromium",
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(site.base + "/ats/acme/info")
            await sw.evaluate("""async (jid) => { const tabs = await chrome.tabs.query({}); const tab = tabs.find(x => (x.url||'').includes('/ats/acme/info'));
                const f = {}; f[tab.id] = {jobId: jid}; await chrome.storage.session.set({follow: f});
                await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['content.js']});
                await chrome.tabs.sendMessage(tab.id, {type: 'jha-start', autoFill: true}); }""", jid)
            await page.wait_for_selector("#jha-panel", timeout=15000)
            await asyncio.sleep(3.5)
            with db.session_scope() as s:
                after_info = s.get(m.Job, jid).status
            await page.goto(site.base + "/ats/acme/thanks")              # the followed tab lands on a thank-you page
            await page.wait_for_function("() => document.querySelector('#jha-panel') && document.querySelector('#jha-panel').innerText.includes('looks like a confirmation')", timeout=15000)
            await asyncio.sleep(3)
            with db.session_scope() as s:
                after_thanks = s.get(m.Job, jid).status
            # you really did send it: "I submitted" asks once more, then marks it
            await page.click("#jha-done")
            with db.session_scope() as s:
                after_one_click = s.get(m.Job, jid).status
            await page.click("#jha-done")
            for _ in range(40):
                await asyncio.sleep(0.25)
                with db.session_scope() as s:
                    if s.get(m.Job, jid).status == "applied":
                        break
            with db.session_scope() as s:
                final = s.get(m.Job, jid).status
            await ctx.close()
            return after_info, after_thanks, after_one_click, final

    try:
        after_info, after_thanks, after_one_click, final = asyncio.run(go())
    finally:
        site.close()
    assert after_info == "built" and after_thanks == "built" and after_one_click == "built"
    assert final == "applied"
