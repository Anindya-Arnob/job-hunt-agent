"""Apply assistant: fills application forms from My details, saved answers and the CV; you press Submit."""
import asyncio
from pathlib import Path

import pytest
from sqlmodel import select

from jobagent import db, models as m
from jobagent.apply import filler, profile as P, session as S
from jobagent.apply.fields import Context, Field, choose_option, decide
from jobagent.cv import service as CV

from fake_apply import FakeApply


def prof(**kw):
    p = P.ApplicantProfile(first_name="Alex", last_name="Morgan", email="alex@example.org",
                           phone="+49 151 000000", linkedin="https://linkedin.com/in/example", city="Berlin",
                           earliest_start="01.12.2026", work_authorized="Yes", german_level="A2",
                           english_level="C1", hours_per_week="20")
    p.salary.working_student_hourly = "18"
    p.salary.internship_monthly = "1800"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def ctx(**kw):
    c = Context(profile=prof(), job_type="Working Student", cv_pdf="/x/cv.pdf", letter_pdf="/x/letter.pdf",
                cv_text="Python, SQL, PyTorch, LangChain. Built RAG pipelines.", company="Acme", title="Werkstudent Data")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ------------------------------------------------------------------ decisions ---
def test_details_and_options():
    c = ctx()
    d = lambda **f: decide(Field(fid="x", tag=f.pop("tag", "input"), **f), c)
    assert d(label="First name").value == "Alex" and d(label="Nachname").value == "Morgan"
    assert d(label="E-Mail-Adresse", type="email").value == "alex@example.org"
    assert d(label="Frühestes Eintrittsdatum").value == "01.12.2026"
    assert d(label="German language level", tag="select", options=["Please select", "A1", "A2", "B1"]).value == "A2"
    assert d(label="English", tag="select", options=["Basic", "Good", "Fluent", "Native"]).value == "Fluent"     # C1
    assert d(label="Are you authorized to work in Germany?", type="radio", options=["Yes", "No"]).value == "Yes"
    assert d(label="Gender", tag="select", options=["Female", "Male", "Prefer not to say"]).value == "Prefer not to say"
    assert d(label="I agree to the privacy policy", type="checkbox").action == "consent"
    assert d(label="Nationality").action == "leave"                       # not set -> yours, never guessed


def test_salary_follows_what_the_form_asks():
    c = ctx()
    sal = lambda label, **kw: decide(Field(fid="s", tag="input", label=label, **kw), c).value
    assert sal("Expected salary (€ per hour)") == "18 € per hour"
    assert sal("Gehaltsvorstellung (brutto, jährlich)") == f"{round(18 * 20 * 52 / 12) * 12} € per year"
    assert sal("Expected monthly salary", type="number") == str(round(18 * 20 * 52 / 12))
    assert decide(Field(fid="s", tag="input", label="Gehaltsvorstellung"), ctx(job_type="Internship")).value == "1800 € per month"
    empty = ctx()
    empty.profile.salary.working_student_hourly = ""
    empty.profile.salary.internship_monthly = ""
    assert decide(Field(fid="s", tag="input", label="Salary expectation"), empty).action == "leave"


def test_skills_only_from_your_cv():
    c = ctx()
    yes = decide(Field(fid="a", tag="input", type="radio", label="Do you have experience with Python?", options=["Yes", "No"]), c)
    no = decide(Field(fid="b", tag="input", type="radio", label="Do you have experience with Spark?", options=["Yes", "No"]), c)
    assert (yes.action, yes.value, yes.source) == ("fill", "Yes", "cv")
    assert no.action == "leave" and "Spark".lower() in no.why.lower()
    yrs = decide(Field(fid="c", tag="input", label="How many years of experience with Python?"), c)
    assert yrs.action == "leave"


def test_saved_answers_and_drafts():
    c = ctx(drafter=lambda q: "Because my RAG projects match your team.")
    P.remember("Why do you want to work at Acme Analytics?", "I love your product.")
    got = decide(Field(fid="w", tag="textarea", label="Why do you want to work at Acme Analytics?"), c)
    assert (got.action, got.value, got.source) == ("fill", "I love your product.", "saved")
    new = decide(Field(fid="w2", tag="textarea", label="Describe a data project you are proud of"), c)
    assert new.action == "draft" and new.source == "claude"
    assert decide(Field(fid="w3", tag="textarea", label="Describe a data project"), ctx()).action == "leave"   # no Claude
    assert choose_option(["Ja", "Nein"], "Yes") == "Ja"


def test_files_go_to_the_right_inputs():
    c = ctx()
    assert decide(Field(fid="f1", tag="input", type="file", label="Lebenslauf"), c).value == "/x/cv.pdf"
    assert decide(Field(fid="f2", tag="input", type="file", label="Anschreiben"), c).value == "/x/letter.pdf"
    assert decide(Field(fid="f3", tag="input", type="file", label="Zeugnisse"), c, file_index=2).action == "leave"


# --------------------------------------------------------- the real browser ---
@pytest.fixture
def site():
    fa = FakeApply()
    yield fa
    fa.close()


