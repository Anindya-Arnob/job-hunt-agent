// Small fetch wrapper for the FastAPI backend.
async function request(method, url, body, isForm = false) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    if (isForm) opts.body = body
    else {
      opts.headers['Content-Type'] = 'application/json'
      opts.body = JSON.stringify(body)
    }
  }
  const res = await fetch(url, opts)
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : res.statusText
    throw new Error(msg)
  }
  return data
}

export const api = {
  get: (u) => request('GET', u),
  post: (u, b) => request('POST', u, b ?? {}),
  put: (u, b) => request('PUT', u, b),
  patch: (u, b) => request('PATCH', u, b),
  del: (u) => request('DELETE', u),
  upload: (u, file) => { const f = new FormData(); f.append('file', file); return request('POST', u, f, true) },
}

export const fileUrl = (path, download = false) =>
  `/api/files?path=${encodeURIComponent(path)}${download ? '&download=true' : ''}`

export const fmtDate = (s) => {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d) ? s : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}
export const fmtDateTime = (s) => {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d) ? s : `${d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`
}
export const fmtTime = (s) => {
  if (!s) return ''
  const d = typeof s === 'number' ? new Date(s * 1000) : new Date(s)
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
export const SOURCE_LABEL = {
  linkedin: 'LinkedIn', glassdoor: 'Glassdoor', indeed: 'Indeed', stepstone: 'StepStone', xing: 'XING',
  wellfound: 'Wellfound', ats_feeds: 'Company site', arbeitsagentur: 'Arbeitsagentur', arbeitnow: 'Arbeitnow',
  'email:stepstone': 'Email · StepStone', 'email:glassdoor': 'Email · Glassdoor', 'email:xing': 'Email · XING',
  'email:linkedin': 'Email · LinkedIn', 'email:indeed': 'Email · Indeed', 'email:microsoft': 'Email · Microsoft', email: 'Email', inbox: 'Your inbox',
  remoteok: 'RemoteOK', career_pages: 'Career page', feeds: 'Job feeds', imported: 'Earlier Excel', web: 'Other job sites', websearch: 'Web search',
}
