"""App configuration.

Values come from (in order): environment variables, the `.env` file in the
project root, then the defaults below. Settings changed in the UI are stored
in the database (table `appsetting`) and override these at runtime.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")


def _default_job_dir() -> str:
    """Where CVs, letters and the job list go: Documents/JobHunt (Windows and macOS), else in the project."""
    docs = Path.home() / "Documents"
    if sys.platform in ("win32", "darwin") or docs.is_dir():
        return str(docs / "JobHunt")
    return str(PROJECT_ROOT / "sample_job_dir")


def _default_private_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "JobHuntAgent")
    return str(Path.home() / ".jobhuntagent")


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default          # an empty line in .env means "use the default"


@dataclass
class Settings:
    # --- AI -------------------------------------------------------------
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    # "anthropic" = real Claude calls, "fake" = offline deterministic mode
    llm_mode: str = field(default_factory=lambda: _env("LLM_MODE", ""))
    model_fast: str = field(default_factory=lambda: _env("MODEL_FAST", "claude-haiku-5-5"))
    model_smart: str = field(default_factory=lambda: _env("MODEL_SMART", "claude-sonnet-5-5"))
    daily_budget_usd: float = field(default_factory=lambda: float(_env("DAILY_BUDGET_USD", "5")))

    # --- Paths ------------------------------------------------------------
    job_dir: str = field(default_factory=lambda: _env("JOB_DIR", _default_job_dir()))
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", str(PROJECT_ROOT / "data")))
    private_dir: str = field(default_factory=lambda: _env("PRIVATE_DIR", _default_private_dir()))

    # --- Behaviour -------------------------------------------------------------
    verdict_skip_below: int = field(default_factory=lambda: int(_env("VERDICT_SKIP_BELOW", "40")))
    verdict_apply_from: int = field(default_factory=lambda: int(_env("VERDICT_APPLY_FROM", "65")))
    build_min_score: int = field(default_factory=lambda: int(_env("BUILD_MIN_SCORE", "40")))
    max_build_attempts: int = field(default_factory=lambda: int(_env("MAX_BUILD_ATTEMPTS", "3")))
    schedule_time: str = field(default_factory=lambda: _env("SCHEDULE_TIME", "07:10"))
    schedule_enabled: bool = field(default_factory=lambda: _env("SCHEDULE_ENABLED", "false").lower() == "true")
    page_delay_seconds: float = field(default_factory=lambda: float(_env("PAGE_DELAY_SECONDS", "3")))
    page_settle_ms: int = field(default_factory=lambda: int(_env("PAGE_SETTLE_MS", "1200")))
    max_pages_per_search: int = field(default_factory=lambda: int(_env("MAX_PAGES_PER_SEARCH", "3")))
    # job pages read per site and run (about 6 s each); the rest keep the short text from the result list
    max_details_per_site: int = field(default_factory=lambda: int(_env("MAX_DETAILS_PER_SITE", "300")))
    headless_guest: bool = field(default_factory=lambda: _env("HEADLESS_GUEST", "true").lower() == "true")
    # Web-search agent (finds postings through Claude's web search, like a Claude chat does)
    websearch_enabled: bool = field(default_factory=lambda: _env("WEBSEARCH_ENABLED", "true").lower() == "true")
    websearch_engine: str = field(default_factory=lambda: _env("WEBSEARCH_ENGINE", "auto"))   # auto / api / cli
    websearch_max_calls: int = field(default_factory=lambda: int(_env("WEBSEARCH_MAX_CALLS", "30")))
    websearch_searches_per_call: int = field(default_factory=lambda: int(_env("WEBSEARCH_SEARCHES_PER_CALL", "8")))
    websearch_parallel: int = field(default_factory=lambda: int(_env("WEBSEARCH_PARALLEL", "3")))
    websearch_other_sites: bool = field(default_factory=lambda: _env("WEBSEARCH_OTHER_SITES", "true").lower() == "true")
    websearch_cli_model: str = field(default_factory=lambda: _env("WEBSEARCH_CLI_MODEL", "sonnet"))
    claude_cli_path: str = field(default_factory=lambda: _env("CLAUDE_CLI_PATH", ""))
    apply_daily_limit: int = field(default_factory=lambda: int(_env("APPLY_DAILY_LIMIT", "20")))
    owner_name: str = field(default_factory=lambda: _env("OWNER_NAME", ""))     # empty = the name on your main CV

    # --- CV building -------------------------------------------------------------
    cv_model: str = field(default_factory=lambda: _env("CV_MODEL", ""))          # haiku / sonnet / opus / fable / model id
    letter_model: str = field(default_factory=lambda: _env("LETTER_MODEL", ""))
    cv_effort: str = field(default_factory=lambda: _env("CV_EFFORT", ""))        # low / medium / high (Claude Code)
    cv_projects: int = field(default_factory=lambda: int(_env("CV_PROJECTS", "3")))
    github_projects: bool = field(default_factory=lambda: _env("GITHUB_PROJECTS", "true").lower() == "true")
    github_user: str = field(default_factory=lambda: _env("GITHUB_USER", ""))    # empty = the GitHub link on your CV
    github_token: str = field(default_factory=lambda: _env("JOBHUNT_GITHUB_TOKEN", ""))  # optional: private repos, higher limit
    ats_target: int = field(default_factory=lambda: int(_env("ATS_TARGET", "75")))
    recruiter_review: bool = field(default_factory=lambda: _env("RECRUITER_REVIEW", "true").lower() == "true")
    humanize: bool = field(default_factory=lambda: _env("HUMANIZE", "true").lower() == "true")
    state_availability: bool = field(default_factory=lambda: _env("STATE_AVAILABILITY", "true").lower() == "true")
    # --- Claude Code extras ----------------------------------------------------------
    claude_lean: bool = field(default_factory=lambda: _env("CLAUDE_LEAN", "true").lower() == "true")
    claude_skills: bool = field(default_factory=lambda: _env("CLAUDE_SKILLS", "false").lower() == "true")
    claude_plugins: str = field(default_factory=lambda: _env("CLAUDE_PLUGINS", "{}"))   # JSON {"name@market": true}
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8780")))

    # ------------------------------------------------------------------
    @property
    def effective_llm_mode(self) -> str:
        if self.llm_mode in ("anthropic", "fake"):
            return self.llm_mode
        return "anthropic" if self.anthropic_api_key else "fake"

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "app.db"

    @property
    def checkpoint_path(self) -> Path:
        return Path(self.data_dir) / "checkpoints.db"

    @property
    def profiles_dir(self) -> Path:
        return Path(self.private_dir) / "profiles"

    @property
    def xl_dir(self) -> Path:
        return Path(self.job_dir) / "XL"

    @property
    def cv_out_dir(self) -> Path:
        return Path(self.job_dir) / "Claude CV"

    @property
    def master_dir(self) -> Path:
        return self.cv_out_dir / "_Master"

    @property
    def uploads_dir(self) -> Path:
        return Path(self.data_dir) / "uploads"

    def ensure_dirs(self) -> None:
        for p in (Path(self.data_dir), self.profiles_dir, self.xl_dir, self.cv_out_dir,
                  self.master_dir, self.uploads_dir):
            p.mkdir(parents=True, exist_ok=True)

    # Keys the UI may change (stored in the DB)
    EDITABLE = (
        "llm_mode", "model_fast", "model_smart", "daily_budget_usd", "verdict_skip_below",
        "verdict_apply_from", "build_min_score", "max_build_attempts", "schedule_time",
        "schedule_enabled", "page_delay_seconds", "max_pages_per_search", "max_details_per_site", "headless_guest",
        "websearch_enabled", "websearch_engine", "websearch_max_calls", "websearch_searches_per_call",
        "websearch_other_sites", "websearch_cli_model", "claude_cli_path", "apply_daily_limit",
        "cv_model", "letter_model", "cv_effort", "cv_projects", "github_projects", "github_user", "github_token",
        "ats_target", "recruiter_review", "humanize", "state_availability",
        "claude_lean", "claude_skills", "claude_plugins",
    )
    SECRET = ("github_token",)

    def apply_overrides(self, overrides: dict) -> None:
        types = {f.name: f.type for f in fields(self)}
        for key, value in overrides.items():
            if key not in self.EDITABLE:
                continue
            current = getattr(self, key)
            if key in self.SECRET and value in (None, "********"):
                continue
            if isinstance(value, (dict, list)) and isinstance(current, str):
                value = json.dumps(value)
            if isinstance(current, bool):
                value = value if isinstance(value, bool) else str(value).lower() == "true"
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            setattr(self, key, value)
        _ = types

    def public_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.EDITABLE if k not in self.SECRET}
        d["has_github_token"] = bool(self.github_token)
        d["effective_llm_mode"] = self.effective_llm_mode
        d["has_api_key"] = bool(self.anthropic_api_key)
        d["job_dir"] = self.job_dir
        return d


settings = Settings()


def owner_name() -> str:
    """Your name for file names: OWNER_NAME in .env, else the name on your main CV."""
    if settings.owner_name:
        return settings.owner_name
    try:
        from .cv.service import get_main
        got = get_main()
        if got and got[0].name:
            return got[0].name
    except Exception:
        pass
    return "Applicant"
