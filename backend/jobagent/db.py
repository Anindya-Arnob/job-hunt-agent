"""Database engine, sessions and first-run seeding."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from . import models as m

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _pragma(dbapi_conn, _):  # pragma: no cover - sqlite tuning
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return _engine


def reset_engine() -> None:
    """Used by tests after changing settings.data_dir."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise


ALL_ROLES = [
    "AI Engineering", "AI/ML Engineering", "Data Science", "Data Engineering",
    "Data Analytics Engineering", "Gen AI", "MLOps / LLMOps", "Machine Learning",
]
ALL_JOB_TYPES = ["Full-time", "Part-time", "Internship", "Working Student"]
STUDENT_JOB_TYPES = ["Internship", "Working Student", "Part-time"]
ALL_LOCATIONS = ["Berlin", "Germany", "Europe", "United Kingdom", "United States",
                 "Canada", "Australia", "New Zealand", "Remote"]
ALL_SOURCES = ["linkedin", "glassdoor", "indeed", "stepstone", "xing", "wellfound",
               "ats_feeds", "arbeitsagentur", "arbeitnow", "remoteok", "career_pages"]
LOGIN_SITES = {"linkedin", "glassdoor", "xing", "wellfound", "indeed"}


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())
    _add_missing_columns()
    seed()
    load_setting_overrides()


def _add_missing_columns() -> None:
    """New fields on existing tables (create_all only makes new tables)."""
    from sqlalchemy import text
    with get_engine().begin() as con:
        have = {r[1] for r in con.execute(text("PRAGMA table_info(filter_profile)"))}
        if have and "plan" not in have:
            con.execute(text("ALTER TABLE filter_profile ADD COLUMN plan JSON"))


def seed() -> None:
    with session_scope() as s:
        if not s.exec(select(m.FilterProfile)).first():
            # a new install starts empty: you type your own job titles and places on the Search screen
            s.add(m.FilterProfile(
                name="My job search", roles=[], job_types=["Full-time"], locations=[],
                work_modes=["Remote", "Hybrid", "On-site"], days=7, sources=ALL_SOURCES,
                language_max="any", is_default=True))
        for site in ALL_SOURCES:
            c = s.get(m.Connection, site)
            if not c:
                s.add(m.Connection(site=site, status="not_connected" if site in LOGIN_SITES else "not_needed"))
            elif site in LOGIN_SITES and c.status == "not_needed":      # site gained a login option
                c.status = "not_connected"
                s.add(c)


def load_setting_overrides() -> None:
    with session_scope() as s:
        rows = s.exec(select(m.AppSetting)).all()
        overrides = {}
        for r in rows:
            if r.key in settings.EDITABLE:
                try:
                    overrides[r.key] = json.loads(r.value)
                except ValueError:
                    pass
    settings.apply_overrides(overrides)


def save_settings(values: dict) -> None:
    clean = {}
    with session_scope() as s:
        for key, value in values.items():
            if key not in settings.EDITABLE:
                continue
            if key in settings.SECRET:
                if value in ("", None, "********"):
                    continue                     # not shown on screen: empty means "keep"
                if value == "__clear__":
                    value = ""
            if isinstance(value, (dict, list)):
                value = json.dumps(value)        # e.g. the plugin switches
            clean[key] = value
            row = s.get(m.AppSetting, key) or m.AppSetting(key=key)
            row.value = json.dumps(value)
            s.add(row)
    settings.apply_overrides(clean)
