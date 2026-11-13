import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AgGridReact } from 'ag-grid-react'
import {
  ArrowDown, ArrowUp, Bookmark, BookmarkPlus, Check, Columns3, Download, ExternalLink, FileText, Hammer, Mail,
  RefreshCw, RotateCcw, Rows2, Rows3, Search, SlidersHorizontal, Trash2, X,
} from 'lucide-react'
import { api, fmtDate, fmtDateTime, SOURCE_LABEL } from '../api'
import { Modal, StatusBadge, useToast, VerdictBadge } from '../ui'
import { useOnEvent } from '../events'

// ───────────────────────── constants ─────────────────────────
const TABS = [
  { id: 'new', label: 'New', hint: 'Scored jobs without a CV yet' },
  { id: 'built', label: 'CV built', hint: 'A tailored CV exists, not applied yet' },
  { id: 'applied', label: 'Applied', hint: 'Applied, interview, offer or rejected' },
  { id: 'review', label: 'Needs review', hint: 'Job type unclear – tell the app what it is' },
  { id: 'email', label: 'From email', hint: 'Good matches from your job-alert mails (StepStone, Glassdoor, XING …)' },
  { id: 'excluded', label: 'Excluded', hint: 'Filtered out (full-time, wrong field, low match from email …)' },
  { id: 'all', label: 'All', hint: 'Everything except excluded jobs' },
]

const VERDICTS = [
  { id: 'Apply', label: 'Apply', dot: 'bg-emerald-500', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
  { id: 'Apply after CV tailoring', label: 'Apply after tailoring', dot: 'bg-amber-500', pill: 'bg-amber-50 text-amber-700 ring-amber-200' },
  { id: 'Skip', label: 'Skip', dot: 'bg-slate-400', pill: 'bg-slate-100 text-slate-500 ring-slate-200' },
]
const V = Object.fromEntries(VERDICTS.map((v) => [v.id, v]))

const STATUS_LABEL = {
  new: 'New', scored: 'Scored', needs_review: 'Needs review', excluded: 'Excluded', built: 'CV built',
  applied: 'Applied', interview: 'Interview', offer: 'Offer', rejected: 'Rejected',
}
const STATUS_STYLE = {
  new: 'bg-sky-50 text-sky-700 ring-sky-200', scored: 'bg-teal-50 text-teal-600 ring-teal-100',
  needs_review: 'bg-amber-50 text-amber-700 ring-amber-200', excluded: 'bg-slate-100 text-slate-500 ring-slate-200',
  built: 'bg-indigo-50 text-indigo-700 ring-indigo-200', applied: 'bg-blue-50 text-blue-700 ring-blue-200',
  interview: 'bg-violet-50 text-violet-700 ring-violet-200', offer: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  rejected: 'bg-rose-50 text-rose-600 ring-rose-200',
}

const COLUMNS = [            // what the column chooser offers
  { id: 'score', label: 'Score' },
  { id: 'title', label: 'Job (title, company, place)' },
  { id: 'company', label: 'Company (own column)' },
  { id: 'verdict', label: 'Verdict' },
  { id: 'job_type', label: 'Job type' },
  { id: 'mode', label: 'Work mode' },
  { id: 'location', label: 'Location (own column)' },
  { id: 'role', label: 'Role' },
  { id: 'posted', label: 'Date posted' },
  { id: 'found', label: 'Date found' },
  { id: 'docs', label: 'CV / letter' },
  { id: 'sources', label: 'Source' },
  { id: 'breakers', label: 'Deal-breakers' },
  { id: 'status', label: 'Status' },
  { id: 'link', label: 'Posting link' },
]
const DEFAULT_HIDDEN = ['company', 'location', 'role', 'mode', 'posted', 'sources']

const SORTS = {
  score: { label: 'Score', dir: 'desc' },
  found: { label: 'Date found', dir: 'desc' },
  posted: { label: 'Date posted', dir: 'desc' },
  title: { label: 'Job title', dir: 'asc' },
  company: { label: 'Company', dir: 'asc' },
  verdict: { label: 'Verdict', dir: 'asc' },
  job_type: { label: 'Job type', dir: 'asc' },
  location: { label: 'Location', dir: 'asc' },
  status: { label: 'Status', dir: 'asc' },
}
const dirLabel = ({ key, dir }) => ['title', 'company', 'job_type', 'location'].includes(key) ? (dir === 'asc' ? 'A → Z' : 'Z → A')
  : key === 'score' ? (dir === 'desc' ? 'Highest first' : 'Lowest first')
    : ['found', 'posted'].includes(key) ? (dir === 'desc' ? 'Newest first' : 'Oldest first')
      : dir === 'asc' ? 'Ascending' : 'Descending'

const EMPTY = {
  q: '', verdicts: [], types: [], modes: [], countries: [], roles: [], sources: [], statuses: [],
  minScore: '', maxScore: '', found: '', posted: '', loc: '', german: '', cv: '', hideHard: false, hasDesc: false,
}
const WITHIN = [{ id: '', label: 'Any', long: 'any time' }, { id: 'today', label: 'Today', long: 'today' }, { id: '3', label: '3 days', long: 'last 3 days' }, { id: '7', label: '7 days', long: 'last 7 days' }, { id: '30', label: '30 d', long: 'last 30 days' }]

const PRESETS = [
  { name: 'Best matches', hint: 'Verdict Apply, highest score first', tab: 'all', filt: { verdicts: ['Apply'] }, sort: { key: 'score', dir: 'desc' } },
  { name: 'Worth tailoring', hint: 'Apply after CV tailoring', tab: 'all', filt: { verdicts: ['Apply after CV tailoring'] }, sort: { key: 'score', dir: 'desc' } },
  { name: 'Found this week', hint: 'Newest first', tab: 'all', filt: { found: '7' }, sort: { key: 'found', dir: 'desc' } },
  { name: 'Ready to build', hint: 'Score 65+, no CV yet', tab: 'all', filt: { minScore: '65', cv: 'no' }, sort: { key: 'score', dir: 'desc' } },
  { name: 'Germany, no hard blockers', hint: 'Country Germany, hard deal-breakers hidden', tab: 'all', filt: { countries: ['Germany'], hideHard: true }, sort: { key: 'score', dir: 'desc' } },
  { name: 'Working student jobs', hint: 'Job type Working Student', tab: 'all', filt: { types: ['Working Student'] }, sort: { key: 'score', dir: 'desc' } },
]

// ───────────────────────── helpers ─────────────────────────
const PREF_KEY = 'jobboard.prefs.v1'
const loadPrefs = () => { try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}') || {} } catch { return {} } }
const savePrefs = (p) => { try { localStorage.setItem(PREF_KEY, JSON.stringify(p)) } catch { /* private window */ } }

const DE_CITIES = /^(berlin|m[uü]nchen|munich|hamburg|frankfurt.*|k[oö]ln|cologne|stuttgart|d[uü]sseldorf|leipzig|dresden|hannover|hanover|n[uü]rnberg|nuremberg|bonn|karlsruhe|heidelberg|potsdam|darmstadt|mannheim|aachen|bremen|essen|dortmund|freiburg.*|mainz|wiesbaden|augsburg|regensburg|ulm|jena|magdeburg|kiel|l[uü]beck|m[uü]nster|bochum|erlangen|ingolstadt|wolfsburg|walldorf)$/i
function countryOf(loc) {
  const parts = (loc || '').split('|')[0].split(',').map((s) => s.replace(/\(.*?\)/g, '').trim()).filter(Boolean)
  const last = parts.filter((p) => !/^(remote|hybrid|on-?site)$/i.test(p)).pop()
  if (!last) return ''
  const area = (x) => x.replace(/^greater\s+|\s+(metropolitan|metro)?\s*(area|region)$/gi, '').trim()
  if (/^(germany|deutschland|de)$/i.test(last) || parts.some((x) => DE_CITIES.test(area(x)))
    || /bavaria|bayern|hesse|hessen|saxony|sachsen|baden|w[uü]rttemberg|nordrhein|north rhine|niedersachsen|brandenburg|rhineland/i.test(last)) return 'Germany'
  if (/^[A-Z]{2}( \d{5})?$/.test(last) || /^(usa|us|united states.*)$/i.test(last)) return 'United States'
  if (/^(uk|england|scotland|united kingdom)$/i.test(last)) return 'United Kingdom'
  return last
}

function enrich(j) {
  const loc = j.location || ''
  const breakers = (j.deal_breakers || []).filter((d) => d.text && !/^\s*none\s*$/i.test(d.text))
  return {
    ...j,
    _mode: j.work_mode || (/remote/i.test(loc) ? 'Remote' : /hybrid/i.test(loc) ? 'Hybrid' : ''),
    _country: countryOf(loc),
    _breakers: breakers,
    _hard: breakers.some((d) => d.hard),
    _german: /german|deutsch/i.test(j.language_req || '') || breakers.some((d) => /german|deutsch/i.test(d.text)),
  }
}

const strip = (t) => (t || '').replace(/^[^\p{L}\p{N}]+/u, '')

function within(iso, key) {
  if (!key) return true
  if (!iso) return false
  const t = new Date(iso).getTime()
  if (key === 'today') { const d = new Date(); d.setHours(0, 0, 0, 0); return t >= d.getTime() }
  return t >= Date.now() - +key * 864e5
}

function matches(j, f) {
  if (f.q) {
    const hay = `${j.title} ${j.company} ${j.location} ${j.job_type} ${(j.sources || []).join(' ')}`.toLowerCase()
    if (!f.q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w))) return false
  }
  if (f.verdicts.length && !f.verdicts.includes(j.verdict || '')) return false
  if (f.types.length && !f.types.includes(j.job_type || '')) return false
  if (f.modes.length && !f.modes.includes(j._mode)) return false
  if (f.countries.length && !f.countries.includes(j._country)) return false
  if (f.roles.length && !f.roles.includes(j.role_category || '')) return false
  if (f.sources.length && !(j.sources || []).some((s) => f.sources.includes(s))) return false
  if (f.statuses.length && !f.statuses.includes(j.status)) return false
  if (f.minScore !== '' && (j.score ?? -1) < +f.minScore) return false
  if (f.maxScore !== '' && (j.score ?? 999) > +f.maxScore) return false
  if (!within(j.first_seen, f.found)) return false
  if (!within(j.posted_date, f.posted)) return false
  if (f.loc && !(j.location || '').toLowerCase().includes(f.loc.toLowerCase())) return false
  if (f.german === 'hide' && j._german) return false
  if (f.german === 'only' && !j._german) return false
  if (f.cv === 'yes' && !j.has_cv) return false
  if (f.cv === 'no' && j.has_cv) return false
  if (f.hideHard && j._hard) return false
  if (f.hasDesc && !j.has_description) return false
  return true
}

