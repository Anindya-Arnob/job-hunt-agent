"""Automatic quality checks for every CV and cover letter.

* exactly 1 page as PDF
* the navy header band touches the very top edge (no white gap)
* every tool and skill exists in the main CV
* every number exists in the main CV
* at most one em dash
* at least 3 projects; format sizes (10 pt headings, 9 pt body)
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from ..cv.service import numbers_in, profile_numbers
from ..schemas import CVContent, CVProfile, LetterContent
from ..steps.score import profile_tools
from ..vocab import norm_tool

YEAR = re.compile(r"^(19|20)\d{2}$")

# pdfium (the PDF renderer) is not thread-safe: every use must hold this lock,
# otherwise parallel requests crash the whole app.
import threading  # noqa: E402
PDFIUM_LOCK = threading.Lock()


def render_page_png(pdf: Path, page: int, scale: float, cache_dir: Path) -> Path:
    """Render one PDF page to a cached PNG (re-rendered only when the PDF changes)."""
    import pypdfium2 as pdfium
    pdf = Path(pdf)
    st = pdf.stat()
    key = f"{pdf.stem[:60]}_{int(st.st_mtime)}_{st.st_size}_p{page}_s{int(scale * 10)}.png"
    out = Path(cache_dir) / key
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            if page < 0 or page >= len(doc):
                raise IndexError("page out of range")
            img = doc[page].render(scale=scale).to_pil()
        finally:
            doc.close()
    import uuid
    tmp = out.with_name(f"{out.stem}.{uuid.uuid4().hex}.tmp")
    img.save(tmp, format="PNG", optimize=True)
    try:
        tmp.replace(out)
    except OSError:          # another request finished the same page first (Windows locks)
        tmp.unlink(missing_ok=True)
    return out


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    soft: bool = False          # shown as a warning; never forces a rewrite or "needs your attention"

    def as_dict(self):
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "soft": self.soft}


def page_count(pdf: Path) -> int:
    return len(PdfReader(str(pdf)).pages)


def overflow_lines(pdf: Path) -> int:
    r = PdfReader(str(pdf))
    if len(r.pages) < 2:
        return 0
    extra = 0
    for pg in r.pages[1:]:
        extra += len([l for l in (pg.extract_text() or "").splitlines() if l.strip()])
    return extra


def band_touches_top(pdf: Path, navy_hex: str) -> tuple[bool, str]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return True, "skipped (pypdfium2 not installed)"
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            img = doc[0].render(scale=1).to_pil().convert("RGB")
        finally:
            doc.close()
    w, h = img.size
    target = tuple(int(navy_hex[i:i + 2], 16) for i in (0, 2, 4))
    bad = []
    # top edge across the width, plus the far left/right edges a little lower down
    points = [(x, 0) for x in (1, int(w * 0.25), int(w * 0.5), int(w * 0.75), w - 2)] + [(1, 30), (w - 2, 30)]
    for x, y in points:
        px = img.getpixel((x, y))
        if sum(abs(a - b) for a, b in zip(px, target)) > 60:
            side = "left" if x < w * 0.1 else ("right" if x > w * 0.9 else "top")
            bad.append(f"{side} edge at x={x}, y={y}")
    return (not bad), ("" if not bad else "white gap at the " + ", ".join(sorted({b.split(' edge')[0] for b in bad})) + " edge of the header band")


def _match_tool(tool: str, allowed_norms: set[str]) -> bool:
    n = norm_tool(tool)
    if not n:
        return True
    if n in allowed_norms:
        return True
    for a in allowed_norms:
        if len(a) >= 4 and len(n) >= 4 and (n in a or a in n):
            return True
    return False


def cv_tools(c: CVContent) -> list[str]:
    tools = [i for g in c.skills for i in g.items]
    for e in c.experience:
        tools += e.stack
    for p in c.projects:
        tools += p.stack
    return tools


def unknown_tools(c: CVContent, prof: CVProfile) -> list[str]:
    """Tools in the tailored CV that your main CV does not show. A tool named anywhere in your main CV (a bullet
    or the summary, not only a skills list) is yours: otherwise this check and 'Keeps the keywords your main CV
    had' contradicted each other (one removed 'Large Language Models', the other put it back) and every round
    cost a rewrite."""
    from ..steps.score import profile_text
    from ..vocab import term_regex
    allowed = {norm_tool(t) for t in profile_tools(prof)}
    text = profile_text(prof)

    def known(t: str) -> bool:
        if _match_tool(t, allowed):
            return True
        forms = {t, re.sub(r"\s*\([^)]*\)", "", t).strip(), *re.findall(r"\(([^)]+)\)", t)}
        return any(f and len(f) > 1 and term_regex(f).search(text) for f in forms)
    return [t for t in cv_tools(c) if not known(t)]


def cv_text_for_numbers(c: CVContent) -> str:
    parts = [c.summary, c.tagline]
    for e in c.experience:
        parts += e.bullets
    for p in c.projects:
        parts += p.bullets
    for ed in c.education:
        parts += ed.bullets
    return "\n".join(parts)


def unknown_numbers(text: str, prof: CVProfile) -> list[str]:
    allowed = profile_numbers(prof)
    out = []
    for n in sorted(numbers_in(text)):
        if YEAR.match(n) or n in allowed:
            continue
        out.append(n)
    return out


def all_text_cv(c: CVContent) -> str:
    parts = [c.tagline, c.summary]
    for g in c.skills:
        parts += [g.category] + g.items
    for e in c.experience:
        parts += [e.title] + e.bullets
    for p in c.projects:
        parts += p.bullets
    return "\n".join(parts)


def docx_font_sizes(docx_path: Path) -> set[int]:
    xml = zipfile.ZipFile(docx_path).read("word/document.xml").decode("utf8", "ignore")
    return {int(x) for x in re.findall(r'<w:sz w:val="(\d+)"', xml)}


def check_cv(content: CVContent, prof: CVProfile, docx: Path, pdf: Optional[Path], style: dict,
             projects_wanted: int = 3) -> list[Check]:
    checks = []
    if pdf is not None:
        pages = page_count(pdf)
        detail = "" if pages == 1 else f"{pages} pages, about {overflow_lines(pdf)} lines too long"
        checks.append(Check("Exactly 1 page", pages == 1, detail))
        ok, d = band_touches_top(pdf, style["navy"])
        checks.append(Check("Header band touches top edge", ok, d))
    unk = unknown_tools(content, prof)
    checks.append(Check("Only tools from your main CV", not unk, ", ".join(unk[:10])))
    nums = unknown_numbers(cv_text_for_numbers(content), prof)
    checks.append(Check("Only numbers from your main CV", not nums, ", ".join(nums[:10])))
    dashes = all_text_cv(content).count("—")
    checks.append(Check("At most one em dash", dashes <= 1, f"{dashes} em dashes" if dashes > 1 else ""))
    least = min(3, projects_wanted)
    n = len(content.projects)
    checks.append(Check(f"At least {least} projects", n >= least, f"{n} projects"))
    if projects_wanted != least or n > least:
        checks.append(Check(f"{projects_wanted} projects as you chose", n == projects_wanted,
                            "" if n == projects_wanted else f"{n} fit on one page", soft=True))
    sizes = docx_font_sizes(docx)
    want = {int(style["heading_pt"] * 2), int(style["body_pt"] * 2)}
    checks.append(Check("Headings 10 pt, body 9 pt", want <= sizes, ""))
    return checks


def check_letter(letter: LetterContent, prof: CVProfile, pdf: Optional[Path], style: dict,
                 job_text: str = "") -> list[Check]:
    checks = []
    if pdf is not None:
        pages = page_count(pdf)
        checks.append(Check("Exactly 1 page", pages == 1, "" if pages == 1 else f"{pages} pages"))
        ok, d = band_touches_top(pdf, style["navy"])
        checks.append(Check("Header band touches top edge", ok, d))
    text = "\n".join(letter.paragraphs)
    # numbers from the job ad (e.g. "5 months", a start date) may be named in the letter; others must be on your CV
    from_ad = numbers_in(job_text or "")
    nums = [n for n in unknown_numbers(text, prof) if n not in ("20",) and n not in from_ad]   # "20 hours a week": the stated default
    checks.append(Check("Only numbers from your main CV", not nums, ", ".join(nums[:10])))
    dashes = text.count("—")
    checks.append(Check("At most one em dash", dashes <= 1, f"{dashes} em dashes" if dashes > 1 else ""))
    return checks


def passed(checks: list[Check]) -> bool:
    return all(c.passed or c.soft for c in checks)


def passed_dicts(checks: list[dict]) -> bool:
    return all(c.get("passed") or c.get("soft") for c in checks)


def issues(checks: list[Check]) -> list[str]:
    out = []
    for c in checks:
        if c.passed or c.soft:
            continue
        if c.name == "Exactly 1 page":
            out.append(f"The CV is too long ({c.detail}). Cut that many lines: fewer/shorter bullets, fewer skill items.")
        elif c.name.startswith("Only tools"):
            out.append(f"Remove tools not in the main CV: {c.detail}.")
        elif c.name.startswith("Only numbers"):
            out.append(f"Remove numbers not in the main CV: {c.detail}.")
        elif c.name.startswith("Keeps the keywords"):
            out.append(f"Your main CV matched these words of the posting and the tailored CV lost them; {c.detail}.")
        elif c.name.startswith("At most one em dash"):
            out.append("Use at most one em dash (—) in the whole CV.")
        else:
            out.append(f"{c.name}: {c.detail}")
    return out
