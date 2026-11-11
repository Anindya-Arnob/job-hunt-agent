"""Gmail watcher: confirmations, rejections, interviews and job alerts update the tracker and job board."""
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from jobagent import db, models as m
from jobagent.mail import reader, service, sort


def mk(uid, frm, name, subject, body, html=False, days_ago=0, unsub=False):
    msg = EmailMessage()
    msg["From"] = f'"{name}" <{frm}>' if name else frm
    msg["To"] = "me@gmail.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<msg{uid}@test>"
    msg["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago, minutes=100 - uid))
    if unsub:
        msg["List-Unsubscribe"] = "<mailto:x@y>"
    if html:
        msg.set_content("view in browser")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    return uid, msg.as_bytes()


class FakeImap:
    PASSWORD = "abcdabcdabcdabcd"

    def __init__(self, mails):
        self.mails = dict(mails)
        self.fetched_full = []

    def login(self, user, pw):
        if pw != self.PASSWORD:
            raise reader.imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    def select(self, box, readonly=False):
        assert readonly                                   # never changes your mailbox
        return "OK", [b"1"]

    def status(self, box, what):
        return "OK", [b'INBOX (UIDVALIDITY 777)']

    def uid(self, cmd, *args):
        if cmd == "search":
            q = args[1]
            if q.startswith("UID"):
                lo = int(q.split()[1].split(":")[0])
                ids = [u for u in self.mails if u >= lo] or [max(self.mails)]
            else:
                ids = list(self.mails)
            return "OK", [" ".join(str(u) for u in sorted(ids)).encode()]
        if cmd == "fetch":
            ids, what = args
            out = []
            for u in str(ids).split(","):
                raw = self.mails[int(u)]
                if "HEADER.FIELDS" in what:
                    head = raw.split(b"\n\n", 1)[0] + b"\n\n"
                    out.append((f"{u} (UID {u} BODY[HEADER.FIELDS] {{1}}".encode(), head))
                else:
                    self.fetched_full.append(int(u))
                    out.append((f"{u} (UID {u} BODY[] {{1}}".encode(), raw))
                out.append(b")")
            return "OK", out

    def logout(self):
        pass


STEPSTONE = """<html><body><p>Hallo Alex, schau dir diese Stellen an!</p>
<div><a href="https://click.stepstone.de/f/a/AAA">Werkstudent Machine Learning (m/w/d)</a></div><div>Nordlicht AI</div><div>Berlin</div>
<div><a href="https://click.stepstone.de/f/a/AAA">Ich bin interessiert</a></div>
<div><a href="https://click.stepstone.de/f/a/BBB">Praktikum Data Science (w/m/d)</a></div><div>Blau Analytics GmbH</div><div>Hamburg</div>
<div><a href="https://click.stepstone.de/f/a/BBB">Ich bin interessiert</a></div>
<p><a href="https://www.stepstone.de/abmelden">Abmelden</a></p></body></html>"""


@pytest.fixture
def mailbox(monkeypatch):
    with db.session_scope() as s:
        a = m.Job(title="Data Science Intern", company="Acme Analytics GmbH", links=["https://acme.example/1"],
                  status=m.JobStatus.BUILT, url_key="acme.example/1", dedupe_key="acme|ds")
        b = m.Job(title="Working Student Machine Learning", company="Zeta Robotics", links=["https://zeta.example/2"],
                  status=m.JobStatus.APPLIED, url_key="zeta.example/2", dedupe_key="zeta|ml")
        s.add(a); s.add(b)
        s.flush()
        ids = (a.id, b.id)
        s.add(m.Application(job_id=b.id))
    box = FakeImap([
        mk(1, "job.abc.acme@recruitee-email.com", "Acme Analytics GmbH/Data Science Intern", "We have received your application!",
           "Hi Alex, Thank you for your application as Data Science Intern and your interest in Acme Analytics GmbH."),
        mk(2, "careers@zeta-robotics.com", "Zeta Robotics Recruiting", "Ihre Bewerbung: Working Student Machine Learning",
           "Guten Tag, vielen Dank für Ihre Bewerbung. Leider müssen wir Ihnen mitteilen, dass wir uns für andere Kandidaten entschieden haben."),
        mk(3, "jobs@beta-labs.de", "Beta Labs HR", "Einladung zum Vorstellungsgespräch – Werkstudent KI (m/w/d)",
           "Hallo Alex, vielen Dank für Ihre Bewerbung als Werkstudent KI (m/w/d). Wir möchten Sie gerne zu einem "
           "Vorstellungsgespräch einladen. Bitte wählen Sie einen Termin: https://calendly.com/beta/30min"),
        mk(4, "career@services.portal.de", "Big Corp Recruiting-Team", "Welcome to the Big Corp Career Cockpit",
           "Thank you for your registration. Your temporary password is: Zx81SECRETpw Please change it."),
        mk(5, "info@jobagent.stepstone.de", "Lisa von Stepstone", "Alex, unsere Empfehlung: neue Jobs für dich", STEPSTONE,
           html=True, unsub=True),
        mk(6, "news@shop.example", "Shop", "Your weekly deals", "Buy things now"),
        mk(7, "support@jobscan.co", "James Hu", "HireReady Vol. 110: interview tips", "Hacks for clearer interview answers.",
           unsub=True),
        mk(8, "info@jobagent.stepstone.de", "Lisa von Stepstone", "Neue Jobs für dich", STEPSTONE, html=True, days_ago=30),
    ])
    monkeypatch.setattr(reader, "IMAP_FACTORY", lambda: box)

    async def same(urls):
        return {u: u.replace("click.stepstone.de/f/a/", "www.stepstone.de/stellenangebote--") + ".html" for u in urls}
    monkeypatch.setattr(service, "RESOLVE", same)

    async def no_fetch(jobs, client=None, limit=80):
        return 0
    import jobagent.feeds.websearch as W
    monkeypatch.setattr(W, "fetch_descriptions", no_fetch)
    def fixed_scores(ids, fp):                               # stands in for classify + score against your CV
        with db.session_scope() as s:
            for jid in ids:
                j = s.get(m.Job, jid)
                s.add(m.Score(job_id=jid, total=SCORES.get(j.title, 80), verdict="apply"))
                j.status = m.JobStatus.SCORED
                s.add(j)
    monkeypatch.setattr(service, "_classify_and_score", fixed_scores)
    SCORES.clear()
    service.save_config({"address": "me@gmail.com", "app_password": FakeImap.PASSWORD, "enabled": True})
    return box, ids


SCORES: dict = {}


def test_mail_updates_tracker_and_adds_jobs(mailbox):
    box, (acme, zeta) = mailbox
    res = service.check_now()
    assert res["ok"], res
    with db.session_scope() as s:
        assert s.get(m.Job, acme).status == "applied"
        app = s.exec(select(m.Application).where(m.Application.job_id == acme)).one()
        assert app.applied_at and "confirmed by Acme Analytics GmbH" in app.notes
        assert s.get(m.Job, zeta).status == "rejected"
        beta = s.exec(select(m.Job).where(m.Job.company == "Beta Labs")).one()       # applied outside the app
        assert beta.status == "interview" and "Werkstudent KI" in beta.title
        evs = {e.uid: e for e in s.exec(select(m.MailEvent)).all()}
        assert evs[1].kind == "confirmation" and evs[1].job_id == acme
        assert evs[2].kind == "rejection" and evs[2].job_id == zeta and evs[2].effect == "marked Rejected"
        assert evs[3].kind == "interview" and not evs[3].done
        assert evs[4].kind == "account" and evs[4].excerpt == ""
        assert 6 not in evs and 7 not in evs                       # shop mail and newsletter are not kept
        assert 6 not in box.fetched_full                           # not even downloaded
        assert evs[5].kind == "job_alert" and len(evs[5].job_ids) == 2
        assert evs[5].effect.startswith("2 good match(es) added to your Job board")
        assert evs[8].effect == "older alert - jobs not added"
        found = {j.title: j for j in s.exec(select(m.Job).where(m.Job.id.in_(evs[5].job_ids))).all()}
        assert set(found) == {"Werkstudent Machine Learning (m/w/d)", "Praktikum Data Science (w/m/d)"}
        assert found["Praktikum Data Science (w/m/d)"].company == "Blau Analytics GmbH"
        assert found["Praktikum Data Science (w/m/d)"].links[0].startswith("https://www.stepstone.de/stellenangebote--")
        assert found["Praktikum Data Science (w/m/d)"].sources == ["email:stepstone"]
        # the password is stored nowhere
        assert not any("Zx81SECRETpw" in (e.excerpt + e.summary + e.action) for e in evs.values())
    # nothing new -> nothing twice
    res2 = service.check_now()
    assert res2["ok"] and res2["read"] == 0
    with db.session_scope() as s:
        assert len(s.exec(select(m.MailEvent)).all()) == 6


def test_wrong_password_is_explained(mailbox):
    service.save_config({"app_password": "wrong"})
    res = service.check_now()
    assert not res["ok"] and "app password" in res["error"]
    assert "app password" in service.config()["last_error"]


def test_mail_api(mailbox):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    service.check_now()
    with TestClient(app) as c:
        st = c.get("/api/mail/status").json()
        assert st["has_password"] and "app_password" not in st and st["todo"] == 1
        todo = c.get("/api/mail/events?kind=todo").json()
        assert len(todo) == 1 and todo[0]["kind"] == "interview" and todo[0]["gmail"].startswith("https://mail.google.com")
        c.post(f"/api/mail/events/{todo[0]['id']}/done")
        assert c.get("/api/mail/events?kind=todo").json() == []
        r = c.put("/api/mail/settings", json={"address": "me@gmail.com", "app_password": "nope"})
        assert r.status_code == 400 and "app password" in r.json()["detail"]


def test_rules_on_real_world_samples():
    def k(frm, name, subj, text):
        return sort.rule_kind(reader.Mail(0, "x", None, frm, name, subj, text))
    assert k("careers@hr.stihl.com", "STIHL Career", "Ihre Bewerbung: Praktikum KI (58417)",
             "wir bedanken uns für Ihre Bewerbung. Leider ...") == "rejection"
    assert k("noreply@glassdoor.com", "Glassdoor-Jobs", "Werkstudent bei X: Diese Stelle würde zu dir passen", "") == "job_alert"
    assert k("info@jobagent.stepstone.de", "Lisa von Stepstone", "Du hast gute Chancen auf ein Interview für diesen Job", "") == "job_alert"
    assert k("no-reply@ashbyhq.com", "Almetra Hiring Team", "Thanks for applying to Almetra!",
             "Thank you for applying for the AI Onboarding Intern role at Almetra! We will review your application and get "
             "back to you if there are next steps") == "confirmation"
    assert k("hr@x.de", "X", "Ihre Bewerbung", "Vielen Dank für Ihre Bewerbung. Wenn Ihr Profil passt, melden wir uns für ein Gespräch.") == "confirmation"
    assert sort.redact("Your temporary password is: ntzFQNgkJ7 thanks") == "Your temporary password is: [hidden] thanks"


def test_newest_mails_first_then_the_older_ones(mailbox, monkeypatch):
    """A big mailbox: today's mails are read in the first round, older ones follow."""
    box, _ = mailbox
    box.mails = dict([mk(100 + i, f"careers@firma{i}.de", f"Firma{i} Recruiting", "Eingangsbestätigung",
                         f"Vielen Dank für Ihre Bewerbung als Werkstudent Data (m/w/d) bei Firma{i}.", days_ago=40 - i)
                      for i in range(40)])
    seen_rounds = []
    orig = reader.fetch

    def small(*a, **k):
        k["body_limit"] = 15
        out = orig(*a, **k)
        seen_rounds.append(sorted(x.uid for x in out[0]))
        return out
    monkeypatch.setattr(reader, "fetch", small)
    res = service.check_now()
    assert res["ok"] and res["read"] == 40 and res["older_waiting"] == 0
    assert seen_rounds[0] == list(range(125, 140))          # round 1 = the 15 newest
    assert seen_rounds[-1][0] == 100                          # the oldest come last


def test_an_older_mail_does_not_undo_a_newer_one(mailbox):
    box, (acme, zeta) = mailbox
    box.mails = dict([
        mk(201, "careers@zeta-robotics.com", "Zeta Robotics Recruiting", "Einladung zum Vorstellungsgespräch",
           "Vielen Dank für Ihre Bewerbung. Wir möchten Sie gerne zu einem Vorstellungsgespräch einladen.", days_ago=5),
        mk(202, "careers@zeta-robotics.com", "Zeta Robotics Recruiting", "Ihre Bewerbung",
           "Vielen Dank für Ihre Bewerbung. Leider haben wir uns für andere Kandidaten entschieden.", days_ago=1)])
    orig = reader.fetch
    reader_fetch = lambda *a, **k: orig(*a, **{**k, "body_limit": 1})
    import jobagent.mail.reader as R
    R.fetch = reader_fetch                                    # newest (rejection) first, interview later
    try:
        service.check_now()
    finally:
        R.fetch = orig
    with db.session_scope() as s:
        assert s.get(m.Job, zeta).status == "rejected"


def test_only_good_matches_from_mails_are_listed(mailbox):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    SCORES.update({"Werkstudent Machine Learning (m/w/d)": 82, "Praktikum Data Science (w/m/d)": 41})
    service.check_now()
    with db.session_scope() as s:
        ev = s.exec(select(m.MailEvent).where(m.MailEvent.uid == 5)).one()
        assert ev.effect == "1 good match(es) added to your Job board · 1 low match(es) hidden"
        low = s.exec(select(m.Job).where(m.Job.title == "Praktikum Data Science (w/m/d)")).one()
        assert low.status == "excluded" and low.exclude_reason == "Low match from email (score 41 < 60)"
    with TestClient(app) as c:
        board = c.get("/api/jobs?tab=email").json()
        assert [j["title"] for j in board] == ["Werkstudent Machine Learning (m/w/d)"]
        assert c.get("/api/jobs/counts").json()["email"] == 1
        alert = [e for e in c.get("/api/mail/events?kind=job_alert").json() if e["uid"] == 5][0]
        assert [x["score"] for x in alert["matches"]] == [82]
        # a lower bar: the hidden one comes back
        c.put("/api/mail/settings", json={"min_score": 40})
        assert len(c.get("/api/jobs?tab=email").json()) == 2
