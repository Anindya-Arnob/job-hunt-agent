import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import { X, CheckCircle2, AlertTriangle, Info } from 'lucide-react'

export function VerdictBadge({ verdict }) {
  if (!verdict) return <span className="chip bg-slate-100 text-slate-500">Not scored</span>
  const cls = verdict === 'Apply' ? 'bg-emerald-100 text-emerald-800'
    : verdict === 'Skip' ? 'bg-slate-200 text-slate-600' : 'bg-amber-100 text-amber-800'
  return <span className={`chip ${cls}`}>{verdict}</span>
}

export function StatusBadge({ status }) {
  const map = {
    new: 'bg-sky-100 text-sky-800', scored: 'bg-teal-50 text-teal-600', needs_review: 'bg-amber-100 text-amber-800',
    excluded: 'bg-slate-200 text-slate-600', built: 'bg-indigo-100 text-indigo-800', applied: 'bg-blue-100 text-blue-800',
    interview: 'bg-violet-100 text-violet-800', offer: 'bg-emerald-100 text-emerald-800', rejected: 'bg-rose-100 text-rose-700',
    connected: 'bg-emerald-100 text-emerald-800', expired: 'bg-amber-100 text-amber-800', not_connected: 'bg-slate-200 text-slate-600',
    not_needed: 'bg-slate-100 text-slate-500', logging_in: 'bg-sky-100 text-sky-800', running: 'bg-sky-100 text-sky-800',
    done: 'bg-emerald-100 text-emerald-800', failed: 'bg-rose-100 text-rose-700', paused: 'bg-amber-100 text-amber-800',
    cancelled: 'bg-slate-200 text-slate-600',
  }
  const label = { needs_review: 'Needs review', not_connected: 'Not connected', not_needed: 'No login needed',
    logging_in: 'Logging in…' }[status] || (status ? status[0].toUpperCase() + status.slice(1) : '')
  return <span className={`chip ${map[status] || 'bg-slate-100 text-slate-600'}`}>{label}</span>
}

export function ScoreBar({ score, outdated }) {
  if (score === null || score === undefined) return <span className="muted">–</span>
  const color = score >= 65 ? 'bg-emerald-500' : score >= 40 ? 'bg-amber-500' : 'bg-slate-400'
  return (
    <div className="flex items-center gap-2" title={outdated ? 'Scored against an older CV version' : ''}>
      <span className={`w-7 text-right font-semibold tabular-nums ${outdated ? 'text-slate-400' : ''}`}>{score}</span>
      <div className="h-1.5 w-16 rounded bg-slate-200"><div className={`h-1.5 rounded ${color}`} style={{ width: `${score}%` }} /></div>
    </div>
  )
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${value === t.id ? 'border-teal text-navy' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
          {t.label}{t.count !== undefined && <span className="ml-1.5 rounded bg-slate-100 px-1.5 text-xs text-slate-600">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Modal({ open, onClose, title, children, wide, footer }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-6 backdrop-blur-sm" onClick={onClose}>
      <div className={`card-solid my-4 w-full ${wide ? 'max-w-6xl' : 'max-w-xl'}`} onClick={(e) => e.stopPropagation()}>
        <div className="card-h"><span>{title}</span><button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button></div>
        <div className="overflow-hidden p-4">{children}</div>
        {footer && <div className="relative z-10 flex flex-wrap items-center justify-end gap-2 rounded-b-2xl border-t border-slate-100 px-5 py-3">{footer}</div>}
      </div>
    </div>
  )
}

export function ChipSelect({ options, value, onChange, labels = {} }) {
  const toggle = (o) => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o])
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button key={o} type="button" onClick={() => toggle(o)}
          className={`rounded-full border px-2.5 py-1 text-xs font-medium transition ${value.includes(o) ? 'border-teal bg-teal-50 text-teal-600' : 'border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-400'}`}>
          {labels[o] || o}
        </button>
      ))}
    </div>
  )
}

