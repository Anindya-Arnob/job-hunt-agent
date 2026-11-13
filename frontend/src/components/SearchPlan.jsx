import { Fragment, useEffect, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, Info, Minus, Plus, RotateCcw } from 'lucide-react'
import { api } from '../api'
import { fmtLeft } from './RunProgress'

// The search plan of a profile: how many searches each job site gets per run, how many of them are narrower
// (city) searches, and how they are shared between your places and job fields (equal unless you say otherwise).
// The numbers come from the same planner the run uses, so what you see here is what the run does.

const WEIGHTS = [[1, 'Normal'], [2, 'More'], [3, 'Most']]

function Stepper({ value, onChange, min = 0, max = 400, step = 5, label }) {
  const set = (v) => onChange(Math.max(min, Math.min(max, Number.isFinite(v) ? v : min)))
  return (
    <div className="inline-flex items-center rounded-md border border-slate-300" aria-label={label}>
      <button type="button" className="px-1.5 py-1 hover:bg-slate-100" onClick={() => set(value - step)} aria-label={`less ${label}`}><Minus size={12} /></button>
      <input className="w-12 bg-transparent text-center text-sm font-semibold outline-none" value={value} inputMode="numeric"
        onChange={(e) => set(parseInt(e.target.value || '0', 10))} aria-label={label} />
      <button type="button" className="px-1.5 py-1 hover:bg-slate-100" onClick={() => set(value + step)} aria-label={`more ${label}`}><Plus size={12} /></button>
    </div>
  )
}

function WeightPick({ value, onChange }) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-slate-300 text-xs">
      {WEIGHTS.map(([w, l]) => (
        <button type="button" key={w} onClick={() => onChange(w)}
          className={`px-2 py-0.5 ${value === w ? 'bg-teal-50 font-semibold text-teal-600' : 'hover:bg-slate-100'}`}>{l}</button>
      ))}
    </div>
  )
}

function Warning({ w }) {
  const risk = w.level === 'risk'
  return (
    <div className={`flex gap-1.5 rounded-md px-2 py-1 text-xs ${risk ? 'border border-amber-200 bg-amber-50 text-amber-700' : 'muted'}`}>
      {risk ? <AlertTriangle size={13} className="mt-0.5 shrink-0" /> : <Info size={13} className="mt-0.5 shrink-0" />}<span>{w.text}</span>
    </div>
  )
}

