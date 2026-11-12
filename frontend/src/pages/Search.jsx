import { useEffect, useMemo, useState } from 'react'
import { Chrome, ExternalLink, Loader2, Play, Plus, RotateCcw, Save, SkipForward, Square, Trash2, ShieldAlert } from 'lucide-react'
import { api, fmtTime, SOURCE_LABEL } from '../api'
import { ChipSelect, StatusBadge, TagInput, useAsync, useToast } from '../ui'
import { useEvents } from '../events'

const BLANK = { name: 'New profile', roles: [], keywords_in: [], keywords_out: [], job_types: ['Internship', 'Working Student', 'Part-time'],
  locations: ['Berlin', 'Remote'], work_modes: ['Remote', 'Hybrid', 'On-site'], days: 5, sources: [], language_max: 'any',
  exclude_companies: [], min_salary: 0, is_default: false }

export default function SearchPage() {
  const toast = useToast()
  const opts = useAsync(() => api.get('/api/filters/options'))
  const filters = useAsync(() => api.get('/api/filters'))
  const status = useAsync(() => api.get('/api/status'))
  const modes = useAsync(async () => Object.fromEntries(await Promise.all(BROWSER_SITES.map(async (x) => [x, (await api.get(`/api/sites/${x}/mode`)).mode]))))
  const [sel, setSel] = useState(null)
  const [form, setForm] = useState(null)
  const [runId, setRunId] = useState(null)
  const { events } = useEvents()

  useEffect(() => {
    if (filters.data && !sel) {
      const f = filters.data.find((x) => x.is_default) || filters.data[0]
      if (f) { setSel(f.id); setForm(f) }
    }
  }, [filters.data]) // eslint-disable-line
  useEffect(() => { if (status.data?.running?.length) setRunId(status.data.running[status.data.running.length - 1]) }, [status.data])

  const runEvents = useMemo(() => events.filter((e) => runId && e.run_id === runId), [events, runId])
  const done = runEvents.some((e) => ['run_done', 'run_failed', 'run_cancelled'].includes(e.type))
  const captchas = runEvents.filter((e) => e.type === 'captcha')
  const siteProgress = useMemo(() => {
    const m = {}
    for (const e of runEvents) {
      if (!e.site) continue
      m[e.site] = { last: e.message, done: e.type === 'site_done' || m[e.site]?.done, jobs: e.jobs ?? m[e.site]?.jobs, level: e.level }
    }
    return m
  }, [runEvents])

  if (!opts.data || !form) return <div className="muted">Loading…</div>
  const O = opts.data
  const set = (k) => (v) => setForm({ ...form, [k]: v })
  const srcLabels = Object.fromEntries(O.sources.map((s) => [s.id, s.label]))

  const save = async () => {
    try {
      const body = { ...form }; delete body.id
      const r = form.id ? await api.put(`/api/filters/${form.id}`, body) : await api.post('/api/filters', body)
      await filters.reload(); setSel(r.id); setForm(r); toast('Filter profile saved')
      return r
    } catch (e) { toast(e.message, 'error') }
  }
  const remove = async () => {
    if (!form.id || !confirm(`Delete “${form.name}”?`)) return
    await api.del(`/api/filters/${form.id}`); await filters.reload(); setSel(null); setForm(null)
  }
  const browserSites = BROWSER_SITES.filter((x) => form.sources.includes(x) && modes.data?.[x] === 'manual')
  const run = async () => {
    // Glassdoor / Indeed refuse the app's browser: they are collected in a tab of your Chrome instead.
    // The tab must be opened right on the click (browsers block pop-ups opened later).
    const tab = browserSites.length ? window.open('about:blank', '_blank') : null
    const saved = await save(); if (!saved) { tab?.close(); return }
    try { const r = await api.post('/api/runs/search', { filter_profile_id: saved.id }); setRunId(r.run_id); toast('Search started') }
    catch (e) { toast(e.message, 'error') }
    if (tab) await startCollect(browserSites, saved.id, tab, toast)
  }
  const cancel = async () => { await api.post(`/api/runs/${runId}/cancel`); toast('Run cancelled', 'warn') }
  const solved = async (site) => { await api.post(`/api/runs/${runId}/captcha-solved`, { site }); toast('Thanks – continuing') }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="h1">Search</h1>
        <div className="flex gap-2">
          <select className="input w-72" value={sel || ''} onChange={(e) => { const f = filters.data.find((x) => x.id === +e.target.value); setSel(f.id); setForm(f) }}>
            {filters.data.map((f) => <option key={f.id} value={f.id}>{f.name}{f.is_default ? ' (default)' : ''}</option>)}
          </select>
          <button className="btn-outline" onClick={() => { setSel(null); setForm({ ...BLANK, sources: O.sources.map((s) => s.id), roles: O.roles }) }}><Plus size={14} />New profile</button>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-5">
        <div className="card xl:col-span-3">
          <div className="card-h">
            <input className="input max-w-sm font-semibold" value={form.name} onChange={(e) => set('name')(e.target.value)} />
            <div className="flex gap-2">
              {form.id && <button className="btn-danger" onClick={remove}><Trash2 size={14} /></button>}
              <button className="btn-outline" onClick={save}><Save size={14} />Save</button>
              <button className="btn-teal" onClick={run} disabled={runId && !done}><Play size={14} />Run</button>
            </div>
          </div>
          <div className="grid gap-4 p-4 md:grid-cols-2">
            <div className="md:col-span-2"><span className="label">Roles</span><ChipSelect options={O.roles} value={form.roles} onChange={set('roles')} /></div>
            <div><span className="label">Must include (any of)</span><TagInput value={form.keywords_in} onChange={set('keywords_in')} placeholder="e.g. NLP" /></div>
            <div><span className="label">Must not include</span><TagInput value={form.keywords_out} onChange={set('keywords_out')} placeholder="e.g. Sales" /></div>
            <div><span className="label">Job type</span><ChipSelect options={O.job_types} value={form.job_types} onChange={set('job_types')} /></div>
            <div><span className="label">Work mode</span><ChipSelect options={O.work_modes} value={form.work_modes} onChange={set('work_modes')} /></div>
            <div className="md:col-span-2"><span className="label">Locations</span><ChipSelect options={O.locations} value={form.locations} onChange={set('locations')} /></div>
            <div><span className="label">Posted within</span>
              <div className="flex gap-1.5">{O.days.map((d) => <button key={d} onClick={() => set('days')(d)} className={`rounded-md border px-3 py-1 text-sm ${form.days === d ? 'border-teal bg-teal-50 font-semibold text-teal-600' : 'border-slate-300 bg-slate-50'}`}>{d} day{d > 1 ? 's' : ''}</button>)}</div></div>
            <div><span className="label">German level you accept</span>
              <select className="input" value={form.language_max} onChange={(e) => set('language_max')(e.target.value)}>
                <option value="any">Show all jobs (flag German as a deal-breaker)</option>
                <option value="none">Hide jobs that require German</option>
                {['A1', 'A2', 'B1', 'B2', 'C1'].map((l) => <option key={l} value={l}>Up to {l}</option>)}
              </select></div>
            <div className="md:col-span-2"><span className="label">Sources</span><ChipSelect options={O.sources.map((s) => s.id)} value={form.sources} onChange={set('sources')} labels={srcLabels} />
              {BROWSER_SITES.some((x) => form.sources.includes(x)) && <BrowserCollect sites={BROWSER_SITES.filter((x) => form.sources.includes(x))} modes={modes} filterId={form.id} />}</div>
            <div><span className="label">Exclude companies</span><TagInput value={form.exclude_companies} onChange={set('exclude_companies')} placeholder="Company name" /></div>
            <div className="flex items-end"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_default} onChange={(e) => set('is_default')(e.target.checked)} />Use for the morning run</label></div>
          </div>
        </div>

        <div className="card xl:col-span-2">
          <div className="card-h">Live progress {runId && <span className="text-xs font-normal muted">run #{runId}</span>}
            {runId && !done && <button className="btn-danger" onClick={cancel}><Square size={12} />Stop</button>}</div>
          {!runId ? <div className="p-4 text-sm muted">Press Run to start. Each job site has its own agent; they run at the same time.</div> : (
            <div className="space-y-3 p-4">
              {captchas.filter((c) => !runEvents.some((e) => e.site === c.site && ['site_done', 'captcha_done'].includes(e.type) && runEvents.indexOf(e) > runEvents.indexOf(c))).map((c, i) => (
                <div key={i} className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-sm">
                  <ShieldAlert size={16} className="text-amber-600" /><span className="flex-1">{c.message}</span>
                  <button className="btn-primary" onClick={() => solved(c.site)}>Solved</button>
                </div>
              ))}
              <div className="space-y-1.5">
                {Object.entries(siteProgress).map(([site, p]) => (
                  <div key={site} className="flex items-center gap-2 text-sm">
                    <span className={`h-2 w-2 rounded-full ${p.done ? 'bg-emerald-500' : p.level === 'warn' ? 'bg-amber-500' : 'animate-pulse bg-sky-500'}`} />
                    <span className="w-28 font-semibold">{SOURCE_LABEL[site] || site}</span>
                    <span className="flex-1 truncate muted" title={p.last}>{p.last}</span>
                  </div>
                ))}
              </div>
              <div className="max-h-80 overflow-y-auto rounded-md bg-slate-50 p-2 font-mono text-xs">
                {runEvents.slice(-120).map((e, i) => (
                  <div key={i} className={e.level === 'warn' ? 'text-amber-700' : e.type === 'run_failed' ? 'text-rose-700' : ''}>
                    <span className="text-slate-400">{fmtTime(e.ts)}</span> {e.message}</div>
                ))}
              </div>
              {done && <div className="flex justify-between"><StatusBadge status={runEvents.some((e) => e.type === 'run_failed') ? 'failed' : 'done'} />
                <a className="text-sm font-semibold text-teal" href="/jobs">Open job board →</a></div>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


const BROWSER_SITES = ['glassdoor', 'indeed']

async function startCollect(sites, filterId, tab, toast) {
  try {
    const r = await api.post('/api/collect/start', { sites, filter_profile_id: filterId })
    if (tab) tab.location.href = r.url
    else window.open(r.url, '_blank')
    toast(`${sites.map((x) => SOURCE_LABEL[x] || x).join(' and ')}: collecting in your Chrome tab`)
  } catch (e) { tab?.close(); toast(e.message, 'error') }
}

// Glassdoor / Indeed: collected in a tab of your own Chrome by the extension (you do the human check or login there)
function BrowserCollect({ sites, modes, filterId }) {
  const toast = useToast()
  const [st, setSt] = useState(null)
  const [showLinks, setShowLinks] = useState(false)
  useEffect(() => {
    let alive = true
    const tick = async () => { try { const r = await api.get('/api/collect/status'); if (alive) setSt(r) } catch (e) { /* app restarting */ } }
    tick(); const t = setInterval(tick, 2500)
    return () => { alive = false; clearInterval(t) }
  }, [])
  const manual = sites.filter((x) => modes.data?.[x] === 'manual')
  const auto = sites.filter((x) => modes.data && modes.data[x] !== 'manual')
  const label = (xs) => xs.map((x) => SOURCE_LABEL[x] || x).join(' and ')
  const begin = () => { const tab = window.open('about:blank', '_blank'); startCollect(manual.length ? manual : sites, filterId, tab, toast) }
  const useBrowser = async (x) => { await api.put(`/api/sites/${x}/mode`, { mode: 'manual' }); modes.reload() }
  const retryAuto = async (x) => { await api.put(`/api/sites/${x}/mode`, { mode: 'window' }); toast(`${SOURCE_LABEL[x] || x} will be tried automatically on the next run`); modes.reload() }
  const running = st?.active
  const quiet = running && st.last_seen && Date.now() / 1000 - st.last_seen > 60
  return (
    <div className="mt-3 rounded-xl border border-sky-200 bg-sky-50 p-3 text-sm">
      <div className="flex items-start gap-2">
        <Chrome size={16} className="mt-0.5 shrink-0 text-sky-700" />
        <div className="flex-1">
          <div className="font-semibold text-sky-900">{label(manual.length ? manual : sites)} in your own Chrome</div>
          <p className="mt-0.5 text-sky-900/80">These sites refuse automated browsers. On Run, the app opens a tab in your Chrome: do the
            "verify you are human" check or log in there once, and the extension collects the job lists and job pages by itself,
            search after search. Keep the tab open; you can use other tabs meanwhile.</p>
          {auto.length > 0 && <p className="mt-1 text-xs text-sky-900/70">{label(auto)} is still searched automatically.
            {auto.map((x) => <button key={x} className="ml-1 font-semibold text-teal underline" onClick={() => useBrowser(x)}>Use Chrome for {SOURCE_LABEL[x] || x}</button>)}</p>}
        </div>
        {!running && <button className="btn-teal shrink-0 py-1 text-xs" onClick={begin}><Chrome size={12} />Collect now</button>}
      </div>
      {running && (
        <div className="mt-2 rounded-lg bg-slate-50 p-2 pl-3">
          <div className="flex items-center gap-2">
            <Loader2 size={13} className="animate-spin text-sky-600" />
            <span className="flex-1 truncate"><b>{st.done} / {st.total}</b> searches · {st.current?.label}</span>
            <button className="btn-ghost py-0.5 text-xs" onClick={async () => { await api.post('/api/collect/skip') }} title="Skip this search"><SkipForward size={12} /></button>
            <button className="btn-danger py-0.5 text-xs" onClick={async () => { await api.post('/api/collect/stop'); toast('Collection stopped', 'warn') }}><Square size={10} />Stop</button>
          </div>
          <div className="mt-1 text-xs muted">{st.counts.found} jobs seen · {st.counts.new} new · {st.counts.described} descriptions read
            {st.waiting && <b className="ml-2 text-amber-700">Waiting for you: {st.waiting} (in the Chrome tab)</b>}
            {quiet && !st.waiting && <span className="ml-2 text-amber-700">No word from the Chrome tab for a minute. Is it still open, with the Job Hunt Agent extension?</span>}</div>
        </div>)}
      {!running && st?.status && st.status !== 'running' && st.total > 0 && (
        <div className="mt-2 pl-6 text-xs muted">Last collection: {st.counts.found} jobs seen, {st.counts.new} new ({st.status}).</div>)}
      <div className="mt-2 flex flex-wrap items-center gap-2 pl-6 text-xs">
        <button className="text-sky-800 underline" onClick={() => setShowLinks(!showLinks)}>{showLinks ? 'Hide' : 'Show'} the searches as links</button>
        {manual.map((x) => <button key={x} className="inline-flex items-center gap-1 text-sky-800 underline" onClick={() => retryAuto(x)}><RotateCcw size={11} />Try {SOURCE_LABEL[x] || x} automatically again</button>)}
      </div>
      {showLinks && sites.map((x) => <SearchLinks key={x} site={x} filterId={filterId} />)}
    </div>
  )
}

function SearchLinks({ site, filterId }) {
  const links = useAsync(() => api.get(`/api/sites/${site}/search-links${filterId ? `?filter_profile_id=${filterId}` : ''}`), [site, filterId])
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 pl-6">
      {(links.data || []).map((l) => (
        <a key={l.url} href={l.url} target="_blank" rel="noreferrer" title={l.url}
          className="inline-flex items-center gap-1 rounded-full bg-slate-50 px-2.5 py-1 text-xs font-medium text-teal-600 ring-1 ring-sky-200 hover:bg-teal-50">
          <ExternalLink size={11} />{SOURCE_LABEL[site] || site}: {l.label}</a>))}
    </div>)
}
