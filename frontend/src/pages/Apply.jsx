import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Copy, ExternalLink, FileText, Loader2, Mail, MonitorPlay, Play, Puzzle, RefreshCw, RotateCcw, SkipForward, Square, UserRound } from 'lucide-react'
import { api, fileUrl, fmtDate, fmtDateTime } from '../api'
import { Empty, Tabs, useAsync, useToast } from '../ui'
import { useOnEvent } from '../events'
import { BuildingNote, useActiveBuilds } from '../components/ActiveBuilds'

function ScorePill({ score }) {
  if (score === null || score === undefined) return <span className="text-slate-300">–</span>
  const c = score >= 65 ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : score >= 40 ? 'bg-amber-50 text-amber-700 ring-amber-200' : 'bg-slate-100 text-slate-500 ring-slate-200'
  return <span className={`inline-flex h-8 w-10 items-center justify-center rounded-lg text-sm font-bold tabular-nums ring-1 ${c}`}>{score}</span>
}

const STATE = { opening: 'Opening the job…', filling: 'Filling the form…', waiting: 'Your turn: check, answer the red fields, tick consent, press Submit', done: 'Finished', stopped: 'Stopped', idle: '' }

function Live({ st, onControl }) {
  const r = st.report || {}
  return (
    <div className="glass rounded-2xl shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3">
        <div className="mr-auto">
          <div className="text-xs font-semibold uppercase tracking-wide text-teal-600">Job {st.index + 1} of {st.total}</div>
          <div className="text-base font-semibold text-navy">{st.current?.company} – {st.current?.title}</div>
          <div className="text-sm text-slate-500">{STATE[st.state] || st.state}</div>
        </div>
        <button className="btn-teal" onClick={() => onControl('next')} title="If the site showed no clear confirmation page"><CheckCircle2 size={14} />I submitted – next job</button>
        <button className="btn-outline" onClick={() => onControl('skip')}><SkipForward size={14} />Skip this job</button>
        <button className="btn-danger" onClick={() => onControl('stop')}><Square size={12} />Stop</button>
      </div>
      {st.state === 'waiting' || r.filled ? (
        <div className="grid gap-4 p-5 md:grid-cols-3">
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-700"><span className="h-2 w-2 rounded-full bg-emerald-500" />Filled ({(r.filled?.length || 0) + (r.uploaded?.length || 0)})</div>
            <ul className="max-h-64 space-y-1 overflow-y-auto text-xs text-slate-600">
              {(r.uploaded || []).map((u) => <li key={u}>📎 {u}</li>)}
              {(r.filled || []).map((f, i) => <li key={i}><span className="text-slate-400">{f.label}:</span> {String(f.value).slice(0, 60)}</li>)}
            </ul>
          </div>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-rose-700"><span className="h-2 w-2 rounded-full bg-rose-500" />For you ({r.open?.length || 0})</div>
            <ul className="max-h-64 space-y-1.5 overflow-y-auto text-xs">
              {(r.open || []).map((o, i) => <li key={i} className="text-slate-700"><b className={o.required ? 'text-rose-700' : ''}>{o.required ? '* ' : ''}{o.label}</b><div className="text-slate-400">{o.why}</div></li>)}
              {(r.consent || []).map((c, i) => <li key={`c${i}`} className="text-blue-700">☐ {c}</li>)}
            </ul>
            <p className="mt-2 text-[11px] text-slate-400">Your answers here are saved and filled automatically next time.</p>
          </div>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700"><span className="h-2 w-2 rounded-full bg-amber-500" />Drafted by Claude – read ({r.drafted?.length || 0})</div>
            <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
              {(r.drafted || []).map((d, i) => <li key={i}><b className="text-slate-700">{d.label}</b><div className="mt-0.5 rounded bg-amber-50 p-2 text-slate-700">{d.value}</div></li>)}
            </ul>
          </div>
        </div>) : <div className="p-5 text-sm muted">Working…</div>}
    </div>
  )
}