export function TagInput({ value, onChange, placeholder }) {
  const [text, setText] = useState('')
  const add = () => { const t = text.trim(); if (t && !value.includes(t)) onChange([...value, t]); setText('') }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 p-1.5">
      {value.map((v) => (
        <span key={v} className="chip bg-slate-100 text-slate-700">{v}
          <button className="ml-1 text-slate-400 hover:text-slate-700" onClick={() => onChange(value.filter((x) => x !== v))}>×</button></span>
      ))}
      <input className="min-w-[120px] flex-1 bg-transparent px-1 text-sm text-slate-900 outline-none placeholder:text-slate-400" value={text} placeholder={placeholder}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add() } }} onBlur={add} />
    </div>
  )
}

export function Empty({ icon: Icon = Info, title, children }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-slate-500">
      <Icon size={28} className="text-slate-300" />
      <div className="font-semibold text-slate-700">{title}</div>
      <div className="max-w-md text-sm">{children}</div>
    </div>
  )
}

export function Stat({ label, value, sub, accent }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${accent || 'text-navy'}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function CheckList({ checks }) {
  if (!checks || !checks.length) return null
  return (
    <ul className="space-y-1">
      {checks.map((c, i) => (
        <li key={i} className="flex items-start gap-1.5 text-sm">
          {c.passed ? <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-600" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600" />}
          <span>{c.name}{c.detail && <span className="muted"> — {c.detail}</span>}</span>
        </li>
      ))}
    </ul>
  )
}

// ----------------------------------------------------------------- toasts ---
const ToastCtx = createContext(() => {})
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const push = useCallback((msg, kind = 'info') => {
    const id = Math.random()
    setItems((x) => [...x, { id, msg, kind }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'error' ? 7000 : 4000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex w-96 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={`card-solid flex items-start gap-2 p-3 text-sm ${t.kind === 'error' ? 'border-rose-200' : t.kind === 'warn' ? 'border-amber-200' : ''}`}>
            {t.kind === 'error' ? <AlertTriangle size={16} className="text-rose-600" /> : t.kind === 'warn' ? <AlertTriangle size={16} className="text-amber-600" /> : <CheckCircle2 size={16} className="text-teal" />}
            <span className="flex-1">{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
export const useToast = () => useContext(ToastCtx)

export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const run = useCallback(async () => {
    try { setState((s) => ({ ...s, loading: true })); const data = await fn(); setState({ loading: false, data, error: null }) }
    catch (e) { setState({ loading: false, data: null, error: e }) }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { run() }, [run])
  return { ...state, reload: run, setData: (d) => setState((s) => ({ ...s, data: d })) }
}

// PDF preview as images (works in every browser, no PDF plugin needed)
export function PdfPages({ path, bust = 0, className = '' }) {
  const [pages, setPages] = useState(0)
  useEffect(() => {
    let on = true
    if (path) fetch(`/api/files/pages?path=${encodeURIComponent(path)}`).then((r) => r.json()).then((d) => on && setPages(d.pages || 0)).catch(() => {})
    return () => { on = false }
  }, [path, bust])
  if (!path) return null
  return (
    <div className={`min-h-0 flex-1 space-y-3 overflow-y-auto bg-slate-100 p-3 ${className}`}>
      {Array.from({ length: pages }, (_, i) => (
        <img key={`${i}-${bust}`} alt={`page ${i + 1}`} className="mx-auto w-full max-w-[820px] rounded-sm bg-white shadow-lg"
          src={`/api/files/page-image?path=${encodeURIComponent(path)}&page=${i}&t=${bust}`} />))}
      <div className="text-center text-xs text-slate-500">{pages} page{pages === 1 ? '' : 's'} · <a className="text-teal" href={`/api/files?path=${encodeURIComponent(path)}`} target="_blank" rel="noreferrer">open PDF</a></div>
    </div>
  )
}
