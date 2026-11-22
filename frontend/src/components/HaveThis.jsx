import { useState } from 'react'
import { api, stoppedText } from '../api'
import { useToast } from '../ui'

// A tool the ad says you must have is not on your CV. If you really have it: pick which one, say where, and it
// goes into your main CV once; then this CV is built (the other knock-outs still apply).
export default function HaveThis({ jobId, groups, onStarted }) {
  const [open, setOpen] = useState(null)
  const [pick, setPick] = useState('')
  const [where, setWhere] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  if (!groups?.length) return null
  const add = async (names) => {
    setBusy(true)
    try {
      const term = pick || names[0]
      const r = await api.post(`/api/jobs/${jobId}/boost`, { force: false, items: [{ term, where }] })
      if ((r.stopped || []).length) toast(`Added ${term} to your main CV. ` + stoppedText(r), 'warn')
      else { toast(`Added ${term} to your main CV. Building the CV…`); onStarted && onStarted() }
      setOpen(null)
    } catch (e) { toast(e.message, 'error') }
    setBusy(false)
  }
  return (
    <div className="mt-2 space-y-1.5" data-testid="have-this">
      {groups.map((names, i) => (
        <div key={names.join('|')} className="rounded-md border border-rose-200 px-2.5 py-1.5 text-xs">
          <div className="flex items-center gap-2">
            <span>Required: <b>{names.length > 1 ? `one of ${names.join(', ')}` : names[0]}</b></span>
            {open !== i && <button className="btn-outline ml-auto py-0.5 text-xs" onClick={() => { setOpen(i); setPick(names[0]); setWhere('') }}>I have this</button>}
          </div>
          {open === i && (
            <div className="mt-1.5 space-y-1.5">
              <div className="flex items-center gap-1.5">
                {names.length > 1 && (
                  <select className="input w-auto shrink-0 py-1 text-xs" value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Which one">
                    {names.map((n) => <option key={n}>{n}</option>)}</select>)}
                <input className="input min-w-0 flex-1 py-1 text-xs" placeholder='Where? e.g. "Master thesis: vehicle routing"' value={where}
                  onChange={(e) => setWhere(e.target.value)} aria-label="Where you used it" />
              </div>
              <div className="flex items-center justify-end gap-1.5">
                <button className="btn-ghost py-1 text-xs" onClick={() => setOpen(null)}>Cancel</button>
                <button className="btn-teal py-1 text-xs" disabled={busy} onClick={() => add(names)}>{busy ? 'Adding…' : 'Add to my CV and build'}</button>
              </div>
            </div>)}
        </div>))}
      <p className="text-xs muted">Only if you really used it: it goes into your main CV and every later CV.</p>
    </div>
  )
}
