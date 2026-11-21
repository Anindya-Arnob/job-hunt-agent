"""Knock-outs: only what an ad really requires, never 'a plus'; a knock-out stops the build before any token."""
import asyncio

import pytest

from jobagent import db, llm, models as m
from jobagent.apply.profile import ApplicantProfile
from jobagent.steps import knockouts as K
from jobagent.schemas import CVProfile

from profile_fixture import sample_profile
from test_10_build_graph import setup  # noqa: F401  (fixture)


@pytest.mark.parametrize("text,years", [
    ("Mehrjährige, fundierte Erfahrung in der Softwareentwicklung", 2),
    ("3+ years of experience with Python", 3),
    ("Mindestens 5 Jahre Berufserfahrung im Backend", 5),
    ("2-4 years of professional experience", 2),
    ("You have 2 years experience in ML", 2),
    ("Langjährige Erfahrung in der Projektleitung", 4),
    ("Several years of hands-on experience with LLMs", 2),
    ("Erste Erfahrung mit Python ist von Vorteil", None),
    ("First experience with LLMs is a plus", None),
    ("Berufserfahrung von Vorteil", None),
    ("20 hours per week for 6 months", None),
    ("We have 10 years of experience as a company", 10),     # rare; still reads as experience
])
def test_years_the_ad_asks_for(text, years):
    assert K.required_years(text) == years


def _prof(exp_dates=("Dez. 2025 – Feb. 2026",), edu=(("M.Sc. Data Science", "Sep. 2025 – heute"),
                                                       ("B.Tech in Information Technology", "Jun. 2019 – Jul. 2023")),
          langs=(("Englisch", "Fortgeschritten (C1)"), ("Deutsch", "Grundkenntnisse (A2)"))) -> CVProfile:
    p = sample_profile()
    p.experience = [p.experience[0].model_copy(update={"dates": d}) for d in exp_dates]
    p.education = [p.education[0].model_copy(update={"degree": d, "dates": t}) for d, t in edu]
    p.languages = [{"language": a, "level": b} for a, b in langs]
    return CVProfile.model_validate(p.model_dump())


def test_months_of_work_from_the_dates():
    assert K.work_months(_prof()) == 3
    assert K.work_months(_prof(("Jan 2022 – Dec 2022", "Jun 2022 – Mar 2023"))) == 15        # overlap counts once
    assert K.work_months(_prof(("09/2021 – 08/2023",))) == 24
    assert K.work_months(_prof(("Sep 2025 – present",))) >= 14


@pytest.mark.parametrize("text,deg", [
    ("Abgeschlossenes Hochschulstudium der Informatik", "bachelor"),
    ("Bachelor's degree in Computer Science", "bachelor"),
    ("PhD in Machine Learning required", "phd"),
    ("Completed Master's in Data Science", "master"),
    ("Du bist Masterstudent:in der Informatik", None),
    ("Studium der Informatik oder vergleichbar", None),
    ("Abgeschlossenes Studium von Vorteil", None),
])
def test_degree_the_ad_asks_for(text, deg):
    assert K.required_degree(text) == deg


BITS = ("Bitte beachte, dass für diese Stelle eine Arbeitserlaubnis für Deutschland erforderlich ist.\n"
        "Sprache: Deutsch - Fließend (C1), Englisch - Konversationssicher (B1-B2)\n"
        "* Mehrjährige, fundierte Erfahrung in der Softwareentwicklung - Sicherer Umgang mit APIs")


def test_bits_shows_two_knock_outs_and_the_permit_you_have():
    reqs = {r["key"]: r for r in K.check(BITS, _prof(), ApplicantProfile(work_authorized="Yes", needs_sponsorship="No"))}
    assert not reqs["language:german"]["ok"] and reqs["language:english"]["ok"]
    assert not reqs["experience"]["ok"] and reqs["experience"]["have"] == "3 months of work"
    assert reqs["permit"]["ok"] and not reqs["permit"].get("unknown")
    assert K.knockouts(BITS, _prof(), ApplicantProfile(work_authorized="Yes")) == [
        "German: the ad asks for C1, your CV says A2",
        "Experience: the ad asks for 2+ years, your CV shows 3 months of work"]


def test_permit_and_student_status_come_from_my_details():
    permit = "A valid work permit for Germany is required."
    assert K.knockouts(permit, _prof(), ApplicantProfile(work_authorized="No"))
    assert K.knockouts(permit, _prof(), ApplicantProfile(work_authorized="Yes", needs_sponsorship="Yes"))
    unknown = K.check(permit, _prof(), ApplicantProfile())
    assert unknown[0]["ok"] and unknown[0]["unknown"]                    # not filled in: asked, not a knock-out
    student = "Du bist an einer Hochschule eingeschrieben."
    assert K.knockouts(student, _prof(), ApplicantProfile(enrolled_student="No"))
    assert not K.knockouts(student, _prof(), ApplicantProfile(enrolled_student="Yes"))


def test_degree_knock_out_uses_finished_degrees_only():
    assert not K.knockouts("Abgeschlossenes Hochschulstudium", _prof(), None)        # B.Tech is finished
    assert K.knockouts("Completed Master's degree required", _prof(), None)          # the M.Sc. is not yet
    assert K.knockouts("PhD required", _prof(), None)


def test_nothing_when_the_ad_asks_for_nothing():
    ad = "Working student Data Science, 20 hours per week. Python, SQL. German is a plus."
    assert K.check(ad, _prof(), ApplicantProfile()) == [
        r for r in K.check(ad, _prof(), ApplicantProfile()) if r["key"] == "language:english"]
    assert K.knockouts(ad, _prof(), ApplicantProfile()) == []


def test_a_knock_out_stops_the_build_before_any_token(setup, monkeypatch):  # noqa: F811
    from jobagent.graphs import build_graph as B
    jid = setup["good"]
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        j.full_description += " Fluent German (C1) is required. At least 5 years of experience."
        s.add(j)
    calls = []
    real = llm.structured
    monkeypatch.setattr(llm, "structured", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    out = asyncio.run(B.start_build([jid]))
    r = out["results"][0]
    assert r["status"] == "stopped" and "German" in r["reason"] and "5+ years" in r["reason"]
    assert calls == []                                                   # no Claude call at all
    out = asyncio.run(B.start_build([jid], force=[jid]))                 # "Build anyway"
    assert out["results"][0]["status"] == "built"


def test_api_stops_and_shows_knock_outs(setup):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    jid = setup["good"]
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        j.full_description += " Verhandlungssicheres Deutsch (C2) erforderlich."
        s.add(j)
    with TestClient(app) as c:
        r = c.post("/api/build", json={"job_ids": [jid]}).json()
        assert r["run_id"] is None and r["stopped"][0]["job_id"] == jid and "Nordlicht" in r["stopped"][0]["job"]
        row = next(x for x in c.get("/api/jobs").json() if x["id"] == jid)
        assert row["verdict"] == "Skip" and any("German" in k for k in row["knockouts"])
        assert c.get(f"/api/jobs/{jid}").json()["knockouts"]
        assert c.post("/api/build", json={"job_ids": [jid], "force": True}).json()["run_id"]
