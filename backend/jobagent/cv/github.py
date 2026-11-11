"""Your GitHub projects as CV material.

Reads your public repositories (and private ones when you add a token): name, description, topics,
main language and the README. Results are cached for a day in data/github_cache.json, so a build
costs no GitHub calls most of the time (without a token GitHub allows 60 calls an hour).

A repository becomes a project the CV writer may use. Its "facts" are sentences from its own README
and description, so the truth checks accept what the README says and nothing more.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from ..config import settings
from ..schemas import CVProfile, ProjectItem
from ..vocab import SKILLS, find_terms

log = logging.getLogger(__name__)
API = "https://api.github.com"
CACHE_HOURS = 24
MAX_REPOS = 40
MAX_READMES = 30


@dataclass
class Repo:
    name: str
    url: str
    description: str = ""
    topics: list = field(default_factory=list)
    language: str = ""
    pushed_at: str = ""
    stars: int = 0
    fork: bool = False
    archived: bool = False
    readme: str = ""


class GitHubError(RuntimeError):
    pass


def user_from_link(link: str) -> str:
    """'https://github.com/alex' or 'github.com/alex/' or '@alex' or 'alex' -> 'alex'."""
    link = (link or "").strip()
    m = re.search(r"github\.com/([A-Za-z0-9-]{1,39})(?:[/?#]|$)", link)
    if m:
        return m.group(1)
    return link.lstrip("@") if re.fullmatch(r"@?[A-Za-z0-9-]{1,39}", link) else ""


def github_user(prof: Optional[CVProfile] = None) -> str:
    if settings.github_user:
        return user_from_link(settings.github_user) or settings.github_user.strip()
    if prof and prof.contact.github:
        return user_from_link(prof.contact.github)
    try:
        from ..apply import profile as ap
        return user_from_link(ap.load().github)
    except Exception:
        return ""


def _get(url: str, raw: bool = False) -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "User-Agent": "job-hunt-agent", "X-GitHub-Api-Version": "2022-11-28"})
    if settings.github_token:
        req.add_header("Authorization", f"Bearer {settings.github_token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(url) from e
        if e.code == 429 or (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0"):
            raise GitHubError("GitHub's hourly limit was reached. Try again later or add a token.") from e
        if e.code == 403:
            try:
                why = json.loads(e.read() or b"{}").get("message", "")
            except ValueError:
                why = ""
            raise GitHubError(f"GitHub refused the request{': ' + why[:160] if why else ''}") from e
        if e.code == 401:
            raise GitHubError("GitHub did not accept the token.") from e
        raise GitHubError(f"GitHub answered {e.code}") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"GitHub is not reachable ({e.reason})") from e


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+")


def no_emoji(t: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", t or "")).strip()


def clean_readme(md: str, limit: int = 8000) -> str:
    t = re.sub(r"```.*?```", " ", md or "", flags=re.S)              # code blocks
    t = "\n".join(ln for ln in t.splitlines() if not ln.lstrip().startswith("|"))   # tables
    t = no_emoji(t.replace("\n", "\x00")).replace("\x00", "\n")
    t = re.sub(r"<[^>]+>", " ", t)                                  # html
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                     # images / badges
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                  # links -> text
    t = re.sub(r"^\s*[-*+]\s+", "- ", t, flags=re.M)
    t = re.sub(r"[*_`>|]+", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()[:limit]


def _cache_path() -> Path:
    return Path(settings.data_dir) / "github_cache.json"


def fetch(user: str) -> list[Repo]:
    if not user:
        raise GitHubError("No GitHub user: add your GitHub link to your CV or in Settings.")
    path = f"/user/repos?per_page=100&sort=pushed&affiliation=owner" if settings.github_token else \
        f"/users/{user}/repos?per_page=100&sort=pushed&type=owner"
    try:
        data = json.loads(_get(API + path))
    except FileNotFoundError:
        raise GitHubError(f"GitHub user '{user}' not found")
    repos = []
    for d in data[:MAX_REPOS]:
        if not isinstance(d, dict) or d.get("fork") or d.get("archived"):
            continue
        repos.append(Repo(name=d.get("name", ""), url=d.get("html_url", ""), description=d.get("description") or "",
                          topics=list(d.get("topics") or []), language=d.get("language") or "",
                          pushed_at=d.get("pushed_at") or "", stars=int(d.get("stargazers_count") or 0)))
    owner = user
    for r in repos[:MAX_READMES]:
        try:
            full = re.search(r"github\.com/([^/]+)/", r.url)
            owner = full.group(1) if full else user
            r.readme = clean_readme(_get(f"{API}/repos/{owner}/{r.name}/readme", raw=True).decode("utf-8", "replace"))
        except FileNotFoundError:
            r.readme = ""
        except GitHubError as e:
            log.info("README of %s: %s", r.name, e)
            break                     # limit reached: keep what we have
    return repos


def repos(prof: Optional[CVProfile] = None, refresh: bool = False) -> tuple[list[Repo], dict]:
    """Your repositories (cached). Returns (repos, info)."""
    user = github_user(prof)
    p = _cache_path()
    if not refresh and p.exists():
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            if c.get("user") == user and time.time() - c.get("fetched", 0) < CACHE_HOURS * 3600:
                return [Repo(**r) for r in c["repos"]], {"user": user, "fetched": c["fetched"], "cached": True}
        except (OSError, ValueError, TypeError, KeyError):
            pass
    got = fetch(user)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    p.write_text(json.dumps({"user": user, "fetched": now, "repos": [asdict(r) for r in got]}, ensure_ascii=False),
                 encoding="utf-8")
    return got, {"user": user, "fetched": now, "cached": False}


# ------------------------------------------------------------------ repo -> project ---
def pretty_name(r: Repo) -> str:
    h = re.search(r"^#\s+(.{3,70})$", r.readme or "", re.M)
    if h and not re.search(r"(readme|badge|\bv\d)", h.group(1), re.I):
        name = no_emoji(h.group(1)).strip("#").strip()
        if len(name) >= 3:
            return name
    n = re.sub(r"[-_]+", " ", r.name).strip()
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in n.split())


def _sentences(text: str) -> list[str]:
    text = re.sub(r"^#+.*$", " ", text or "", flags=re.M)
    parts = re.split(r"(?<=[.!?])\s+|\n\s*-\s+|\n{2,}", text)
    out = []
    for s in parts:
        s = " ".join(s.split()).strip(" -")
        if (6 <= len(s.split()) <= 40 and not re.search(r"(install|pip |npm |clone|license|contribut|usage:|http|:---|---)", s, re.I)
                and s.count("·") < 2):
            out.append(s if s.endswith((".", "!", "?")) else s + ".")
    return out


def repo_facts(r: Repo, n: int = 4) -> list[str]:
    facts = []
    desc = no_emoji(r.description)
    if desc and len(desc.split()) >= 4:
        facts.append(desc.rstrip(".") + ".")
    for s in _sentences(r.readme):
        if skill_terms(s) or re.search(r"\d+(\.\d+)?\s*%|\d{1,3}(,\d{3})+|\d+(\.\d+)?\s*[KkM]\b", s):
            facts.append(s)
        if len(facts) >= n:
            break
    return list(dict.fromkeys(facts))[:n]


LANG_NAME = {"Jupyter Notebook": "Jupyter", "HTML": "", "CSS": "", "Shell": "", "Makefile": "", "Dockerfile": "Docker"}


def skill_terms(text: str) -> list[str]:
    """Skills named in a README. Short names (Go, R, C) only count when written exactly like that."""
    from ..vocab import find_terms_strict
    return find_terms_strict(text, SKILLS)


def repo_stack(r: Repo) -> list[str]:
    lang = LANG_NAME.get(r.language, r.language) if r.language else ""
    terms = ([lang] if lang else []) + skill_terms(f"{r.description}\n{' '.join(r.topics)}\n{r.readme}")
    return list(dict.fromkeys(t for t in terms if t))[:10]


def as_project(r: Repo) -> ProjectItem:
    return ProjectItem(name=pretty_name(r), link=r.url, stack=repo_stack(r), bullets=repo_facts(r))


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def same_project(p: ProjectItem, r: Repo) -> bool:
    if p.link and r.url and r.url.rstrip("/").lower() in p.link.rstrip("/").lower():
        return True
    a, b, c = _compact(p.name), _compact(r.name), _compact(pretty_name(r))
    if not a:
        return False
    return a in (b, c) or (len(b) >= 5 and (b in a or a in b)) or SequenceMatcher(None, a, b).ratio() >= 0.82
