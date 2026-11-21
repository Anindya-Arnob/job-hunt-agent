import { useEffect, useState } from 'react'
import { CheckCircle2, Target } from 'lucide-react'
import { api } from '../api'
import { useToast } from '../ui'
import { BuildingNote } from './ActiveBuilds'

// After a build below your ATS target: the ad's missing words that would lift it, fewest first. Words your CV
// or GitHub already shows come ticked (with the proof); others only if you tick them. What you tick goes into
// your main CV once, then this CV is built again.
export default function ReachTarget({ jobId, building, onStarted }) {
  const [plan, setPlan] = useState(null)
  const [picked, setPicked] = useState({})
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const load = async () => {
    try {
      const p = await api.get(`/api/jobs/${jobId}/boost`)
      setPlan(p)
      setPicked(Object.fromEntries(p.items.filter((i) => i.proof).map((i) => [i.term, { where: '' }])))
    } catch { setPlan(null) }
  }
  useEffect(() => { setPlan(null); load() }, [jobId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (building) return <div className="rounded-xl border border-teal-200 bg-teal-50/40 p-3"><BuildingNote b={building} /></div>
  if (!plan) return null
  if (plan.score >= plan.target) return null
  const all = [...plan.items, ...(showMore(plan) ? plan.others : [])]
  const ticked = all.filter((i) => picked[i.term])
  const gain = Math.round(ticked.reduce((a, i) => a + i.points, 0))
  const flip = (t) => setPicked((p) => { const n = { ...p }; if (n[t]) delete n[t]; else n[t] = { where: '' }; return n })
  const add = async () => {
    setBusy(true)
    try {
      const r = await api.post(`/api/jobs/${jobId}/boost`, { items: ticked.map((i) => ({ term: i.term, where: picked[i.term].where, proof: i.proof })) })
      toast(`Added to your main CV: ${r.added.map((a) => a.term).join(', ')}. Building the CV again…`)
      onStarted && onStarted()
    } catch (e) { toast(e.message, 'error') }
    setBusy(false)
  }
  return (
    <div className="space-y-2 rounded-xl border border-slate-200 p-3 text-sm" data-testid="reach-target">
      <div className="flex items-center gap-2 font-semibold"><Target size={15} className="text-teal-600" />
        Your CV: {plan.score} · target {plan.target}<span className="ml-auto text-xs font-normal muted">{plan.need} points to go</span></div>
      {!plan.items.length
        ? <p className="text-xs muted">No missing words left that would raise the score.</p>
        : <p className="text-xs muted">The ad asks for these. Tick only what you have really done; ticked words go into your main CV once (you won't be asked again) and this CV is built again.</p>}
      {all.map((i) => (
        <div key={i.term} className="rounded-lg border border-slate-100 px-2.5 py-1.5" data-testid="boost-item">
          <label className="flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={!!picked[i.term]} onChange={() => flip(i.term)} aria-label={i.term} />
            <span className="font-medium">{i.term}</span><span className="text-xs text-teal-700">+{i.points}</span>
            {i.proof ? <span className="ml-auto flex items-center gap-1 text-xs text-emerald-700" title={i.proof.text}><CheckCircle2 size={12} />found in {i.proof.where}</span>
              : <span className="ml-auto text-xs muted">no proof found: tick only if you did it</span>}
          </label>
          {picked[i.term] && (
            <input className="input mt-1.5 py-1 text-xs" placeholder={i.proof ? `Optional – Claude writes it from: “${i.proof.text.slice(0, 70)}…”` : 'Where? (optional, e.g. "Job Hunt Agent: retries and timeouts")'}
              value={picked[i.term].where} onChange={(e) => setPicked((p) => ({ ...p, [i.term]: { where: e.target.value } }))} aria-label={`Where ${i.term}`} />)}
        </div>))}
      {!plan.reachable && plan.items.length > 0 && <p className="text-xs text-amber-700">Even with every word the target is out of reach for this ad; the language or experience gaps above weigh more.</p>}
      {plan.items.length > 0 && (
        <div className="flex items-center gap-2 pt-1">
          <span className="text-xs muted">{ticked.length} ticked · about +{gain} points</span>
          <button className="btn-teal ml-auto" disabled={busy || !ticked.length} onClick={add}>{busy ? 'Adding…' : 'Add and rebuild'}</button>
        </div>)}
    </div>
  )
}

const showMore = () => false