function countBy(rows, get) {
  const m = new Map()
  for (const r of rows) for (const v of [].concat(get(r))) m.set(v, (m.get(v) || 0) + 1)
  return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([id, count]) => ({ id, count }))
}

// ───────────────────────── small pieces ─────────────────────────
function ScorePill({ score, outdated, compact }) {
  if (score === null || score === undefined) return <span className="text-slate-300">–</span>
  const c = score >= 65 ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : score >= 40 ? 'bg-amber-50 text-amber-700 ring-amber-200' : 'bg-slate-100 text-slate-500 ring-slate-200'
  return <span title={outdated ? 'Scored against an older CV version' : `Match score ${score}/100`}
    className={`inline-flex items-center justify-center rounded-lg font-bold tabular-nums ring-1 ${compact ? 'h-7 w-9 text-xs' : 'h-9 w-11 text-sm'} ${c} ${outdated ? 'opacity-60' : ''}`}>{score}</span>
}

const Pill = ({ cls, children, title }) => <span title={title} className={`inline-flex max-w-full items-center truncate rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${cls}`}>{children}</span>

function Popover({ label, icon: Icon, badge, children, align = 'left', width = 'w-72', title, active }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    if (!open) return
    const h = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const k = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', h); document.addEventListener('keydown', k)
    return () => { document.removeEventListener('mousedown', h); document.removeEventListener('keydown', k) }
  }, [open])
  return (
    <div className="relative" ref={ref}>
      <button title={title} onClick={() => setOpen(!open)}
        className={`btn-outline rounded-lg py-2 ${open || active ? 'border-teal text-navy ring-2 ring-teal/20' : ''}`}>
        <Icon size={14} />{label}{badge ? <span className="rounded-full bg-teal px-1.5 text-[11px] font-semibold leading-4 text-white">{badge}</span> : null}
      </button>
      {open && (
        <div className={`absolute top-full z-30 mt-1.5 ${align === 'right' ? 'right-0' : 'left-0'} ${width} rounded-2xl border border-slate-200 bg-surface2 p-2 shadow-2xl backdrop-blur-xl`}>
          {typeof children === 'function' ? children(() => setOpen(false)) : children}
        </div>)}
    </div>
  )
}

