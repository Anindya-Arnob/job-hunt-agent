import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own database, job folder and private folder, in fake LLM mode."""
    from jobagent.config import settings
    from jobagent import db, llm

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "job_dir", str(tmp_path / "Job"))
    monkeypatch.setattr(settings, "private_dir", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "llm_mode", "fake")
    monkeypatch.setattr(settings, "page_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "page_settle_ms", 50)
    monkeypatch.setattr(settings, "websearch_enabled", False)    # tests that need it switch it on with a fake
    from jobagent.feeds import websearch
    monkeypatch.setattr(websearch, "claude_cli", lambda: "")       # never call the real Claude Code in tests
    db.reset_engine()
    llm.reset_cost_tracker()
    db.init_db()
    yield
    db.reset_engine()


@pytest.fixture
def master_cv_path():
    p = FIXTURES / "master_cv.docx"
    if not p.exists():
        pytest.skip("master CV fixture missing")
    return p
