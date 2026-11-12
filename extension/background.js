// Job Hunt Agent – Apply helper (background). Talks to the app on this PC and follows the job tabs you open.
const APP = 'http://127.0.0.1:8780'

async function api(method, path, body) {
  const r = await fetch(APP + path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
  const text = await r.text()
  let data = null
  try { data = JSON.parse(text) } catch (e) { data = { detail: text } }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`)
  return data
}

async function inject(tabId, autoFill, manual) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] })
    await chrome.tabs.sendMessage(tabId, { type: 'jha-start', autoFill, manual: !!manual })
  } catch (e) { /* chrome:// pages, PDF viewer, etc. */ }
}

const hello = () => fetch(APP + '/api/ext/ping?source=ext').catch(() => {})
chrome.runtime.onStartup.addListener(hello)
chrome.runtime.onInstalled.addListener(hello)
hello()

const getFollow = async () => (await chrome.storage.session.get('follow')).follow || {}
const setFollow = (f) => chrome.storage.session.set({ follow: f })

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      if (msg.type === 'api') { hello(); return reply({ ok: true, data: await api(msg.method || 'GET', msg.path, msg.body) }) }
      if (msg.type === 'screenshot') {
        const url = await chrome.tabs.captureVisibleTab(sender.tab.windowId, { format: 'png' })
        return reply({ ok: true, data: url })
      }
      if (msg.type === 'follow') {            // keep filling this tab as you move through the form
        const f = await getFollow(); f[msg.tabId || sender.tab.id] = { jobId: msg.jobId }; await setFollow(f)
        return reply({ ok: true })
      }
      if (msg.type === 'unfollow') { const f = await getFollow(); delete f[msg.tabId || sender.tab.id]; await setFollow(f); return reply({ ok: true }) }
      if (msg.type === 'follow-info') { const f = await getFollow(); return reply({ ok: true, data: f[sender.tab.id] || null }) }
      if (msg.type === 'fill-tab') { await inject(msg.tabId, true, true); return reply({ ok: true }) }
      if (msg.type === 'collect-next') {      // collecting: move this tab to the next search after a short pause
        const t = sender.tab.id
        setTimeout(() => chrome.tabs.update(t, { url: msg.url }).catch(() => {}), Math.max(500, msg.pause || 3000))
        return reply({ ok: true })
      }
      if (msg.type === 'pending') {           // you pressed Submit: the next page may be the confirmation
        const f = await getFollow(); const t = sender.tab.id; f[t] = { ...(f[t] || {}), pendingDone: msg.jobId, pendingAt: Date.now(), jobId: msg.jobId }
        await setFollow(f); return reply({ ok: true })
      }
      reply({ ok: false, error: 'unknown message' })
    } catch (e) { reply({ ok: false, error: String(e.message || e) }) }
  })()
  return true
})

async function injectCollect(tabId) {
  try { await chrome.scripting.executeScript({ target: { tabId }, files: ['collect.js'] }) } catch (e) { /* not a web page */ }
}

// A tab opened from the app: from the Search screen -> collect jobs there; from the Apply page -> follow the job.
chrome.tabs.onCreated.addListener(async (tab) => {
  if (!tab.openerTabId) return
  try {
    const opener = await chrome.tabs.get(tab.openerTabId)
    if (!(opener.url || '').startsWith(APP)) return
    if ((opener.url || '').startsWith(APP + '/search')) {
      const c = await api('GET', '/api/collect/status')
      if (c.active) { const f = await getFollow(); f[tab.id] = { collect: c.id }; await setFollow(f); return }
    }
    const a = await api('GET', '/api/ext/active')
    if (a.job) { const f = await getFollow(); f[tab.id] = { jobId: a.job.id }; await setFollow(f) }
  } catch (e) { /* app not running */ }
})

// Pages of a followed tab: show the helper and fill (also after "Apply" leads to the company's form).
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== 'complete' || !tab.url || tab.url.startsWith(APP) || tab.url.startsWith('chrome')) return
  const f = await getFollow()
  if (!f[tabId] && tab.openerTabId && /(^|\.)(indeed|glassdoor)\./.test(new URL(tab.url).host)) {
    // the app opened this tab first and started collecting a moment later
    try {
      const opener = await chrome.tabs.get(tab.openerTabId)
      if ((opener.url || '').startsWith(APP + '/search')) {
        const c = await api('GET', '/api/collect/status')
        if (c.active) { f[tabId] = { collect: c.id }; await setFollow(f) }
      }
    } catch (e) { /* app not running or opener closed */ }
  }
  if (f[tabId] && f[tabId].collect) return injectCollect(tabId)
  if (f[tabId]) await inject(tabId, true)
})
chrome.tabs.onRemoved.addListener(async (tabId) => { const f = await getFollow(); delete f[tabId]; await setFollow(f) })

chrome.commands.onCommand.addListener(async (cmd) => {
  if (cmd !== 'fill-form') return
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  if (tab) await inject(tab.id, true, true)
})