function ChipGroup({ label, options, value, onChange, labels = {}, empty = 'No data yet' }) {
  const toggle = (id) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id])
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
        {value.length > 0 && <button className="text-[11px] text-teal hover:underline" onClick={() => onChange([])}>clear</button>}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {!options.length && <span className="text-xs text-slate-400">{empty}</span>}
        {options.map((o) => {
          const on = value.includes(o.id)
          return (
            <button key={o.id || '_none'} onClick={() => toggle(o.id)}
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition ${on ? 'bg-teal text-white ring-teal' : 'bg-slate-50 text-slate-600 ring-slate-200 hover:bg-slate-50 hover:ring-slate-300'}`}>
              {on && <Check size={11} />}{labels[o.id] || o.id || 'Not stated'}
              {o.count !== undefined && <span className={`tabular-nums ${on ? 'text-teal-100' : 'text-slate-400'}`}>{o.count}</span>}
            </button>)
        })}
      </div>
    </div>
  )
}

function Segmented({ label, options, value, onChange }) {
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="inline-flex flex-wrap rounded-lg bg-slate-100 p-0.5">
        {options.map((o) => (
          <button key={o.id} onClick={() => onChange(o.id)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${value === o.id ? 'seg-on text-navy' : 'text-slate-500 hover:text-slate-800'}`}>{o.label}</button>))}
      </div>
    </div>
  )
}

function Toggle({ checked, onChange, children }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
      <span className={`relative h-5 w-9 rounded-full transition ${checked ? 'bg-teal' : 'bg-slate-300'}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition ${checked ? 'left-[18px]' : 'left-0.5'}`} />
      </span>
      <input type="checkbox" className="sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />{children}
    </label>
  )
}

