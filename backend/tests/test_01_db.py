from datetime import date

from sqlmodel import select

from jobagent import db, models as m
from jobagent.config import settings


def test_tables_and_seed():
    with db.session_scope() as s:
        profiles = s.exec(select(m.FilterProfile)).all()
        assert len(profiles) == 2
        default = [p for p in profiles if p.is_default][0]
        assert "Working Student" in default.job_types and default.days == 5
        conns = {c.site: c.status for c in s.exec(select(m.Connection)).all()}
        assert conns["linkedin"] == "not_connected" and conns["indeed"] == "not_connected" and conns["stepstone"] == "not_needed"


def test_job_crud_with_json_fields():
    with db.session_scope() as s:
        j = m.Job(company="Acme GmbH", title="Werkstudent Data Science", sources=["linkedin"],
                  links=["https://x/1"], keywords=["Python", "SQL"], posted_date=date(2026, 11, 3))
        s.add(j)
        s.flush()
        jid = j.id
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        assert j.keywords == ["Python", "SQL"] and j.link == "https://x/1"
        j.status = m.JobStatus.SCORED
        s.add(j)
    with db.session_scope() as s:
        assert s.get(m.Job, jid).status == "scored"


def test_settings_persist_and_override():
    db.save_settings({"verdict_apply_from": 70, "not_editable": 1, "schedule_enabled": True})
    assert settings.verdict_apply_from == 70 and settings.schedule_enabled is True
    settings.verdict_apply_from = 65
    db.load_setting_overrides()
    assert settings.verdict_apply_from == 70


def test_dirs_created():
    from pathlib import Path
    assert Path(settings.data_dir).exists() and settings.xl_dir.exists() and settings.master_dir.exists()
