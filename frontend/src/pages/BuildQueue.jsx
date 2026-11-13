import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, Hammer, Play, SlidersHorizontal } from 'lucide-react'
import { api, fmtTime } from '../api'
import { CheckList, Empty, ScoreBar, StatusBadge, useAsync, useToast, VerdictBadge } from '../ui'
import { useEvents, useOnEvent } from '../events'
import { BuildingNote, useActiveBuilds } from '../components/ActiveBuilds'
import BuildOptions from '../components/BuildOptions'
import { AtsBadge } from '../components/AtsPanel'

export default function BuildQueue() {
  const toast = useToast()
  const runs = useAsync(() => api.get('/api/runs?limit=30'))
  const cands = useAsync(() => api.get('/api/build/candidates'))
  const docs = useAsync(() => api.get('/api/documents'))
  const [picked, setPicked] = useState([])
  const [showOpts, setShowOpts] = useState(false)
  const { events } = useEvents()
  const builds = useActiveBuilds()
  useOnEvent((ev) => { if (['run_done', 'run_failed', 'review', 'run_started'].includes(ev.type)) { runs.reload(); docs.reload(); cands.reload() } }, [])

  const buildRuns = (runs.data || []).filter((r) => r.kind === 'build')
  const paused = buildRuns.find((r) => r.status === 'paused')
  const active = buildRuns.find((r) => r.status === 'running')
  const current = active || paused || buildRuns[0]
  const runEvents = useMemo(() => events.filter((e) => current && e.run_id === current.id), [events, current])
  useEffect(() => { if (cands.data) setPicked(cands.data.filter((c) => c.verdict !== 'Skip').map((c) => c.job_id)) }, [cands.data])

  const toggle = (id) => setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id])
  const go = async () => {
    try {
      if (paused) { await api.post(`/api/build/${paused.id}/resume`, { job_ids: picked }) }
      else {
        const r = await api.post('/api/build', { job_ids: picked })
        if ((r.already_building || []).length) toast(`${r.already_building.length} job(s) were already being built – not started twice`, 'warn')
      }
      toast(`Building ${picked.length} CV(s)`); runs.reload(); builds.reload()
    } catch (e) { toast(e.message, 'error') }
  }
  const startReview = async () => { await api.post('/api/build', { job_ids: [] }); runs.reload() }

  const byJob = useMemo(() => {
    const m = {}
    for (const d of docs.data || []) { (m[d.job_id] = m[d.job_id] || { company: d.company, title: d.title, docs: [] }).docs.push(d) }
    return m
  }, [docs.data])

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="h1">Build queue</h1>
        {!paused && !active && <button className="btn-outline" onClick={startReview}>Start a review of all scored jobs</button>}
      </div>
      <button className="btn-outline" onClick={() => setShowOpts(!showOpts)}><SlidersHorizontal size={14} />Build options: model, projects, GitHub, ATS
        <ChevronDown size={14} className={`transition ${showOpts ? 'rotate-180' : ''}`} /></button>
      {showOpts && <BuildOptions compact />}
      {builds.list.length > 0 && (
        <div className="card" data-testid="being-built">
          <div className="card-h">Being built right now ({builds.list.length})<span className="text-xs font-normal muted">also when you started it on another screen</span></div>
          <div className="divide-y divide-slate-100">
            {builds.list.map((b) => (
              <div key={b.job_id} className="px-4 py-2.5 text-sm"><div className="font-semibold">{b.company} – {b.title}</div><BuildingNote b={b} onStopped={() => { builds.reload(); runs.reload() }} /></div>
            ))}
          </div>
        </div>)}
      <div className="grid gap-5 xl:grid-cols-5">
        <div className="card xl:col-span-3">
          <div className="card-h">{paused ? 'Waiting for your review – tick the jobs to build' : 'Ready to build (score 40+)'}
            <button className="btn-teal" disabled={!picked.length || !!active} onClick={go}>{paused ? <Play size={14} /> : <Hammer size={14} />}Build {picked.length}</button></div>
          {!cands.data?.length ? <Empty title="Nothing to build">Jobs appear here once they are scored 40 or higher.</Empty> : (<>
            <div className="flex items-center gap-3 border-b border-slate-100 bg-slate-50 px-4 py-2 text-sm">
              <input type="checkbox" aria-label="Tick all"
                checked={picked.length === cands.data.length && cands.data.length > 0}
                ref={(el) => { if (el) el.indeterminate = picked.length > 0 && picked.length < cands.data.length }}
                onChange={(e) => setPicked(e.target.checked ? cands.data.map((c) => c.job_id) : [])} />
              <span className="font-medium">{picked.length} of {cands.data.length} ticked</span>
              <span className="ml-auto flex gap-1">
                <button className="btn-outline py-1" onClick={() => setPicked(cands.data.map((c) => c.job_id))}>Tick all</button>
                <button className="btn-outline py-1" onClick={() => setPicked([])}>Untick all</button>
              </span>
            </div>
            <div className="max-h-[520px] overflow-y-auto">
              <table className="w-full text-sm"><tbody>
                {cands.data.map((c) => (
                  <tr key={c.job_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="w-10 px-4"><input type="checkbox" checked={picked.includes(c.job_id)} onChange={() => toggle(c.job_id)} /></td>
                    <td className="py-2"><ScoreBar score={c.score} /></td>
                    <td className="py-2"><Link to={`/jobs/${c.job_id}`} className="font-medium hover:text-teal">{c.title}</Link><div className="text-xs muted">{c.company}</div></td>
                    <td className="px-4 text-right"><VerdictBadge verdict={c.verdict} /></td>
                  </tr>))}
              </tbody></table>
            </div></>)}
          {cands.data?.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 p-3 text-sm">
              <span className="muted">Quick pick:</span>
              <button className="btn-ghost" onClick={() => setPicked(cands.data.filter((c) => c.verdict === 'Apply').map((c) => c.job_id))}>Apply only</button>
              <button className="btn-ghost" onClick={() => setPicked(cands.data.filter((c) => c.verdict !== 'Skip').map((c) => c.job_id))}>Apply + Tailor</button>
              <button className="btn-ghost" onClick={() => setPicked(cands.data.map((c) => c.job_id))}>All 40+ (incl. Skip)</button>
            </div>)}
        </div>
        <div className="card xl:col-span-2">
          <div className="card-h">Progress {current && <StatusBadge status={current.status} />}</div>
          {!current ? <div className="p-4 muted text-sm">No builds yet.</div> : (
            <div className="max-h-[560px] space-y-1 overflow-y-auto p-3 font-mono text-xs">
              {runEvents.map((e, i) => <div key={i} className={e.type === 'run_failed' ? 'text-rose-700' : ''}><span className="text-slate-400">{fmtTime(e.ts)}</span> {e.message}</div>)}
              {current.counts && Object.keys(current.counts).length > 0 && <div className="pt-2 font-sans text-sm">Result: {Object.entries(current.counts).map(([k, v]) => `${v} ${k}`).join(', ')}</div>}
            </div>)}
        </div>
      </div>
      <div className="card">
        <div className="card-h">Built documents</div>
        {!Object.keys(byJob).length ? <Empty title="No documents yet" /> : (
          <div className="divide-y divide-slate-100">
            {Object.entries(byJob).map(([jid, g]) => (
              <div key={jid} className="grid gap-3 p-4 md:grid-cols-3">
                <div><Link to={`/jobs/${jid}`} className="font-semibold hover:text-teal">{g.title}</Link><div className="flex items-center gap-2 text-xs muted">{g.company}<AtsBadge ats={g.docs.find((d) => d.kind === 'cv')?.ats} /></div>
                  <Link to={`/documents?job=${jid}`} className="text-xs text-teal">Preview & edit →</Link></div>
                {g.docs.sort((a, b) => a.kind.localeCompare(b.kind)).map((d) => (
                  <div key={d.id}><div className="mb-1 text-xs font-semibold uppercase muted">{d.kind === 'cv' ? 'CV' : 'Cover letter'} · {d.attempts} attempt{d.attempts > 1 ? 's' : ''}</div><CheckList checks={d.qa_results} />
                    {d.blockers_stated?.length > 0 && <div className="mt-1 text-xs text-amber-800">Named in letter: {d.blockers_stated.join('; ')}</div>}</div>))}
              </div>))}
          </div>)}
      </div>
    </div>
  )
}