// ───────────────────────── the board ─────────────────────────
export default function JobBoard() {
  const { id } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const grid = useRef()
  const prefs0 = useMemo(loadPrefs, [])
  const [tab, setTab] = useState(prefs0.tab || 'new')
  const [raw, setRaw] = useState(null)
  const [counts, setCounts] = useState({})
  const [filt, setFilt] = useState(EMPTY)
  const [showFilters, setShowFilters] = useState(!!prefs0.showFilters)
  const [hidden, setHidden] = useState(prefs0.hidden || DEFAULT_HIDDEN)
  const [density, setDensity] = useState(prefs0.density || 'comfortable')
  const [sort, setSort] = useState(prefs0.sort || { key: 'score', dir: 'desc' })
  const [widths, setWidths] = useState(prefs0.widths || {})
  const [views, setViews] = useState(prefs0.views || [])
  const [viewName, setViewName] = useState('')
  const [selected, setSelected] = useState([])
  const [toDelete, setToDelete] = useState(null)

  useEffect(() => { savePrefs({ tab, showFilters, hidden, density, sort, widths, views }) }, [tab, showFilters, hidden, density, sort, widths, views])

  const load = useCallback(async () => {
    const [rows, c] = await Promise.all([api.get(`/api/jobs?tab=${tab}`), api.get('/api/jobs/counts')])
    setRaw(rows.map(enrich)); setCounts(c)
  }, [tab])
  useEffect(() => { setRaw(null); load() }, [load])
  useOnEvent((ev) => { if (ev.type === 'run_done') load() }, [load])
  useEffect(() => { grid.current?.api?.redrawRows() }, [id])      // highlight the open row

  const all = raw || []
  const rows = useMemo(() => all.filter((j) => matches(j, filt)), [all, filt])
  const set = (patch) => setFilt((f) => ({ ...f, ...patch }))
  const compact = density === 'compact'

  // options for the filter chips come from the jobs on this tab
  const opts = useMemo(() => ({
    verdicts: countBy(all, (j) => j.verdict || '').filter((o) => o.id),
    types: countBy(all, (j) => j.job_type || ''),
    modes: countBy(all, (j) => j._mode),
    countries: countBy(all, (j) => j._country).slice(0, 10),
    roles: countBy(all, (j) => j.role_category || '').filter((o) => o.id),
    sources: countBy(all, (j) => j.sources || []),
    statuses: countBy(all, (j) => j.status),
  }), [all])

  const nFilters = ['verdicts', 'types', 'modes', 'countries', 'roles', 'sources', 'statuses'].reduce((n, k) => n + (filt[k].length ? 1 : 0), 0)
    + ['minScore', 'maxScore', 'found', 'posted', 'loc', 'german', 'cv'].filter((k) => filt[k] !== '').length + (filt.hideHard ? 1 : 0) + (filt.hasDesc ? 1 : 0)

  // chips for every active filter, each with its own ×
  const active = []
  const lst = (k, name, lab = (x) => x || 'Not stated') => filt[k].length && active.push({ k, label: `${name}: ${filt[k].map(lab).join(', ')}`, clear: () => set({ [k]: [] }) })
  lst('verdicts', 'Verdict', (x) => V[x]?.label || x); lst('types', 'Type'); lst('modes', 'Mode'); lst('countries', 'Country')
  lst('roles', 'Role'); lst('sources', 'Source', (x) => SOURCE_LABEL[x] || x); lst('statuses', 'Status', (x) => STATUS_LABEL[x] || x)
  if (filt.minScore !== '' || filt.maxScore !== '') active.push({ k: 'score', label: `Score ${filt.minScore || 0}–${filt.maxScore || 100}`, clear: () => set({ minScore: '', maxScore: '' }) })
  if (filt.found) active.push({ k: 'found', label: `Found: ${WITHIN.find((w) => w.id === filt.found).long}`, clear: () => set({ found: '' }) })
  if (filt.posted) active.push({ k: 'posted', label: `Posted: ${WITHIN.find((w) => w.id === filt.posted).long}`, clear: () => set({ posted: '' }) })
  if (filt.loc) active.push({ k: 'loc', label: `Location contains “${filt.loc}”`, clear: () => set({ loc: '' }) })
  if (filt.german) active.push({ k: 'german', label: filt.german === 'hide' ? 'No German required' : 'German required', clear: () => set({ german: '' }) })
  if (filt.cv) active.push({ k: 'cv', label: filt.cv === 'yes' ? 'CV built' : 'No CV yet', clear: () => set({ cv: '' }) })
  if (filt.hideHard) active.push({ k: 'hard', label: 'No hard deal-breakers', clear: () => set({ hideHard: false }) })
  if (filt.hasDesc) active.push({ k: 'desc', label: 'With description', clear: () => set({ hasDesc: false }) })

  // ── grid columns ──
  const openDocs = (e, j) => { e.stopPropagation(); nav(`/documents?job=${j.id}`) }
  const cols = useMemo(() => {
    const w = (cid, def) => widths[cid] ? { width: widths[cid], minWidth: Math.min(def.minWidth || 70, widths[cid]) } : def
    const c = [
      { colId: 'sel', headerName: '', checkboxSelection: true, headerCheckboxSelection: true, headerCheckboxSelectionFilteredOnly: true, width: 48, pinned: 'left', resizable: false, sortable: false, suppressMovable: true },
      { colId: 'score', field: 'score', headerName: 'Score', ...w('score', { width: 88 }), comparator: (a, b) => (a ?? -1) - (b ?? -1),
        cellRenderer: (p) => <ScorePill score={p.value} outdated={p.data.score_outdated} compact={compact} /> },
      { colId: 'title', field: 'title', headerName: 'Job', ...w('title', { flex: 2, minWidth: 240 }),
        comparator: (a, b) => strip(a).localeCompare(strip(b), undefined, { sensitivity: 'base' }),
        cellRenderer: (p) => compact
          ? <span className="flex min-w-0 items-baseline gap-1.5" title={`${p.value} · ${p.data.company}`}><span className="truncate font-semibold text-slate-800">{p.value}</span>
            {hidden.includes('company') && <span className="max-w-[45%] shrink-0 truncate text-xs text-slate-400">· {p.data.company}</span>}</span>
          : <div className="flex h-full min-w-0 flex-col justify-center leading-tight">
            <div className="truncate font-semibold text-slate-800" title={p.value}>{p.value}</div>
            <div className="mt-1 truncate text-xs text-slate-500" title={`${p.data.company} · ${p.data.location || ''}`}>
              {[hidden.includes('company') && <span key="c" className="font-medium text-slate-600">{p.data.company}</span>,
                hidden.includes('location') && p.data.location && <span key="l">{p.data.location}</span>,
                hidden.includes('mode') && p.data._mode && !(p.data.location || '').toLowerCase().includes(p.data._mode.toLowerCase()) && <span key="m">{p.data._mode}</span>]
                .filter(Boolean).map((el, i) => <span key={i}>{i > 0 && <span className="mx-1.5 text-slate-300">•</span>}{el}</span>)}</div>
          </div> },
      { colId: 'company', field: 'company', headerName: 'Company', ...w('company', { flex: 1, minWidth: 150 }),
        comparator: (a, b) => (a || '').localeCompare(b || '', undefined, { sensitivity: 'base' }), cellClass: 'font-medium text-slate-700' },
      { colId: 'verdict', field: 'verdict', headerName: 'Verdict', ...w('verdict', { width: 160 }),
        comparator: (a, b) => VERDICTS.findIndex((v) => v.id === a) - VERDICTS.findIndex((v) => v.id === b),
        cellRenderer: (p) => p.value ? <Pill cls={V[p.value]?.pill || 'bg-slate-100 text-slate-600 ring-slate-200'} title={p.value}>{V[p.value]?.label || p.value}</Pill> : <span className="text-xs text-slate-300">Not scored</span> },
      { colId: 'job_type', field: 'job_type', headerName: 'Type', ...w('job_type', { width: 132 }),
        cellRenderer: (p) => p.value ? <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">{p.value}</span> : <span className="text-slate-300">–</span> },
      { colId: 'mode', field: '_mode', headerName: 'Mode', ...w('mode', { width: 100 }),
        cellRenderer: (p) => p.value ? <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${p.value === 'Remote' ? 'bg-teal-50 text-teal-600' : p.value === 'Hybrid' ? 'bg-sky-50 text-sky-700' : 'bg-slate-100 text-slate-600'}`}>{p.value}</span> : <span className="text-slate-300">–</span> },
      { colId: 'location', field: 'location', headerName: 'Location', ...w('location', { flex: 1, minWidth: 150 }), cellClass: 'text-slate-600' },
      { colId: 'role', field: 'role_category', headerName: 'Role', ...w('role', { width: 160 }), cellClass: 'text-slate-600' },
      { colId: 'posted', field: 'posted_date', headerName: 'Posted', ...w('posted', { width: 112 }), headerTooltip: 'Date the company posted the job (when the site shows it)',
        cellRenderer: (p) => p.value ? <span className="text-slate-600">{fmtDate(p.value)}</span> : <span className="text-slate-300">–</span> },
      { colId: 'found', field: 'first_seen', headerName: 'Found', ...w('found', { width: 146 }), headerTooltip: 'Date and time this app first found the job',
        cellRenderer: (p) => !p.value ? <span className="text-slate-300">–</span> : compact
          ? <span className="text-xs text-slate-600">{fmtDate(p.value)} · {new Date(p.value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          : <div className="flex h-full flex-col justify-center leading-tight"><span className="text-slate-700">{fmtDate(p.value)}</span>
            <span className="mt-1 text-xs text-slate-400">{new Date(p.value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span></div> },
      { colId: 'docs', field: 'has_cv', headerName: 'CV / letter', ...w('docs', { width: 136 }),
        cellRenderer: (p) => p.value || p.data.has_letter
          ? <button onClick={(e) => openDocs(e, p.data)} title="Open in Documents" className="inline-flex items-center gap-2 rounded-md px-1.5 py-1 text-xs font-medium text-teal-600 hover:bg-teal-50">
            {p.value && <span className="inline-flex items-center gap-1"><FileText size={13} />CV</span>}
            {p.data.has_letter && <span className="inline-flex items-center gap-1"><Mail size={13} />Letter</span>}</button>
          : <span className="text-xs text-slate-300">Not built</span> },
      { colId: 'sources', field: 'sources', headerName: 'Source', ...w('sources', { width: 130 }), sortable: false,
        cellRenderer: (p) => <span className="truncate text-xs text-slate-500">{(p.value || []).map((s) => SOURCE_LABEL[s] || s).join(', ')}</span> },
      { colId: 'breakers', field: '_breakers', headerName: 'Deal-breakers', ...w('breakers', { flex: 1, minWidth: 150 }), sortable: false,
        cellRenderer: (p) => !p.value.length ? <span className="text-xs text-slate-300">None</span> : (
          <span className={`truncate text-xs ${p.data._hard ? 'text-rose-600' : 'text-amber-700'}`} title={p.value.map((d) => d.text).join('\n')}>
            {p.data._hard ? '⛔ ' : '⚠ '}{p.value[0].text.replace(/^(BLOCKER|Check):\s*/i, '')}{p.value.length > 1 && <span className="text-slate-400"> +{p.value.length - 1}</span>}</span>) },
      ...(tab === 'excluded' || tab === 'review' ? [{ colId: 'reason', field: 'exclude_reason', headerName: 'Reason', ...w('reason', { flex: 1, minWidth: 200 }), cellClass: 'text-slate-600' }] : []),
      { colId: 'status', field: 'status', headerName: 'Status', ...w('status', { width: 120 }),
        cellRenderer: (p) => <Pill cls={STATUS_STYLE[p.value] || 'bg-slate-100 text-slate-600 ring-slate-200'}>{STATUS_LABEL[p.value] || p.value}</Pill> },
      { colId: 'link', field: 'link', headerName: '', width: 52, pinned: 'right', resizable: false, sortable: false, cellStyle: { paddingLeft: 8, paddingRight: 8 },
        cellRenderer: (p) => p.value ? <a href={p.value} target="_blank" rel="noreferrer" title="Open the job posting" onClick={(e) => e.stopPropagation()}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-teal-50 hover:text-teal-600"><ExternalLink size={14} /></a> : null },
    ]
    return c.map((d) => ({ ...d, hide: hidden.includes(d.colId), sort: sort.key === d.colId ? sort.dir : null }))
  }, [tab, hidden, sort, compact, widths]) // eslint-disable-line

  const onSortChanged = (e) => {
    const s = e.api.getColumnState().find((c) => c.sort)
    if (s && (s.colId !== sort.key || s.sort !== sort.dir)) setSort({ key: s.colId, dir: s.sort })
  }
  const onResized = (e) => {
    if (!e.finished || !e.columns || e.source !== 'uiColumnResized') return      // only when you drag a column edge
    setWidths((w) => ({ ...w, ...Object.fromEntries(e.columns.filter((c) => c.getColId() !== 'sel').map((c) => [c.getColId(), c.getActualWidth()])) }))
  }

  const applyView = (v) => {
    setTab(v.tab || 'all'); setFilt({ ...EMPTY, ...(v.filt || {}) })
    if (v.sort) setSort(v.sort)
    if (v.hidden) setHidden(v.hidden)
    if (v.density) setDensity(v.density)
    toast(`View “${v.name}”`)
  }
  const saveView = () => {
    const name = viewName.trim()
    if (!name) return
    setViews([...views.filter((v) => v.name !== name), { name, tab, filt, sort, hidden, density }])
    setViewName(''); toast(`Saved view “${name}”`)
  }

  const buildSelected = async () => {
    try {
      const r = await api.post('/api/build', { job_ids: selected.map((s) => s.id) })
      const already = (r.already_building || []).length
      toast(already ? `${selected.length - already} CV(s) started; ${already} already being built (not started twice)` : `Building ${selected.length} CV(s) – run #${r.run_id}`)
      nav('/build')
    } catch (e) { toast(e.message, 'error') }
  }
  const setStatusSelected = async (status) => {
    if (!status) return
    try {
      await Promise.all(selected.map((j) => api.patch(`/api/jobs/${j.id}`, { status })))
      toast(`${selected.length} job${selected.length > 1 ? 's' : ''} set to “${STATUS_LABEL[status]}”`)
      clearSel(); load()
    } catch (e) { toast(e.message, 'error') }
  }
  const clearSel = () => { setSelected([]); grid.current?.api?.deselectAll() }

  // quick tiles above the tabs
  const tiles = [
    ...VERDICTS.map((v) => ({ id: v.id, label: v.label, dot: v.dot, n: all.filter((j) => j.verdict === v.id).length,
      on: filt.verdicts.length === 1 && filt.verdicts[0] === v.id, click: (on) => set({ verdicts: on ? [] : [v.id] }) })),
    { id: 'week', label: 'Found in 7 days', dot: 'bg-sky-500', n: all.filter((j) => within(j.first_seen, '7')).length,
      on: filt.found === '7', click: (on) => set({ found: on ? '' : '7' }) },
    { id: 'nocv', label: 'No CV yet', dot: 'bg-indigo-400', n: all.filter((j) => !j.has_cv).length,
      on: filt.cv === 'no', click: (on) => set({ cv: on ? '' : 'no' }) },
  ]

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-4">
      {/* header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="h1">Job board</h1>
          <p className="mt-1 text-sm muted">{TABS.find((t) => t.id === tab)?.hint}. Click a row for details, tick rows to build or delete.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-outline" title="Reload" onClick={load}><RefreshCw size={14} />Refresh</button>
          <a className="btn-outline" href="/api/export/jobs.xlsx"><Download size={14} />Excel</a>
        </div>
      </div>

      {/* quick tiles */}
      <div className="grid grid-cols-5 gap-3">
        {tiles.map((t) => (
          <button key={t.id} onClick={() => t.click(t.on)} title={t.on ? 'Show all' : `Show only “${t.label}”`}
            className={`rounded-2xl border bg-surface px-4 py-2.5 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow ${t.on ? 'border-teal ring-2 ring-teal/20' : 'border-slate-200'}`}>
            <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-slate-500"><span className={`h-2 w-2 rounded-full ${t.dot}`} />{t.label}</div>
            <div className="mt-0.5 text-xl font-bold tabular-nums text-navy">{raw ? t.n : '…'}</div>
          </button>))}
      </div>

      {/* tabs */}
      <div className="flex flex-wrap items-center gap-1 rounded-xl bg-slate-100 p-1">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => { setTab(t.id); clearSel() }} title={t.hint}
            className={`flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-sm font-medium transition ${tab === t.id ? 'seg-on text-navy' : 'text-slate-500 hover:text-slate-800'}`}>
            {t.label}{counts[t.id] !== undefined && <span className={`rounded-full px-1.5 text-xs tabular-nums ${tab === t.id ? 'bg-teal-50 text-teal-600' : 'bg-slate-200/70 text-slate-500'}`}>{counts[t.id]}</span>}
          </button>))}
      </div>

      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
          <input className="input rounded-lg py-2 pl-9 pr-8" placeholder="Search title, company, place…" value={filt.q} onChange={(e) => set({ q: e.target.value })} />
          {filt.q && <button className="absolute right-2 top-2 rounded p-0.5 text-slate-400 hover:text-slate-700" onClick={() => set({ q: '' })}><X size={15} /></button>}
        </div>
        <button className={`btn-outline rounded-lg py-2 ${showFilters || nFilters ? 'border-teal text-navy ring-2 ring-teal/20' : ''}`} onClick={() => setShowFilters(!showFilters)}>
          <SlidersHorizontal size={14} />Filters{nFilters > 0 && <span className="rounded-full bg-teal px-1.5 text-[11px] font-semibold leading-4 text-white">{nFilters}</span>}
        </button>
        <div className="flex items-center overflow-hidden rounded-full border border-slate-200 bg-slate-50 p-0.5">
          <span className="pl-3 text-xs text-slate-500">Sort</span>
          <select aria-label="Sort by" className="border-0 bg-transparent py-2 pl-2 pr-1 text-sm focus:outline-none" value={sort.key}
            onChange={(e) => setSort({ key: e.target.value, dir: SORTS[e.target.value].dir })}>
            {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
          <button className="flex items-center gap-1 border-l border-slate-200 px-3 py-2 text-sm hover:bg-slate-50" title="Reverse order"
            onClick={() => setSort({ ...sort, dir: sort.dir === 'asc' ? 'desc' : 'asc' })}>
            {sort.dir === 'asc' ? <ArrowUp size={14} /> : <ArrowDown size={14} />}{dirLabel(sort)}</button>
        </div>
        <Popover label="Views" icon={Bookmark} title="Ready-made and saved views" width="w-80">
          {(close) => (<div className="text-sm">
            <div className="px-2 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Ready-made</div>
            {PRESETS.map((v) => (
              <button key={v.name} onClick={() => { applyView(v); close() }} className="block w-full rounded-lg px-2 py-1.5 text-left hover:bg-slate-50">
                <div className="font-medium text-slate-800">{v.name}</div><div className="text-xs text-slate-500">{v.hint}</div></button>))}
            <div className="mt-2 border-t border-slate-100 px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Your views</div>
            {!views.length && <div className="px-2 py-1 text-xs text-slate-400">None yet – save the current filters, sort and columns below.</div>}
            {views.map((v) => (
              <div key={v.name} className="group flex items-center rounded-lg hover:bg-slate-50">
                <button className="flex-1 px-2 py-1.5 text-left font-medium text-slate-800" onClick={() => { applyView(v); close() }}>{v.name}</button>
                <button title="Delete this view" className="mr-1 rounded p-1 text-slate-300 hover:bg-rose-50 hover:text-rose-600" onClick={() => setViews(views.filter((x) => x.name !== v.name))}><Trash2 size={13} /></button>
              </div>))}
            <div className="mt-2 flex gap-1.5 border-t border-slate-100 px-1 pt-2">
              <input className="input py-1.5" placeholder="Name this view…" value={viewName} onChange={(e) => setViewName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && saveView()} />
              <button className="btn-teal shrink-0" disabled={!viewName.trim()} onClick={saveView}><BookmarkPlus size={14} />Save</button>
            </div>
          </div>)}
        </Popover>
        <Popover label="Columns" icon={Columns3} align="right" width="w-64" title="Show or hide columns">
          <div className="text-sm">
            <div className="flex items-center justify-between px-2 pb-1 pt-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Show columns</span>
              <button className="inline-flex items-center gap-1 text-xs text-teal hover:underline" onClick={() => { setHidden(DEFAULT_HIDDEN); setWidths({}) }}><RotateCcw size={11} />Reset</button>
            </div>
            {COLUMNS.map((c) => {
              const on = !hidden.includes(c.id)
              return (
                <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-50">
                  <input type="checkbox" className="accent-teal" checked={on} disabled={c.id === 'title' && on && hidden.length >= COLUMNS.length - 1}
                    onChange={() => setHidden(on ? [...hidden, c.id] : hidden.filter((x) => x !== c.id))} />{c.label}
                </label>)
            })}
            <p className="px-2 pt-1 text-xs text-slate-400">Drag a column edge to resize. Your choice is remembered.</p>
          </div>
        </Popover>
        <div className="flex overflow-hidden rounded-full border border-slate-200 bg-slate-50 p-0.5" role="group" aria-label="Row density">
          <button title="Comfortable rows" className={`px-2.5 py-2 ${!compact ? 'bg-slate-100 text-navy' : 'text-slate-400 hover:bg-slate-50'}`} onClick={() => setDensity('comfortable')}><Rows2 size={15} /></button>
          <button title="Compact rows" className={`border-l border-slate-200 px-2.5 py-2 ${compact ? 'bg-slate-100 text-navy' : 'text-slate-400 hover:bg-slate-50'}`} onClick={() => setDensity('compact')}><Rows3 size={15} /></button>
        </div>
      </div>

      {/* filter panel */}
      {showFilters && (
        <div className="max-h-[38vh] shrink-0 overflow-y-auto glass rounded-2xl px-5 py-4">
          <div className="grid gap-x-8 gap-y-4 md:grid-cols-2 xl:grid-cols-4">
            <ChipGroup label="Verdict" options={opts.verdicts} value={filt.verdicts} onChange={(v) => set({ verdicts: v })} labels={Object.fromEntries(VERDICTS.map((v) => [v.id, v.label]))} />
            <ChipGroup label="Job type" options={opts.types} value={filt.types} onChange={(v) => set({ types: v })} />
            <ChipGroup label="Work mode" options={opts.modes} value={filt.modes} onChange={(v) => set({ modes: v })} />
            <ChipGroup label="Source" options={opts.sources} value={filt.sources} onChange={(v) => set({ sources: v })} labels={SOURCE_LABEL} />
            <div className="xl:col-span-2"><ChipGroup label="Country" options={opts.countries} value={filt.countries} onChange={(v) => set({ countries: v })} /></div>
            <ChipGroup label="Role" options={opts.roles} value={filt.roles} onChange={(v) => set({ roles: v })} empty="Roles are set for jobs found by searches" />
            {opts.statuses.length > 1 && <ChipGroup label="Status" options={opts.statuses} value={filt.statuses} onChange={(v) => set({ statuses: v })} labels={STATUS_LABEL} />}
            <div>
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Score</div>
              <div className="flex flex-wrap items-center gap-1.5">
                <input aria-label="Minimum score" type="number" min="0" max="100" className="input w-16 py-1" placeholder="0" value={filt.minScore} onChange={(e) => set({ minScore: e.target.value })} />
                <span className="text-slate-400">to</span>
                <input aria-label="Maximum score" type="number" min="0" max="100" className="input w-16 py-1" placeholder="100" value={filt.maxScore} onChange={(e) => set({ maxScore: e.target.value })} />
                <div className="flex gap-1">{[40, 55, 65, 75].map((n) => (
                  <button key={n} onClick={() => set({ minScore: filt.minScore === String(n) ? '' : String(n), maxScore: '' })}
                    className={`rounded-full px-2 py-0.5 text-xs ring-1 ${filt.minScore === String(n) ? 'bg-teal text-white ring-teal' : 'text-slate-600 ring-slate-200 hover:bg-slate-50'}`}>{n}+</button>))}</div>
              </div>
            </div>
            <div>
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Location contains</div>
              <input className="input py-1" placeholder="e.g. Berlin, Munich, Remote" value={filt.loc} onChange={(e) => set({ loc: e.target.value })} />
            </div>
            <Segmented label="Found within" options={WITHIN} value={filt.found} onChange={(v) => set({ found: v })} />
            <Segmented label="Posted within" options={WITHIN} value={filt.posted} onChange={(v) => set({ posted: v })} />
            <Segmented label="German" options={[{ id: '', label: 'Any' }, { id: 'hide', label: 'Not required' }, { id: 'only', label: 'Required' }]} value={filt.german} onChange={(v) => set({ german: v })} />
            <Segmented label="CV" options={[{ id: '', label: 'Any' }, { id: 'yes', label: 'Built' }, { id: 'no', label: 'Not built' }]} value={filt.cv} onChange={(v) => set({ cv: v })} />
            <div className="flex flex-col justify-end gap-2">
              <Toggle checked={filt.hideHard} onChange={(v) => set({ hideHard: v })}>Hide hard deal-breakers</Toggle>
              <Toggle checked={filt.hasDesc} onChange={(v) => set({ hasDesc: v })}>Only jobs with a full description</Toggle>
            </div>
          </div>
        </div>)}

      {/* active filters */}
      {active.length > 0 && (
        <div className="-mt-1 flex flex-wrap items-center gap-1.5">
          {active.map((a) => (
            <span key={a.k} className="inline-flex items-center gap-1 rounded-full bg-teal-50 py-0.5 pl-2.5 pr-1 text-xs font-medium text-teal-600 ring-1 ring-teal-100">
              {a.label}<button title="Remove this filter" className="rounded-full p-0.5 hover:bg-teal-100" onClick={a.clear}><X size={12} /></button></span>))}
          <button className="text-xs font-medium text-slate-500 hover:text-rose-600" onClick={() => setFilt({ ...EMPTY, q: filt.q })}>Clear all</button>
        </div>)}

      {/* selection bar */}
      <div className={`flex items-center gap-2 rounded-xl px-4 py-2 text-sm transition ${selected.length ? 'bg-ink text-white shadow-lg' : 'bg-transparent text-slate-500'}`}>
        {selected.length ? <>
          <span className="font-semibold">{selected.length} selected</span>
          <button className="ml-1 text-xs text-white/70 underline-offset-2 hover:underline" onClick={clearSel}>Clear</button>
          <div className="ml-auto flex items-center gap-2">
            <select aria-label="Set status of selected jobs" className="rounded-md border-0 bg-white/10 py-1.5 pl-2 pr-7 text-sm text-white focus:ring-2 focus:ring-white/30" value=""
              onChange={(e) => setStatusSelected(e.target.value)}>
              <option value="" className="text-slate-800">Set status…</option>
              {['scored', 'built', 'applied', 'interview', 'offer', 'rejected', 'excluded'].map((s) => <option key={s} value={s} className="text-slate-800">{STATUS_LABEL[s]}</option>)}
            </select>
            <button className="btn border border-white/20 bg-white/5 text-white hover:bg-rose-500/80" onClick={() => setToDelete(selected)}><Trash2 size={14} />Delete ({selected.length})</button>
            <button className="btn bg-teal text-white hover:bg-teal-600" onClick={buildSelected}><Hammer size={14} />Build CV{selected.length > 1 ? 's' : ''} ({selected.length})</button>
          </div>
        </> : <>
          <span><b className="tabular-nums text-slate-700">{rows.length}</b> of {all.length} jobs{nFilters || filt.q ? ' match' : ''}</span>
          <span className="ml-auto text-xs text-slate-400">Tick jobs to build CVs, change status or delete</span>
        </>}
      </div>

      {/* grid + detail */}
      <div className="flex min-h-0 flex-1 gap-3">
        <div className="jb ag-theme-quartz min-w-0 flex-1 overflow-hidden glass rounded-2xl">
          <AgGridReact ref={grid} rowData={raw ? rows : null} columnDefs={cols} rowSelection="multiple" suppressRowClickSelection
            getRowId={(p) => String(p.data.id)} onSelectionChanged={(e) => setSelected(e.api.getSelectedRows())}
            onRowClicked={(e) => nav(`/jobs/${e.data.id}`)} rowHeight={compact ? 42 : 62} headerHeight={40} animateRows={false}
            rowClassRules={{ 'jb-open': (p) => p.data && p.data.id === +id }} maintainColumnOrder suppressCellFocus
            onSortChanged={onSortChanged} onColumnResized={onResized} tooltipShowDelay={300}
            overlayNoRowsTemplate={all.length ? '<span class="text-sm text-slate-500">No jobs match these filters.</span>' : '<span class="text-sm text-slate-500">No jobs on this tab yet.</span>'}
            defaultColDef={{ sortable: true, resizable: true, filter: false, suppressHeaderMenuButton: true }} />
        </div>
        {id && <JobDetail id={+id} onClose={() => nav('/jobs')} onChanged={load} onDelete={(j) => setToDelete([j])} />}
      </div>
      {toDelete && <DeleteModal jobs={toDelete} onClose={() => setToDelete(null)} onDone={(r) => {
        toast(`Deleted ${r.deleted} job${r.deleted === 1 ? '' : 's'}${r.files_deleted ? ` and ${r.files_deleted} file(s)` : ''}`)
        setToDelete(null); clearSel()
        if (id && toDelete.some((j) => j.id === +id)) nav('/jobs')
        load()
      }} />}
    </div>
  )
}

