"""Reads an application form on the page and fills it (you watch and press Submit yourself).

Colours on the page:  green = filled from your details / saved answers / files,
amber = written by Claude (read it), red = please answer, blue = consent box for you.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .fields import Context, Decision, Field, claude_rest, decide

log = logging.getLogger(__name__)

# Collects every visible form field with its best label. Gives each one a data-ja-id.
COLLECT_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none') || el.type === 'file'; };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\*+/g, '').trim();
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(l => l.innerText).join(' '));
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(' ').map(i => document.getElementById(i)).filter(Boolean).map(n => n.innerText).join(' '); if (t) return clean(t); }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText);
    if (el.type === 'radio' || el.type === 'checkbox') {
      const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg && el.type === 'radio') return clean(lg.innerText);
    }
    let n = el; for (let i = 0; i < 4 && n; i++) { n = n.parentElement; if (!n) break;
      const c = [...n.childNodes].filter(x => x !== el && !(x.contains && x.contains(el)));
      const t = clean(c.map(x => x.innerText || x.textContent || '').join(' ')); if (t && t.length < 300) return t; }
    return '';
  };
  const out = []; let k = 0; const seenRadio = {};
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type) || el.disabled || !vis(el)) continue;
    if (type === 'radio') {
      const g = el.name || el.id; if (seenRadio[g]) { seenRadio[g].options.push(labelOf(el) || el.value); el.setAttribute('data-ja-id', seenRadio[g].fid); continue; }
      const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
      let q = lg ? clean(lg.innerText) : '';
      if (!q) { let n = el.parentElement; for (let i = 0; i < 5 && n; i++) { n = n.parentElement; if (n && n.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
        const t = clean([...n.childNodes].filter(x => !(x.querySelector && x.querySelector('input'))).map(x => x.innerText || x.textContent || '').join(' ')); if (t) { q = t; break; } } } }
      const fid = 'ja' + (k++); el.setAttribute('data-ja-id', fid);
      const item = { fid, tag: 'input', type: 'radio', label: q, name: el.name || '', placeholder: '', required: el.required,
                     options: [labelOf(el) || el.value], value: '' };
      seenRadio[g] = item; out.push(item); continue;
    }
    const fid = 'ja' + (k++); el.setAttribute('data-ja-id', fid);
    out.push({ fid, tag: el.tagName.toLowerCase(), type, label: labelOf(el), name: el.name || el.id || '',
      placeholder: el.placeholder || '', required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      options: el.tagName === 'SELECT' ? [...el.options].map(o => clean(o.text)).filter(Boolean) : [],
      value: type === 'checkbox' ? (el.checked ? 'checked' : '') : (type === 'file' ? (el.files && el.files.length ? 'file' : '') :
             (el.tagName === 'SELECT' ? (el.selectedIndex > 0 ? clean(el.options[el.selectedIndex].text) : '') : (el.value || ''))) });
  }
  for (const r of out) if (r.type === 'radio') {
    const checked = document.querySelector(`input[type=radio][data-ja-id="${r.fid}"]:checked`); r.value = checked ? 'checked' : '';
  }
  return out;
}
"""

MARK_JS = r"""
([fid, colour, note]) => {
  const els = document.querySelectorAll(`[data-ja-id="${fid}"]`);
  const c = {green: '#16a34a', amber: '#d97706', red: '#dc2626', blue: '#2563eb'}[colour];
  els.forEach(el => { el.style.outline = `3px solid ${c}`; el.style.outlineOffset = '2px'; if (note) el.title = note; });
}
"""

READ_JS = r"""
(fids) => fids.map(fid => { const els = [...document.querySelectorAll(`[data-ja-id="${fid}"]`)]; if (!els.length) return [fid, null];
  const el = els[0]; if (el.type === 'radio') { const c = els.find(e => e.checked); if (!c) return [fid, '']; const l = c.labels && c.labels[0];
    return [fid, (l ? l.innerText : c.value).trim()]; }
  if (el.tagName === 'SELECT') return [fid, el.selectedIndex > 0 ? el.options[el.selectedIndex].text.trim() : ''];
  if (el.type === 'checkbox') return [fid, el.checked ? 'Yes' : ''];
  return [fid, (el.value || '').trim()]; })
"""

DONE_TEXT = re.compile(
    r"thank(s| you) for (your )?(application|applying|bewerbung)|application (has been |was )?(received|submitted|sent)|"
    r"we('ve| have) received your application|successfully (submitted|applied)|vielen dank für ihre bewerbung|"
    r"danke für deine bewerbung|bewerbung (ist |wurde )?(erfolgreich )?(eingegangen|versendet|übermittelt|abgeschickt)|"
    r"ihre bewerbung (ist|wurde) bei uns eingegangen|you have (successfully )?applied", re.I)

