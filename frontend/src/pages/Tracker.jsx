import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowDown, ArrowUp, CalendarClock, ChevronDown, Download, ExternalLink, FileText, Mail, RefreshCw, Search, StickyNote } from 'lucide-react'
import { api, fileUrl, fmtDate, fmtDateTime } from '../api'
import { Empty, useAsync, useToast } from '../ui'

const STATUSES = ['built', 'applied', 'interview', 'offer', 'rejected']
const LABEL = { built: 'CV built', applied: 'Applied', interview: 'Interview', offer: 'Offer', rejected: 'Rejected' }
const STATUS_STYLE = {
  built: 'bg-slate-100 text-slate-700 ring-slate-200',
  applied: 'bg-blue-50 text-blue-700 ring-blue-200',
  interview: 'bg-violet-50 text-violet-700 ring-violet-200',
  offer: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  rejected: 'bg-rose-50 text-rose-600 ring-rose-200',
}
const DOT = { built: 'bg-slate-400', applied: 'bg-blue-500', interview: 'bg-violet-500', offer: 'bg-emerald-500', rejected: 'bg-rose-400' }

const SORTS = {
  score: { label: 'Score', get: (j) => j.score ?? -1, dir: 'desc' },
  company: { label: 'Company', get: (j) => (j.company || '').toLowerCase(), dir: 'asc' },
  title: { label: 'Job title', get: (j) => (j.title || '').toLowerCase(), dir: 'asc' },
  built: { label: 'Date built', get: (j) => j.docs?.cv?.built_at || '', dir: 'desc' },
  found: { label: 'Date found', get: (j) => j.first_seen || '', dir: 'desc' },
  applied: { label: 'Date applied', get: (j) => j.application?.applied_at || '', dir: 'desc' },
  follow: { label: 'Follow-up date', get: (j) => j.application?.follow_up_at || '9999', dir: 'asc' },
  status: { label: 'Status', get: (j) => STATUSES.indexOf(j.status), dir: 'asc' },
}

function dirLabel({ key, dir }) {
  if (['company', 'title'].includes(key)) return dir === 'asc' ? 'A → Z' : 'Z → A'
  if (key === 'score') return dir === 'desc' ? 'Highest first' : 'Lowest first'
  if (key === 'status') return dir === 'asc' ? 'CV built → Rejected' : 'Rejected → CV built'
  return dir === 'desc' ? 'Newest first' : 'Oldest first'
}

const GRID = 'grid grid-cols-[52px_minmax(0,1fr)_150px_128px_150px_24px] items-center gap-4'

function ScorePill({ score }) {
  if (score === null || score === undefined) return <span className="text-slate-300">–</span>
  const c = score >= 65 ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : score >= 40 ? 'bg-amber-50 text-amber-700 ring-amber-200' : 'bg-slate-100 text-slate-500 ring-slate-200'
  return <span className={`inline-flex h-9 w-11 items-center justify-center rounded-lg text-sm font-bold tabular-nums ring-1 ${c}`}>{score}</span>
}

function DocLink({ d, icon: Icon, label }) {
  if (!d || (!d.pdf_path && !d.docx_path)) {
    return <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-rose-500" title={`No ${label.toLowerCase()} in your Claude CV folder`}><Icon size={13} />{label} missing</span>
  }
  const href = d.pdf_path ? fileUrl(d.pdf_path) : fileUrl(d.docx_path, true)
  return (
    <a href={href} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
      title={`Open ${label.toLowerCase()} (${d.pdf_path ? 'PDF' : 'Word'})${d.built_at ? ' · built ' + fmtDateTime(d.built_at) : ''}`}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-teal-600 hover:bg-teal-50">
      <Icon size={13} />{label}
    </a>
  )
}

