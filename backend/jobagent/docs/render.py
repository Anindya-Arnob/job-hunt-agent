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
PHOTO_W = 1300                          # photo width in the header band (about 2.3 cm, 3:4)
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


def _left_border(paragraph, hex_: str, size: int = 24) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:left")
    b.set(qn("w:val"), "single")
    b.set(qn("w:sz"), str(size))
    b.set(qn("w:space"), "4")
    b.set(qn("w:color"), hex_)
    bdr.append(b)
    pPr.append(bdr)


def _tiny(paragraph) -> None:
    """The empty paragraph Word needs after a table in a cell: as small as possible."""
    _spacing(paragraph, 0, 0, line=0.5)
    rpr = paragraph._p.get_or_add_pPr()
    r = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "2")
    r.append(sz)
    rpr.append(r)


class Builder:
    """Draws one page in a template: where paragraphs go (the page, a column, a cell) is `box`, its width
    `width`. Headings, items and the header follow the template's layout keys (docs/templates.py)."""

    def __init__(self, style: dict, compact: int = 0):
        self.st = style
        self.compact = compact            # 0 normal, 1-2 tighter spacing
        self.side = style.get("side", SIDE)
        self.top = style.get("top", 0)
        self.text_w = PAGE_W - 2 * self.side
        self.doc = Document()
        sec = self.doc.sections[0]
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Twips(PAGE_W), Twips(PAGE_H)
        sec.top_margin, sec.bottom_margin = Twips(self.top), Twips(BOTTOM)
        sec.left_margin = sec.right_margin = Twips(self.side)
        sec.header_distance, sec.footer_distance = Twips(0), Twips(0)
        normal = self.doc.styles["Normal"]
        normal.font.name = style["font"]
        normal.font.size = Pt(style["body_pt"])
        normal.paragraph_format.space_after = Pt(0)
        normal.paragraph_format.space_before = Pt(0)
        _word2013_layout(self.doc)
        # remove the default empty first paragraph so the header is the first element
        body = self.doc.element.body
        for p in list(body.iterchildren(qn("w:p"))):
            body.remove(p)
        self._fresh: set[int] = set()     # cells whose one empty paragraph is still unused
        self._keep: list = []
        self.base(self.doc, self.text_w)

    # ------------------------------------------------------------- where ---
    # base = the column being written (the page, or a column of the sidebar layout); sec_box = where the
    # current section's items go (the base, or the right cell of a label heading); box = the next paragraph's place
    def use(self, box, width: int) -> None:
        self.box, self.width = box, width

    def base(self, box, width: int) -> None:
        self._base, self._base_w = box, width
        self.sec_box, self.sec_width = box, width
        self.use(box, width)

    def cell(self, c):
        self._fresh.add(id(c))
        self._keep.append(c)
        return c

    def item_start(self) -> None:
        self.use(self.sec_box, self.sec_width)

    def p(self, align=None, style: Optional[str] = None):
        if id(self.box) in self._fresh:      # a new cell comes with one empty paragraph: use it
            self._fresh.discard(id(self.box))
            para = self.box.paragraphs[0]
            if style:
                para.style = self.doc.styles[style]
        else:
            para = self.box.add_paragraph(style=style) if style else self.box.add_paragraph()
        if align is not None:
            para.alignment = align
        return para

    def table(self, widths: list[int], indent: int = 0):
        box = self.box
        if id(box) in self._fresh:            # no blank line above a table in a cell
            self._fresh.discard(id(box))
            box._tc.remove(box.paragraphs[0]._p)
        t = box.add_table(rows=1, cols=len(widths))
        _tbl_props(t, sum(widths), widths, indent=indent)
        if box is not self.doc:
            _tiny(box.paragraphs[-1])
        return t

    @property
    def body_pt(self):
        return self.st["body_pt"]

    # ------------------------------------------------------------ header ---
    def header(self, prof: CVProfile, tagline: str, photo: Optional[Path] = None) -> None:
        kind = self.st.get("header", "band")
        if kind == "band":
            return self.header_band(prof, tagline, photo)
        center = kind == "center"
        st, f = self.st, self.st["font"]
        if photo:
            t = self.table([self.text_w - PHOTO_W - 200, PHOTO_W + 200])
            box, pc = t.rows[0].cells
            _cell_margins(pc, left=200)
            pp = pc.paragraphs[0]
            pp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            pp.add_run().add_picture(str(photo), width=Twips(PHOTO_W))
            self.use(self.cell(box), self.text_w - PHOTO_W - 200)
        align = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
        p = self.p(align)
        _spacing(p, 0, 1)
        _run(p, prof.name, st.get("name_pt", 20), st["navy"], bold=True, font=f)
        if tagline:
            p = self.p(align)
            _spacing(p, 0, 2)
            _run(p, tagline, self.body_pt + 1, st["tagline"], italic=center, font=f)
        c = prof.contact
        p = self.p(align)
        _spacing(p, 0, 0)
        first = True
        for b in [x for x in (c.location, c.phone) if x]:
            if not first:
                _run(p, "  |  ", 8.5, st["muted"], font=f)
            _run(p, b, 8.5, st["contact"], font=f)
            first = False
        if c.email:
            if not first:
                _run(p, "  |  ", 8.5, st["muted"], font=f)
            _hyperlink(p, f"mailto:{c.email}", c.email, 8.5, st["link"], f, underline=False)
            first = False
        for lbl, url in (("LinkedIn", c.linkedin), ("GitHub", c.github)):
            if url:
                if not first:
                    _run(p, "  |  ", 8.5, st["muted"], font=f)
                _hyperlink(p, url, lbl, 8.5, st["link"], f, underline=False)
                first = False
        self.use(self.doc, self.text_w)
        rule = self.p()
        _spacing(rule, 0, 2)
        _bottom_border(rule, st["teal"], 8 if kind != "center" else 4)
        self.base(self.doc, self.text_w)

    def header_band(self, prof: CVProfile, tagline: str, photo: Optional[Path] = None) -> None:
        st = self.st
        pad = 300 if self.compact == 0 else 220
        band = st.get("band", st["navy"])
        # Same set-up as your Word-saved master CV: exactly the page width, pulled left by the side margin
        over = 0
        if photo:      # your photo on the right of the band, the text centred in the rest
            pw = PHOTO_W + self.side
            t = self.doc.add_table(rows=1, cols=2)
            _tbl_props(t, PAGE_W, [PAGE_W - pw, pw], indent=-self.side)
            cell, pc = t.rows[0].cells
            for c in (cell, pc):
                _set_cell_shading(c, band)
            _cell_margins(cell, top=pad, bottom=pad - 60, left=self.side + PHOTO_W // 2, right=120)
            _cell_margins(pc, top=pad - 120, bottom=pad - 160, left=0, right=self.side)
            pp = pc.paragraphs[0]
            pp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _spacing(pp, 0, 0)
            pp.add_run().add_picture(str(photo), width=Twips(PHOTO_W))
        else:
            t = self.doc.add_table(rows=1, cols=1)
            _tbl_props(t, PAGE_W, [PAGE_W], indent=-self.side)
            cell = t.rows[0].cells[0]
            _set_cell_shading(cell, band)
            _cell_margins(cell, top=pad, bottom=pad - 60, left=self.side + over, right=self.side + over)
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

    # ---------------------------------------------------------- headings ---
    LABEL_W = 2350

    def heading(self, text: str) -> None:
        st, f = self.st, self.st["font"]
        kind = st.get("heading", "rule")
        self.base(self._base, self._base_w)
        before = 7 if self.compact == 0 else (5 if self.compact == 1 else 4)
        if kind == "label":                              # section name in a left column, content on the right
            t = self.table([self.LABEL_W, self.width - self.LABEL_W])
            lc, rc = t.rows[0].cells
            _cell_margins(lc, top=before * 20, right=120)
            _cell_margins(rc, top=before * 20)
            p = lc.paragraphs[0]
            _run(p, text.upper(), st["heading_pt"], st["teal"] if st["teal"] != st["navy"] else st["navy"],
                 bold=True, font=f)
            self.sec_box, self.sec_width = self.cell(rc), self.width - self.LABEL_W
            self.item_start()
            return
        p = self.p(WD_ALIGN_PARAGRAPH.CENTER if kind == "center_rule" else None)
        if kind == "plain":
            _spacing(p, before + 1, 2)
            _run(p, text, st["heading_pt"], st["teal"], bold=True, font=f)
        elif kind == "bar":
            _spacing(p, before, 3)
            _left_border(p, st["teal"], 24)
            _run(p, text.upper(), st["heading_pt"], st["navy"], bold=True, font=f)
        elif kind == "center_rule":
            _spacing(p, before, 3)
            _bottom_border(p, st["teal"], 4)
            # (no letter spacing: some PDF makers then write "E X P E R I E N C E", which an ATS cannot read)
            _run(p, text.upper(), st["heading_pt"], st["navy"], bold=True, font=f)
        else:                                            # rule
            _spacing(p, before, 2)
            _bottom_border(p, st["teal"])
            _run(p, text.upper(), st["heading_pt"], st["navy"], bold=True, font=f)

    # ------------------------------------------------------------- items ---
    def text(self, text: str, color=None, italic=False, after=0) -> None:
        self.item_start()
        p = self.p()
        _spacing(p, 0, after)
        _run(p, text, self.body_pt, color or self.st["text"], italic=italic, font=self.st["font"])

    DATE_W = 1350

    def title_line(self, bold: str, italic: str = "", dates: str = "", link: tuple[str, str] | None = None,
                   note: str = "") -> None:
        st, f = self.st, self.st["font"]
        self.item_start()
        column = st.get("dates") == "column" and self.sec_width > 6000
        if column:                           # dates in a left column (tabular Lebenslauf)
            t = self.table([self.DATE_W, self.sec_width - self.DATE_W])
            dc, rc = t.rows[0].cells
            _cell_margins(dc, top=40 if self.compact < 2 else 20, right=100)
            _cell_margins(rc, top=40 if self.compact < 2 else 20)
            dp = dc.paragraphs[0]
            _run(dp, dates or "", self.body_pt, st["muted"], font=f)
            self.use(self.cell(rc), self.sec_width - self.DATE_W)
            p = self.p()
        else:
            p = self.p()
            _spacing(p, 2 if self.compact < 2 else 1, 0)
            p.paragraph_format.tab_stops.add_tab_stop(Twips(self.width), WD_TAB_ALIGNMENT.RIGHT)
        _run(p, bold, self.body_pt + 0.5, st["text"], bold=True, font=f)
        if note:
            _run(p, f" {note}", self.body_pt, st["muted"], font=f)
        if italic:
            _run(p, "  |  ", self.body_pt, st["muted"], font=f)
            _run(p, italic, self.body_pt, st["org"], italic=True, font=f)
        if link:
            _run(p, "  |  ", self.body_pt, st["muted"], font=f)
            _hyperlink(p, link[1], link[0], self.body_pt, st.get("link_text", "0563C1"), f, italic=True)
        if dates and not column:
            _run(p, "\t" + dates, self.body_pt, st["muted"], font=f)

    def stack(self, items: list[str], label: str = "Stack") -> None:
        if not items:
            return
        p = self.p()
        _spacing(p, 0, 0)
        _run(p, f"{label}: " + " · ".join(items), self.body_pt - 0.5, self.st["org"], italic=True, font=self.st["font"])

    def bullets(self, items: list[str]) -> None:
        for b in items:
            p = self.p(style="List Bullet")
            pf = p.paragraph_format
            pf.left_indent, pf.first_line_indent = Twips(260), Twips(-180)
            _spacing(p, 0, 0)
            _run(p, b, self.body_pt, self.st["text"], font=self.st["font"])

    def skills_table(self, groups) -> None:
        self.item_start()
        if self.width < 4500:                # a narrow column (sidebar): category, then its items
            for g in groups:
                p = self.p()
                _spacing(p, 3, 0)
                _run(p, g.category, self.body_pt, self.st["navy"], bold=True, font=self.st["font"])
                p = self.p()
                _run(p, " · ".join(g.items), self.body_pt, self.st["text"], font=self.st["font"])
            return
        first = min(2050, self.width * 3 // 10)
        t = self.table([first, self.width - first])
        for i, g in enumerate(groups):
            row = t.rows[0] if i == 0 else t.add_row()
            for cell in row.cells:
                _cell_margins(cell, top=10, bottom=10, left=0, right=60)
            a, b = row.cells
            _run(a.paragraphs[0], g.category, self.body_pt, self.st["navy"], bold=True, font=self.st["font"])
            _run(b.paragraphs[0], " · ".join(g.items), self.body_pt, self.st["text"], font=self.st["font"])
        for row in t.rows:          # widths of rows added later
            for cell, cw in zip(row.cells, [first, self.width - first]):
                cell.width = Twips(cw)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        return path


# ------------------------------------------------------------------- CV ---
HEADINGS = {
    "en": {"summary": "Professional Summary", "skills": "Technical Skills", "experience": "Experience",
           "projects": "Projects", "education": "Education", "certifications": "Certifications",
           "languages": "Languages", "certificate": "Certificate", "contact": "Contact", "stack": "Stack"},
    "de": {"summary": "Profil", "skills": "Kenntnisse", "experience": "Berufserfahrung", "projects": "Projekte",
           "education": "Ausbildung", "certifications": "Zertifikate", "languages": "Sprachen",
           "certificate": "Zertifikat", "contact": "Kontakt", "stack": "Tools"},
}
MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
             "November", "Dezember"]


# fixed words a German CV keeps in English otherwise (they come from your main CV, not from the writer)
_DE_WORDS = [(r"\bWorking Student\b", "Werkstudent"), (r"\bInternship\b", "Praktikum"), (r"\bIntern\b(?= ·|$)", "Praktikant"),
             (r"\bFull-time\b", "Vollzeit"), (r"\bPart-time\b", "Teilzeit"), (r"\bFreelance\b", "Freiberuflich"),
             (r"\bGermany\b", "Deutschland"), (r"\bIndia\b", "Indien"), (r"\bAustria\b", "Österreich"),
             (r"\bSwitzerland\b", "Schweiz"), (r"\bNetherlands\b", "Niederlande"), (r"\bMunich\b", "München"),
             (r"\bCologne\b", "Köln"), (r"\bVienna\b", "Wien"), (r"\bOngoing\b", "laufend"), (r"\bongoing\b", "laufend"),
             (r"\bPresent\b", "heute"), (r"\bpresent\b", "heute")]


def de_words(text: str) -> str:
    import re as _re
    for rx, de in _DE_WORDS:
        text = _re.sub(rx, de, text or "")
    return text


def _german_copy(content: CVContent, prof: CVProfile) -> tuple[CVContent, CVProfile]:
    c = content.model_copy(deep=True)
    for e in c.experience:
        e.meta, e.dates = de_words(e.meta), de_words(e.dates)
    for pr in c.projects:
        pr.note, pr.dates = de_words(pr.note), de_words(pr.dates)
    for ed in c.education:
        ed.school, ed.dates, ed.degree = de_words(ed.school), de_words(ed.dates), de_words(ed.degree)
    for ce in c.certifications:
        ce.dates = de_words(ce.dates)
    p = prof.model_copy(deep=True)
    p.contact.location = de_words(p.contact.location)
    return c, p


def letter_date(d: date, lang: str = "en") -> str:
    return f"{d.day}. {MONTHS_DE[d.month - 1]} {d.year}" if lang == "de" else d.strftime("%d %B %Y")


def _section(b: Builder, key: str, content: CVContent, H: dict) -> None:
    c = content
    if key == "summary" and c.summary:
        b.heading(H["summary"])
        b.text(c.summary)
    elif key == "skills" and c.skills:
        b.heading(H["skills"])
        b.skills_table(c.skills)
    elif key == "experience" and c.experience:
        b.heading(H["experience"])
        for e in c.experience:
            org = " · ".join(x for x in (tidy(e.org), tidy(e.meta)) if x)
            b.title_line(e.title, org, e.dates)
            b.stack(e.stack, H["stack"])
            b.bullets(e.bullets)
    elif key == "projects" and c.projects:
        b.heading(H["projects"])
        for pr in c.projects:
            b.title_line(tidy_name(pr.name), "", pr.dates, link=("GitHub", pr.link) if pr.link else None, note=pr.note)
            b.stack(pr.stack, H["stack"])
            b.bullets(pr.bullets)
    elif key == "education" and c.education:
        b.heading(H["education"])
        for ed in c.education:
            b.title_line(tidy_name(ed.degree), tidy(ed.school), ed.dates)
            b.bullets(ed.bullets)
    elif key == "certifications" and c.certifications:
        b.heading(H["certifications"])
        for ct in c.certifications:
            b.title_line(" – ".join(x for x in (ct.name, ct.issuer.split("|")[0].strip()) if x), "", ct.dates,
                         link=(H["certificate"], ct.link) if ct.link else None)
            b.bullets(ct.bullets)
    elif key == "languages" and c.languages:
        b.heading(H["languages"])
        if b.sec_width < 4500:
            for lang in c.languages:
                b.text(f"{lang.language}: {lang.level}")
        else:
            b.text("   |   ".join(f"{lang.language}: {lang.level}" for lang in c.languages))


def _sidebar_cv(b: Builder, content: CVContent, prof: CVProfile, H: dict, photo: Optional[Path]) -> None:
    """Shaded side column (photo, contact, skills, languages, certificates) and the main column."""
    st, f = b.st, b.st["font"]
    sw = st.get("sidebar_w", 3300)
    t = b.doc.add_table(rows=1, cols=2)
    _tbl_props(t, PAGE_W, [sw, PAGE_W - sw])
    side, main = t.rows[0].cells
    _set_cell_shading(side, st.get("sidebar_bg", "EEF2F7"))
    trPr = t.rows[0]._tr.get_or_add_trPr()            # the shaded column runs down the whole page
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), str(PAGE_H - BOTTOM - 40))
    h.set(qn("w:hRule"), "atLeast")
    trPr.append(h)
    pad = 520 if b.compact == 0 else 400
    _cell_margins(side, top=pad, bottom=200, left=420, right=320)
    _cell_margins(main, top=pad, bottom=200, left=420, right=560)
    # --- side column
    b.base(b.cell(side), sw - 740)
    if photo:
        p = b.p(WD_ALIGN_PARAGRAPH.CENTER)
        _spacing(p, 0, 6)
        p.add_run().add_picture(str(photo), width=Twips(1500))
    b.heading(H["contact"])
    cn = prof.contact
    for x in (cn.location, cn.phone):
        if x:
            b.text(x)
    for lbl, url in ((cn.email, f"mailto:{cn.email}" if cn.email else ""), ("LinkedIn", cn.linkedin),
                     ("GitHub", cn.github)):
        if url:
            p = b.p()
            _hyperlink(p, url, lbl, b.body_pt, st["link"], f, underline=False)
    for key in ("skills", "languages", "certifications"):
        _section(b, key, content, H)
    # --- main column
    b.base(b.cell(main), PAGE_W - sw - 980)
    p = b.p()
    _spacing(p, 0, 1)
    _run(p, prof.name, st.get("name_pt", 20), st["navy"], bold=True, font=f)
    if content.tagline:
        p = b.p()
        _spacing(p, 0, 2)
        _run(p, content.tagline, b.body_pt + 1, st["tagline"], font=f)
    for key in ("summary", "experience", "projects", "education"):
        _section(b, key, content, H)


