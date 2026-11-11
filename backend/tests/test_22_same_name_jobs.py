"""Two jobs with the same title and no company (XING) used to share one CV file name: building the second
said 'already built' and nothing happened. A run cut off by closing the app stayed 'running' forever."""
import asyncio
from pathlib import Path

import pytest

from jobagent import db, models as m
from jobagent.api import runs
from jobagent.cv import service as CV
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import build_graph as B
from jobagent.steps.clean import fill_missing_companies
from jobagent.steps.score import score_job, store_score
from jobagent.textutil import guess_company

XING = ("Skip navigation\n\nFind jobs\n\nWerkstudent:in AI & Automation - Energy (m/w/d)\n"
        "Werkstudent:in AI & Automation - Energy (m/w/d)\n\n1KOMMA5°\nRenewable energy\n\nHamburg\n\n"
        "About this job\nPython, LLMs, RAG, n8n, REST APIs, AI agents. Working student, 20 hours per week.\n\n"
        "About the company\n\n1KOMMA5°\nRenewable energy\n1-10 employees\n")


def test_company_is_read_from_the_xing_page():
    t = "Werkstudent:in AI & Automation - Energy (m/w/d)"
    assert guess_company(t, XING) == "1KOMMA5°"
    assert guess_company(t, XING.split("About the company")[0]) == "1KOMMA5°"     # from the title block
    assert guess_company("Praktikum", "Erscheinungsdatum\n\nNeuer als 24h66\n\nGehalt") == ""
    assert guess_company("X", "About the company\n\nXING\nfoo\n") == ""


def test_missing_companies_are_filled_in_at_start():
    with db.session_scope() as s:
        j = m.Job(company="", title="Werkstudent:in AI & Automation - Energy (m/w/d)", full_description=XING)
        s.add(j); s.flush(); jid = j.id
    assert fill_missing_companies() >= 1
    with db.session_scope() as s:
        assert s.get(m.Job, jid).company == "1KOMMA5°"


def test_a_run_left_running_is_closed_at_start():
    with db.session_scope() as s:
        r = m.Run(kind="build", status="running"); s.add(r); s.flush(); rid = r.id
    assert runs.close_stale_runs() >= 1
    with db.session_scope() as s:
        r = s.get(m.Run, rid)
        assert r.status == "failed" and r.finished_at and "closed" in r.errors[-1]


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_second_job_with_the_same_name_gets_its_own_files(master_cv_path):
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    prof, vid = CV.get_main()
    ids = []
    with db.session_scope() as s:
        for city in ("Berlin", "Hamburg"):
            j = m.Job(company="", title="Werkstudent:in AI & Automation - Energy (m/w/d)", location=city,
                      full_description=XING.replace("About the company\n\n1KOMMA5°\n", "").replace("1KOMMA5°\nRenewable", "\nRenewable"),
                      job_type="Working Student", status="scored", links=[f"https://x/{city}"],
                      keywords=["LLM", "RAG", "Python"])
            s.add(j); s.flush(); ids.append(j.id)
    for jid in ids:
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
        store_score(jid, score_job(job, prof), vid)
    first = asyncio.run(B.start_build([ids[0]]))["results"][0]
    assert first["status"].startswith("built")
    second = asyncio.run(B.start_build([ids[1]]))["results"][0]
    assert second["status"].startswith("built"), second
    assert second["cv_file"] != first["cv_file"] and "Hamburg" in second["cv_file"]
    assert Path(first["folder"], first["cv_file"]).exists() and Path(second["folder"], second["cv_file"]).exists()
    again = asyncio.run(B.start_build([ids[1]]))["results"][0]
    assert again["status"] == "already built" and again["cv_file"] == second["cv_file"]