export function DeleteModal({ jobs, onClose, onDone }) {
  const [files, setFiles] = useState(false)
  const [forget, setForget] = useState(false)
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const go = async () => {
    setBusy(true)
    try { onDone(await api.post('/api/jobs/delete', { job_ids: jobs.map((j) => j.id), delete_files: files, forget })) }
    catch (e) { toast(e.message, 'error'); setBusy(false) }
  }
  return (
    <Modal open title={`Delete ${jobs.length} job${jobs.length === 1 ? '' : 's'}?`} onClose={onClose}
      footer={<><button className="btn-outline" onClick={onClose}>Cancel</button>
        <button className="btn bg-rose-600 text-white hover:bg-rose-700" disabled={busy} onClick={go}><Trash2 size={14} />{busy ? 'Deleting…' : 'Delete'}</button></>}>
      <div className="space-y-3 text-sm">
        <div className="max-h-40 overflow-y-auto rounded-md bg-slate-50 p-2">
          {jobs.slice(0, 50).map((j) => <div key={j.id}>{j.title} <span className="muted">· {j.company}</span></div>)}
          {jobs.length > 50 && <div className="muted">… and {jobs.length - 50} more</div>}
        </div>
        <p className="muted">Removes the jobs with their scores, notes and tracker entries.</p>
        <label className="flex items-start gap-2"><input type="checkbox" className="mt-0.5" checked={files} onChange={(e) => setFiles(e.target.checked)} />
          <span>Also delete their built CV and cover letter files (.docx and .pdf) from your Claude CV folder</span></label>
        <label className="flex items-start gap-2"><input type="checkbox" className="mt-0.5" checked={forget} onChange={(e) => setForget(e.target.checked)} />
          <span>Allow these jobs to be found again by future searches<br /><span className="muted text-xs">Off (default): deleted jobs never come back.</span></span></label>
      </div>
    </Modal>
  )
}