def render_cv(content: CVContent, prof: CVProfile, style: dict, path: Path, compact: int = 0,
              photo: Optional[Path] | str = "auto") -> Path:
    """photo: 'auto' = your setting on My CV (Automatic: German CVs only / Always / Never), or a file, or None.
    Templates without a photo area never show one."""
    if photo == "auto":
        from ..cv.service import photo_for
        photo = photo_for(content.lang)
    if not style.get("photo_area", True):
        photo = None
    if content.lang == "de":
        content, prof = _german_copy(content, prof)
    b = Builder(style, compact)
    H = HEADINGS.get(content.lang, HEADINGS["en"])
    if style.get("header") == "sidebar":
        _sidebar_cv(b, content, prof, H, photo)
        return b.save(path)
    b.header(prof, content.tagline, photo)
    for key in style.get("order") or ["summary", "skills", "experience", "projects", "education",
                                      "certifications", "languages"]:
        _section(b, key, content, H)
    return b.save(path)


# --------------------------------------------------------------- letter ---
def render_letter(letter: LetterContent, prof: CVProfile, tagline: str, style: dict, path: Path,
                  company: str = "", today: Optional[date] = None) -> Path:
    st = dict(style)
    if st.get("header") == "sidebar":             # a letter is one column: the same colours, a plain header
        st.update(header="left", side=SIDE, top=560)
    b = Builder(st)
    size = 10
    b.header(prof, tagline)
    f = st["font"]
    p = b.p(WD_ALIGN_PARAGRAPH.RIGHT)
    _spacing(p, 16, 10)
    loc = de_words(prof.contact.location) if letter.lang == "de" else prof.contact.location
    _run(p, f"{loc}, {letter_date(today or date.today(), letter.lang)}".strip(", "), size,
         st["muted"], font=f)
    if letter.recipient or company:
        p = b.p()
        _spacing(p, 0, 8)
        _run(p, letter.recipient or (f"Personalabteilung, {company}" if letter.lang == "de" else f"Hiring Team, {company}"),
             size, st["text"], font=f)
    p = b.p()
    _spacing(p, 0, 10)
    _run(p, letter.subject, size + 0.5, st["navy"], bold=True, font=f)
    p = b.p()
    _spacing(p, 0, 8)
    _run(p, letter.greeting, size, st["text"], font=f)
    for para in letter.paragraphs:
        p = b.p()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _spacing(p, 0, 8, line=1.08)
        _run(p, para, size, st["text"], font=f)
    p = b.p()
    _spacing(p, 6, 2)
    _run(p, letter.closing, size, st["text"], font=f)
    p = b.p()
    _run(p, prof.name, size, st["navy"], bold=True, font=f)
    return b.save(path)
