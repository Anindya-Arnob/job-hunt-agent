"""Turn an uploaded CV (.docx or .pdf) into the structured profile.

1. A layout-aware reader for CVs in your format (header band, section headings,
   skills table, "Title | Org ... dates", "Stack:" lines, bullets). It copies
   text exactly, costs nothing and works offline.
2. If that reader can't make sense of the file (a very different layout, or a
   PDF), Claude extracts it with the parse_cv prompt.

Either way you review the result side by side with the original before saving.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .. import llm
from ..schemas import (CertificationItem, Contact, CVProfile, EducationItem, ExperienceItem, LanguageItem,
                       ProjectItem, SkillGroup)

SECTION_ALIASES = {
    "summary": ("professional summary", "summary", "profile", "about me", "profil"),
    "skills": ("technical skills", "skills", "core skills", "kenntnisse", "fähigkeiten"),
    "experience": ("experience", "work experience", "professional experience", "berufserfahrung"),
    "projects": ("projects", "selected projects", "projekte"),
    "education": ("education", "ausbildung", "studium"),
    "certifications": ("certifications", "certificates", "zertifikate"),
    "languages": ("languages", "languages & additional information", "sprachen", "additional information"),
}
DATE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\s*[–\-—]\s*(?:present|now|heute|ongoing|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4})|\d{4}\s*[–\-—]\s*(?:\d{4}|present)|\b(?:19|20)\d{2}\b)\s*$",
    re.I)


def _section_of(text: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", text.strip().lower().replace("&", "&"))
    t = re.sub(r"\s*&\s*", " & ", t)
    for key, names in SECTION_ALIASES.items():
        if t in names or any(t.startswith(n) for n in names if len(n) > 8):
            return key
    return None


def _split_items(s: str) -> list[str]:
    parts = re.split(r"\s*[·•]\s*|\s*;\s*", s)
    if len(parts) == 1:
        parts = [p for p in re.split(r",\s*(?![^()]*\))", s)]
    return [p.strip() for p in parts if p.strip()]


def _split_title_line(text: str) -> tuple[str, str, str]:
    """'Title  |  Org · meta \\t\\t dates' -> (left, right, dates)"""
    t = text.replace("\t", "  ").strip()
    dates = ""
    m = DATE_RE.search(t)
    if m:
        dates = m.group(1).strip()
        t = t[:m.start()].strip()
    left, _, right = t.partition("|")
    return left.strip(), right.strip(), dates


def _is_list(p) -> bool:
    ppr = p._p.pPr
    if ppr is not None and ppr.numPr is not None:
        return True
    st = (p.style.name or "").lower() if p.style is not None else ""
    return "list" in st or p.text.strip().startswith(("•", "-", "–", "▪"))


def _clean_bullet(t: str) -> str:
    return re.sub(r"^[•\-–▪]\s*", "", t.strip())


def _links(p) -> list[str]:
    try:
        return [h.address for h in p.hyperlinks if h.address]
    except Exception:
        return []


# ------------------------------------------------------------------- .docx ---
def parse_docx(path: Path) -> CVProfile:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    prof = CVProfile(name="")
    section: Optional[str] = None
    current = None   # last experience/project/education/cert item
    uncertain: list[str] = []
    header_done = False

    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "tbl":
            tb = Table(el, d)
            if not header_done and section is None:
                _parse_header(tb, prof)
                header_done = True
                continue
            if section == "skills":
                for row in tb.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if len(cells) >= 2 and cells[0] and cells[-1]:
                        prof.skills.append(SkillGroup(category=re.sub(r"\s+", " ", cells[0]),
                                                      items=_split_items(cells[-1])))
            elif section == "languages":
                _parse_languages(" | ".join(c.text for r in tb.rows for c in r.cells), prof)
            continue
        if tag != "p":
            continue
        p = Paragraph(el, d)
        text = p.text.strip()
        if not text:
            continue
        sec = _section_of(text)
        if sec and len(text) < 60:
            section, current = sec, None
            continue
        if not prof.name and section is None:
            prof.name = text.title() if text.isupper() else text
            continue

        if section == "summary":
            prof.summary = (prof.summary + " " + text).strip()
        elif section == "skills":
            cat, _, items = text.partition(":")
            if items:
                prof.skills.append(SkillGroup(category=cat.strip(), items=_split_items(items)))
        elif section in ("experience", "projects"):
            if text.lower().startswith("stack:") and current is not None:
                current.stack = _split_items(text.split(":", 1)[1])
            elif _is_list(p) and current is not None:
                current.bullets.append(_clean_bullet(text))
            else:
                left, right, dates = _split_title_line(p.text)
                if section == "experience":
                    org, _, meta = right.partition("·")
                    current = ExperienceItem(title=left, org=org.strip(), meta=meta.strip(" ·"), dates=dates)
                    prof.experience.append(current)
                else:
                    note = ""
                    m = re.search(r"\((ongoing|in progress|wip)\)", left, re.I)
                    if m:
                        note, left = m.group(0), left.replace(m.group(0), "").strip()
                    links = _links(p)
                    current = ProjectItem(name=left, link=links[0] if links else "", note=note, dates=dates)
                    prof.projects.append(current)
                if not dates:
                    uncertain.append(f"{section}[{len(getattr(prof, section)) - 1}].dates")
        elif section == "education":
            if _is_list(p) and current is not None:
                current.bullets.append(_clean_bullet(text))
            else:
                left, right, dates = _split_title_line(p.text)
                bold = any(r.bold for r in p.runs if r.text.strip())
                if current is not None and not current.school and not bold:
                    current.school = left if not right else f"{left} | {right}"
                    current.dates = current.dates or dates
                else:
                    current = EducationItem(degree=left, school=right, dates=dates)
                    prof.education.append(current)
        elif section == "certifications":
            if _is_list(p) and current is not None:
                current.bullets.append(_clean_bullet(text))
            else:
                left, right, dates = _split_title_line(p.text)
                bits = re.split(r"\s+[—–-]\s+", left, maxsplit=1)
                name, issuer = bits[0], (bits[1] if len(bits) > 1 else "")
                links = _links(p)
                current = CertificationItem(name=name.strip(), issuer=issuer.strip(), dates=dates,
                                            link=links[0] if links else "")
                if right:
                    current.issuer = f"{current.issuer} | {right}".strip(" |")
                prof.certifications.append(current)
        elif section == "languages":
            _parse_languages(text, prof)
    prof.uncertain_fields = uncertain
    return prof


def _parse_header(tb, prof: CVProfile) -> None:
    from docx.text.paragraph import Paragraph
    cell = tb.rows[0].cells[0]
    paras = [p for p in cell.paragraphs if p.text.strip()]
    if not paras:
        return
    name = paras[0].text.strip()
    prof.name = name.title() if name.isupper() else name
    if len(paras) > 1:
        prof.tagline = paras[1].text.strip()
    for p in paras[2:]:
        t = p.text
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t)
        phone = re.search(r"\+?\d[\d\s()/-]{7,}\d", t)
        if email:
            prof.contact.email = email.group(0)
        if phone:
            prof.contact.phone = phone.group(0).strip()
        if email or phone:
            first = t.split("|")[0].strip()
            if first and "@" not in first and not re.search(r"\d{4,}", first):
                prof.contact.location = first
        for addr in _links(p):
            if "linkedin" in addr:
                prof.contact.linkedin = addr
            elif "github" in addr:
                prof.contact.github = addr
            elif addr.startswith("mailto:"):
                prof.contact.email = prof.contact.email or addr[7:]


def _parse_languages(text: str, prof: CVProfile) -> None:
    for part in re.split(r"\s*\|\s*|\n", text):
        m = re.match(r"\s*([A-Za-zÄÖÜäöüß ]+?)\s*[—–:-]+\s*(.+)$", part.strip())
        if m and len(m.group(1)) < 25:
            prof.languages.append(LanguageItem(language=m.group(1).strip(), level=m.group(2).strip()))


# -------------------------------------------------------------------- text ---
def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    import docx
    d = docx.Document(str(path))
    lines = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            from docx.text.paragraph import Paragraph
            lines.append(Paragraph(el, d).text)
        elif tag == "tbl":
            from docx.table import Table
            for row in Table(el, d).rows:
                lines.append(" | ".join(c.text.replace("\n", " / ") for c in row.cells))
    return "\n".join(lines)


def looks_complete(p: CVProfile) -> bool:
    return bool(p.name) and len(p.skills) >= 2 and (len(p.experience) + len(p.projects)) >= 2 and bool(p.education)


def _fake_from_text(text: str) -> CVProfile:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    prof = CVProfile(name=lines[0].title() if lines else "Unknown")
    section = None
    for l in lines[1:]:
        sec = _section_of(l)
        if sec and len(l) < 60:
            section = sec
            continue
        if section == "summary":
            prof.summary = (prof.summary + " " + l).strip()
        elif section == "skills" and (":" in l or "|" in l):
            cat, _, items = l.replace("|", ":", 1).partition(":")
            prof.skills.append(SkillGroup(category=cat.strip(), items=_split_items(items)))
        elif section == "languages":
            _parse_languages(l, prof)
    prof.uncertain_fields = ["experience", "projects", "education"]
    return prof


def parse_cv(path: Path) -> CVProfile:
    path = Path(path)
    if path.suffix.lower() == ".docx":
        try:
            prof = parse_docx(path)
            if looks_complete(prof):
                return prof
        except Exception:  # unusual docx -> Claude
            pass
    text = extract_text(path)
    return llm.structured("parse_cv", CVProfile, llm.prompt("parse_cv"), f"CV TEXT:\n{text[:30000]}",
                          fake=lambda: _fake_from_text(text), max_tokens=8000)