@pytest.fixture
def files(tmp_path):
    cv, letter = tmp_path / "cv.pdf", tmp_path / "letter.pdf"
    cv.write_bytes(b"%PDF-1.4 cv")
    letter.write_bytes(b"%PDF-1.4 letter")
    return str(cv), str(letter)


async def _browser():
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    b = await pw.chromium.launch()
    context = await b.new_context()
    return pw, b, context, await context.new_page()


def test_form_is_filled_in_a_real_browser(site, files):
    async def go():
        pw, b, _, page = await _browser()
        try:
            await page.goto(site.base + "/ats/acme/apply")
            c = ctx(cv_pdf=files[0], letter_pdf=files[1], drafter=lambda q: "Your data team builds what I study.")
            rep, waiting = await filler.fill_page(page, c)
            vals = {k: await page.input_value(f"#{k}") for k in ("fn", "ln", "em", "ph", "li", "start", "sal")}
            german = await page.eval_on_selector("#de", "e => e.options[e.selectedIndex].text")
            auth = await page.is_checked("input[name=auth][value=yes]")
            py = await page.is_checked("input[name=py][value=yes]")
            spark = await page.is_checked("input[name=spark][value=yes]")
            files_set = await page.eval_on_selector("#cv", "e => e.files.length") + await page.eval_on_selector("#cl", "e => e.files.length")
            why = await page.input_value("#why")
            privacy = await page.is_checked("input[name=privacy]")
            return rep, waiting, vals, german, auth, py, spark, files_set, why, privacy
        finally:
            await b.close()
            await pw.stop()

    rep, waiting, vals, german, auth, py, spark, files_set, why, privacy = asyncio.run(go())
    assert vals == {"fn": "Alex", "ln": "Morgan", "em": "alex@example.org", "ph": "+49 151 000000",
                    "li": "https://linkedin.com/in/example", "start": "01.12.2026", "sal": "18 € per hour"}
    assert german == "A2" and auth and py and not spark and files_set == 2
    assert why == "Your data team builds what I study."                       # drafted, amber for you to read
    assert not privacy                                                          # consent is yours
    open_labels = " ".join(o["label"] for o in rep.open)
    assert "Spark" in open_labels and rep.consent
    assert len(rep.uploaded) == 2


def test_whole_session_from_posting_to_applied(site, files, master_cv_path):
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    P.save(prof())
    with db.session_scope() as s:
        job = m.Job(title="Working Student Data Science", company="Acme Analytics GmbH", location="Berlin",
                    job_type="Working Student", links=[site.base + "/board/job/1"], status=m.JobStatus.BUILT,
                    full_description="Python, SQL. Spark nice to have.", url_key="acme1", dedupe_key="acme1")
        s.add(job)
        s.flush()
        jid = job.id
        s.add(m.Score(job_id=jid, total=82, verdict="Apply"))
        s.add(m.Document(job_id=jid, kind="cv", pdf_path=files[0]))
        s.add(m.Document(job_id=jid, kind="letter", pdf_path=files[1]))
    assert [q["id"] for q in S.queue()] == [jid]

    async def go():
        pw, b, context, page = await _browser()

        async def factory():
            return context, page
        S.PAGE_FACTORY = factory
        S.POLL_SECONDS = 0.3
        sess = S.ApplySession()
        sess.start([jid])
        for _ in range(100):                                # wait until the form is filled
            await asyncio.sleep(0.2)
            if sess.state == "waiting":
                break
        assert sess.state == "waiting", sess.state
        assert sess.current["company"] == "Acme Analytics GmbH" and "/ats/acme/apply" in sess.report["form_url"]
        # you: answer what is red, tick consent, press Submit
        await page.fill("#team", "The analytics platform team, because I build data pipelines.")
        await page.check("input[name=spark][value=no]")
        await page.fill("#why", "I like your product.")
        await page.check("input[name=privacy]")
        await asyncio.sleep(0.8)
        await page.click("button[type=submit]")
        for _ in range(100):
            await asyncio.sleep(0.2)
            if not sess.running:
                break
        await b.close()
        await pw.stop()
        return sess

    try:
        sess = asyncio.run(go())
    finally:
        S.PAGE_FACTORY, S.POLL_SECONDS = None, 2.0
    assert sess.history and sess.history[0]["status"] == "applied"
    assert site.submissions and b"Alex" in site.submissions[0]
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
        app_row = s.exec(select(m.Application).where(m.Application.job_id == jid)).one()
        att = s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.job_id == jid)).one()
    assert job.status == "applied" and app_row.applied_at is not None
    assert att.status == "applied" and Path(att.screenshot).exists()
    assert P.find_answer("Which team at Acme would you most like to join and why?").answer.startswith("The analytics platform")
    assert S.queue() == []                                   # not offered again



def test_security_checks_are_always_left_to_you():
    c = ctx(drafter=lambda q: "9")
    for label in ("Security query What is the sum of five and four?", "Sicherheitsabfrage: Wie viel ist 3 + 4?", "Captcha"):
        d = decide(Field(fid="q", tag="input", type="number", label=label), c)
        assert d.action == "leave" and "Security check" in d.why



