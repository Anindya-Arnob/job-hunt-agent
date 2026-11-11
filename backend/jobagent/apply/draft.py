"""Claude drafts answers to open questions ("Why do you want to join us?") from your CV and the job ad.

Same honesty rules as your cover letters: only facts from your CV, no invented skills or numbers,
short, in the language of the question. You read every draft (amber) before you press Submit.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from typing import Callable, Optional

from ..config import settings

log = logging.getLogger(__name__)

RULES = """Rules:
- Use ONLY facts from the CV below. Never invent skills, tools, numbers, employers or dates.
- If the CV does not support a requested claim, say honestly what is closest (e.g. a related project).
- 2-5 sentences (or the single word/number the question needs). No greeting, no sign-off.
- Answer in the language of the question (German question -> German answer).
- Plain text only. Return ONLY the answer."""


def build_prompt(question: str, cv: str, title: str, company: str, job_text: str) -> str:
    return (f"You fill in a job application form for the candidate described in the CV.\n\n"
            f"JOB: {title} at {company}\nJOB AD (extract):\n{(job_text or '')[:3500]}\n\nCV:\n{cv[:6000]}\n\n{RULES}\n\n"
            f"FORM QUESTION: {question}\nANSWER:")


def _api(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=90)
    msg = client.messages.create(model=settings.model_smart, max_tokens=600,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def _cli(prompt: str) -> str:
    from pathlib import Path
    from .. import claude_code as cc
    exe = cc.exe()
    with tempfile.TemporaryDirectory() as cwd:
        extra, _ = cc.flags(Path(cwd), system=None, no_tools=True, model=cc.cli_model(""))
        cmd = cc.prefix(exe) + ["-p", "--output-format", "json", "--max-turns", "1"] + extra
        out = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=cwd, timeout=180)
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace"))
        return (data.get("result") or "").strip() if not data.get("is_error") else ""
    except ValueError:
        return ""


def make_drafter(ctx) -> Optional[Callable[[str], str]]:
    """question -> draft, or None when neither an API key nor Claude Code is available."""
    from ..feeds.websearch import claude_cli
    if settings.effective_llm_mode == "fake" and not claude_cli():
        return None
    engine = _api if settings.anthropic_api_key else (_cli if claude_cli() else None)
    if engine is None:
        return None

    def draft(question: str) -> str:
        try:
            text = engine(build_prompt(question, ctx.cv_text, ctx.title, ctx.company, ctx.job_text))
        except Exception as e:
            log.info("draft failed: %s", e)
            return ""
        return text.strip().strip('"')[:3000]

    return draft


# ---------------------------------------------------------------- all at once ---
BATCH_RULES = """You fill in the remaining questions of a job application form for the candidate below.
Rules:
- Use ONLY the candidate's details, CV and the job ad given here. Never invent facts, numbers, dates, skills or employers.
- If the information to answer a question truthfully is not given, answer null (the candidate answers it).
- For a question with "options", answer with exactly one of the options, copied character for character - or null.
- Yes/No about a skill or experience: "Yes" only if the CV clearly shows it; otherwise null.
- Open questions (motivation, "why us", what excites you, strengths, describe a project): write a complete, specific
  answer built from the COVER LETTER FOR THIS JOB and the CV - the role and company by name, two concrete projects or
  experiences tied to the job, and what the candidate would contribute. Use most of the allowed length ("max_chars")
  but never exceed it; without max_chars write 4-7 sentences. Same language as the question. Plain, human sentences:
  no "passionate", "thrilled", "perfect fit", "leverage", no em dashes.
- Never answer security checks, captchas, arithmetic puzzles or consent questions - answer null.
Return ONLY JSON: {"answers": {"<id>": "<answer or null>", ...}}"""


def build_batch_prompt(items: list[dict], ctx) -> str:
    prof = {k: v for k, v in ctx.profile.model_dump().items() if v and k != "salary"}
    qs = "\n".join(json.dumps({"id": it["id"], "question": it["question"], "type": it.get("type", "text"),
                               **({"options": it["options"]} if it.get("options") else {})}, ensure_ascii=False)
                   for it in items)
    return (f"{BATCH_RULES}\n\nJOB: {ctx.title} at {ctx.company}\nJOB AD (extract):\n{(ctx.job_text or '')[:3000]}\n\n"
            f"CANDIDATE DETAILS:\n{json.dumps(prof, ensure_ascii=False)}\n\nCV:\n{(ctx.cv_text or '')[:6000]}\n\n"
            f"COVER LETTER FOR THIS JOB:\n{(getattr(ctx, 'letter_text', '') or '(none)')[:4000]}\n\n"
            f"QUESTIONS (one JSON object per line):\n{qs}\n\nJSON:")


def _parse_answers(text: str) -> dict:
    import re
    m_ = re.search(r"\{.*\}", text or "", re.S)
    if not m_:
        return {}
    try:
        data = json.loads(m_.group(0))
    except ValueError:
        return {}
    ans = data.get("answers", data) if isinstance(data, dict) else {}
    return {str(k): (str(v).strip() if v not in (None, "", "null") else None) for k, v in ans.items()}


def make_batch_answerer(ctx) -> Optional[Callable[[list[dict]], dict]]:
    """[{id, question, options?, type?}] -> {id: answer or None}; one Claude call per page."""
    from ..feeds.websearch import claude_cli
    engine = _api if settings.anthropic_api_key else (_cli if claude_cli() else None)
    if engine is None:
        return None

    def answer(items: list[dict]) -> dict:
        if not items:
            return {}
        try:
            return _parse_answers(engine(build_batch_prompt(items, ctx)))
        except Exception as e:
            log.info("batch answer failed: %s", e)
            return {}

    return answer
