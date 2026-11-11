import { Github, UserCheck } from 'lucide-react'

const tone = (s, target) => (s >= target ? 'text-emerald-600' : s >= target - 15 ? 'text-amber-600' : 'text-rose-600')

// "ATS 65 → 96" as one line (lists, headers)
export function AtsBadge({ ats }) {
  if (!ats?.after?.score && ats?.after?.score !== 0) return null
  const t = ats.target || 75
  return (
    <span className="chip gap-1 bg-slate-100 text-slate-700" title="ATS score: your main CV → the tailored CV, for this job">
      ATS <span className={tone(ats.before?.score ?? 0, t)}>{ats.before?.score ?? '–'}</span>→<b className={tone(ats.after.score, t)}>{ats.after.score}</b>
    </span>)
}

const PARTS = [['keywords', 'Keywords', 60], ['title', 'Job title', 10], ['sections', 'Sections', 10], ['contact', 'Contact', 10],
  ['results', 'Numbers', 5], ['languages', 'Languages', 5]]

export default function AtsPanel({ ats, projects, review }) {
  if (!ats?.after) return null
  const { before = {}, after, target = 75 } = ats
  const gained = (after.matched || []).filter((k) => !(before.matched || []).includes(k))
  return (
    <div className="space-y-3">
      <div className="flex items-end gap-4">
        <Score label="Main CV" value={before.score} target={target} />
        <div className="pb-2 text-slate-400">→</div>
        <Score label="Tailored" value={after.score} target={target} big />
        <div className="ml-auto pb-1 text-right text-xs muted">target {target}<br />{after.score - (before.score || 0) >= 0 ? '+' : ''}{after.score - (before.score || 0)} points</div>
      </div>
      <div className="grid grid-cols-3 gap-x-3 gap-y-1.5">
        {PARTS.map(([k, l, max]) => (
          <div key={k}>
            <div className="flex justify-between text-[11px] muted"><span>{l}</span><span className="tabular-nums">{after.parts?.[k] ?? 0}/{max}</span></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-teal" style={{ width: `${Math.round(100 * (after.parts?.[k] || 0) / max)}%` }} /></div>
          </div>))}
      </div>
      {(after.matched?.length > 0 || after.missing?.length > 0) && (
        <div className="flex flex-wrap gap-1">
          {(after.matched || []).map((k) => <span key={k} className={`chip ${gained.includes(k) ? 'bg-teal-50 text-teal-700 ring-1 ring-teal-200' : 'bg-emerald-50 text-emerald-700'}`} title={gained.includes(k) ? 'added by tailoring' : 'already on your main CV'}>{k}</span>)}
          {(after.missing || []).map((k) => <span key={k} className="chip bg-slate-100 text-slate-500 line-through decoration-slate-400/60" title="in the posting, not on your CV (left out on purpose)">{k}</span>)}
        </div>)}
      {projects?.some((p) => !p.unused) && (
        <div className="text-xs"><span className="muted">Projects: </span>
          {projects.filter((p) => !p.unused).map((p, i) => <span key={i} className="mr-2 inline-flex items-center gap-1">{p.source !== 'cv' && <Github size={11} className="text-slate-500" />}{p.name}
            <span className="muted">({p.source === 'github' ? 'GitHub' : p.source === 'cv+github' ? 'CV + GitHub' : 'CV'})</span></span>)}
        </div>)}
      {review && (
        <div className={`rounded-xl border px-3 py-2 text-xs ${review.verdict === 'interview' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : review.verdict === 'maybe' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-rose-200 bg-rose-50 text-rose-800'}`}>
          <div className="flex items-center gap-1.5 font-semibold"><UserCheck size={13} />Recruiter: {review.verdict === 'interview' ? 'would call' : review.verdict === 'maybe' ? 'maybe' : 'would reject'}</div>
          {review.reason && <div className="mt-0.5">{review.reason}</div>}
          {review.knockouts?.length > 0 && <div className="mt-1"><b>Knock-out:</b> {review.knockouts.join('; ')}</div>}
        </div>)}
    </div>)
}

function Score({ label, value, target, big }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider muted">{label}</div>
      <div className={`${big ? 'text-3xl' : 'text-xl'} font-semibold tabular-nums ${tone(value ?? 0, target)}`}>{value ?? '–'}</div>
    </div>)
}
