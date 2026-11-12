import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'
import { api } from '../api'

// The progress bar of a search run: the whole run, then one bar per job site, each with about how long it still takes.
// The time left comes from how fast each site has been so far, so the first minute says "working out the time".

export const fmtLeft = (s) => {
  if (s == null) return null
  if (s < 60) return 'less than a minute'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60), r = m % 60
  return r ? `${h} h ${r} min` : `${h} h`
}
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const STAGE = { storing: 'Saving the new jobs', sorting: 'Sorting the new jobs', scoring: 'Scoring the new jobs against your CV' }
const FINISHED = ['done', 'failed', 'cancelled']

function Bar({ fraction, done, tall }) {
  const pct = Math.max(done ? 100 : 2, Math.round(100 * (fraction || 0)))
  return (
    <div className={`${tall ? 'h-2.5' : 'h-1.5'} overflow-hidden rounded-full bg-slate-200`}>
      <div className={`h-full rounded-full transition-all duration-700 ${done ? 'bg-emerald-500' : 'bg-teal'}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

export default function RunProgress({ runId, finished }) {
  const [p, setP] = useState(null)
  useEffect(() => {
    if (!runId) return
    let stop = false, timer
    const tick = async () => {
      try {
        const r = await api.get(`/api/runs/${runId}/progress`)
        if (stop) return
        setP(r)
        if (FINISHED.includes(r.stage)) return
      } catch { /* the app restarted: try again */ }
      if (!stop) timer = setTimeout(tick, 2000)
    }
    tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [runId, finished])

  if (!p || p.run_id !== runId) return null
  const over = FINISHED.includes(p.stage)
  if (!p.sources?.length && !over) return null
  const left = fmtLeft(p.eta_seconds)
  const head = over ? (p.stage === 'done' ? 'Finished' : p.stage === 'cancelled' ? 'Stopped' : 'Failed')
    : STAGE[p.stage] ? `${STAGE[p.stage]}${p.post?.total ? ` · ${p.post.done} of ${p.post.total}` : ''}`
      : 'Searching'
  return (
    <div className="space-y-2.5 rounded-md border border-slate-200 p-3" data-testid="run-progress">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-semibold">{head} <span className="font-normal muted">{Math.round(100 * p.fraction)}%</span></span>
        {!over && <span className="flex items-center gap-1 text-xs muted"><Clock size={12} />
          {left ? <>about {left} left · done around {clock(p.finishes_at)}</> : 'working out the time left…'}</span>}
        {over && p.elapsed_seconds != null && <span className="text-xs muted">took {fmtLeft(p.elapsed_seconds)}</span>}
      </div>
      <Bar fraction={p.fraction} done={p.stage === 'done'} tall />
      <div className="space-y-2 pt-1">
        {p.sources.map((s) => {
          const done = s.phase === 'done'
          return (
            <div key={s.key} className="text-xs">
              <div className="mb-1 flex justify-between gap-2">
                <span><span className="font-semibold">{s.label}</span> <span className="muted">{s.what}</span></span>
                <span className="shrink-0 muted">{done ? '' : s.eta_seconds == null ? '…' : `~${fmtLeft(s.eta_seconds)}`}</span>
              </div>
              <Bar fraction={s.fraction} done={done} />
            </div>
          )
        })}
      </div>
    </div>
  )
}