function ShareTable({ title, names, kind, pv, weights, setWeight, hint }) {
  if (!names.length) return null
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between"><span className="label mb-0">{title}</span><span className="text-xs muted">{hint}</span></div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs muted">
            <th className="py-1 pr-2 font-normal"></th><th className="py-1 pr-3 font-normal">Share</th>
            {pv.sites.map((s) => <th key={s.site} className="py-1 pr-2 text-right font-normal">{s.label}</th>)}
          </tr></thead>
          <tbody>
            {names.map((n) => (
              <tr key={n} className="border-t border-slate-100">
                <td className="py-1 pr-2">{n}</td>
                <td className="py-1 pr-3"><WeightPick value={weights[n] || 1} onChange={(w) => setWeight(kind, n, w)} /></td>
                {pv.sites.map((s) => {
                  const map = s[kind] || {}
                  const off = !(n in map)
                  const v = map[n]
                  return <td key={s.site} className={`py-1 pr-2 text-right tabular-nums ${off ? 'muted' : v === 0 ? 'font-semibold text-rose-600' : ''}`}
                    title={off ? `${s.label} does not search ${n}` : `${v} searches on ${s.label}`}>{off ? '–' : v}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function SearchPlan({ form, onPlan }) {
  const plan = form.plan || {}
  const [pv, setPv] = useState(null)
  const [open, setOpen] = useState(false)
  const key = JSON.stringify([form.roles, form.job_types, form.locations, form.sources, form.keywords_in, form.work_modes, form.days, plan])
  useEffect(() => {
    let stop = false
    const t = setTimeout(async () => {
      try {
        const body = { ...form, name: form.name || 'x' }; delete body.id
        const r = await api.post('/api/filters/plan', body)
        if (!stop) setPv(r)
      } catch { /* shown next time */ }
    }, 300)
    return () => { stop = true; clearTimeout(t) }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!pv || !pv.sites.length) return null
  const setSite = (site, k, v) => {
    const cur = pv.sites.find((s) => s.site === site)
    const next = { searches: cur.searches, narrow: cur.narrow, ...(plan.sites?.[site] || {}), [k]: v }
    if (next.narrow > next.searches) next.narrow = next.searches
    onPlan({ ...plan, sites: { ...(plan.sites || {}), [site]: next } })
  }
  const setWeight = (kind, name, w) => onPlan({ ...plan, [kind]: { ...(plan[kind] || {}), [name]: w } })
  const equal = () => onPlan({ ...plan, places: {}, fields: {} })
  const resetSites = () => onPlan({ ...plan, sites: {} })
  const risky = pv.sites.filter((s) => s.risky)
  const customWeights = Object.values(plan.places || {}).some((w) => w > 1) || Object.values(plan.fields || {}).some((w) => w > 1)

  return (
    <div className="mt-3 rounded-md border border-slate-200" data-testid="search-plan">
      <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        <span className="text-sm font-semibold">Search plan</span>
        <span className="flex flex-1 flex-wrap gap-x-3 gap-y-0.5 text-xs muted">
          {pv.sites.map((s) => (
            <span key={s.site} className={s.risky ? 'font-semibold text-amber-700' : ''}>{s.label} {s.used + s.narrow} · ~{fmtLeft(s.minutes * 60)}</span>
          ))}
          <span>{customWeights ? 'your own shares' : 'equal shares'}</span>
        </span>
        <span className="text-xs font-semibold text-teal">{open ? 'Close' : 'Change'}</span>
      </button>
      {!open && risky.length > 0 && <div className="space-y-1 px-3 pb-2">{risky.map((s) => <Warning key={s.site} w={s.warnings.find((w) => w.level === 'risk')} />)}</div>}
      {open && (
        <div className="space-y-4 border-t border-slate-200 p-3">
          <div>
            <div className="mb-1 flex items-baseline justify-between">
              <span className="label mb-0">Searches per site, each run</span>
              <button type="button" className="flex items-center gap-1 text-xs muted hover:underline" onClick={resetSites}><RotateCcw size={11} />Back to the usual numbers</button>
            </div>
            <p className="mb-2 text-xs muted">A search reads up to 10 result pages. When a search has more jobs than that, a <b>narrower search</b> repeats it in smaller places: a country in its cities one at a time (e.g. Canada → Toronto, Vancouver, Montreal …), a region in its countries. The searches with the most unread jobs go first, until the number you set is used up.</p>
            <div className="space-y-2">
              {pv.sites.map((s) => (
                <div key={s.site} className="rounded-md bg-slate-50 p-2">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
                    <span className="w-24 font-semibold">{s.label}</span>
                    <span className="flex items-center gap-1.5"><Stepper label={`${s.label} searches`} value={s.searches} onChange={(v) => setSite(s.site, 'searches', v)} max={pv.max_searches} /> searches</span>
                    <span className="flex items-center gap-1.5">of them <Stepper label={`${s.label} narrower searches`} value={s.narrow} onChange={(v) => setSite(s.site, 'narrow', v)} max={s.searches} /> narrower</span>
                    <span className="text-xs muted">about {fmtLeft(s.minutes * 60)}</span>
                    <span className={`ml-auto rounded px-1.5 py-0.5 text-xs font-semibold ${s.risky ? 'bg-amber-100 text-amber-700' : 'bg-emerald-50 text-emerald-600'}`}>
                      {s.risky ? `may block (above ~${s.safe})` : `safe (up to ~${s.safe})`}</span>
                  </div>
                  <div className="mt-1 text-xs muted">
                    {s.used} for your places and fields{s.possible < s.base ? ` (only ${s.possible} different searches exist)` : ''} + up to {s.narrow} narrower
                    {s.in_chrome ? ' · collected in your own Chrome' : ''}
                    {s.not_here.length > 0 && ` · ${s.label} only covers ${Object.keys(s.places).join(', ') || 'Germany'}`}
                  </div>
                  {s.warnings.length > 0 && <div className="mt-1.5 space-y-1">{s.warnings.map((w, i) => <Warning key={i} w={w} />)}</div>}
                </div>
              ))}
            </div>
          </div>
          <div className="flex items-baseline justify-between">
            <p className="text-xs muted">Every place and job field gets the same number of searches. Choose <b>More</b> (double) or <b>Most</b> (triple) to focus on some; the others share what is left. The numbers show the searches on each site.</p>
            {customWeights && <button type="button" className="ml-3 shrink-0 text-xs font-semibold text-teal" onClick={equal}>Make all equal</button>}
          </div>
          <ShareTable title="Places" names={pv.places} kind="places" pv={pv} weights={pv.weights.places} setWeight={setWeight} hint="– = the site does not cover it" />
          <ShareTable title="Job fields" names={pv.fields} kind="fields" pv={pv} weights={pv.weights.fields} setWeight={setWeight} hint="red 0 = no searches left" />
        </div>
      )}
    </div>
  )
}
