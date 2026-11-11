import pytest

from jobagent import llm
from jobagent.config import settings
from jobagent.schemas import CheckResult


def test_fake_mode_uses_heuristic():
    r = llm.structured("check_cv", CheckResult, "sys", "user", fake=lambda: CheckResult(passed=True))
    assert r.passed is True
    assert llm.cost_tracker.calls == 1


def test_fake_mode_validates_dict():
    r = llm.structured("check_cv", CheckResult, "s", "u", fake=lambda: {"passed": False, "issues": ["x"]})
    assert isinstance(r, CheckResult) and r.issues == ["x"]


class _Raw:
    usage_metadata = {"input_tokens": 1_000_000, "output_tokens": 100_000}


class _FakeStructured:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return self.outputs.pop(0)


class _FakeChat:
    def __init__(self, structured):
        self.s = structured

    def with_structured_output(self, schema, include_raw=True):
        return self.s


def test_real_mode_path_cost_and_retry(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "anthropic")
    monkeypatch.setattr(settings, "model_smart", "claude-sonnet-5-5")
    fs = _FakeStructured([
        {"raw": _Raw(), "parsed": None, "parsing_error": "bad json"},
        {"raw": _Raw(), "parsed": CheckResult(passed=True), "parsing_error": None},
    ])
    monkeypatch.setattr(llm, "_chat", lambda model, max_tokens: _FakeChat(fs))
    llm.set_current_run(7)
    r = llm.structured("check_cv", CheckResult, "s", "u", fake=lambda: None)
    assert r.passed and fs.calls == 2
    # 2 calls x (1M in * $3 + 0.1M out * $15) = 2 x 4.5
    assert abs(llm.cost_tracker.run_cost[7] - 9.0) < 1e-6
    llm.set_current_run(None)


def test_budget_cap(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "anthropic")
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    llm.cost_tracker.add(None, 1.5)
    with pytest.raises(llm.BudgetExceeded):
        llm.structured("score", CheckResult, "s", "u", fake=lambda: None)


def test_no_api_key_means_offline(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert settings.effective_llm_mode == "fake"
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-x")
    assert settings.effective_llm_mode == "anthropic"


def test_prompts_exist():
    for name in ["classify", "score", "extract_page", "parse_cv", "write_cv", "check_cv", "write_letter"]:
        assert len(llm.prompt(name)) > 200


def test_real_client_constructs(monkeypatch):
    """ChatAnthropic can be built with our settings (no network call)."""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    llm._client_cache.clear()
    chat = llm._chat("claude-haiku-5-5", 1024)
    assert chat.model == "claude-haiku-5-5"
    s = chat.with_structured_output(CheckResult, include_raw=True)
    assert s is not None
