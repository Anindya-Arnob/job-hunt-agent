"""Render a tailored CV or cover letter as .docx in your layout.

Navy header band touching the top edge, teal-underlined section headings at
10 pt, body at 9 pt, two-column skills table, bold title | italic organisation
with the date right-aligned, italic "Stack:" line, bullets.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Twips

from ..schemas import CVContent, CVProfile, LetterContent

PAGE_W, PAGE_H = 11906, 16838          # A4 in twips
SIDE = 680                              # left/right margin
BOTTOM = 480
TEXT_W = PAGE_W - 2 * SIDE


def _rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_.upper())


def _set_cell_shading(cell, hex_: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_)
    tcPr.append(shd)


def _cell_margins(cell, top=0, bottom=0, left=0, right=0) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for k, v in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = OxmlElement(f"w:{k}")
        el.set(qn("w:w"), str(v))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcPr.append(mar)


def _word2013_layout(doc) -> None:
    """python-docx's blank template is in Word 2010 compatibility mode, where Word lays out
    full-width tables differently (the header band stopped short of the right edge).
    Switch to mode 15 (Word 2013+), the same as your Word-saved master CV."""
    settings_el = doc.settings.element
    compat = settings_el.find(qn("w:compat"))
    if compat is None:
        compat = OxmlElement("w:compat")
        settings_el.append(compat)
    for cs in compat.findall(qn("w:compatSetting")):
        if cs.get(qn("w:name")) == "compatibilityMode":
            cs.set(qn("w:val"), "15")
            return
    cs = OxmlElement("w:compatSetting")
    cs.set(qn("w:name"), "compatibilityMode")
    cs.set(qn("w:uri"), "http://schemas.microsoft.com/office/word")
    cs.set(qn("w:val"), "15")
    compat.append(cs)


def _tbl_props(table, width: int, cols: list[int], indent: int = 0) -> None:
    """Rebuild the table properties in the order Word expects, with ONE fixed width.

    (python-docx adds an automatic width; Word honours the first width it finds, so a
    second one appended later is ignored and Word shrinks the table.)
    """
    tbl = table._tbl
    old = tbl.tblPr
    new = OxmlElement("w:tblPr")

    def el(tag, **attrs):
        e = OxmlElement(tag)
        for k, v in attrs.items():
            e.set(qn(k), str(v))
        return e
    new.append(el("w:tblW", **{"w:w": width, "w:type": "dxa"}))
    new.append(el("w:jc", **{"w:val": "left"}))
    new.append(el("w:tblInd", **{"w:w": indent, "w:type": "dxa"}))
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borders.append(el(f"w:{edge}", **{"w:val": "nil"}))
    new.append(borders)
    new.append(el("w:tblLayout", **{"w:type": "fixed"}))
    mar = OxmlElement("w:tblCellMar")
    for k in ("top", "left", "bottom", "right"):
        mar.append(el(f"w:{k}", **{"w:w": 0, "w:type": "dxa"}))
    new.append(mar)
    new.append(el("w:tblLook", **{"w:val": "0000"}))
    tbl.replace(old, new)
    table.autofit = False
    grid = tbl.tblGrid
    for gc, cw in zip(grid.findall(qn("w:gridCol")), cols):
        gc.set(qn("w:w"), str(cw))
    for row in table.rows:
        for cell, cw in zip(row.cells, cols):
            cell.width = Twips(cw)


def _bottom_border(paragraph, hex_: str, size: int = 6) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    b.set(qn("w:val"), "single")
    b.set(qn("w:sz"), str(size))
    b.set(qn("w:space"), "1")
    b.set(qn("w:color"), hex_)
    bdr.append(b)
    pPr.append(bdr)


def _spacing(p, before=0, after=0, line: Optional[float] = None) -> None:
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    if line:
        pf.line_spacing = line


def _run(p, text: str, size: float, color: str, bold=False, italic=False, font: str = "Calibri"):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.color.rgb = _rgb(color)
    r.font.bold, r.font.italic = bold, italic
    r.font.name = font
    rpr = r._r.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(a), font)
    return r


def _hyperlink(p, url: str, text: str, size: float, color: str, font: str, underline=True, italic=False):
    part = p.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), r_id)
    r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    f = OxmlElement("w:rFonts")
    for a in ("w:ascii", "w:hAnsi", "w:cs"):
        f.set(qn(a), font)
    rPr.append(f)
    c = OxmlElement("w:color")
    c.set(qn("w:val"), color)
    rPr.append(c)
    if italic:
        rPr.append(OxmlElement("w:i"))
    if underline:
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rPr.append(u)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size * 2)))
    rPr.append(sz)
    r.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    r.append(t)
    h.append(r)
    p._p.append(h)


def _right_tab(p) -> None:
    p.paragraph_format.tab_stops.add_tab_stop(Twips(TEXT_W), WD_TAB_ALIGNMENT.RIGHT)


def tidy(text: str) -> str:
    """Structural em dashes in names/places become commas or en dashes (keeps the one-em-dash rule)."""
    return re.sub(r"\s+—\s+", ", ", text or "").strip()


def tidy_name(text: str) -> str:
    return re.sub(r"\s+—\s+", " – ", text or "").strip()


class Builder:
    def __init__(self, style: dict, compact: int = 0):
        self.st = style
        self.compact = compact            # 0 normal, 1-2 tighter spacing
        self.doc = Document()
        sec = self.doc.sections[0]
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Twips(PAGE_W), Twips(PAGE_H)
        sec.top_margin, sec.bottom_margin = Twips(0), Twips(BOTTOM)
        sec.left_margin = sec.right_margin = Twips(SIDE)
        sec.header_distance, sec.footer_distance = Twips(0), Twips(0)
        normal = self.doc.styles["Normal"]
        normal.font.name = style["font"]
        normal.font.size = Pt(style["body_pt"])
        normal.paragraph_format.space_after = Pt(0)
        normal.paragraph_format.space_before = Pt(0)
        _word2013_layout(self.doc)
        # remove the default empty first paragraph so the band is the first element
        body = self.doc.element.body
        for p in list(body.iterchildren(qn("w:p"))):
            body.remove(p)

    @property
    def body_pt(self):
        return self.st["body_pt"]

    def p(self, align=None):
        para = self.doc.add_paragraph()
        if align is not None:
            para.alignment = align
        return para

    # ----------------------------------------------------------- blocks ---
    def header_band(self, prof: CVProfile, tagline: str) -> None:
        st = self.st
        t = self.doc.add_table(rows=1, cols=1)
        # Same set-up as your Word-saved master CV: exactly the page width, pulled left by the side margin
        over = 0
        _tbl_props(t, PAGE_W, [PAGE_W], indent=-SIDE)
        cell = t.rows[0].cells[0]
        _set_cell_shading(cell, st["navy"])
        pad = 300 if self.compact == 0 else 220
        _cell_margins(cell, top=pad, bottom=pad - 60, left=SIDE + over, right=SIDE + over)
        f = st["font"]
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _spacing(p, 0, 1)
        _run(p, prof.name.upper(), st["name_pt"], "FFFFFF", bold=True, font=f)
        if tagline:
            p = cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _spacing(p, 0, 2)
            _run(p, tagline, 9, st["tagline"], italic=True, font=f)
        c = prof.contact
        bits = [x for x in (c.location, c.phone) if x]
        p = cell.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _spacing(p, 0, 1)
        for i, b in enumerate(bits):
            if i:
                _run(p, "   |   ", 8.5, "7799AA", font=f)
            _run(p, b, 8.5, st["contact"], font=f)
        if c.email:
            if bits:
                _run(p, "   |   ", 8.5, "7799AA", font=f)
            _hyperlink(p, f"mailto:{c.email}", c.email, 8.5, st["link"], f)
        links = [(lbl, url) for lbl, url in (("LinkedIn", c.linkedin), ("GitHub", c.github)) if url]
        if links:
            p = cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for i, (lbl, url) in enumerate(links):
                if i:
                    _run(p, "   |   ", 8.5, "7799AA", font=f)
                _hyperlink(p, url, lbl, 8.5, st["link"], f)

    def heading(self, text: str) -> None:
        p = self.p()
        before = 7 if self.compact == 0 else (5 if self.compact == 1 else 4)
        _spacing(p, before, 2)
        _bottom_border(p, self.st["teal"])
        _run(p, text.upper(), self.st["heading_pt"], self.st["navy"], bold=True, font=self.st["font"])

    def text(self, text: str, color=None, italic=False, after=0) -> None:
        p = self.p()
        _spacing(p, 0, after)
        _run(p, text, self.body_pt, color or self.st["text"], italic=italic, font=self.st["font"])

    def title_line(self, bold: str, italic: str = "", dates: str = "", link: tuple[str, str] | None = None,
                   note: str = "") -> None:
        p = self.p()
        _spacing(p, 2 if self.compact < 2 else 1, 0)
        _right_tab(p)
        f = self.st["font"]
        _run(p, bold, self.body_pt + 0.5, self.st["text"], bold=True, font=f)
        if note:
            _run(p, f" {note}", self.body_pt, self.st["muted"], font=f)
        if italic:
            _run(p, "  |  ", self.body_pt, self.st["muted"], font=f)
            _run(p, italic, self.body_pt, self.st["org"], italic=True, font=f)
        if link:
            _run(p, "  |  ", self.body_pt, self.st["muted"], font=f)
            _hyperlink(p, link[1], link[0], self.body_pt, "0563C1", f, italic=True)
        if dates:
            _run(p, "\t" + dates, self.body_pt, self.st["muted"], font=f)

    def stack(self, items: list[str]) -> None:
        if not items:
            return
        p = self.p()
        _spacing(p, 0, 0)
        _run(p, "Stack: " + " · ".join(items), self.body_pt - 0.5, self.st["org"], italic=True, font=self.st["font"])

    def bullets(self, items: list[str]) -> None:
        for b in items:
            p = self.doc.add_paragraph(style="List Bullet")
            pf = p.paragraph_format
            pf.left_indent, pf.first_line_indent = Twips(260), Twips(-180)
            _spacing(p, 0, 0)
            _run(p, b, self.body_pt, self.st["text"], font=self.st["font"])

    def skills_table(self, groups) -> None:
        t = self.doc.add_table(rows=len(groups), cols=2)
        first = 2050
        _tbl_props(t, TEXT_W, [first, TEXT_W - first])
        for row, g in zip(t.rows, groups):
            for cell in row.cells:
                _cell_margins(cell, top=10, bottom=10, left=0, right=60)
            a, b = row.cells
            pa = a.paragraphs[0]
            _run(pa, g.category, self.body_pt, self.st["navy"], bold=True, font=self.st["font"])
            pb = b.paragraphs[0]
            _run(pb, " · ".join(g.items), self.body_pt, self.st["text"], font=self.st["font"])

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        return path


# ------------------------------------------------------------------- CV ---
def render_cv(content: CVContent, prof: CVProfile, style: dict, path: Path, compact: int = 0) -> Path:
    b = Builder(style, compact)
    b.header_band(prof, content.tagline)
    b.heading("Professional Summary")
    b.text(content.summary)
    b.heading("Technical Skills")
    b.skills_table(content.skills)
    if content.experience:
        b.heading("Experience")
        for e in content.experience:
            org = " · ".join(x for x in (tidy(e.org), tidy(e.meta)) if x)
            b.title_line(e.title, org, e.dates)
            b.stack(e.stack)
            b.bullets(e.bullets)
    b.heading("Projects")
    for pr in content.projects:
        b.title_line(tidy_name(pr.name), "", pr.dates, link=("GitHub", pr.link) if pr.link else None, note=pr.note)
        b.stack(pr.stack)
        b.bullets(pr.bullets)
    b.heading("Education")
    for ed in content.education:
        b.title_line(tidy_name(ed.degree), tidy(ed.school), ed.dates)
        b.bullets(ed.bullets)
    if content.certifications:
        b.heading("Certifications")
        for c in content.certifications:
            b.title_line(" – ".join(x for x in (c.name, c.issuer.split("|")[0].strip()) if x), "", c.dates,
                         link=("Certificate", c.link) if c.link else None)
            b.bullets(c.bullets)
    if content.languages:
        b.heading("Languages")
        b.text("   |   ".join(f"{l.language}: {l.level}" for l in content.languages))
    return b.save(path)


# --------------------------------------------------------------- letter ---
def render_letter(letter: LetterContent, prof: CVProfile, tagline: str, style: dict, path: Path,
                  company: str = "", today: Optional[date] = None) -> Path:
    b = Builder(style)
    size = 10
    b.header_band(prof, tagline)
    f = style["font"]
    p = b.p(WD_ALIGN_PARAGRAPH.RIGHT)
    _spacing(p, 16, 10)
    _run(p, f"{prof.contact.location}, {(today or date.today()).strftime('%d %B %Y')}".strip(", "), size,
         style["muted"], font=f)
    if letter.recipient or company:
        p = b.p()
        _spacing(p, 0, 8)
        _run(p, letter.recipient or f"Hiring Team, {company}", size, style["text"], font=f)
    p = b.p()
    _spacing(p, 0, 10)
    _run(p, letter.subject, size + 0.5, style["navy"], bold=True, font=f)
    p = b.p()
    _spacing(p, 0, 8)
    _run(p, letter.greeting, size, style["text"], font=f)
    for para in letter.paragraphs:
        p = b.p()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _spacing(p, 0, 8, line=1.08)
        _run(p, para, size, style["text"], font=f)
    p = b.p()
    _spacing(p, 6, 2)
    _run(p, letter.closing, size, style["text"], font=f)
    p = b.p()
    _run(p, prof.name, size, style["navy"], bold=True, font=f)
    return b.save(path)
