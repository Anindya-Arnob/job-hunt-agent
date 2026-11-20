"""The CV templates you can switch between on My CV. All are one page, A4, ATS-readable text (no text boxes,
no pictures of text), headings 10 pt and body 9 pt, so every check works the same for each.

Built from what European / German recruiters and ATS guides recommend (single column first, tabular
"Lebenslauf" with dates on the left, Europass-like label column, an optional photo top right). Original
designs: no copied paid templates.
"""
from __future__ import annotations

ORDER = ["summary", "skills", "experience", "projects", "education", "certifications", "languages"]

# palette keys: navy = main colour (names, headings), teal = accent (rules, bars), text, muted, org (organisation,
# stack), tagline / contact / link (on the header), band = header background
TEMPLATES: list[dict] = [
    {"id": "classic", "name": "Classic Band", "photo": True, "ats": "best",
     "desc": "Your current look: dark header band across the top, underlined headings. Uses the colours of the "
             "template you uploaded.",
     "header": "band", "heading": "rule", "dates": "right", "own_colours": True},
    {"id": "ats", "name": "Clean ATS", "photo": False, "ats": "best",
     "desc": "Plain black and white, one column, no photo. The safest choice for big company portals "
             "(Workday, SAP SuccessFactors, Greenhouse).",
     "header": "left", "heading": "rule", "dates": "right", "font": "Arial", "name_pt": 18, "top": 560,
     "navy": "111111", "teal": "111111", "text": "1A1A1A", "muted": "555555", "org": "333333",
     "tagline": "333333", "contact": "333333", "link": "1A1A1A"},
    {"id": "lebenslauf", "name": "Lebenslauf (tabular)", "photo": True, "ats": "good",
     "desc": "The classic German tabellarischer Lebenslauf: dates in a left column, details on the right, "
             "photo top right. Ideal for German companies and Mittelstand.",
     "header": "left", "heading": "rule", "dates": "column", "font": "Calibri", "name_pt": 20, "top": 560,
     "order": ["summary", "experience", "projects", "education", "skills", "certifications", "languages"],
     "navy": "1F3A5F", "teal": "1F3A5F", "text": "1A1A1A", "muted": "5A6475", "org": "3B4A5E",
     "tagline": "3B4A5E", "contact": "3B4A5E", "link": "1F3A5F"},
    {"id": "europass", "name": "Europass style", "photo": True, "ats": "good",
     "desc": "Inspired by the EU Europass CV: section names in a blue left column, content on the right, "
             "languages with CEFR levels. Good for EU institutions, research and public sector.",
     "header": "left", "heading": "label", "dates": "right", "font": "Arial", "name_pt": 18, "top": 560,
     "navy": "003399", "teal": "003399", "text": "1A1A1A", "muted": "5C6B7A", "org": "2B3A4A",
     "tagline": "003399", "contact": "2B3A4A", "link": "003399"},
    {"id": "modern", "name": "Modern Accent", "photo": True, "ats": "best",
     "desc": "Clean modern layout with a teal accent bar on each heading. Fits startups and tech companies.",
     "header": "left", "heading": "bar", "dates": "right", "font": "Calibri", "name_pt": 22, "top": 560,
     "navy": "0F3D3E", "teal": "0F766E", "text": "1A1A1A", "muted": "5B6B6B", "org": "2F4F4F",
     "tagline": "0F766E", "contact": "3F4F4F", "link": "0F766E"},
    {"id": "executive", "name": "Executive Serif", "photo": False, "ats": "best",
     "desc": "Centred name and serif type for a formal, traditional feel: consulting, finance, law, banking.",
     "header": "center", "heading": "center_rule", "dates": "right", "font": "Georgia", "name_pt": 20, "top": 600,
     "navy": "2B2B2B", "teal": "7A1F2B", "text": "1F1F1F", "muted": "666666", "org": "444444",
     "tagline": "7A1F2B", "contact": "444444", "link": "7A1F2B"},
    {"id": "compact", "name": "Compact Tech", "photo": False, "ats": "best",
     "desc": "Dense layout with skills and projects first, for students and engineers with many projects.",
     "header": "left", "heading": "plain", "dates": "right", "font": "Calibri", "name_pt": 18, "top": 440,
     "side": 560, "order": ["summary", "skills", "projects", "experience", "education", "certifications",
                            "languages"],
     "navy": "1E293B", "teal": "2563EB", "text": "1A1A1A", "muted": "64748B", "org": "334155",
     "tagline": "2563EB", "contact": "334155", "link": "2563EB"},
    {"id": "sidebar", "name": "Sidebar", "photo": True, "ats": "ok",
     "desc": "Two columns: photo, contact, skills and languages in a shaded side column. Eye-catching; some "
             "older ATS portals read two columns less well, so prefer it for e-mail applications.",
     "header": "sidebar", "heading": "rule", "dates": "right", "font": "Calibri", "name_pt": 20, "top": 0,
     "side": 0, "sidebar_w": 3300, "sidebar_bg": "EEF2F7",
     "navy": "1E3A5F", "teal": "4A7AB0", "text": "1A1A1A", "muted": "5A6475", "org": "34495E",
     "tagline": "4A7AB0", "contact": "34495E", "link": "1E3A5F"},
    {"id": "minimal", "name": "Minimal Line", "photo": False, "ats": "best",
     "desc": "Light and airy: grey type, thin lines, lots of white space. Design and product roles.",
     "header": "left", "heading": "rule", "dates": "right", "font": "Calibri", "name_pt": 24, "top": 600,
     "navy": "333333", "teal": "BBBBBB", "text": "2A2A2A", "muted": "8A8A8A", "org": "555555",
     "tagline": "777777", "contact": "666666", "link": "333333"},
    {"id": "swiss", "name": "Swiss Grid", "photo": True, "ats": "good",
     "desc": "Strict grid: bold section names in a left column, red accent, Arial. Clear and confident; "
             "popular in DACH design and engineering.",
     "header": "left", "heading": "label", "dates": "right", "font": "Arial", "name_pt": 22, "top": 560,
     "navy": "111111", "teal": "D62828", "text": "1A1A1A", "muted": "666666", "org": "333333",
     "tagline": "D62828", "contact": "333333", "link": "D62828"},
]
BY_ID = {t["id"]: t for t in TEMPLATES}
LAYOUT_KEYS = ("header", "heading", "dates", "font", "name_pt", "top", "side", "order", "sidebar_w", "sidebar_bg")
COLOUR_KEYS = ("navy", "teal", "text", "muted", "org", "tagline", "contact", "link")


def get(tid: str) -> dict:
    return BY_ID.get(tid or "classic", BY_ID["classic"])


def style_for(tid: str, own: dict) -> dict:
    """The style dict the renderer uses: your uploaded colours for Classic, the template's own for the rest."""
    t = get(tid)
    style = dict(own)
    if not t.get("own_colours"):
        style.update({k: t[k] for k in COLOUR_KEYS if k in t})
    style.update({k: t[k] for k in LAYOUT_KEYS if k in t})
    style.setdefault("header", "band")
    style.setdefault("heading", "rule")
    style.setdefault("dates", "right")
    style.setdefault("order", ORDER)
    style["template"] = t["id"]
    style["photo_area"] = t["photo"]
    style["heading_pt"], style["body_pt"] = 10, 9          # your prompt fixes these sizes for every template
    return style


def public() -> list[dict]:
    return [{k: t[k] for k in ("id", "name", "desc", "photo", "ats")} for t in TEMPLATES]