export function JobDetail({ id, onClose, onChanged, onDelete }) {
  const [j, setJ] = useState(null)
  const toast = useToast()
  const nav = useNavigate()
  const load = useCallback(() => api.get(`/api/jobs/${id}`).then(setJ), [id])
  useEffect(() => { setJ(null); load() }, [load])
  if (!j) return <div className="card w-[480px] p-4 muted">Loading…</div>
  const b = j.breakdown
  const patch = async (body) => { await api.patch(`/api/jobs/${id}`, body); await load(); onChanged && onChanged() }
  const build = async (rebuild = false) => {
    try {
      const r = await api.post('/api/build', { job_ids: [id], rebuild })
      toast((r.already_building || []).length ? 'Already being built – not started twice' : 'Building CV and cover letter'); nav('/build')
    }
    catch (e) { toast(e.message, 'error') }
  }
  const bars = b && [['Skills & tools', b.skills, 40], ['Experience & projects', b.experience, 25], ['Title fit', b.title_fit, 10],
    ['Education', b.education, 10], ['Language', b.language, 10], ['Type & location', b.type_location, 5]]
  return (
    <div className="card flex w-[500px] shrink-0 flex-col overflow-hidden">
      <div className="flex items-start gap-2 border-b border-slate-100 p-4">
        <div className="flex-1">
          <div className="text-base font-bold text-navy">{j.title}</div>
          <div className="text-sm muted">{j.company} · {j.location} {j.work_mode && `· ${j.work_mode}`}</div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5"><VerdictBadge verdict={j.verdict} /><StatusBadge status={j.status} />
            {j.job_type && <span className="chip bg-slate-100">{j.job_type}</span>}
            <span className="chip bg-slate-100">Posted {j.posted_date ? fmtDate(j.posted_date) : 'date unknown'}</span>
            {j.first_seen && <span className="chip bg-slate-100" title="When this app first found the job">Found {fmtDateTime(j.first_seen)}</span>}</div>
        </div>
        <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-4 text-sm">
        {j.status === 'needs_review' && (
          <div className="rounded-md bg-amber-50 p-3">
            <div className="mb-2 font-semibold text-amber-900">Job type unclear – what is it?</div>
            <div className="flex flex-wrap gap-1.5">{['Working Student', 'Internship', 'Part-time'].map((t) => <button key={t} className="btn-outline" onClick={() => patch({ job_type: t })}>{t}</button>)}
              <button className="btn-danger" onClick={() => patch({ status: 'excluded' })}>Full-time / not relevant</button></div>
          </div>
        )}
        {j.status === 'excluded' && <div className="rounded-md bg-slate-100 p-3">Excluded: <b>{j.exclude_reason}</b>
          <button className="btn-outline ml-2" onClick={() => patch({ status: 'new' })}>Include anyway</button></div>}
        {b && (
          <div>
            <div className="mb-2 flex items-baseline justify-between"><span className="label">Match score</span><span className="text-2xl font-bold text-navy">{j.score}<span className="text-sm muted">/100</span></span></div>
            <div className="space-y-1.5">{bars.map(([l, v, mx]) => (
              <div key={l} className="flex items-center gap-2"><span className="w-40 text-xs">{l}</span>
                <div className="h-1.5 flex-1 rounded bg-slate-200"><div className="h-1.5 rounded bg-teal" style={{ width: `${(v / mx) * 100}%` }} /></div>
                <span className="w-12 text-right text-xs tabular-nums">{v}/{mx}</span></div>))}</div>
            {j.rationale && <p className="mt-2 text-xs muted">{j.rationale}</p>}
            {j.score_outdated && <p className="mt-1 text-xs text-amber-700">Scored against an older CV version – re-score on the Runs screen.</p>}
          </div>
        )}
        {j.deal_breakers?.length > 0 && (
          <div><span className="label">Deal-breakers</span>
            <ul className="space-y-1">{j.deal_breakers.map((d, i) => <li key={i} className={d.hard ? 'text-rose-700' : 'text-amber-800'}>{d.hard ? '⛔' : '⚠️'} {d.text}</li>)}</ul></div>
        )}
        {j.missing_wording?.length > 0 && (
          <div><span className="label">Missing wording (you have it – use their word)</span>
            <div className="flex flex-wrap gap-1">{j.missing_wording.map((w) => <span key={w} className="chip bg-teal-50 text-teal-600">{w}</span>)}</div></div>
        )}
        {j.missing_skills?.length > 0 && (
          <div><span className="label">Missing skills (not on your CV)</span>
            <div className="flex flex-wrap gap-1">{j.missing_skills.map((w) => <span key={w} className="chip bg-rose-50 text-rose-700">{w}</span>)}</div></div>
        )}
        {j.keywords?.length > 0 && <div><span className="label">Keywords</span><div className="text-xs">{j.keywords.join(' · ')}</div></div>}
        {j.language_req && <div><span className="label">Languages</span>{j.language_req}</div>}
        {j.salary && <div><span className="label">Salary</span>{j.salary}</div>}
        <div><span className="label">Full description</span>
          {j.full_description ? <div className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs leading-relaxed">{j.full_description}</div>
            : <div className="text-amber-700">{j.notes || 'No description was retrieved.'}</div>}</div>
        {j.documents?.length > 0 && (
          <div><span className="label">Documents</span>
            {j.documents.map((d) => <div key={d.id} className="flex items-center justify-between"><span>{d.kind === 'cv' ? 'CV' : 'Cover letter'} {d.imported ? <span className="muted">(earlier build)</span> : d.qa_passed ? '✓' : '⚠️'}{d.outdated && <span className="text-amber-700"> (older CV version)</span>}</span>
              <a className="text-teal" href={`/api/files?path=${encodeURIComponent(d.pdf_path)}`} target="_blank" rel="noreferrer">Open PDF</a></div>)}</div>
        )}
      </div>
      <div className="flex flex-wrap gap-2 border-t border-slate-100 p-3">
        {j.links.map((l, i) => <a key={l} href={l} target="_blank" rel="noreferrer" className="btn-outline"><ExternalLink size={14} />Posting{j.links.length > 1 ? ` ${i + 1}` : ''}</a>)}
        {onDelete && <button className="btn-danger" title="Delete this job" onClick={() => onDelete(j)}><Trash2 size={14} /></button>}
        <div className="ml-auto flex gap-2">
          {j.documents?.length ? <button className="btn-outline" onClick={() => build(true)}>Rebuild</button>
            : <button className="btn-teal" disabled={!j.score} onClick={() => build(false)}><Hammer size={14} />Build CV</button>}
          {['built', 'scored'].includes(j.status) && <button className="btn-primary" onClick={() => patch({ status: 'applied' })}>Mark applied</button>}
        </div>
      </div>
    </div>
  )
}
