"""CV building options: model choice, lean Claude Code calls, plugin switches, GitHub projects,
project count, ATS before/after and the recruiter-screen checks."""
import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import select

from jobagent import claude_code, db, llm, models as m
from jobagent.config import settings
from jobagent.cv import github as gh
from jobagent.cv import projects as P
from jobagent.cv import service as CV
from jobagent.docs import ats
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import build_graph as B
from jobagent.schemas import CVContent, JobForWriting, ProjectItem
from jobagent.steps.score import score_job, store_score

from profile_fixture import sample_profile as make_profile

HELP = ("--system-prompt-file --disable-slash-commands --strict-mcp-config --no-session-persistence "
        "--settings --effort --model")

JOB = JobForWriting(company="Voltwerk", title="Werkstudent:in AI & Automation - Energy (m/w/d)",
                    job_type="Working Student",
                    description="Build AI agents with n8n and LangChain, RAG pipelines, REST APIs and Python. "
                                "FastAPI, Docker. German C1 required. English fluent.",
                    keywords=["LangChain", "RAG", "n8n", "Python"], required_skills=["n8n", "REST", "Python"],
                    deal_breakers=["German C1 required (CV: A2) (hard)"])

README = ("# Grid Agent\n\nAn AI agent that automates energy-grid alerts with n8n workflows and LangChain.\n\n"
          "- Calls REST APIs of 3 grid operators and summarises incidents with RAG.\n"
          "- Deployed with Docker and FastAPI.\n\n## Install\npip install -r requirements.txt\n")
REPOS = [{"name": "grid-agent", "html_url": "https://github.com/alex-morgan-demo/grid-agent",
          "description": "AI agent for energy grid alerts built with n8n and LangChain", "topics": ["n8n", "rag"],
          "language": "Python", "pushed_at": "2026-10-01", "stargazers_count": 2, "fork": False, "archived": False},
         {"name": "dotfiles", "html_url": "https://github.com/alex-morgan-demo/dotfiles", "description": "",
          "topics": [], "language": "Shell", "fork": False, "archived": False},
         {"name": "someone-elses", "html_url": "https://github.com/x/y", "fork": True}]


@pytest.fixture
def fake_github(monkeypatch):
    calls = []

    def get(url, raw=False):
        calls.append(url)
        if url.endswith("/readme"):
            if "grid-agent" in url:
                return README.encode()
            raise FileNotFoundError(url)
        return json.dumps(REPOS).encode()
    monkeypatch.setattr(gh, "_get", get)
    monkeypatch.setattr(settings, "github_projects", True)
    return calls


# ------------------------------------------------------------------ Claude Code command line ---
def test_lean_mode_replaces_the_system_prompt_and_turns_extras_off(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "help_text", lambda e=None: HELP)
    monkeypatch.setattr(claude_code, "installed_plugins", lambda: {"ponytail@skills": True, "humanizer@x": True})
    monkeypatch.setattr(settings, "claude_lean", True)
    flags, used = claude_code.flags(tmp_path, system="SYSTEM", model="opus", effort="high")
    assert used and (tmp_path / "_system.md").read_text() == "SYSTEM"
    for f in ("--system-prompt-file", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
        assert f in flags
    assert json.loads((tmp_path / "_settings.json").read_text()) == {"enabledPlugins": {"ponytail@skills": False,
                                                                                      "humanizer@x": False}}
    assert flags[flags.index("--disallowedTools") + 1].endswith(",*")
    assert flags[flags.index("--model") + 1] == "opus" and flags[flags.index("--effort") + 1] == "high"


def test_without_lean_mode_your_plugin_switches_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "help_text", lambda e=None: HELP)
    monkeypatch.setattr(claude_code, "installed_plugins", lambda: {"ponytail@skills": True, "humanizer@x": True})
    monkeypatch.setattr(settings, "claude_lean", False)
    monkeypatch.setattr(settings, "claude_skills", False)
    monkeypatch.setattr(settings, "claude_plugins", json.dumps({"ponytail@skills": False}))
    flags, used = claude_code.flags(tmp_path, system="SYSTEM")
    assert not used and "--system-prompt-file" not in flags and "--disable-slash-commands" in flags
    assert json.loads((tmp_path / "_settings.json").read_text())["enabledPlugins"] == {"ponytail@skills": False,
                                                                                      "humanizer@x": True}


