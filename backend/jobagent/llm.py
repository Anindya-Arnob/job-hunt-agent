"""Claude access for every node.

* Real mode: Claude through `langchain-anthropic`, always with a Pydantic schema
  (structured output), so every answer is validated JSON.
* Fake mode: a deterministic heuristic passed in by the caller. Used by the
  tests and when no API key is set, so the whole app runs offline.

Every call records token use and an estimated cost; a daily budget cap stops a
run before it goes over.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Which model tier each node uses (editable in Settings via model_fast / model_smart)
NODE_TIER = {
    "plan_queries": "fast",
    "extract_page": "fast",
    "classify": "fast",
    "score": "smart",
    "parse_cv": "smart",
    "write_cv": "smart",
    "check_cv": "smart",
    "write_letter": "smart",
    "review_cv": "smart",
}

# Nodes that follow the model you picked for CV building (Settings → CV building)
def chosen_model(node: str) -> str:
    if node in ("write_cv", "check_cv", "review_cv"):
        return settings.cv_model
    if node == "write_letter":
        return settings.letter_model or settings.cv_model
    return ""

# Estimated USD per million tokens (input, output). Used only for the cost display
# and the budget cap; check current prices at https://www.anthropic.com/pricing.
PRICE_PER_MTOK = {
    "haiku": (1.0, 5.0),
    "sonnet": (3.0, 15.0),
    "opus": (5.0, 25.0),
}


class BudgetExceeded(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class CostTracker:
    day: date = field(default_factory=date.today)
    day_cost: float = 0.0
    run_cost: dict = field(default_factory=dict)       # run_id -> usd
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, run_id: Optional[int], usd: float) -> None:
        with self.lock:
            if self.day != date.today():
                self.day, self.day_cost = date.today(), 0.0
            self.day_cost += usd
            self.calls += 1
            if run_id is not None:
                self.run_cost[run_id] = self.run_cost.get(run_id, 0.0) + usd

    def check_budget(self) -> None:
        if settings.daily_budget_usd and self.day_cost >= settings.daily_budget_usd:
            raise BudgetExceeded(
                f"Daily budget of ${settings.daily_budget_usd:.2f} reached (spent ${self.day_cost:.2f}). "
                "Raise it in Settings or continue tomorrow.")


cost_tracker = CostTracker()
_current_run = threading.local()


def reset_cost_tracker() -> None:
    global cost_tracker
    cost_tracker = CostTracker()


def set_current_run(run_id: Optional[int]) -> None:
    _current_run.id = run_id


def current_run() -> Optional[int]:
    return getattr(_current_run, "id", None)


def prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def model_for(node: str) -> str:
    from .claude_code import api_model
    tier = NODE_TIER.get(node, "smart")
    base = settings.model_fast if tier == "fast" else settings.model_smart
    return api_model(chosen_model(node), base)


def _price(model: str) -> tuple[float, float]:
    for key, p in PRICE_PER_MTOK.items():
        if key in model:
            return p
    return PRICE_PER_MTOK["sonnet"]


def _usage_cost(model: str, raw_msg) -> float:
    usage = getattr(raw_msg, "usage_metadata", None) or {}
    inp, out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    pin, pout = _price(model)
    return inp / 1e6 * pin + out / 1e6 * pout


_client_cache: dict = {}


def _chat(model: str, max_tokens: int):
    key = (model, max_tokens)
    if key not in _client_cache:
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:  # pragma: no cover
            raise LLMUnavailable("langchain-anthropic is not installed") from e
        if not settings.anthropic_api_key:
            raise LLMUnavailable("No ANTHROPIC_API_KEY set. Add it in .env or switch to offline mode.")
        _client_cache[key] = ChatAnthropic(
            model=model, max_tokens=max_tokens, temperature=0,
            api_key=settings.anthropic_api_key, max_retries=3, timeout=120)
    return _client_cache[key]


CLI_NODES = {"write_cv", "write_letter", "review_cv", "read_mail", "mail_jobs"}      # written by Claude Code when there is no API key


def _cli_structured(node: str, schema: Type[T], system: str, user: str) -> T:
    """The same structured call through Claude Code (`claude -p`) using your Claude subscription."""
    import re
    import subprocess
    import tempfile
    from . import claude_code as cc
    exe = cc.exe()
    if not exe:
        raise LLMUnavailable("Claude Code not found")
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    tail = (f"Return ONLY one JSON object that validates against this JSON Schema. "
            f"No explanation, no code fence.\nJSON SCHEMA:\n{schema_json}")
    effort = settings.cv_effort if node in ("write_cv", "write_letter", "review_cv") else ""
    last, fix = None, ""
    for attempt in range(2):
        with tempfile.TemporaryDirectory() as cwd:
            extra, sys_set = cc.flags(Path(cwd), system=system + "\n\n" + tail, no_tools=True,
                                      model=cc.cli_model(chosen_model(node)), effort=effort)
            prompt = (f"{user}\n\n{tail}" if sys_set else f"{system}\n\n{user}\n\n{tail}") + fix
            cmd = cc.prefix(exe) + ["-p", "--output-format", "json", "--max-turns", "1"] + extra
            out = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=cwd, timeout=420)
        try:
            env = json.loads(out.stdout.decode("utf-8", "replace"))
        except ValueError:
            raise LLMUnavailable(cc.friendly((out.stderr or b"").decode("utf-8", "replace")[-300:] or "Claude Code gave no answer"))
        if env.get("is_error"):
            raise LLMUnavailable(cc.friendly(str(env.get("result") or env.get("subtype"))[:300]))
        _note_usage(node, env)
        text = env.get("result") or ""
        m_ = re.search(r"\{.*\}", text, re.S)
        try:
            return schema.model_validate(json.loads(m_.group(0) if m_ else text))
        except (ValueError, ValidationError, AttributeError) as e:
            last = e
            log.warning("%s via Claude Code: invalid JSON (attempt %s): %s", node, attempt + 1, str(e)[:200])
            fix = f"\n\nYour previous answer was not valid for the schema: {str(e)[:500]}. Answer again."
    raise ValueError(f"{node}: Claude Code did not return valid {schema.__name__}: {last}")


usage_log: list[dict] = []          # the last Claude Code calls: node, input/output tokens (Settings shows them)


def _note_usage(node: str, env: dict) -> None:
    from .claude_code import note_ok
    note_ok()                                  # the login works
    u = env.get("usage") or {}
    inp = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    usage_log.append({"node": node, "input_tokens": inp, "output_tokens": u.get("output_tokens") or 0,
                      "lean": settings.claude_lean})
    del usage_log[:-50]


def cli_available() -> bool:
    try:
        from .claude_code import exe
        return bool(exe()) and settings.llm_mode != "fake"
    except Exception:
        return False


def structured(node: str, schema: Type[T], system: str, user: str, *,
               fake: Callable[[], T], max_tokens: int = 4096) -> T:
    """Run one structured call. `fake` is used in offline mode."""
    if settings.effective_llm_mode == "fake" and node in CLI_NODES and cli_available():
        try:
            result = _cli_structured(node, schema, system, user)
            cost_tracker.add(current_run(), 0.0)
            return result
        except Exception as e:  # Claude Code missing / not logged in / limit -> the offline writer
            log.warning("%s: Claude Code failed (%s) - using the offline writer", node, str(e)[:200])
    if settings.effective_llm_mode == "fake":
        result = fake()
        if not isinstance(result, schema):
            result = schema.model_validate(result)
        cost_tracker.add(current_run(), 0.0)
        return result

    cost_tracker.check_budget()
    model = model_for(node)
    chat = _chat(model, max_tokens).with_structured_output(schema, include_raw=True)
    messages = [("system", system), ("human", user)]
    last_err: Exception | None = None
    for attempt in range(2):
        out = chat.invoke(messages)
        cost_tracker.add(current_run(), _usage_cost(model, out.get("raw")))
        parsed = out.get("parsed")
        if parsed is not None:
            return parsed
        last_err = out.get("parsing_error")
        log.warning("%s: structured output failed (attempt %s): %s", node, attempt + 1, last_err)
        messages.append(("human", f"Your previous answer did not match the schema: {last_err}. "
                                  "Answer again, strictly following the schema."))
    raise ValueError(f"{node}: model did not return valid {schema.__name__}: {last_err}")


def to_json(obj) -> str:
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=1)
    return json.dumps(obj, indent=1, default=str, ensure_ascii=False)


__all__ = ["structured", "prompt", "to_json", "cost_tracker", "BudgetExceeded",
           "LLMUnavailable", "set_current_run", "ValidationError"]
