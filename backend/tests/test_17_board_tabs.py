"""Job board tabs, per-tab counts and the CV / letter flags on each job."""
from fastapi.testclient import TestClient

from jobagent import db, models as m
from tests.test_14_delete import _seed


def test_tabs_counts_and_doc_flags(tmp_path):
    from jobagent.api.main import app
    ids, *_ = _seed(tmp_path)
    with db.session_scope() as s:
        j = s.get(m.Job, ids[2]); j.status = "applied"; s.add(j)
    from jobagent.sync_docs import sync_all
    sync_all()                         # what the app does at start (there it runs in the background)
    with TestClient(app) as c:
        counts = c.get("/api/jobs/counts").json()
        assert counts["built"] == 1 and counts["applied"] == 1 and counts["new"] == 1   # ids[0] has a CV file -> built
        assert counts["all"] == 3 and counts["excluded"] == 0
        built = c.get("/api/jobs?tab=built").json()
        assert [d["id"] for d in built] == [ids[0]] and built[0]["has_cv"] and not built[0]["has_letter"]
        assert [d["id"] for d in c.get("/api/jobs?tab=applied").json()] == [ids[2]]
        rows = {d["id"]: d for d in c.get("/api/jobs?tab=all").json()}
        assert rows[ids[1]]["has_cv"] is False and "first_seen" in rows[ids[1]]
        assert c.get("/api/jobs/counts").status_code == 200          # not shadowed by /api/jobs/{jid}