def test_old_claude_code_gets_only_options_it_knows(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "help_text", lambda e=None: "--model --disallowedTools")
    monkeypatch.setattr(claude_code, "installed_plugins", lambda: {})
    flags, used = claude_code.flags(tmp_path, system="SYSTEM", effort="high")
    assert not used and "--effort" not in flags and "--disable-slash-commands" not in flags


def test_cv_writer_uses_the_model_you_chose_through_claude_code(monkeypatch):
    seen = {}

    class Out:
        stdout = json.dumps({"result": json.dumps({"passed": True, "issues": []}),
                             "usage": {"input_tokens": 10, "cache_creation_input_tokens": 2900, "output_tokens": 20}}).encode()
        stderr = b""

    def run(cmd, input=None, **kw):
        seen["cmd"], seen["stdin"] = cmd, input.decode()
        return Out()
    # Claude Code is started through llm._run_claude (so the Stop button can end it)
    monkeypatch.setattr(llm, "_run_claude", lambda cmd, prompt, cwd, timeout=420: run(cmd, input=prompt.encode()))
    monkeypatch.setattr(claude_code, "exe", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "help_text", lambda e=None: HELP)
    monkeypatch.setattr(claude_code, "installed_plugins", lambda: {})
    monkeypatch.setattr(settings, "cv_model", "opus")
    monkeypatch.setattr(settings, "claude_lean", True)
    from jobagent.schemas import CheckResult
    r = llm._cli_structured("write_cv", CheckResult, "THE SYSTEM RULES", "THE USER PART")
    assert r.passed
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"
    assert "THE SYSTEM RULES" not in seen["stdin"] and "THE USER PART" in seen["stdin"]     # in the system prompt file
    assert llm.usage_log[-1]["input_tokens"] == 2910
    assert llm.model_for("write_cv") == "claude-opus-5-5"                                  # API mode follows it too


def test_github_token_is_never_shown_and_empty_keeps_it(client=None):
    db.save_settings({"github_token": "ghp_secret"})
    assert settings.github_token == "ghp_secret"
    pub = settings.public_dict()
    assert "github_token" not in pub and pub["has_github_token"]
    db.save_settings({"github_token": "", "cv_projects": 4})
    assert settings.github_token == "ghp_secret" and settings.cv_projects == 4
    db.save_settings({"github_token": "__clear__"})
    assert settings.github_token == ""
    db.save_settings({"claude_plugins": {"a@b": False}})
    assert json.loads(settings.claude_plugins) == {"a@b": False}


# ------------------------------------------------------------------ GitHub ---
def test_github_repos_become_projects_with_only_readme_facts(fake_github):
    assert gh.user_from_link("https://github.com/alex-morgan-demo/") == "alex-morgan-demo"
    assert gh.user_from_link("@alex") == "alex"
    repos, info = gh.repos(make_profile())
    assert [r.name for r in repos] == ["grid-agent", "dotfiles"]           # forks are skipped
    item = gh.as_project(repos[0])
    assert item.name == "Grid Agent" and item.link.endswith("/grid-agent")
    assert {"n8n", "LangChain", "Docker", "FastAPI"} <= set(item.stack)
    assert any("3 grid operators" in b for b in item.bullets) and not any("pip install" in b for b in item.bullets)
    n = len(fake_github)
    gh.repos(make_profile())                                                # cached: no new calls
    assert len(fake_github) == n


def test_picker_takes_the_closest_projects_from_cv_and_github(fake_github):
    prof = make_profile()
    picked, info = P.pick(JOB, prof, n=3)
    names = [x.item.name for x in picked]
    assert names[0] == "Grid Agent" and picked[0].source == "github"
    assert len(picked) == 3 + P.SPARE and info["want"] == 3 and info["github"]["repos"] == 2   # shortlist
    truth = P.truth_profile(prof, picked)
    assert "Grid Agent" in [p.name for p in truth.projects] and len(truth.projects) == len(prof.projects) + 1
    assert [p.name for p in P.writer_profile(prof, picked).projects] == names