function SureButton({ className, title, onClick, children, ask = 'Sure? Click again', disabled = false }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => { if (!armed) return; const t = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(t) }, [armed])
  return <button className={className} title={title} disabled={disabled} onClick={(e) => { e.preventDefault(); e.stopPropagation(); if (disabled) return; if (!armed) return setArmed(true); setArmed(false); onClick() }}>{armed ? ask : children}</button>
}

const ATTEMPT = (a) => !a ? null : a.status === 'applied' ? null
  : a.status === 'running' ? 'in progress' : a.note === 'stopped by you' ? 'stopped' : a.note === 'not finished' ? 'not finished'
  : (a.note || '').startsWith('not applied') ? 'undone' : 'skipped'

function ExtensionCard({ ext, active, onStop }) {
  const connected = ext?.seen_at && (Date.now() - new Date(ext.seen_at).getTime()) < 6 * 3600e3
  const [open, setOpen] = useState(false)
  const toast = useToast()
  return (
    <div className="glass rounded-2xl p-5">
      <div className="flex flex-wrap items-center gap-3">
        <Puzzle size={20} className="text-teal-600" />
        <div className="mr-auto"><div className="text-sm font-semibold text-navy">Apply in your own browser <span className="ml-1 rounded bg-teal-50 px-1.5 py-0.5 text-[11px] text-teal-600">recommended</span></div>
          <div className="text-xs muted">Your normal Chrome or Edge, with your own logins. The Job Hunt Agent extension fills the forms; you press Submit.</div></div>
        {connected ? <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200"><CheckCircle2 size={13} />Extension connected</span>
          : <button className="btn-outline" onClick={() => setOpen(!open)}>{open ? 'Hide' : 'How to install (2 minutes)'}</button>}
      </div>
      {(open || !connected) && (
        <ol className={`mt-3 list-decimal space-y-1.5 pl-9 text-sm text-slate-700 ${open ? '' : 'hidden'}`}>
          <li>In your browser, open <b>chrome://extensions</b> (Edge: <b>edge://extensions</b>).</li>
          <li>Switch on <b>Developer mode</b> (top right).</li>
          <li>Click <b>Load unpacked</b> and choose this folder:
            <span className="ml-1 inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 font-mono text-xs">{ext?.folder}
              <button title="Copy" onClick={() => { navigator.clipboard?.writeText(ext?.folder || ''); toast('Folder path copied') }}><Copy size={12} /></button></span></li>
          <li>Click the puzzle icon in the toolbar and <b>pin</b> “Job Hunt Agent – Apply helper”.</li>
          <li>Come back here and reload – it says <b>Extension connected</b>.</li>
        </ol>)}
      {active && <div className="mt-3 flex items-center gap-3 rounded-lg bg-teal-50 px-3 py-2 text-sm text-teal-800">
        <span className="flex-1">Now applying in your browser: <b>{active.company} – {active.title}</b></span>
        <button className="btn-danger py-1 text-xs" onClick={onStop}><Square size={11} />Stop applying</button></div>}
    </div>
  )
}

