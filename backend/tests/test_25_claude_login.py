"""The app knows when Claude Code's login is gone and says so (banner, notification, Log in again)."""
import json

from fastapi.testclient import TestClient

from jobagent import claude_code as cc
from jobagent.config import settings


class Out:
    def __init__(self, stdout, stderr=b""):
        self.stdout, self.stderr = stdout, stderr


def _fake(monkeypatch, status=None, probe=None):
    import subprocess
    monkeypatch.setattr(cc, "exe", lambda: "/usr/bin/claude")
    monkeypatch.setattr(cc, "help_text", lambda e=None: "Commands:\n  auth   Manage authentication\n --system-prompt-file")
    monkeypatch.setattr(cc, "installed_plugins", lambda: {})
    monkeypatch.setattr(settings, "llm_mode", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "auth" in cmd:
            return Out(json.dumps(status or {"loggedIn": True, "authMethod": "oauth_token"}).encode())
        return Out(json.dumps(probe or {"result": "OK", "usage": {}}).encode())
    monkeypatch.setattr(subprocess, "run", run)
    cc.LOGIN.update(state="unknown", last_ok=0.0, last_error=0.0)
    return calls


def test_logged_in_is_read_from_claude_auth_status(monkeypatch):
    calls = _fake(monkeypatch)
    assert cc.check_login()["state"] == "ok"
    assert calls[0][-3:] == ["auth", "status", "--json"] and len(calls) == 1          # no model call needed


def test_logged_out_shows_the_banner(monkeypatch):
    _fake(monkeypatch, status={"loggedIn": False})
    r = cc.check_login()
    assert r["state"] == "logged_out" and "not logged in" in r["message"]


def test_an_expired_token_turns_the_status_red_until_a_call_works(monkeypatch):
    _fake(monkeypatch)
    cc.check_login()
    msg = cc.friendly("Failed to authenticate. API Error: 401 OAuth access token has expired. Re-authenticate to continue.")
    assert "Log in again" in msg and cc.LOGIN["state"] == "expired"
    assert cc.check_login()["state"] == "expired"           # 'auth status' still says logged in: not trusted
    assert cc.check_login(probe=True)["state"] == "ok"       # one real call works again


def test_the_probe_sees_an_expired_login(monkeypatch):
    _fake(monkeypatch, probe={"is_error": True, "result": "Failed to authenticate. API Error: 401 OAuth access token has expired"})
    assert cc.check_login(probe=True)["state"] == "expired"


def test_offline_mode_never_shows_a_login_banner(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "fake")
    assert cc.check_login()["state"] == "offline"


def test_login_status_endpoint(monkeypatch):
    _fake(monkeypatch)
    from jobagent.api.main import app
    with TestClient(app) as c:
        assert c.get("/api/claude/login?check=true").json()["state"] == "ok"
