import time

import httpx
import pytest
from fastapi.testclient import TestClient

from jobagent.agents import sites as S
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import search_graph as G

from fake_sites import FakeReader, FakeSites
from test_07_search_graph import feed_handler

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")


@pytest.fixture
def client(monkeypatch):
    from jobagent.api.main import app
    with FakeSites() as fs:
        for site, prefix in [("linkedin", "/li"), ("glassdoor", "/gd"), ("xing", "/xg")]:
            monkeypatch.setattr(S.SITES[site], "base", fs.base + prefix)
        monkeypatch.setattr(G, "READER_FACTORY", lambda s, mode: FakeReader(s, mode, fs.base))
        monkeypatch.setattr(G, "FEED_TRANSPORT", httpx.MockTransport(feed_handler))
        orig = G.planner.plan_site_queries
        monkeypatch.setattr(G.planner, "plan_site_queries", lambda *a, **k: orig(*a, **k)[:1])
        monkeypatch.setattr(G.planner, "plan_feed_queries", lambda *a, **k: [{"keywords": "Werkstudent Data", "location": "Berlin"}])
        with TestClient(app) as c:
            yield c


def wait_run(c, run_id, statuses=("done", "failed", "paused"), timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/runs/{run_id}").json()
        if r["run"]["status"] in statuses:
            return r
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish: {r['run']}")


def test_full_api_flow(client, master_cv_path):
    c = client
    st = c.get("/api/status").json()
    assert st["has_main_cv"] is False and st["settings"]["effective_llm_mode"] == "fake"
    assert c.get("/api/dashboard").json()["counts"]["new"] == 0

    # --- My CV: upload -> review -> confirm -> preview
    with open(master_cv_path, "rb") as f:
        draft = c.post("/api/cv/upload", files={"file": ("Alex CV.docx", f)}).json()
    assert draft["profile"]["name"] == "Alex Morgan"
    r = c.post("/api/cv/confirm", json={"profile": draft["profile"], "source_file": draft["source_file"],
                                        "name": "Main CV"}).json()
    pid = r["profile_id"]
    cv = c.get("/api/cv").json()
    assert cv["main"]["name"] == "Alex Morgan" and cv["profiles"][0]["is_main"]
    prev = c.post("/api/cv/preview", json={"profile": cv["main"]}).json()
    assert prev["pages"] >= 2      # the full master CV is longer than one page
    assert c.get("/api/files", params={"path": prev["pdf_path"]}).status_code == 200
    assert c.delete(f"/api/cv/{pid}").status_code == 400            # main can't be deleted
    edited = dict(cv["main"])
    edited["summary"] = edited["summary"] + " Edited."
    assert c.put(f"/api/cv/{pid}", json={"profile": edited, "note": "test edit"}).status_code == 200
    vers = c.get("/api/cv").json()["profiles"][0]["versions"]
    assert len(vers) == 2 and c.get(f"/api/cv/compare/{vers[1]['id']}/{vers[0]['id']}").json()[0]["section"] == "summary"

    # --- filters
    opts = c.get("/api/filters/options").json()
    assert "Working Student" in opts["job_types"] and any(s["id"] == "linkedin" for s in opts["sources"])
    f = c.post("/api/filters", json={"name": "API test", "roles": ["Data Science", "Machine Learning"],
                                     "job_types": ["Internship", "Working Student", "Part-time"],
                                     "locations": ["Berlin", "Remote"], "work_modes": ["Remote", "Hybrid", "On-site"],
                                     "days": 5, "sources": ["linkedin", "glassdoor", "arbeitsagentur"]}).json()
    assert c.put(f"/api/filters/{f['id']}", json={**f, "name": "API test 2"}).json()["name"] == "API test 2"

    # --- connections
    conns = {x["site"]: x for x in c.get("/api/connections").json()}
    assert conns["glassdoor"]["supports_login"] and conns["stepstone"]["status"] == "not_needed" and conns["indeed"]["supports_login"]

    # --- search run + live events over the websocket
    with c.websocket_connect("/ws/events") as ws:
        assert ws.receive_json()["type"] == "hello"
        run_id = c.post("/api/runs/search", json={"filter_profile_id": f["id"]}).json()["run_id"]
        got = ws.receive_json()
        assert "run_id" in got
    run = wait_run(c, run_id)
    assert run["run"]["status"] == "done", run["run"]
    cov = {x["site"]: x for x in run["coverage"]}
    assert cov["linkedin"]["collected"] > 0 and cov["glassdoor"]["failed"] == 1     # not connected -> login needed
    assert any(e["type"] == "run_done" for e in run["events"])

    jobs = c.get("/api/jobs", params={"tab": "new"}).json()
    assert jobs and jobs[0]["score"] is not None and jobs == sorted(jobs, key=lambda j: -(j["score"] or -1))
    excluded = c.get("/api/jobs", params={"tab": "excluded"}).json()
    assert any("Seniority" in j["exclude_reason"] or "days" in j["exclude_reason"] for j in excluded)
    hard_hidden = c.get("/api/jobs", params={"tab": "new", "hide_hard_blockers": True}).json()
    assert len(hard_hidden) < len(jobs)
    detail = c.get(f"/api/jobs/{jobs[0]['id']}").json()
    assert detail["breakdown"]["text"].startswith("Skills") and detail["full_description"]
    dash = c.get("/api/dashboard").json()
    assert dash["counts"]["scored"] > 0 and dash["top"] and dash["last_run"]["id"] == run_id

    # --- build: candidates -> build -> documents -> quick edit
    cands = c.get("/api/build/candidates").json()
    pick = [x["job_id"] for x in cands if x["verdict"] != "Skip"][:1]
    b = c.post("/api/build", json={"job_ids": pick}).json()
    brun = wait_run(c, b["run_id"])
    assert brun["run"]["status"] == "done", brun["run"]
    docs = c.get("/api/documents", params={"job_id": pick[0]}).json()
    assert {d["kind"] for d in docs} == {"cv", "letter"}
    cvdoc = [d for d in docs if d["kind"] == "cv"][0]
    assert cvdoc["qa_passed"], cvdoc["qa_results"]
    assert c.get("/api/files", params={"path": cvdoc["pdf_path"]}).headers["content-type"] == "application/pdf"
    content = cvdoc["content"]
    content["summary"] = "Short summary for this role."
    upd = c.put(f"/api/documents/{cvdoc['id']}", json={"content": content}).json()
    assert upd["content"]["summary"] == "Short summary for this role." and upd["qa_passed"]
    assert c.get("/api/files", params={"path": "/etc/passwd"}).status_code == 403

    # --- tracker
    assert c.put(f"/api/applications/{pick[0]}", json={"status": "applied", "notes": "sent via site"}).json()["ok"]
    apps = {a["id"]: a for a in c.get("/api/applications").json()}
    assert apps[pick[0]]["status"] == "applied" and apps[pick[0]]["application"]["follow_up_at"]

    # --- needs review -> resolve
    review = c.get("/api/jobs", params={"tab": "review"}).json()
    if review:
        c.patch(f"/api/jobs/{review[0]['id']}", json={"job_type": "Working Student"})
        assert c.get(f"/api/jobs/{review[0]['id']}").json()["status"] in ("scored", "new")

    # --- misc
    assert c.post("/api/companies", json={"name": "N26", "ats": "greenhouse", "board_id": "n26"}).json()["id"]
    assert c.put("/api/settings", json={"verdict_apply_from": 70, "schedule_enabled": True,
                                        "schedule_time": "07:10"}).json()["verdict_apply_from"] == 70
    assert c.get("/api/export/jobs.xlsx").status_code == 200
    assert isinstance(c.get("/api/insights/missing-keywords").json(), list)
    rs = c.post("/api/runs/rescore", json={"scope": "all"}).json()
    assert rs["rescored"] > 0
    assert len(c.get("/api/runs").json()) >= 3