export default function Apply() {
  const q = useAsync(() => api.get('/api/apply/queue?all=true'))
  const prof = useAsync(() => api.get('/api/apply/profile'))
  const hist = useAsync(() => api.get('/api/apply/history?limit=30'))
  const [st, setSt] = useState(null)
  const ext = useAsync(() => api.get('/api/ext/status'))
  const act = useAsync(() => api.get('/api/ext/active'))
  const [sel, setSel] = useState(null)
  const [tab, setTab] = useState('todo')
  const builds = useActiveBuilds()                      // asked from the app: still right after you come back
  const toast = useToast()
  const refresh = async () => setSt(await api.get('/api/apply/state'))
  useEffect(() => { refresh() }, [])
  useEffect(() => { if (q.data && sel === null) setSel(new Set(q.data.jobs.filter((j) => !j.applied).slice(0, q.data.limit).map((j) => j.id))) }, [q.data]) // eslint-disable-line
  useOnEvent((ev) => {
    if (ev.type === 'apply') { refresh(); act.reload(); ext.reload(); if (ev.result || ['done', 'stopped'].includes(ev.state)) { q.reload(); hist.reload() } }
    if (['run_done', 'run_failed', 'run_cancelled'].includes(ev.type) && ev.run_id) q.reload()
  }, [])
  const rebuild = async (ids) => {
    ids = ids.filter((id) => !builds.byJob[id])
    if (!ids.length) { toast('Already being built – see the line under the job', 'warn'); return }
    try {
      const r = await api.post('/api/build', { job_ids: ids, rebuild: true })
      const started = ids.length - (r.already_building || []).length
      if (started) toast(`Rebuilding CV + cover letter for ${started} job(s), one after another. Progress shows under each job.`)
      if ((r.already_building || []).length) toast(`${r.already_building.length} job(s) were already being built – not started twice`, 'warn')
      builds.reload()
    } catch (e) { toast(e.message, 'error') }
  }
  const notApplied = async (id) => {
    try { await api.post(`/api/apply/jobs/${id}/not-applied`); q.reload(); hist.reload(); act.reload(); toast('Back in your queue – not counted as applied') }
    catch (e) { toast(e.message, 'error') }
  }
  const markApplied = async (id) => {
    try { await api.post(`/api/apply/jobs/${id}/applied`); q.reload(); hist.reload(); toast('Marked as applied') }
    catch (e) { toast(e.message, 'error') }
  }
  const stopBrowser = async () => {
    try { await api.post('/api/ext/active', { job_id: null }); act.reload(); hist.reload(); toast('Stopped – nothing more will be filled') }
    catch (e) { toast(e.message, 'error') }
  }
  const applyInBrowser = async (j) => {
    try { await api.post('/api/ext/active', { job_id: j.id }); act.reload(); window.open(j.link, '_blank') }
    catch (e) { toast(e.message, 'error') }
  }

  const allJobs = q.data?.jobs || []
  const counts = { todo: allJobs.filter((j) => !j.applied).length, applied: allJobs.filter((j) => j.applied).length, all: allJobs.length }
  const jobs = tab === 'todo' ? allJobs.filter((j) => !j.applied) : tab === 'applied' ? allJobs.filter((j) => j.applied) : allJobs
  const chosen = useMemo(() => allJobs.filter((j) => sel?.has(j.id)), [allJobs, sel])
  const chosenTodo = chosen.filter((j) => !j.applied)
  const toggle = (id) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); setSel(n) }
  const start = async () => {
    try { setSt(await api.post('/api/apply/start', { job_ids: chosenTodo.map((j) => j.id) })); toast(`Applying to ${chosenTodo.length} job(s) – a Chrome window opens`) }
    catch (e) { toast(e.message, 'error') }
  }
  const control = async (action) => { setSt(await api.post('/api/apply/control', { action })) }
  const today = (hist.data || []).filter((h) => h.started_at?.slice(0, 10) === new Date().toISOString().slice(0, 10))
  const missing = prof.data?.missing || []

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="h1">Apply</h1>
          <p className="mt-1 text-sm muted">Open a job with <b>Apply in my browser</b>: the extension fills the form and uploads your tailored CV and letter. You check it, answer what's red, tick consent and press Submit. The job is then marked Applied automatically.</p>
        </div>
        <div className="text-right text-sm"><div className="text-2xl font-bold tabular-nums text-navy">{today.filter((h) => h.status === 'applied').length}<span className="text-sm font-normal text-slate-400"> / {q.data?.limit ?? 20}</span></div><div className="muted">applied today</div></div>
      </div>

      {missing.length > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle size={16} className="shrink-0" /><div className="flex-1">Still empty in My details: <b>{missing.join(', ')}</b> – these will be left for you on every form.</div>
          <Link className="btn-outline" to="/details"><UserRound size={14} />My details</Link>
        </div>)}

      <ExtensionCard ext={ext.data} active={act.data?.job} onStop={stopBrowser} />

      {st?.running && <Live st={st} onControl={control} />}
      {!st?.running && st?.history?.length > 0 && (
        <div className="glass rounded-2xl p-4 text-sm shadow-sm">
          <b>Last session:</b> {st.history.filter((h) => h.status === 'applied').length} applied, {st.history.filter((h) => h.status === 'skipped').length} skipped.
          {' '}{st.history.reduce((n, h) => n + (h.learned || 0), 0)} new answers saved.
        </div>)}

      <div className="overflow-hidden glass rounded-2xl">
        <div className="flex flex-wrap items-center gap-2 px-5 pt-3">
          <div className="mr-auto"><div className="text-sm font-semibold text-navy">Your jobs with a finished CV</div>
            <div className="text-xs muted">Best score first. Apply to up to {q.data?.limit ?? 20} a day (change in Settings). Wrongly marked as applied? Open <b>Applied</b> and press <b>Not applied</b>.</div></div>
          <button className="btn-ghost text-xs" onClick={() => setSel(new Set([...(sel || []), ...jobs.map((j) => j.id)]))}>Tick all</button>
          <button className="btn-ghost text-xs" onClick={() => setSel(new Set())}>Untick all</button>
          <SureButton className="btn-outline" ask={`Rebuild ${chosen.length}? Click again`} onClick={() => chosen.length && rebuild(chosen.map((j) => j.id))} title="Write a new CV and cover letter for the ticked jobs (the old files are kept in _old_versions)"><RefreshCw size={14} />Rebuild CV + letter ({chosen.length})</SureButton>
          <button className="btn-outline" disabled={!chosenTodo.length || st?.running} onClick={start} title="The app opens its own Chrome window and goes through the ticked jobs"><MonitorPlay size={14} />Use the app's Chrome window ({chosenTodo.length})</button>
        </div>
        <div className="px-5"><Tabs value={tab} onChange={setTab} tabs={[{ id: 'todo', label: 'To apply', count: counts.todo }, { id: 'applied', label: 'Applied', count: counts.applied }, { id: 'all', label: 'All', count: counts.all }]} /></div>
        {!jobs.length ? <Empty title={tab === 'applied' ? 'Nothing applied yet' : 'Nothing to apply to'}>{tab === 'applied' ? 'Jobs you sent show up here.' : 'Build CVs for jobs first (Job board → Build CV).'}</Empty> : (
          <div className="divide-y divide-slate-100">
            {jobs.map((j) => {
              const busy = !!builds.byJob[j.id]
              const att = ATTEMPT(j.last_attempt)
              return (
              <label key={j.id} className={`grid cursor-pointer grid-cols-[24px_48px_minmax(0,1fr)_96px_110px_96px_170px_24px] items-center gap-3 px-5 py-3 ${st?.current?.id === j.id && st?.running ? 'bg-teal-50' : j.applied ? 'bg-emerald-50/30 hover:bg-emerald-50/60' : 'hover:bg-slate-50'}`}>
                <input type="checkbox" className="accent-teal" checked={!!sel?.has(j.id)} onChange={() => toggle(j.id)} disabled={st?.running} />
                <ScorePill score={j.score} />
                <div className="min-w-0"><div className="truncate font-semibold text-slate-800">{j.title}</div>
                  <div className="truncate text-xs text-slate-500">{j.company}{j.location ? ` · ${j.location}` : ''}
                    {j.applied && <span className="ml-2 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-200">applied {fmtDate(j.applied_at)}</span>}
                    {!j.applied && att && <span className="ml-2 rounded-full bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500 ring-1 ring-slate-200" title={`Last try: ${fmtDateTime(j.last_attempt.at)}`}>last try: {att}</span>}</div>
                  <BuildingNote b={builds.byJob[j.id]} /></div>
                <span className="truncate text-xs text-slate-500">{j.job_type || '–'}</span>
                <span className="flex gap-2 text-xs">
                  {busy ? <span className="inline-flex items-center gap-1 text-slate-400"><Loader2 size={12} className="animate-spin" />writing…</span> : <>
                  <a className="inline-flex items-center gap-1 text-teal-600 hover:underline" href={fileUrl(j.cv)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}><FileText size={12} />CV</a>
                  {j.letter && <a className="inline-flex items-center gap-1 text-teal-600 hover:underline" href={fileUrl(j.letter)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}><Mail size={12} />Letter</a>}</>}
                </span>
                <SureButton className="btn border border-slate-200 bg-slate-50 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50" ask="Rebuild?"
                  title={busy ? 'Being built right now' : 'Write a new CV and cover letter for this job'} disabled={busy} onClick={() => !busy && rebuild([j.id])}>
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Rebuild</SureButton>
                {j.applied
                  ? <SureButton className="btn border border-amber-200 bg-slate-50 py-1.5 text-xs text-amber-700 hover:bg-amber-50" ask="Not sent? Click again"
                      title="It failed or you did not finish – put the job back in your queue" onClick={() => notApplied(j.id)}><RotateCcw size={12} />Not applied</SureButton>
                  : act.data?.job?.id === j.id
                  ? <button className="btn border border-rose-200 bg-slate-50 py-1.5 text-xs text-rose-700 hover:bg-rose-50" title="Stop applying to this job"
                      onClick={(e) => { e.preventDefault(); stopBrowser() }}><Square size={11} />Stop applying</button>
                  : <button className="btn bg-teal py-1.5 text-xs text-white hover:bg-teal-600 disabled:opacity-50" disabled={busy} title="Opens the job in your browser; the extension fills the form"
                      onClick={(e) => { e.preventDefault(); applyInBrowser(j) }}><Play size={12} />Apply in my browser</button>}
                <a href={j.link} target="_blank" rel="noreferrer" title="Open posting" onClick={(e) => e.stopPropagation()} className="text-slate-400 hover:text-teal-600"><ExternalLink size={14} /></a>
              </label>)})}
          </div>)}
      </div>

      {(hist.data || []).length > 0 && (
        <div className="glass rounded-2xl">
          <div className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-navy">Recent attempts</div>
          <div className="divide-y divide-slate-100 text-sm">
            {hist.data.map((h) => (
              <div key={h.id} className="flex items-center gap-3 px-5 py-2.5">
                <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${h.status === 'applied' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : h.status === 'running' ? 'bg-sky-50 text-sky-700 ring-sky-200' : 'bg-slate-100 text-slate-500 ring-slate-200'}`}>{ATTEMPT(h) || 'applied'}</span>
                <span className="min-w-0 flex-1 truncate"><b>{h.company}</b> – {h.title}</span>
                <span className="text-xs text-slate-400">{fmtDateTime(h.started_at)}</span>
                {h.screenshot && <a className="text-xs text-teal-600 hover:underline" href={fileUrl(h.screenshot)} target="_blank" rel="noreferrer">confirmation</a>}
                {h.status === 'applied' && h.job_status === 'applied' &&
                  <SureButton className="text-xs text-amber-700 hover:underline" ask="Not sent? Click again" title="It failed or you did not finish – put the job back in your queue" onClick={() => notApplied(h.job_id)}>Not applied</SureButton>}
                {h.status !== 'applied' && h.job_status === 'built' && <>
                  <button className="text-xs text-teal-600 hover:underline" onClick={() => applyInBrowser({ id: h.job_id, link: h.link })}>Apply again</button>
                  <SureButton className="text-xs text-slate-500 hover:underline" ask="Sent? Click again" title="You did send it, but the app did not notice" onClick={() => markApplied(h.job_id)}>I did apply</SureButton></>}
              </div>))}
          </div>
        </div>)}
    </div>
  )
}
