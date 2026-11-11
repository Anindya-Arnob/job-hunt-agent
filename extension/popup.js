const APP = 'http://127.0.0.1:8780'
const $ = (id) => document.getElementById(id)
const api = async (method, path, body) => {
  const r = await fetch(APP + path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
  if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json()
}
async function render() {
  try { await api('GET', '/api/ext/ping') } catch (e) {
    $('state').textContent = 'The app is not running on this PC. Start scripts\\start.bat, then open this again.'; $('queue').textContent = ''; return }
  $('state').textContent = 'Connected to the app ✓'
  const a = await api('GET', '/api/ext/active')
  $('active').innerHTML = a.job ? `<b>Now applying:</b> ${a.job.company} – ${a.job.title}` : '<span class="muted">No job selected – pick one below.</span>'
  const q = await api('GET', '/api/apply/queue')
  if (!q.jobs.length) { $('queue').textContent = 'Nothing in the queue – build CVs first.'; return }
  $('queue').innerHTML = ''
  for (const j of q.jobs) {
    const d = document.createElement('div'); d.className = 'job'
    d.innerHTML = `<span class="pill">${j.score ?? '–'}</span><span class="t"><b></b><span class="muted"></span></span><button class="o">Apply</button>`
    d.querySelector('b').textContent = j.title; d.querySelector('.muted').textContent = j.company
    d.querySelector('button').onclick = async () => {
      await api('POST', '/api/ext/active', { job_id: j.id })
      const tab = await chrome.tabs.create({ url: j.link })
      await chrome.runtime.sendMessage({ type: 'follow', tabId: tab.id, jobId: j.id })
      window.close()
    }
    $('queue').appendChild(d)
  }
}
$('fill').onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  const a = await api('GET', '/api/ext/active').catch(() => ({}))
  if (tab) { if (a.job) await chrome.runtime.sendMessage({ type: 'follow', tabId: tab.id, jobId: a.job.id }); await chrome.runtime.sendMessage({ type: 'fill-tab', tabId: tab.id }) }
  window.close()
}
render()