function SortButton({ id, sort, setSort, children, className = '' }) {
  const on = sort.key === id
  return (
    <button className={`inline-flex items-center gap-1 text-left ${on ? 'text-navy' : 'hover:text-slate-700'} ${className}`}
      onClick={() => setSort(on ? { key: id, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key: id, dir: SORTS[id].dir })}>
      {children}{on && (sort.dir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
    </button>
  )
}

function DateLine({ a, due, status }) {
  if (status === 'rejected' || status === 'offer') {
    return a.applied_at ? <span className="text-xs text-slate-400">Applied {fmtDate(a.applied_at)}</span> : <span className="text-xs text-slate-300">—</span>
  }
  if (a.follow_up_at) {
    return <span className={`inline-flex items-center gap-1 text-xs ${due ? 'font-semibold text-rose-600' : 'text-slate-500'}`}>
      <CalendarClock size={13} />{due ? 'Follow up now' : `Follow up ${fmtDate(a.follow_up_at)}`}</span>
  }
  if (a.applied_at) return <span className="text-xs text-slate-500">Applied {fmtDate(a.applied_at)}</span>
  return <span className="text-xs text-slate-300">Not applied yet</span>
}

export default function Tracker() {
  const { data, reload, setData } = useAsync(() => api.get('/api/applications'))
  const toast = useToast()
  const [q, setQ] = useState('')
  const [statusF, setStatusF] = useState('')
  const [sort, setSort] = useState({ key: 'score', dir: 'desc' })
  const [open, setOpen] = useState(null)

  const rows = useMemo(() => {
    if (!data) return []
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const f = data.filter((j) => {
      if (statusF && j.status !== statusF) return false
      const hay = `${j.company} ${j.title} ${j.location || ''} ${j.application?.notes || ''}`.toLowerCase()
      return words.every((w) => hay.includes(w))
    })
    const { get } = SORTS[sort.key]
    const mul = sort.dir === 'asc' ? 1 : -1
    return [...f].sort((a, b) => { const x = get(a), y = get(b); return (x > y ? 1 : x < y ? -1 : 0) * mul })
  }, [data, q, statusF, sort])

  // optimistic update: change the row at once, then save
  const save = async (id, body) => {
    setData(data.map((j) => j.id !== id ? j : {
      ...j, status: body.status || j.status,
      application: { ...(j.application || {}), ...Object.fromEntries(Object.entries(body).filter(([k]) => k !== 'status')) },
    }))
    try { await api.put(`/api/applications/${id}`, body); if (body.status) reload() } catch (e) { toast(e.message, 'error'); reload() }
  }
  const today = new Date().toISOString().slice(0, 10)
  const plusDays = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10)

  if (!data) return <div className="muted">Loading…</div>
  const counts = Object.fromEntries(STATUSES.map((s) => [s, data.filter((d) => d.status === s).length]))
  const due = data.filter((d) => d.follow_up_due).length

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="h1">Application tracker</h1>
          <p className="mt-1 text-sm muted">CVs and letters are checked against your Claude CV folder each time this page opens.
            {due > 0 && <span className="ml-1 font-semibold text-rose-600">{due} follow-up{due > 1 ? 's' : ''} due.</span>}</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-outline" title="Check the Claude CV folder again" onClick={reload}><RefreshCw size={14} />Re-check files</button>
          <a className="btn-outline" href="/api/export/jobs.xlsx"><Download size={14} />Export</a>
        </div>
      </div>

      {/* status pipeline */}
      <div className="grid grid-cols-5 gap-3">
        {STATUSES.map((s) => (
          <button key={s} onClick={() => setStatusF(statusF === s ? '' : s)} title={statusF === s ? 'Show all' : `Show only “${LABEL[s]}”`}
            className={`group rounded-2xl border bg-surface px-4 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow ${statusF === s ? 'border-teal ring-2 ring-teal/20' : 'border-slate-200'}`}>
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500"><span className={`h-2 w-2 rounded-full ${DOT[s]}`} />{LABEL[s]}</div>
            <div className="mt-1 text-2xl font-bold tabular-nums text-navy">{counts[s]}</div>
          </button>))}
      </div>

      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[260px] flex-1">
          <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
          <input className="input rounded-lg py-2 pl-9" placeholder="Search company or job title…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="input w-40 rounded-lg py-2" value={statusF} onChange={(e) => setStatusF(e.target.value)}>
          <option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{LABEL[s]}</option>)}</select>
        <div className="flex items-center overflow-hidden rounded-full border border-slate-200 bg-slate-50 p-0.5">
          <span className="pl-3 text-xs text-slate-500">Sort</span>
          <select className="border-0 bg-transparent py-2 pl-2 pr-1 text-sm focus:outline-none" value={sort.key}
            onChange={(e) => setSort({ key: e.target.value, dir: SORTS[e.target.value].dir })}>
            {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
          <button className="flex items-center gap-1 border-l border-slate-200 px-3 py-2 text-sm hover:bg-slate-50" title="Reverse order"
            onClick={() => setSort({ ...sort, dir: sort.dir === 'asc' ? 'desc' : 'asc' })}>
            {sort.dir === 'asc' ? <ArrowUp size={14} /> : <ArrowDown size={14} />}{dirLabel(sort)}</button>
        </div>
        <span className="ml-auto text-sm muted">{rows.length} of {data.length} jobs</span>
      </div>

      {/* list */}
      <div className="overflow-hidden glass rounded-2xl">
        <div className={`${GRID} border-b border-slate-100 bg-slate-50/70 px-5 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500`}>
          <SortButton id="score" sort={sort} setSort={setSort}>Score</SortButton>
          <span className="flex gap-4"><SortButton id="title" sort={sort} setSort={setSort}>Job</SortButton>
            <SortButton id="company" sort={sort} setSort={setSort}>Company</SortButton></span>
          <SortButton id="built" sort={sort} setSort={setSort}>Documents</SortButton>
          <SortButton id="status" sort={sort} setSort={setSort}>Status</SortButton>
          <SortButton id="follow" sort={sort} setSort={setSort}>Next step</SortButton>
          <span />
        </div>
        {!data.length ? <Empty title="Nothing to track yet">Jobs appear here once a CV is built.</Empty>
          : !rows.length ? <Empty title="No matches">Try another search or status.</Empty> : rows.map((j) => {
            const a = j.application || {}
            const isOpen = open === j.id
            return (
              <Fragment key={j.id}>
                <div onClick={() => setOpen(isOpen ? null : j.id)}
                  className={`${GRID} cursor-pointer border-b border-slate-100 px-5 py-3.5 transition last:border-0 ${isOpen ? 'bg-teal-50/40' : 'hover:bg-slate-50'} ${j.follow_up_due ? 'border-l-4 border-l-rose-400 pl-4' : ''}`}>
                  <ScorePill score={j.score} />
                  <div className="min-w-0">
                    <div className="truncate font-semibold text-slate-800" title={j.title}>{j.title}</div>
                    <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-slate-500">
                      <span className="truncate">{j.company}</span>
                      {j.docs?.cv?.built_at && <><span className="text-slate-300">•</span><span className="shrink-0">built {fmtDate(j.docs.cv.built_at)}</span></>}
                      {a.notes && <><span className="text-slate-300">•</span><span className="inline-flex min-w-0 items-center gap-1 truncate"><StickyNote size={11} className="shrink-0" />{a.notes}</span></>}
                    </div>
                  </div>
                  <div className="flex items-center gap-0.5">
                    <DocLink d={j.docs?.cv} icon={FileText} label="CV" />
                    <DocLink d={j.docs?.letter} icon={Mail} label="Letter" />
                  </div>
                  <div className="relative" onClick={(e) => e.stopPropagation()} title="Change status">
                    <select value={j.status} onChange={(e) => save(j.id, { status: e.target.value })}
                      className={`w-full cursor-pointer appearance-none rounded-full border-0 py-1.5 pl-3 pr-7 text-xs font-semibold ring-1 focus:outline-none focus:ring-2 focus:ring-teal/40 ${STATUS_STYLE[j.status]}`}>
                      {STATUSES.map((s) => <option key={s} value={s}>{LABEL[s]}</option>)}</select>
                    <ChevronDown size={13} className="pointer-events-none absolute right-2.5 top-2 opacity-60" />
                  </div>
                  <DateLine a={a} due={j.follow_up_due} status={j.status} />
                  <ChevronDown size={16} className={`text-slate-400 transition ${isOpen ? 'rotate-180' : ''}`} />
                </div>
                {isOpen && (
                  <div className="grid gap-5 border-b border-slate-100 bg-slate-50/60 px-5 py-4 md:grid-cols-[1fr_1fr_2fr]">
                    <div className="space-y-3">
                      <label className="block"><span className="label">Applied on</span>
                        <input type="date" className="input" value={a.applied_at || ''} onChange={(e) => save(j.id, { applied_at: e.target.value })} /></label>
                      <button className="btn-ghost px-0 text-xs text-teal" onClick={() => save(j.id, { status: 'applied', applied_at: today, follow_up_at: a.follow_up_at || plusDays(12) })}>
                        Mark applied today</button>
                    </div>
                    <div className="space-y-3">
                      <label className="block"><span className="label">Follow up on</span>
                        <input type="date" className="input" value={a.follow_up_at || ''} onChange={(e) => save(j.id, { follow_up_at: e.target.value })} /></label>
                      <div className="flex gap-3 text-xs">
                        <button className="text-teal hover:underline" onClick={() => save(j.id, { follow_up_at: plusDays(7) })}>in 1 week</button>
                        <button className="text-teal hover:underline" onClick={() => save(j.id, { follow_up_at: plusDays(14) })}>in 2 weeks</button>
                      </div>
                    </div>
                    <div className="flex flex-col">
                      <label className="block flex-1"><span className="label">Notes</span>
                        <textarea className="input h-20 resize-none" defaultValue={a.notes || ''} placeholder="Contact person, interview date, next step…"
                          onBlur={(e) => e.target.value !== (a.notes || '') && save(j.id, { notes: e.target.value })} /></label>
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                        <Link to={`/jobs/${j.id}`} className="font-medium text-teal hover:underline">Open job details</Link>
                        {j.link && <a href={j.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-teal hover:underline"><ExternalLink size={12} />Job posting</a>}
                        {j.docs?.cv?.docx_path && <a href={fileUrl(j.docs.cv.docx_path, true)} className="text-slate-500 hover:underline">CV as Word</a>}
                        {j.docs?.letter?.docx_path && <a href={fileUrl(j.docs.letter.docx_path, true)} className="text-slate-500 hover:underline">Letter as Word</a>}
                      </div>
                    </div>
                  </div>)}
              </Fragment>)
          })}
      </div>
    </div>
  )
}