def test_github_error_falls_back_to_your_cv(monkeypatch):
    def boom(url, raw=False):
        raise gh.GitHubError("GitHub is not reachable (offline)")
    monkeypatch.setattr(gh, "_get", boom)
    picked, info = P.pick(JOB, make_profile(), n=3, use_github=True)
    assert len(picked) == 3 + P.SPARE and all(x.source == "cv" for x in picked) and "not reachable" in info["github"]["error"]


# ------------------------------------------------------------------ ATS + screen ---
def test_ats_counts_exact_posting_words():
    prof = make_profile()
    before = ats.score(JOB, prof, prof)
    assert 0 <= before["score"] <= 100 and "n8n" in before["missing"]
    assert set(before["parts"]) == {"keywords", "upfront", "title", "sections", "contact", "results", "languages"}
    assert ats.title_words(JOB.title) == ["AI", "Automation", "Energy"]


def test_screen_checks_catch_what_gets_cvs_rejected():
    prof = make_profile()
    cv = CVContent(tagline="Data Science M.Sc. Student", summary="I build models with my team.",
                   skills=prof.skills, experience=prof.experience,
                   projects=[ProjectItem(name="X", bullets=["Responsible for dashboards.", "Worked on ETL."])],
                   education=prof.education, languages=prof.languages)
    after = ats.score(JOB, cv, prof)
    checks = {c.name: c for c in ats.screen_checks(JOB, cv, prof, prof, after, 75,
                                                   "Available 20 hours per week; enrolled until 2027.", None)}
    assert not checks["Job title in your headline"].passed and not checks["Job title in your headline"].soft
    assert not checks["No 'I' or 'my' in the CV"].passed
    assert not checks["Bullets start with what you did"].passed
    assert not checks["States your availability"].passed
    assert checks["No knock-out requirement"].soft and "German C1" in checks["No knock-out requirement"].detail
    assert not ats.FIRST_PERSON.search("Built an AI agent for I/O-heavy jobs")


# ------------------------------------------------------------------ a whole build ---
@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_build_uses_github_project_count_and_records_ats(master_cv_path, fake_github, monkeypatch):
    monkeypatch.setattr(settings, "cv_projects", 4)
    monkeypatch.setattr(settings, "github_user", "alex-morgan-demo")
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    prof, vid = CV.get_main()
    from jobagent.apply import profile as AP
    ap = AP.load()
    ap.hours_per_week, ap.graduation, ap.enrolled_student = "20", "2027", "Yes"
    AP.save(ap)
    with db.session_scope() as s:
        j = m.Job(company="Voltwerk", title=JOB.title, full_description=JOB.description, job_type="Working Student",
                  keywords=JOB.keywords, required_skills=JOB.required_skills, status="scored", links=["https://x/v"])
        s.add(j); s.flush(); jid = j.id
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
    store_score(jid, score_job(job, prof), vid)
    out = asyncio.run(B.start_build([jid]))
    r = out["results"][0]
    assert r["status"].startswith("built"), r.get("failed_checks")
    assert "Grid Agent" in r["projects"] and len(r["projects"]) in (3, 4)
    assert r["ats_after"] >= r["ats_before"]
    with db.session_scope() as s:
        doc = s.exec(select(m.Document).where(m.Document.job_id == jid, m.Document.kind == "cv")).one()
        content = doc.content
    used = [p for p in content["_projects"] if not p.get("unused")]
    assert content["_ats"]["before"]["score"] == r["ats_before"] and used[0]["source"] == "github"
    assert [p["name"] for p in used] == r["projects"]
    _debug_dump(r, content, doc.qa_results)
    cv = CVContent.model_validate(content)
    assert "20" in cv.summary and "AI & Automation" in cv.tagline
    assert not any(c["name"].startswith("Only tools") and not c["passed"] for c in doc.qa_results)


def _debug_dump(r, content, qa):          # handy when tuning: DEBUG_BUILD=1 pytest -s -k build_uses
    import os
    if os.environ.get("DEBUG_BUILD"):
        print("\nRESULT", json.dumps(r, indent=1)[:1500])
        print("TAGLINE", content["tagline"], "\nSUMMARY", content["summary"])
        print("ATS", json.dumps(content["_ats"], indent=1)[:1500])
        for c in qa:
            print("CHECK", c)