APPLY_BUTTON = re.compile(
    r"^\s*(apply( now| for this job| for this position)?|jetzt bewerben|bewerben|zur bewerbung|online bewerben|"
    r"apply on (the )?company('s)? (website|site)|auf (der )?unternehmenswebsite bewerben|bewerbung starten|i'm interested)\s*$", re.I)


@dataclass
class FillReport:
    filled: list[dict] = field(default_factory=list)       # {label, value, source}
    drafted: list[dict] = field(default_factory=list)      # {fid, label, value}
    open: list[dict] = field(default_factory=list)         # {fid, label, why}
    consent: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    fields_seen: int = 0

    def as_dict(self) -> dict:
        return {"filled": self.filled, "drafted": self.drafted, "open": self.open, "consent": self.consent,
                "uploaded": self.uploaded, "fields_seen": self.fields_seen}


async def collect(page) -> list[Field]:
    rows = []
    for frame in [page.main_frame]:
        try:
            rows += await frame.evaluate(COLLECT_JS)
        except Exception as e:
            log.debug("collect failed: %s", e)
    return [Field(**r) for r in rows]


async def _apply(page, f: Field, d: Decision) -> bool:
    sel = f'[data-ja-id="{f.fid}"]'
    try:
        if d.action == "upload":
            await page.set_input_files(sel, d.value)
        elif f.type == "radio":
            loc = page.locator(sel)
            n = await loc.count()
            for i in range(n):
                el = loc.nth(i)
                lab = await el.evaluate("e => ((e.labels && e.labels[0] && e.labels[0].innerText) || e.value || '').trim()")
                if lab.strip().lower() == d.value.strip().lower():
                    await el.check(force=True)
                    return True
            return False
        elif f.tag == "select":
            await page.select_option(sel, label=d.value)
        elif f.type == "checkbox":
            if d.value.lower() in ("yes", "ja", "true", "checked"):
                await page.check(sel, force=True)
        else:
            await page.fill(sel, d.value)
        return True
    except Exception as e:
        log.info("could not fill %s (%s): %s", f.label, f.fid, e)
        return False


async def fill_page(page, ctx: Context, done_fids: Optional[set] = None) -> tuple[FillReport, dict[str, Field]]:
    """Fill every field on the current page once. Returns the report and the fields left for you."""
    done_fids = done_fids if done_fids is not None else set()
    report, waiting = FillReport(), {}
    fields = await collect(page)
    report.fields_seen = len(fields)
    files = 0
    todo, decisions = [], {}
    for f in fields:
        if f.fid in done_fids:
            continue
        idx = files if f.type == "file" else 0
        if f.type == "file":
            files += 1
        decisions[f.fid] = decide(f, ctx, file_index=idx)
        todo.append(f)
    claude_rest(todo, decisions, ctx)                 # the questions still open -> one Claude call
    for f in todo:
        d = decisions[f.fid]
        done_fids.add(f.fid)
        if d.action in ("fill", "upload", "draft"):
            ok = await _apply(page, f, d)
            if not ok:
                d = Decision("leave", why=f"'{f.label[:80]}' - could not be filled automatically")
        if d.action == "fill":
            report.filled.append({"label": f.label or f.name, "value": d.value, "source": d.source})
            await page.evaluate(MARK_JS, [f.fid, "green", f"Filled from {d.source}"])
        elif d.action == "upload":
            report.uploaded.append(d.value.replace("\\", "/").rsplit("/", 1)[-1])
            await page.evaluate(MARK_JS, [f.fid, "green", "File uploaded"])
        elif d.action == "draft":
            report.drafted.append({"fid": f.fid, "label": f.label, "value": d.value})
            waiting[f.fid] = f
            await page.evaluate(MARK_JS, [f.fid, "amber", d.why])
        elif d.action == "consent":
            report.consent.append(f.label[:120])
            await page.evaluate(MARK_JS, [f.fid, "blue", d.why])
        elif d.action == "leave":
            report.open.append({"fid": f.fid, "label": f.label or f.placeholder or f.name, "why": d.why,
                                "required": f.required})
            waiting[f.fid] = f
            await page.evaluate(MARK_JS, [f.fid, "red", d.why])
    return report, waiting


async def read_values(page, fids: list[str]) -> dict[str, str]:
    try:
        pairs = await page.evaluate(READ_JS, fids)
        return {k: v for k, v in pairs if v}
    except Exception:
        return {}


async def page_says_done(page) -> bool:
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 20000) : ''")
    except Exception:
        return False
    return bool(DONE_TEXT.search(text or ""))


async def find_apply_link(page) -> Optional[str]:
    """On a job posting page: the button/link that leads to the application form."""
    cands = await page.evaluate(r"""() => [...document.querySelectorAll('a, button')].map((e, i) => {
        e.setAttribute('data-ja-btn', 'b' + i);
        return ['b' + i, (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 80), e.href || '',
                e.getBoundingClientRect().width > 0]; })""")
    for bid, text, href, visible in cands:
        if visible and APPLY_BUTTON.search(text or ""):
            return bid
    return None
