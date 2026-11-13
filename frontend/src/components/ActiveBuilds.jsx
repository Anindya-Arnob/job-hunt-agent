import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api } from '../api'
import { useOnEvent } from '../events'

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

export function BuildingNote({ b }) {
  if (!b) return null
  const mins = Math.max(0, Math.round((Date.now() / 1000 - b.started) / 60))
  return (
    <div className="mt-1 flex items-center gap-1.5 text-xs text-teal-600" data-testid="building-note">
      <Loader2 size={12} className="shrink-0 animate-spin" />
      <span className="truncate" title={b.last}>
        {b.state === 'waiting' ? 'Waiting for its turn' : `Building${b.attempt ? ` · attempt ${b.attempt}` : ''} · ${mins} min`} – {b.last}
      </span>
    </div>
  )
}
