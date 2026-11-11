// Job Hunt Agent – Apply helper (runs on the page you are looking at, only when you ask or follow a job).
// Reads the form, asks the app on your PC what to fill, fills it, colours it. You press Submit.
(() => {
  // Already running on this page? Only if that copy still talks to the extension. After the extension is
  // reloaded or updated, the old copy is cut off ("context invalidated"): then this fresh copy takes over.
  if (window.__jhaAlive && window.__jhaAlive()) return
  document.getElementById('jha-panel')?.remove()
  window.__jhaAlive = () => { try { return !!chrome.runtime?.id } catch (e) { return false } }

  const once = (msg) => new Promise((res) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => {
        const err = chrome.runtime.lastError
        res(r || { ok: false, error: err ? err.message : 'no answer', retry: true })
      })
    } catch (e) { res({ ok: false, error: String(e.message || e), dead: true }) }
  })
  // the extension's background wakes up on the first message: try again a few times before giving up
  const send = async (msg) => {
    let r
    for (let i = 0; i < 4; i++) {
      r = await once(msg)
      if (r.ok || r.dead || !r.retry) return r
      await new Promise((ok) => setTimeout(ok, 250 * (i + 1)))
    }
    return r
  }
  const api = async (method, path, body) => { const r = await send({ type: 'api', method, path, body }); if (!r.ok) throw new Error(r.error); return r.data }
  // what to tell you when the app can't be reached, by the real reason
  const whyNot = (e) => {
    const m = String((e && e.message) || e || '')
    if (/context invalidated|Extension context|dead/i.test(m) || !window.__jhaAlive())
      return '⚠ The helper was updated. Reload this page (F5), then press Fill this page.'
    if (/Failed to fetch|NetworkError|ERR_CONNECTION|Load failed/i.test(m))
      return '⚠ The Job Hunt Agent app is not running on this PC (start scripts\\start.bat).'
    return `⚠ The app answered with an error: ${m.slice(0, 160)}`
  }

  const DONE = /thank(s| you) for (your )?(application|applying|bewerbung)|application (has been |was )?(received|submitted|sent)|we('ve| have) received your application|successfully (submitted|applied)|vielen dank für (ihre|deine) bewerbung|danke für (ihre|deine) bewerbung|bewerbung (ist |wurde )?(erfolgreich )?(eingegangen|versendet|übermittelt|abgeschickt)|ihre bewerbung (ist|wurde) bei uns eingegangen|you have successfully applied|bewerbung erfolgreich/i
  const APPLY = /^\s*(apply( now| for this job| for this position)?|jetzt bewerben|bewerben|zur bewerbung|online bewerben|apply on (the )?company('s)? (website|site)|auf (der )?unternehmenswebsite bewerben|bewerbung starten|i'm interested|easy apply)\s*$/i
  const COLOURS = { green: '#16a34a', amber: '#d97706', red: '#dc2626', blue: '#2563eb' }

  let job = null, decisions = {}, labels = {}, kept = {}, k = 0
  let started = false, stopped = false, watcher = null, lastUser = 0, busy = false, submittedAt = 0, hinted = false
  const touched = new Set()
  // you are working on the page: remember which fields are yours and when you last did something
  for (const ev of ['keydown', 'mousedown', 'input', 'change', 'paste']) {
    document.addEventListener(ev, (e) => {
      if (!e.isTrusted) return
      lastUser = Date.now()
      const t = e.target && e.target.closest && e.target.closest('[data-jha-id]')
      if (t && ['input', 'change', 'paste', 'keydown'].includes(ev)) touched.add(t.dataset.jhaId)
    }, true)
  }

  // ------------------------------------------------------------ read fields
  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\*+/g, '').trim()
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el)
    return (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none') || el.type === 'file' }
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean([...el.labels].map((l) => l.innerText).join(' '))
    const by = el.getAttribute('aria-labelledby')
    if (by) { const t = by.split(' ').map((i) => document.getElementById(i)).filter(Boolean).map((n) => n.innerText).join(' '); if (t) return clean(t) }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'))
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText)
    let n = el
    for (let i = 0; i < 4 && n; i++) {
      n = n.parentElement; if (!n) break
      const c = [...n.childNodes].filter((x) => x !== el && !(x.contains && x.contains(el)))
      const t = clean(c.map((x) => x.innerText || x.textContent || '').join(' ')); if (t && t.length < 300) return t
    }
    return ''
  }
  const limitOf = (el) => {
    if (el.maxLength > 0 && el.maxLength < 100000) return el.maxLength
    if (el.tagName !== 'TEXTAREA' && el.type !== 'text') return 0
    let n = el.parentElement
    for (let i = 0; i < 3 && n; i++, n = n.parentElement) {
      const m = (n.innerText || '').match(/(?:^|\s)\d{1,5}\s*\/\s*(\d{2,5})(?:\s|$)|max(?:imal|imum)?\.?\s*(\d{2,5})\s*(?:zeichen|characters|chars)/i)
      if (m) return +(m[1] || m[2])
    }
    return 0
  }
  function collect() {
    const out = []; const radios = {}
    for (const el of document.querySelectorAll('input, select, textarea')) {
      const type = (el.type || el.tagName).toLowerCase()
      if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(type) || el.disabled || !vis(el)) continue
      if (el.closest('#jha-panel')) continue
      if (type === 'radio') {
        const g = el.name || el.id
        if (radios[g]) { radios[g].options.push(labelOf(el) || el.value); el.dataset.jhaId = radios[g].fid; continue }
        const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend')
        let q = lg ? clean(lg.innerText) : ''
        if (!q) { let n = el.parentElement; for (let i = 0; i < 5 && n; i++) { n = n.parentElement
          if (n && n.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
            const t = clean([...n.childNodes].filter((x) => !(x.querySelector && x.querySelector('input'))).map((x) => x.innerText || x.textContent || '').join(' ')); if (t) { q = t; break } } } }
        const fid = el.dataset.jhaId || ('f' + (k++)); el.dataset.jhaId = fid
        const item = { fid, tag: 'input', type: 'radio', label: q, name: el.name || '', placeholder: '', required: el.required, options: [labelOf(el) || el.value],
                       value: document.querySelector(`input[type=radio][name="${CSS.escape(el.name)}"]:checked`) ? 'checked' : '' }
        radios[g] = item; out.push(item); continue
      }
      const fid = el.dataset.jhaId || ('f' + (k++)); el.dataset.jhaId = fid
      out.push({ fid, tag: el.tagName.toLowerCase(), type, label: labelOf(el), name: el.name || el.id || '', placeholder: el.placeholder || '',
        required: !!(el.required || el.getAttribute('aria-required') === 'true'), maxlength: limitOf(el),
        options: el.tagName === 'SELECT' ? [...el.options].map((o) => clean(o.text)).filter(Boolean) : [],
        value: type === 'checkbox' ? (el.checked ? 'checked' : '') : type === 'file' ? (el.files && el.files.length ? 'file' : '')
          : el.tagName === 'SELECT' ? (el.selectedIndex > 0 ? clean(el.options[el.selectedIndex].text) : '') : (el.value || '') })
      labels[fid] = out[out.length - 1].label
    }
    for (const r of out) if (r.type === 'radio') labels[r.fid] = r.label
    return out
  }

  // ----------------------------------------------------------- fill fields
  const setNative = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; setter.call(el, v)
    el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }))
  }
  const mark = (fid, colour, note) => document.querySelectorAll(`[data-jha-id="${fid}"]`).forEach((el) => {
    el.style.outline = `3px solid ${COLOURS[colour]}`; el.style.outlineOffset = '2px'; if (note) el.title = note })

  async function putFile(el, kind) {
    const f = await api('GET', `/api/ext/file/${job.id}/${kind}`)
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0))
    const dt = new DataTransfer(); dt.items.add(new File([bytes], f.name, { type: f.mime })); el.files = dt.files
    el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }))
    return f.name
  }

  const isEmpty = (els) => {
    const el = els[0]
    if (el.type === 'radio') return !els.some((r) => r.checked)
    if (el.type === 'checkbox') return !el.checked
    if (el.type === 'file') return !(el.files && el.files.length)
    if (el.tagName === 'SELECT') return el.selectedIndex <= 0
    return !(el.value || '').trim()
  }
  async function applyDecision(d) {
    const els = [...document.querySelectorAll(`[data-jha-id="${d.fid}"]`)]; if (!els.length) return false
    if (touched.has(d.fid) || !isEmpty(els)) return 'kept'          // yours: never overwritten
    const el = els[0]
    if (d.action === 'upload') {
      await putFile(el, (d.file && d.file.kind) || 'cv'); return true
    }
    if (el.type === 'radio') {
      const hit = els.find((r) => clean((r.labels && r.labels[0] && r.labels[0].innerText) || r.value).toLowerCase() === d.value.toLowerCase())
      if (!hit) return false; if (!hit.checked) hit.click(); return true
    }
    if (el.tagName === 'SELECT') {
      const opt = [...el.options].find((o) => clean(o.text).toLowerCase() === d.value.toLowerCase()); if (!opt) return false
      setNative(el, opt.value); return true
    }
    if (el.type === 'checkbox') { if (/^(yes|ja|true|checked)$/i.test(d.value) && !el.checked) el.click(); return true }
    const lim = limitOf(el)
    setNative(el, lim && d.value.length > lim ? d.value.slice(0, lim) : d.value); return true
  }

  function holdStill() {
    const x = scrollX, y = scrollY, since = Date.now(), focus = document.activeElement
    const boxes = [...document.querySelectorAll('*')].filter((e) => e.scrollTop > 0).map((e) => [e, e.scrollTop])
    const back = () => {
      if (lastUser > since) return                       // you scrolled or typed meanwhile: leave it alone
      if (Math.abs(scrollY - y) > 2 || Math.abs(scrollX - x) > 2) scrollTo({ left: x, top: y, behavior: 'instant' })
      for (const [e, t] of boxes) if (Math.abs(e.scrollTop - t) > 2) e.scrollTop = t
      if (document.activeElement !== focus && document.activeElement && !document.activeElement.closest('#jha-panel')) {
        try { focus && focus !== document.body ? focus.focus({ preventScroll: true }) : document.activeElement.blur() } catch (e) {}
      }
    }
    return back
  }
  async function fill(auto) {
    if (busy || stopped || !job) return
    busy = true
    const back = holdStill()
    try { await fillInner(auto, back) } finally {
      back(); requestAnimationFrame(back); setTimeout(back, 300); setTimeout(back, 900)   // pages that scroll after a change
      busy = false
    }
  }
  async function reapply() {            // earlier answers into fields that are empty again (page re-drawn) – never yours
    for (const d of Object.values(decisions)) {
      if (!['fill', 'upload', 'draft'].includes(d.action)) continue
      try { await applyDecision(d) } catch (e) {}
    }
  }
  async function fillInner(auto, back) {
    if (!auto) await reapply()
    status('Reading the form…')
    const all = collect()
    const fields = all.filter((f) => !decisions[f.fid])
    const formLike = all.filter((f) => ['text', 'email', 'tel', 'file', 'textarea', 'select-one'].includes(f.type) || f.tag === 'textarea' || f.tag === 'select').length
    if (formLike < 2 && !Object.keys(decisions).length) {
      const btn = [...document.querySelectorAll('a, button')].find((b) => APPLY.test((b.innerText || '').trim()) && b.getBoundingClientRect().width > 0 && !b.closest('#jha-panel'))
      if (btn) { status('This is the job posting.', btn); return }
    }
    if (!fields.length) { status(auto ? 'Waiting for the application form…' : 'No new fields on this page.'); return }
    status(`Asking the app… (${fields.length} fields${fields.some((f) => f.tag === 'textarea') ? ', Claude may write a few answers – up to a minute' : ''})`)
    let res
    try { res = await api('POST', '/api/ext/decide', { job_id: job && job.id, url: location.href, fields }) } catch (e) { status('⚠ ' + e.message); return }
    const n = { filled: 0, open: 0, draft: 0, consent: 0, files: 0 }
    for (const d of res.decisions) {
      decisions[d.fid] = d
      if (['fill', 'upload', 'draft'].includes(d.action)) {
        let ok = false; try { ok = await applyDecision(d) } catch (e) { ok = false }
        back()
        if (ok === 'kept') { d.action = 'skip'; continue }
        if (!ok) { d.action = 'leave'; d.why = 'Could not be filled automatically – please fill in' }
      }
      if (d.action === 'fill') { n.filled++; mark(d.fid, 'green', `Filled from ${d.source}`) }
      else if (d.action === 'upload') { n.files++; mark(d.fid, 'green', 'File uploaded') }
      else if (d.action === 'draft') { n.draft++; kept[d.fid] = d.value; mark(d.fid, 'amber', d.why) }
      else if (d.action === 'consent') { n.consent++; mark(d.fid, 'blue', d.why) }
      else if (d.action === 'leave') { n.open++; mark(d.fid, 'red', d.why) }
    }
    status(`✓ ${n.filled + n.files} filled · ${n.draft} drafts to read · ${n.open} for you · ${n.consent} consent boxes. Check, then press Submit.`)
  }

  // ---------------------------------------------- learn + confirmation page
  function answersToLearn() {
    const out = []
    for (const [fid, d] of Object.entries(decisions)) {
      if (!['leave', 'draft'].includes(d.action) || !labels[fid]) continue
      const els = [...document.querySelectorAll(`[data-jha-id="${fid}"]`)]; if (!els.length) continue
      const el = els[0]; let v = ''
      if (el.type === 'radio') { const c = els.find((r) => r.checked); v = c ? clean((c.labels && c.labels[0] && c.labels[0].innerText) || c.value) : '' }
      else if (el.type === 'file') continue
      else if (el.tagName === 'SELECT') v = el.selectedIndex > 0 ? clean(el.options[el.selectedIndex].text) : ''
      else if (el.type === 'checkbox') continue
      else v = (el.value || '').trim()
      if (v) out.push({ question: labels[fid], answer: v, kept_draft: kept[fid] === v })
    }
    return out
  }
  const SEND = /^\s*(submit|send|senden|absenden|abschicken|submit( my)? application|send( my)? application|bewerbung (ab)?senden|bewerbung abschicken|jetzt (bewerbung )?(absenden|abschicken)|apply|apply now|jetzt bewerben|bewerben|finish|abschließen|fertigstellen|bewerbung abschließen|complete( application)?|done|fertig)\s*[→›>]?\s*$/i
  const filledFormFields = () => collect().filter((f) => f.value && f.type !== 'radio' && f.type !== 'checkbox').length
  document.addEventListener('submit', (e) => { if (e.isTrusted !== false && filledFormFields() >= 2) onSubmit() }, true)
  document.addEventListener('click', (e) => {
    if (!e.isTrusted) return
    const b = e.target.closest && e.target.closest('button, input[type=submit], a, [role=button]')
    if (!b || b.closest('#jha-panel')) return
    if (SEND.test((b.innerText || b.value || '').trim()) && filledFormFields() >= 2) onSubmit()   // a send on a filled form, not "Apply" on a posting
  }, true)
  async function onSubmit() {
    if (!job) return
    const a = answersToLearn()
    if (a.length) api('POST', '/api/ext/learn', { answers: a }).catch(() => {})
    submittedAt = Date.now()
    send({ type: 'pending', jobId: job.id })
  }
  // the confirmation must be what the page is about (heading, message box or a short page) - not a sentence in an FAQ
  function confirmationText() {
    const body = (document.body && document.body.innerText) || ''
    if (body.length < 2500 && DONE.test(body)) return true
    const sel = 'h1, h2, h3, h4, [role=alert], [role=status], [role=dialog], [aria-live], [class*=success], [class*=confirm], [class*=thank], [class*=Success], [class*=Confirm], [class*=Thank]'
    return [...document.querySelectorAll(sel)].some((e) => !e.closest('#jha-panel') && vis(e) && (e.innerText || '').length < 600 && DONE.test(e.innerText || ''))
  }
  async function confirmIfDone() {
    if (!job || !confirmationText()) return false
    const stillAForm = collect().filter((f) => ['text', 'email', 'tel', 'textarea', 'file'].includes(f.type) || f.tag === 'textarea').length >= 3
    if (stillAForm) return false
    const info = await send({ type: 'follow-info' })
    const sent = (Date.now() - submittedAt < 15 * 60e3) || (info && info.ok && info.data && info.data.pendingDone === job.id && Date.now() - (info.data.pendingAt || 0) < 15 * 60e3)
    if (!sent) {                         // looks like a thank-you page, but you did not press Send here: you decide
      if (!hinted) { hinted = true; status('This page looks like a confirmation. If you really sent the application, click “I submitted ✓”.') }
      return 'hint'
    }
    const shot = await send({ type: 'screenshot' })
    try { await api('POST', '/api/ext/done', { job_id: job.id, url: location.href, screenshot: shot.ok ? shot.data : '', answers: answersToLearn(), how: 'confirmation page' }) } catch (e) {}
    send({ type: 'unfollow' })
    status('🎉 Application sent – marked as Applied in your tracker. Open the next job from the app.', null, true)
    job = null
    return true
  }

  // ------------------------------------------------------------------ panel
  function panel() {
    let p = document.getElementById('jha-panel'); if (p) return p
    p = document.createElement('div'); p.id = 'jha-panel'
    p.style.cssText = 'position:fixed;top:14px;right:14px;z-index:2147483647;width:330px;background:#fff;border:1px solid #cbd5e1;border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.18);font:13px/1.4 Calibri,Segoe UI,system-ui,sans-serif;color:#1e293b'
    p.innerHTML = `<div style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:#1B2A4A;color:#fff;border-radius:12px 12px 0 0">
        <b style="flex:1">Job Hunt Agent</b><span id="jha-min" style="cursor:pointer;opacity:.8" title="Hide">✕</span></div>
      <div style="padding:10px 12px"><div id="jha-job" style="font-weight:600;margin-bottom:4px"></div><div id="jha-status" style="color:#475569"></div>
      <div id="jha-pick" style="display:none;margin-top:8px"><select id="jha-sel" style="width:100%;padding:5px;border:1px solid #cbd5e1;border-radius:6px"></select>
        <button id="jha-use" style="margin-top:6px;background:#1B2A4A;color:#fff;border:0;border-radius:6px;padding:6px 10px;cursor:pointer">Use this job and fill</button></div>
      <div id="jha-legend" style="margin-top:6px;font-size:11px;color:#64748b">🟩 filled · 🟧 Claude draft – read · 🟥 for you · 🟦 consent – tick yourself</div>
      <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
        <button id="jha-fill" style="background:#2F7CFF;color:#fff;border:0;border-radius:6px;padding:6px 10px;cursor:pointer;font-weight:600">Fill this page</button>
        <button id="jha-done" style="background:#fff;color:#1B2A4A;border:1px solid #cbd5e1;border-radius:6px;padding:6px 10px;cursor:pointer;font-weight:600">I submitted ✓</button>
        <button id="jha-stop" style="background:#fff;color:#b91c1c;border:1px solid #fecaca;border-radius:6px;padding:6px 10px;cursor:pointer" title="Stop the helper for this job – nothing more is filled">Stop</button></div></div>`
    document.documentElement.appendChild(p)
    p.querySelector('#jha-min').onclick = () => p.remove()
    p.querySelector('#jha-fill').onclick = () => fillClicked()
    p.querySelector('#jha-use').onclick = async () => {
      const id = +p.querySelector('#jha-sel').value; if (!id) return
      try { job = (await api('POST', '/api/ext/active', { job_id: id })).job } catch (e) { status('⚠ ' + e.message); return }
      p.querySelector('#jha-pick').style.display = 'none'
      send({ type: 'follow', jobId: id }); stopped = false; await fill(false); watch()
    }
    p.querySelector('#jha-stop').onclick = async () => {
      stopped = true; if (watcher) clearInterval(watcher)
      send({ type: 'unfollow' })
      try { await api('POST', '/api/ext/active', { job_id: null }) } catch (e) {}
      job = null; status('Stopped – nothing more will be filled. Pick another job in the app when you want.', null, true)
    }
    let sure = 0
    p.querySelector('#jha-done').onclick = async () => {
      if (!job) return
      const btn = p.querySelector('#jha-done')
      if (Date.now() - sure > 5000) { sure = Date.now(); btn.textContent = 'Really sent? Click again'; setTimeout(() => { btn.textContent = 'I submitted ✓' }, 5000); return }
      btn.textContent = 'I submitted ✓'
      const shot = await send({ type: 'screenshot' })
      await api('POST', '/api/ext/done', { job_id: job.id, url: location.href, screenshot: shot.ok ? shot.data : '', answers: answersToLearn(), how: 'marked by you' }).catch(() => {})
      send({ type: 'unfollow' }); status('✓ Marked as Applied.', null, true); job = null
    }
    return p
  }
  function status(text, applyBtn, final) {
    const p = panel()
    p.querySelector('#jha-job').textContent = job ? `${job.company} – ${job.title}` : (final ? '' : 'Job not chosen yet')
    const s = p.querySelector('#jha-status'); s.textContent = text || ''
    if (applyBtn) {
      const b = document.createElement('button'); b.textContent = 'Go to the application form →'
      b.style.cssText = 'display:block;margin-top:6px;background:#1B2A4A;color:#fff;border:0;border-radius:6px;padding:6px 10px;cursor:pointer'
      b.onclick = () => applyBtn.click(); s.appendChild(b)
    }
    p.querySelector('#jha-legend').style.display = final ? 'none' : 'block'
  }

  // which job is this page? 1) the one picked in the app, 2) the company in the address/title, 3) you pick it here
  async function ensureJob() {
    if (job) return true
    try { job = (await api('GET', '/api/ext/active')).job } catch (e) { status(whyNot(e)); return false }
    if (job) return true
    let cands = [], queue = []
    try { cands = (await api('GET', `/api/ext/match?url=${encodeURIComponent(location.href)}&title=${encodeURIComponent(document.title)}`)).candidates } catch (e) {}
    const built = cands.filter((c) => c.status === 'built')
    if (built.length === 1) {
      try { job = (await api('POST', '/api/ext/active', { job_id: built[0].id })).job; send({ type: 'follow', jobId: job.id }); return true } catch (e) {}
    }
    try { queue = (await api('GET', '/api/apply/queue?limit=60')).jobs } catch (e) {}
    const p = panel(), sel = p.querySelector('#jha-sel'); sel.innerHTML = ''
    const add = (j, tag) => { const o = document.createElement('option'); o.value = j.id; o.textContent = `${tag}${j.company} – ${j.title}`; sel.appendChild(o) }
    const seen = new Set()
    for (const c of cands) { add(c, '★ '); seen.add(c.id) }
    for (const q of queue) if (!seen.has(q.id)) add(q, '')
    p.querySelector('#jha-pick').style.display = sel.options.length ? 'block' : 'none'
    status(sel.options.length ? 'Which job is this form for? (★ = matches this website)' : 'No jobs with a finished CV yet – build one in the app first.')
    return false
  }
  function watch() {                     // one watcher per page: the thank-you text, or a new step of the form
    if (watcher) return
    let last = document.body ? document.body.innerText.length : 0
    watcher = setInterval(async () => {
      if (!job || stopped || busy) return
      if (await confirmIfDone()) return
      const now = document.body ? document.body.innerText.length : 0
      if (Math.abs(now - last) <= 200) return
      last = now
      if (Date.now() - lastUser < 8000) return       // you are working: wait
      const fresh = collect().filter((f) => !decisions[f.fid] && !touched.has(f.fid) && !f.value)
      if (fresh.length >= 2) fill(true)               // a real new step, not a small change
    }, 2500)
  }
  async function fillClicked() {
    stopped = false
    if (!(await ensureJob())) return
    if ((await confirmIfDone()) === true) return
    await fill(false); watch()
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type !== 'jha-start') return
    ;(async () => {
      if (started) { if (!msg.autoFill || msg.manual) fillClicked(); return }   // already here: a manual Fill fills what is still empty
      started = true
      if (!(await ensureJob())) return
      const c = await confirmIfDone()
      if (c === true) return
      if (c !== 'hint') await fill(msg.autoFill)
      watch()
    })()
  })
})()
