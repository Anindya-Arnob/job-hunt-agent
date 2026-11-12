"""How the app starts Claude Code (`claude -p`) on your Claude subscription.

Lean mode (Settings, on by default) is the big token saver. A normal `claude -p` call carries Claude
Code's own system prompt, every tool definition, your skills, plugins and MCP servers: about 36,000
input tokens before the app says a word. The app only needs one JSON answer, so lean mode replaces
the system prompt with the app's own instructions and switches all of that off (about 3,000 tokens).

With lean mode off, your skills can be switched on or off as a group and each plugin on its own.
Inside the app Claude Code answers in one turn with its tools off, so skills and plugins cannot
actually run there; they only add their descriptions to every call.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import settings

log = logging.getLogger(__name__)

NO_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite"
_help: dict[str, str] = {}


def exe() -> str:
    e = settings.claude_cli_path or shutil.which("claude") or ""
    if not e and os.name == "nt":
        for p in (os.path.expandvars(r"%APPDATA%\npm\claude.cmd"), os.path.expanduser(r"~\.local\bin\claude.exe")):
            if os.path.exists(p):
                return p
    return e


def prefix(e: str) -> list[str]:
    return ["cmd", "/c", e] if e.lower().endswith((".cmd", ".bat")) else [e]


def help_text(e: Optional[str] = None) -> str:
    """`claude --help`, read once, so the app only passes options your Claude Code version knows."""
    e = e or exe()
    if not e:
        return ""
    if e not in _help:
        try:
            out = subprocess.run(prefix(e) + ["--help"], capture_output=True, timeout=30)
            _help[e] = (out.stdout or b"").decode("utf-8", "replace") + (out.stderr or b"").decode("utf-8", "replace")
        except Exception as ex:          # never block a build on this
            log.info("claude --help failed: %s", ex)
            _help[e] = ""
    return _help[e]


def has(flag: str, e: Optional[str] = None) -> bool:
    return re.search(re.escape(flag) + r"(?![\w-])", help_text(e)) is not None


# ------------------------------------------------------------------ your extras ---
def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def installed_plugins() -> dict[str, bool]:
    """'name@marketplace' -> enabled in your Claude Code settings."""
    home = claude_home()
    enabled = dict((_read_json(home / "settings.json").get("enabledPlugins") or {}))
    inst = _read_json(home / "plugins" / "installed_plugins.json").get("plugins") or {}
    for name in inst:
        enabled.setdefault(name, True)
    return {k: bool(v) for k, v in sorted(enabled.items())}


def installed_skills() -> list[str]:
    root = claude_home() / "skills"
    names = []
    if root.is_dir():
        for md in sorted(root.rglob("SKILL.md")):
            if len(md.relative_to(root).parts) > 4:
                continue
            name = md.parent.name
            try:
                m = re.search(r"^name:\s*(.+)$", md.read_text(encoding="utf-8", errors="ignore")[:2000], re.M)
                name = m.group(1).strip().strip("'\"") if m else name
            except OSError:
                pass
            names.append(name)
    return sorted(dict.fromkeys(names))


def plugin_choices() -> dict[str, bool]:
    try:
        v = json.loads(settings.claude_plugins or "{}")
        return {str(k): bool(x) for k, x in v.items()} if isinstance(v, dict) else {}
    except ValueError:
        return {}


def extras() -> dict:
    """What the Settings screen shows."""
    plugins = installed_plugins()
    choice = plugin_choices()
    return {"claude_code": exe() or None, "lean": settings.claude_lean, "skills_on": settings.claude_skills,
            "skills": installed_skills(),
            "plugins": [{"name": k, "in_claude_code": v, "in_app": choice.get(k, v) and not settings.claude_lean}
                        for k, v in plugins.items()],
            "flags_ok": {f: has(f) for f in ("--system-prompt-file", "--disable-slash-commands", "--strict-mcp-config",
                                             "--settings", "--effort")} if exe() else {}}


# ------------------------------------------------------------------ the command line ---
def flags(workdir: Path, *, system: Optional[str] = None, no_tools: bool = True,
          model: str = "", effort: str = "") -> tuple[list[str], bool]:
    """Extra options for one call. Returns (options, system_prompt_was_set). Files go in `workdir`."""
    out: list[str] = []
    used_system = False
    lean = settings.claude_lean
    plugins = installed_plugins()
    if lean:
        if system is not None and has("--system-prompt-file"):
            p = workdir / "_system.md"
            p.write_text(system, encoding="utf-8")
            out += ["--system-prompt-file", str(p)]
            used_system = True
        for f in ("--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
            if has(f):
                out.append(f)
        wanted = {k: False for k in plugins}
    else:
        if not settings.claude_skills and has("--disable-slash-commands"):
            out.append("--disable-slash-commands")
        choice = plugin_choices()
        wanted = {k: choice.get(k, v) for k, v in plugins.items()}
    if wanted and any(wanted[k] != plugins[k] for k in wanted) and has("--settings"):
        p = workdir / "_settings.json"
        p.write_text(json.dumps({"enabledPlugins": wanted}), encoding="utf-8")
        out += ["--settings", str(p)]
    if no_tools:
        out += ["--disallowedTools", NO_TOOLS + (",*" if lean else "")]
    if model:
        out += ["--model", model]
    if effort and has("--effort"):
        out += ["--effort", effort]
    return out, used_system


# ------------------------------------------------------------------ models ---
CLI_ALIASES = {"haiku", "sonnet", "opus", "fable"}
API_IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
           "fable": "claude-fable-5-1"}


def cli_model(choice: str) -> str:
    """The --model value: your choice, else the web-search model setting (default 'sonnet')."""
    return (choice or settings.websearch_cli_model or "").strip()


def api_model(choice: str, fallback: str) -> str:
    c = (choice or "").strip()
    return API_IDS.get(c, c) or fallback


LOGIN_GONE = re.compile(r"oauth access token has expired|re-?authenticate|not logged in|please (run /)?log ?in|"
                        r"invalid api key|failed to authenticate|\b401\b", re.I)


def friendly(why: str) -> str:
    """Claude Code's own error, said plainly when it is the login (and the app's login status turns red)."""
    if LOGIN_GONE.search(why or ""):
        note_auth_error(why)
        return ("Claude Code's login on this PC has expired. Press 'Log in again' at the top of the app "
                "(or type claude in PowerShell and log in). Then run this again.")
    return why


# ------------------------------------------------------------------ login status ---
# Every Claude Code call tells the app whether the login worked; `claude auth status` is read every 30 minutes.
# The screen shows a banner (and a desktop notification) as soon as the login is gone or expired.
LOGIN: dict = {"state": "unknown", "checked": 0.0, "detail": "", "last_ok": 0.0, "last_error": 0.0, "email": "",
               "method": "", "expires": None}


def _set(state: str, detail: str = "") -> None:
    import time
    before = LOGIN["state"]
    LOGIN.update(state=state, detail=detail, checked=time.time())
    if state != before:
        try:
            from . import events
            events.emit({"type": "claude_login", "state": state, "message": login_message()})
        except Exception:
            pass


def note_ok() -> None:
    import time
    LOGIN["last_ok"] = time.time()
    _set("ok")


def note_auth_error(why: str = "") -> None:
    import time
    LOGIN["last_error"] = time.time()
    _set("expired", (why or "")[:200])


def login_message() -> str:
    st = LOGIN["state"]
    return {"ok": "Claude Code is logged in.",
            "expired": "Claude Code's login on this PC has expired. Log in again to keep searching and writing CVs.",
            "logged_out": "Claude Code is not logged in on this PC. Log in to search the web and write CVs.",
            "missing": "Claude Code is not installed on this PC.",
            "api": "The app uses your Anthropic API key, not a Claude Code login.",
            "offline": "Offline mode: the app works without Claude."}.get(st, "Login not checked yet.")


def check_login(probe: bool = False) -> dict:
    """`claude auth status` (free). With probe=True also one tiny real call (about 3,000 tokens in lean mode),
    which is the only sure test: an expired token can still say 'logged in'."""
    import tempfile
    import time
    from .config import settings
    e = exe()
    if settings.llm_mode == "fake":
        _set("offline")                  # offline mode on purpose: no Claude, no banner
        return login_public()
    if settings.anthropic_api_key and settings.llm_mode == "api":
        _set("api")
        return login_public()
    if not e:
        _set("missing")
        return login_public()
    if re.search(r"^\s+auth\b", help_text(e), re.M):
        try:
            out = subprocess.run(prefix(e) + ["auth", "status", "--json"], capture_output=True, timeout=30)
            info = json.loads((out.stdout or b"{}").decode("utf-8", "replace") or "{}")
            LOGIN.update(email=str(info.get("email") or ""), method=str(info.get("authMethod") or ""),
                         expires=info.get("loginExpiresAt"))
            if info.get("loggedIn") is False:
                _set("logged_out")
                return login_public()
            if info.get("loggedIn") and not (LOGIN["last_error"] > LOGIN["last_ok"]) and not probe:
                _set("ok")
                return login_public()
        except Exception as ex:          # older Claude Code without `auth status`: fall back to the probe
            log.info("claude auth status: %s", ex)
    if probe or LOGIN["state"] == "unknown":
        with tempfile.TemporaryDirectory() as cwd:
            extra, used = flags(Path(cwd), system="Reply with the single word OK.", no_tools=True, model="haiku")
            prompt = "OK?" if used else "Reply with the single word OK."
            try:
                out = subprocess.run(prefix(e) + ["-p", "--output-format", "json", "--max-turns", "1"] + extra,
                                     input=prompt.encode(), capture_output=True, cwd=cwd, timeout=120)
                env = json.loads((out.stdout or b"{}").decode("utf-8", "replace") or "{}")
                why = str(env.get("result") or "") + (out.stderr or b"").decode("utf-8", "replace")[-300:]
                if env.get("is_error") or not env:
                    if LOGIN_GONE.search(why):
                        note_auth_error(why)
                    else:
                        _set(LOGIN["state"] if LOGIN["state"] != "unknown" else "unknown", why[:200])
                else:
                    note_ok()
            except Exception as ex:
                _set(LOGIN["state"], str(ex)[:200])
    LOGIN["checked"] = time.time()
    return login_public()


def login_public() -> dict:
    return {"state": LOGIN["state"], "message": login_message(), "detail": LOGIN["detail"], "checked": LOGIN["checked"],
            "last_ok": LOGIN["last_ok"], "email": LOGIN["email"], "method": LOGIN["method"], "expires": LOGIN["expires"],
            "can_open_login": os.name == "nt" or sys.platform == "darwin"}


def open_login() -> dict:
    """Open Claude Code's own login in a new window (your browser then asks you to confirm)."""
    e = exe()
    if not e:
        raise RuntimeError("Claude Code is not installed on this PC.")
    has_auth = bool(re.search(r"^\s+auth\b", help_text(e), re.M))
    args = ["auth", "login"] if has_auth else []
    if os.name == "nt":
        title = "Claude Code login - close this window when it says you are logged in"
        inner = subprocess.list2cmdline(prefix(e)[-1:] + args) if not e.lower().endswith((".cmd", ".bat")) \
            else subprocess.list2cmdline([e] + args)
        subprocess.Popen(f'start "{title}" cmd /k {inner}', shell=True)
    elif sys.platform == "darwin":
        cmd = " ".join([e] + args).replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
    else:
        return {"opened": False, "how": f"Open a terminal and run: {e} {' '.join(args) or '(then type /login)'}"}
    return {"opened": True, "how": "A window opened: log in there (your browser asks you to confirm)"
                                   + ("" if has_auth else ", then type /login") + ". Then press 'Check again'."}
