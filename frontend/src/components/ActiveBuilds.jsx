import { useEffect, useState } from 'react'
import { Loader2, Square } from 'lucide-react'
import { api } from '../api'
import { useOnEvent } from '../events'
import { useToast } from '../ui'

// Which jobs' CVs are being built right now. Asked from the app (not kept in the page), so leaving a screen and
// coming back still shows the build, and a second build of the same job is never started by accident.
export function useActiveBuilds() {
  const [list, setList] = useState([])
  const load = async () => { try { setList(await api.get('/api/build/active')) } catch { /* app restarting */ } }
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!list.length) return
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [list.length])
  useOnEvent((ev) => { if (ev.type === 'build' || String(ev.type).startsWith('run_')) load() }, [])
  const byJob = Object.fromEntries(list.map((b) => [b.job_id, b]))
  return { list, byJob, reload: load }
}

export function BuildingNote({ b, onStopped }) {
  const [armed, setArmed] = useState(false)
  const toast = useToast()
  useEffect(() => { if (!armed) return; const t = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(t) }, [armed])
  if (!b) return null
  const mins = Math.max(0, Math.round((Date.now() / 1000 - b.started) / 60))
  const others = (b.in_run || 1) - 1
  const stop = async (e) => {
    e.preventDefault(); e.stopPropagation()
    if (!armed) return setArmed(true)
    setArmed(false)
    try {
      await api.post(`/api/runs/${b.run_id}/cancel`)
      toast(others ? `Stopped – also the ${others} other job(s) waiting in the same build` : 'Stopped')
      onStopped && onStopped()
    } catch (err) { toast(err.message, 'error') }
  }
  return (
    <div className="mt-1 flex items-center gap-1.5 text-xs text-teal-600" data-testid="building-note">
      <Loader2 size={12} className="shrink-0 animate-spin" />
      <span className="min-w-0 truncate" title={b.last}>
        {b.state === 'waiting' ? 'Waiting for its turn (CVs are built one after another)' : `Building${b.attempt ? ` · attempt ${b.attempt}` : ''} · ${mins} min – ${b.last}`}
      </span>
      <button type="button" onClick={stop} data-testid="stop-build"
        title={others ? `Stops this build and the ${others} other job(s) queued in it` : 'Stop building this CV'}
        className="ml-1 inline-flex shrink-0 items-center gap-1 rounded border border-rose-200 px-1.5 py-0.5 font-semibold text-rose-600 hover:bg-rose-50">
        <Square size={10} />{armed ? (others ? `Stop all ${others + 1}?` : 'Stop?') : 'Stop'}</button>
    </div>
  )
}

// Every CV being built right now, wherever it was started (Apply, Build queue, Job board), with Stop.
export function BeingBuilt({ builds, onStopped, note = 'also when you started it on another screen' }) {
  if (!builds.list.length) return null
  return (
    <div className="card" data-testid="being-built">
      <div className="card-h">Being built right now ({builds.list.length})<span className="text-xs font-normal muted">{note}</span></div>
      <div className="divide-y divide-slate-100">
        {builds.list.map((b) => (
          <div key={b.job_id} className="px-4 py-2.5 text-sm"><div className="font-semibold">{b.company} – {b.title}</div>
            <BuildingNote b={b} onStopped={() => { builds.reload(); onStopped && onStopped() }} /></div>
        ))}
      </div>
    </div>
  )
}