def test_claude_answers_the_rest_in_one_call():
    from jobagent.apply.fields import claude_rest
    calls = []

    def answerer(items):
        calls.append(items)
        return {i["id"]: {"Which team would you like to join?": "The data platform team.",
                          "Preferred working language": "English",
                          "Do you have experience with Spark?": None}.get(i["question"]) for i in items}

    c = ctx(answerer=answerer)
    fs = [Field(fid="a", tag="input", label="Which team would you like to join?"),
          Field(fid="b", tag="select", label="Preferred working language", options=["Please select", "German", "English"]),
          Field(fid="c", tag="input", type="radio", label="Do you have experience with Spark?", options=["Yes", "No"]),
          Field(fid="d", tag="input", label="Security query: What is the sum of five and four?"),
          Field(fid="e", tag="input", type="checkbox", label="I accept the privacy policy")]
    dec = {f.fid: decide(f, c) for f in fs}
    n = claude_rest(fs, dec, c)
    assert len(calls) == 1 and {i["id"] for i in calls[0]} == {"a", "b", "c"}       # one call, no security/consent
    assert n == 2 and dec["a"].action == "draft" and dec["b"].value == "English"
    assert dec["c"].action == "leave" and dec["d"].action == "leave" and dec["e"].action == "consent"


def test_queue_shows_applied_and_a_wrong_applied_can_be_undone(tmp_path):
    from jobagent.apply import session as AS, bridge
    cvp = tmp_path / "cv.pdf"
    cvp.write_bytes(b"%PDF-1.4")
    with db.session_scope() as s:
        a = m.Job(title="A", company="", links=["https://jobs.firma-a.de/1"], status=m.JobStatus.BUILT, url_key="qa", dedupe_key="qa")
        b = m.Job(title="B", company="Firma B", links=["https://b.example/1"], status=m.JobStatus.BUILT, url_key="qb", dedupe_key="qb")
        s.add(a); s.add(b)
        s.flush()
        ida, idb = a.id, b.id
        for j in (ida, idb):
            s.add(m.Document(job_id=j, kind="cv", pdf_path=str(cvp)))
        s.add(m.ApplyAttempt(job_id=ida, status="skipped", note="stopped by you"))
    # a stopped job comes back in the queue; a job without a company shows its website
    q = {j["id"]: j for j in AS.queue()}
    assert ida in q and q[ida]["company"] == "jobs.firma-a.de" and q[ida]["last_attempt"]["note"] == "stopped by you"
    # B was marked applied by mistake -> still listed with all=True, then undone
    bridge.set_active(idb)
    bridge.done(idb, "https://b.example/thanks")
    assert idb not in {j["id"] for j in AS.queue()}
    allq = {j["id"]: j for j in AS.queue(include_all=True)}
    assert allq[idb]["applied"] and allq[idb]["applied_at"]
    AS.mark_not_applied(idb)
    with db.session_scope() as s:
        assert s.get(m.Job, idb).status == "built"
        assert s.exec(select(m.Application).where(m.Application.job_id == idb)).one().applied_at is None
        assert all(x.status != "applied" for x in s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.job_id == idb)).all())
    assert idb in {j["id"] for j in AS.queue()}
    # and the other way round: you did apply but the app missed it
    AS.mark_applied_by_hand(ida)
    with db.session_scope() as s:
        assert s.get(m.Job, ida).status == "applied"


def test_attempts_left_open_become_not_finished():
    from jobagent.apply import bridge
    with db.session_scope() as s:
        j1 = m.Job(title="1", company="C1", links=["http://x/1"], status=m.JobStatus.BUILT, url_key="o1", dedupe_key="o1")
        j2 = m.Job(title="2", company="C2", links=["http://x/2"], status=m.JobStatus.BUILT, url_key="o2", dedupe_key="o2")
        s.add(j1); s.add(j2)
        s.flush()
        id1, id2 = j1.id, j2.id
        s.add(m.ApplyAttempt(job_id=id1, note="in your browser"))          # left "running" by an old version
    bridge.ACTIVE.update(job_id=None, attempt_id=None)
    bridge.set_active(id2)
    with db.session_scope() as s:
        old = s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.job_id == id1)).one()
    assert old.status == "skipped" and old.note == "not finished"
    bridge.set_active(None)


def test_phone_box_uses_your_number_not_an_old_country_code_answer():
    """A saved answer 'Germany (+49)' (from a country-code dropdown) must not end up in a phone text box."""
    P.save(prof())
    P.remember("Phone", "Germany (+49)", "you")
    ctx = Context(profile=P.load(), job_type="Working Student")
    d = decide(Field(fid="1", tag="input", type="tel", label="Phone"), ctx)
    assert (d.action, d.value) == ("fill", prof().phone)
    # the dropdown version still gets the saved choice
    d2 = decide(Field(fid="2", tag="select", type="select", label="Phone", options=["Germany (+49)", "France (+33)"]), ctx)
    assert d2.value == "Germany (+49)"
