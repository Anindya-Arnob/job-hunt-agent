// Job Hunt Agent – collect jobs in your own Chrome (Glassdoor, Indeed). Started from the app's Search screen.
// Waits while you pass a human check or log in, then sends the job list to the app on your PC, reads the
// new job pages with your session and moves this tab to the next search. It never clicks or submits anything.
(() => {
  if (window.__jhcAlive && window.__jhcAlive()) return
  document.getElementById('jhc-panel')?.remove()
  window.__jhcAlive = () => { try { return !!chrome.runtime?.id } catch (e) { return false } }

  const once = (msg) => new Promise((res) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => {
        const err = chrome.runtime.lastError
        res(r || { ok: false, error: err ? err.message : 'no answer', retry: true })
      })
    } catch (e) { res({ ok: false, error: String(e.message || e), dead: true }) }
  })
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
  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms))
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a))

  const SITE = /indeed\./.test(location.host) ? 'indeed' : /glassdoor\./.test(location.host) ? 'glassdoor' : null
  const CARDS = {
    indeed: 'a.jcs-JobTitle, h2.jobTitle a, div.job_seen_beacon',
    glassdoor: "li[data-test='jobListing'], a[data-test='job-title'], li.react-job-listing",
  }
  const EMPTY = {
    indeed: '.jobsearch-NoResult-messageContainer, [data-testid="noResultsMessage"], .no_results',
    glassdoor: "[data-test='no-jobs-found'], [class*='NoResults'], [data-test='search-no-results']",
  }
  const CHECK = /verify you are human|bestätigen sie,? dass sie ein mensch|additional verification required|zusätzliche (überprüfung|verifizierung)|security check|sicherheitsüberprüfung|just a moment|einen moment|are you a robot|press (&|and) hold|captcha/i
  const LOGIN_WALL = "[data-test='authModalContainerV2-content'], [data-test='hardsellOverlay'], #LoginModal, form[action*='login']"

  let stopped = false
  const panel = () => {
    let p = document.getElementById('jhc-panel')
    if (p) return p
    p = document.createElement('div')
    p.id = 'jhc-panel'
    p.style.cssText = 'position:fixed;top:14px;right:14px;z-index:2147483647;width:330px;background:#fff;color:#1B2A4A;' +
      'border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.25);font:13px/1.45 system-ui,Segoe UI,sans-serif;overflow:hidden'
    p.innerHTML = '<div style="background:#1B2A4A;color:#fff;padding:9px 12px;font-weight:600;display:flex;justify-content:space-between">' +
      '<span>Job Hunt Agent · collecting</span><span id="jhc-count" style="font-weight:400;opacity:.8"></span></div>' +
      '<div style="padding:10px 12px"><div id="jhc-what" style="font-weight:600;margin-bottom:4px"></div><div id="jhc-status"></div>' +
      '<div style="display:flex;gap:6px;margin-top:9px">' +
      '<button id="jhc-skip" style="flex:1;padding:6px;border:1px solid #cbd5e1;background:#fff;color:#1B2A4A;border-radius:7px;cursor:pointer">Skip this search</button>' +
      '<button id="jhc-stop" style="padding:6px 10px;border:1px solid #fecaca;background:#fff;color:#b91c1c;border-radius:7px;cursor:pointer">Stop</button></div></div>'
    document.documentElement.appendChild(p)
    p.querySelector('#jhc-stop').onclick = async () => {
      stopped = true
      try { await api('POST', '/api/collect/stop') } catch (e) {}
      send({ type: 'unfollow' }); say('Stopped. The jobs found so far are on your job board.')
    }
    p.querySelector('#jhc-skip').onclick = async () => {
      stopped = true
      try { const d = await api('POST', '/api/collect/skip'); go(d) } catch (e) { say(String(e.message || e)) }
    }
    return p
  }
  const say = (text, what) => {
    const p = panel()
    p.querySelector('#jhc-status').textContent = text
    if (what !== undefined) p.querySelector('#jhc-what').textContent = what
  }
  const go = (d) => {
    if (d && d.next) {
      say(`Next search in ${Math.round((d.pause_ms || 3000) / 1000)} s…`)
      send({ type: 'collect-next', url: d.next, pause: d.pause_ms || 3000 })
    } else {
      const c = (d && d.counts) || {}
      say(`Done: ${c.found || 0} jobs seen, ${c.new || 0} new on your job board, ${c.duplicates || 0} already known.`)
      send({ type: 'unfollow' })
    }
  }

  const bodyText = () => (document.title + ' ' + (document.body ? document.body.innerText.slice(0, 4000) : ''))
  const hasCards = () => SITE && document.querySelectorAll(CARDS[SITE]).length > 0
  const isEmpty = () => SITE && !!document.querySelector(EMPTY[SITE])
  const walled = () => !!document.querySelector(LOGIN_WALL)

  async function waitForList() {
    const t0 = Date.now()
    let lastBeat = 0, quiet = 0
    while (!stopped) {
      const check = CHECK.test(bodyText()) && !hasCards()
      const login = !check && walled()
      if (!check && !login && (hasCards() || isEmpty())) return true
      const waiting = check ? 'human check' : login ? 'login' : ''
      if (check) say('Please do the "verify you are human" check in this tab. Collecting starts by itself afterwards.')
      else if (login) say('Please log in (or close the sign-in box). Collecting starts by itself afterwards.')
      else say('Waiting for the job list…')
      if (Date.now() - lastBeat > 5000) { lastBeat = Date.now(); try { await api('POST', '/api/ext/collect/heartbeat', { url: location.href, waiting }) } catch (e) {} }
      quiet = waiting ? 0 : quiet + 1
      if (!waiting && Date.now() - t0 > 45000 && quiet > 20) return true      // no list and no check: send what is there
      await sleep(1500)
    }
    return false
  }

  async function readDetails(urls) {
    let blockedInRow = 0, got = 0
    for (let i = 0; i < urls.length && !stopped; i++) {
      say(`Reading job pages ${i + 1} of ${urls.length}…`)
      await sleep(rand(1200, 2800))
      try {
        const r = await fetch(urls[i], { credentials: 'include' })
        const html = await r.text()
        const res = await api('POST', '/api/ext/collect/detail', { url: urls[i], html })
        if (res.ok) { got++; blockedInRow = 0 } else if (res.blocked || r.status >= 400) blockedInRow++
      } catch (e) { blockedInRow++ }
      if (blockedInRow >= 3) { say('The site stopped showing job pages; the rest keep their short text.'); await sleep(1500); break }
    }
    return got
  }

  async function main() {
    if (!SITE) return
    let st
    try { st = await api('GET', '/api/collect/status') } catch (e) {
      return say(/context|dead/i.test(String(e.message)) || !window.__jhcAlive() ? 'The helper was updated. Reload this page (F5).'
        : 'The Job Hunt Agent app is not running on this PC (start scripts\\start.bat).', '')
    }
    if (!st.active) { send({ type: 'unfollow' }); return }
    const cur = st.current || {}
    panel().querySelector('#jhc-count').textContent = `${st.done + 1} / ${st.total}`
    say('Starting…', cur.label || '')
    if (!(await waitForList())) return
    await sleep(rand(800, 1600))
    let r
    try { r = await api('POST', '/api/ext/collect/page', { url: location.href, html: document.documentElement.outerHTML }) } catch (e) { return say(String(e.message || e)) }
    say(`${r.jobs} jobs on this page${r.details.length ? `, reading ${r.details.length} new ones` : ''}.`)
    if (r.details.length) await readDetails(r.details)
    if (stopped) return
    try { go(await api('POST', '/api/ext/collect/done')) } catch (e) { say(String(e.message || e)) }
  }
  main()
})()
