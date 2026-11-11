import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Bot, CalendarClock, CheckCircle2, Clock, FileCheck2, Hammer, IdCard, Inbox, ListChecks,
  Mail, Play, RefreshCw, Search, Send, Sparkles, Target, TrendingUp, XCircle,
} from 'lucide-react'
import { api, fmtDate, fmtDateTime, fmtTime, SOURCE_LABEL } from '../api'
import { Empty, StatusBadge, useAsync, useToast, VerdictBadge } from '../ui'
import { useOnEvent } from '../events'

const hello = () => { const h = new Date().getHours(); return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening' }
const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0)
const ago = (iso) => {
  if (!iso) return ''
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`
}

// ───────────────────────── small pieces ─────────────────────────
function Spark({ values, color = 'rgb(var(--c-teal-500))' }) {
  const max = Math.max(1, ...values)
  const w = 98, h = 30, bw = w / values.length
  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden>
      {values.map((v, i) => {
        const bh = v ? Math.max(2, (v / max) * h) : 0
        return <rect key={i} x={i * bw + 1} y={h - bh} width={bw - 2} height={bh} rx={1.5} style={{ fill: color }} opacity={i === values.length - 1 ? 1 : 0.55} />
      })}
    </svg>
  )
}

function Ring({ value, max, size = 54 }) {
  const r = (size - 8) / 2, c = 2 * Math.PI * r, p = Math.min(1, max ? value / max : 0)
  return (
    <svg width={size} height={size} className="-rotate-90" aria-hidden>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" style={{ stroke: 'rgb(var(--c-slate-200) / var(--a-slate-200))' }} strokeWidth={6} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" style={{ stroke: p >= 1 ? 'rgb(var(--c-emerald-500))' : 'rgb(var(--c-teal-500))' }} strokeWidth={6}
        strokeDasharray={`${c * p} ${c}`} strokeLinecap="round" />
    </svg>
  )
}

function Kpi({ icon: Icon, label, value, sub, to, tone = 'navy', children }) {
  const tones = { navy: 'bg-navy-50 text-navy', teal: 'bg-teal-50 text-teal-600', green: 'bg-emerald-50 text-emerald-700',
    amber: 'bg-amber-50 text-amber-700', violet: 'bg-violet-50 text-violet-700' }
  const body = (
    <div className="card group flex h-full flex-col gap-3 p-4 transition hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <div className={`rounded-lg p-2 ${tones[tone]}`}><Icon size={16} /></div>
        {children}
      </div>
      <div className="mt-auto">
        <div className="text-[28px] font-bold leading-none tabular-nums text-navy">{value}</div>
        <div className="mt-1.5 text-sm font-semibold text-slate-700">{label}</div>
        {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
      </div>
    </div>
  )
  return to ? <Link to={to} className="block h-full">{body}</Link> : body
}

function Card({ title, icon: Icon, action, children, className = '' }) {
  return (
    <div className={`card overflow-hidden ${className}`}>
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <div className="flex items-center gap-2 font-semibold text-navy">{Icon && <Icon size={16} className="text-teal-600" />}{title}</div>
        {action}
      </div>
      {children}
    </div>
  )
}

function ScoreDot({ score }) {
  const cls = score >= 75 ? 'bg-emerald-600 text-white' : score >= 60 ? 'bg-emerald-100 text-emerald-800' : score >= 40 ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-500'
  return <span className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold tabular-nums ${cls}`}>{score ?? '–'}</span>
}

// 14-day bar chart (one series, hover tooltip)
function DayBars({ title, data, k, color, unit }) {
  const [hover, setHover] = useState(null)
  const max = Math.max(1, ...data.map((d) => d[k]))
  const total = data.reduce((a, d) => a + d[k], 0)
  const H = 120
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <div className="text-sm font-semibold text-slate-700">{title}</div>
        <div className="text-xs text-slate-500"><b className="text-base tabular-nums text-navy">{total}</b> in 14 days</div>
      </div>
      <div className="relative mt-3" onMouseLeave={() => setHover(null)}>
        <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-slate-200" />
        <div className="pointer-events-none absolute -top-2 right-0 rounded bg-surface2 px-1 text-[10px] tabular-nums text-slate-400">{max}</div>
        <div className="flex items-end gap-[3px]" style={{ height: H }}>
          {data.map((d, i) => {
            const v = d[k], h = v ? Math.max(3, (v / max) * H) : 0
            return (
              <div key={d.date} className="relative flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}>
                <div className="w-full rounded-t-[4px] transition-opacity" style={{ height: h, background: `linear-gradient(180deg, ${color} 0%, ${color} 35%, transparent 160%)`, opacity: hover === null || hover === i ? 1 : 0.45, boxShadow: hover === i ? `0 0 14px ${color}` : 'none' }} />
                {hover === i && (
                  <div className="absolute bottom-full left-1/2 z-10 mb-1 -translate-x-1/2 whitespace-nowrap rounded-lg bg-ink px-2 py-1 text-xs text-white shadow-lg">
                    <b className="tabular-nums">{v}</b> {unit} · {new Date(d.date).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}
                  </div>)}
              </div>)
          })}
        </div>
        <div className="mt-1.5 flex justify-between border-t border-slate-200 pt-1 text-[10px] text-slate-400">
          <span>{fmtDate(data[0]?.date)}</span><span>today</span>
        </div>
      </div>
    </div>
  )
}

function Funnel({ steps }) {
  const max = Math.max(1, steps[0]?.count || 1)
  return (
    <div className="space-y-2.5 p-5">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].count : null
        return (
          <div key={s.key}>
            <div className="mb-1 flex items-baseline justify-between text-sm">
              <span className="font-medium text-slate-700">{s.label}</span>
              <span className="tabular-nums"><b className="text-navy">{s.count.toLocaleString()}</b>
                {prev !== null && <span className="ml-1.5 text-xs text-slate-400">{pct(s.count, prev)}%</span>}</span>
            </div>
            <div className="h-2.5 rounded-full bg-slate-100">
              <div className="h-2.5 rounded-full" style={{ width: `${Math.max(s.count ? 1.5 : 0, (100 * s.count) / max)}%`, background: i < 3 ? 'rgb(var(--c-teal-500))' : 'rgb(var(--c-slate-600))', opacity: 1 - i * 0.06 }} />
            </div>
          </div>)
      })}
    </div>
  )
}

function HBars({ rows, labels = {} }) {
  const max = Math.max(1, ...rows.map((r) => r[1]))
  return (
    <div className="space-y-2">
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[110px_1fr_36px] items-center gap-2 text-sm" title={`${labels[k] || k}: ${v}`}>
          <span className="truncate text-slate-600">{labels[k] || k}</span>
          <div className="h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-teal" style={{ width: `${(100 * v) / max}%` }} /></div>
          <span className="text-right tabular-nums text-slate-700">{v}</span>
        </div>))}
    </div>
  )
}

const MAIL = {
  interview: ['Interview', 'bg-violet-100 text-violet-800', CalendarClock], assessment: ['Test', 'bg-fuchsia-100 text-fuchsia-800', CalendarClock],
  offer: ['Offer', 'bg-emerald-100 text-emerald-800', Sparkles], rejection: ['Rejection', 'bg-rose-100 text-rose-700', XCircle],
  confirmation: ['Received', 'bg-blue-100 text-blue-800', CheckCircle2], update: ['Update', 'bg-sky-100 text-sky-800', Mail],
}

// ───────────────────────── page ─────────────────────────
export default function Dashboard() {
  const { data, reload } = useAsync(() => api.get('/api/dashboard'))
  const toast = useToast()
  const nav = useNavigate()
  useOnEvent((ev) => { if (['run_done', 'run_failed'].includes(ev.type) || (ev.type === 'mail' && ev.result) || (ev.type === 'apply' && ev.result)) reload() }, [])

  const runDefault = async () => {
    try {
      const filters = await api.get('/api/filters')
      const fp = filters.find((f) => f.is_default) || filters[0]
      await api.post('/api/runs/search', { filter_profile_id: fp.id })
      toast(`Search started with “${fp.name}”`)
      nav('/search')
    } catch (e) { toast(e.message, 'error') }
  }

  const steps = useMemo(() => {
    if (!data) return []
    const k = data.kpi, out = []
    if (data.inbox.todo_count) out.push({ icon: CalendarClock, tone: 'text-violet-700 bg-violet-50', text: <><b>{data.inbox.todo_count} interview / test invitation{data.inbox.todo_count > 1 ? 's' : ''}</b> to answer</>, to: '/inbox', cta: 'Open Inbox' })
    const left = Math.max(0, k.daily_limit - k.applied_today)
    if (k.ready && !left) out.push({ icon: CheckCircle2, tone: 'text-emerald-700 bg-emerald-50', text: <><b>Daily goal reached</b> ({k.applied_today} applications today) · {k.ready} more are ready for tomorrow</>, to: '/apply', cta: 'Apply page' })
    if (k.ready && left) out.push({ icon: Send, tone: 'text-teal-600 bg-teal-50', text: <>Apply today: <b>{k.applied_today} of {k.daily_limit}</b> done · {k.ready} jobs have their CV + letter ready</>, to: '/apply', cta: 'Start applying' })
    if (k.to_act) out.push({ icon: Hammer, tone: 'text-amber-700 bg-amber-50', text: <><b>{k.to_act} good matches</b> have no CV yet</>, to: '/build', cta: 'Build CVs' })
    if (k.follow_ups_due) out.push({ icon: Clock, tone: 'text-rose-700 bg-rose-50', text: <><b>{k.follow_ups_due} follow-up{k.follow_ups_due > 1 ? 's' : ''}</b> due</>, to: '/tracker', cta: 'Tracker' })
    if (k.needs_review) out.push({ icon: ListChecks, tone: 'text-slate-600 bg-slate-100', text: <><b>{k.needs_review} jobs</b> need a quick look (job type unclear)</>, to: '/jobs', cta: 'Review' })
    if (!data.inbox.connected) out.push({ icon: Mail, tone: 'text-navy bg-navy-50', text: <>Connect Gmail so confirmations, rejections and interviews update your tracker by themselves</>, to: '/inbox', cta: 'Connect' })
    return out
  }, [data])

  if (!data) return <div className="muted">Loading…</div>
  const k = data.kpi
  const act = data.activity
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="flex flex-wrap items-end justify-between gap-4 pt-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" /><span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" /></span>
            {today} · Dashboard
          </div>
          <h1 className="mt-1.5 text-[28px] font-semibold leading-tight tracking-tight text-navy">{hello()}{data.name ? `, ${data.name}` : ''}</h1>
          <p className="mt-1 text-sm text-slate-500">
            <b className="font-semibold text-slate-800">{data.new_week}</b> new jobs this week · <b className="font-semibold text-slate-800">{data.good_week}</b> good matches
            {k.applied_week ? <> · <b className="font-semibold text-slate-800">{k.applied_week}</b> applications sent</> : null}
            {k.interviews ? <> · <b className="font-semibold text-slate-800">{k.interviews}</b> interview{k.interviews > 1 ? 's' : ''}</> : null}
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">
            <span className="glass inline-flex items-center gap-1.5 rounded-full px-2.5 py-1"><Bot size={12} className="text-teal-600" />{data.ai}</span>
            {data.next_search && <span className="glass inline-flex items-center gap-1.5 rounded-full px-2.5 py-1"><Search size={12} className="text-teal-600" />next search {fmtDateTime(data.next_search)}</span>}
            <span className="glass inline-flex items-center gap-1.5 rounded-full px-2.5 py-1"><Mail size={12} className={data.inbox.connected ? 'text-emerald-500' : 'text-slate-400'} />{data.inbox.connected ? `Gmail checked ${ago(data.inbox.last_check)}` : 'Gmail not connected'}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="glass grid h-9 w-9 place-items-center rounded-full text-slate-700 transition hover:text-navy" title="Refresh" onClick={reload}><RefreshCw size={15} /></button>
          <button className="btn-primary h-9 px-4" onClick={runDefault}><Play size={14} />Run search now</button>
        </div>
      </div>

      {!data.has_main_cv && (
        <div className="card flex items-center gap-3 border-amber-200 bg-amber-50 p-4">
          <IdCard className="text-amber-600" />
          <div className="flex-1 text-sm"><b>Upload your main CV first.</b> Jobs can only be scored and CVs built once the app knows your CV.</div>
          <Link to="/cv" className="btn-primary">Go to My CV</Link>
        </div>)}
      {data.paused_runs.map((r) => (
        <div key={r.id} className="card flex items-center gap-3 border-teal-100 bg-teal-50 p-4">
          <div className="flex-1 text-sm"><b>A build is waiting for your review.</b> Tick the jobs you want CVs for.</div>
          <Link to="/build" className="btn-teal">Review now <ArrowRight size={14} /></Link>
        </div>))}

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi icon={TrendingUp} label="New jobs this week" value={data.new_week} sub={`+${data.new_today} today`} to="/jobs" tone="teal">
          <Spark values={act.map((d) => d.found)} />
        </Kpi>
        <Kpi icon={Target} label="Good matches to act on" value={k.to_act} sub="scored Apply / Tailor, no CV yet" to="/build" tone="amber" />
        <Kpi icon={FileCheck2} label="Ready to apply" value={k.ready} sub="CV + cover letter done" to="/apply" tone="green" />
        <Kpi icon={Send} label="Applications sent" value={k.applied} sub={`${k.applied_week} this week`} to="/tracker" tone="navy">
          <Spark values={act.map((d) => d.applied)} color="rgb(var(--c-cyan-500))" />
        </Kpi>
        <Kpi icon={CheckCircle2} label="Today's applications" value={<>{k.applied_today}<span className="text-base font-semibold text-slate-400"> / {k.daily_limit}</span></>}
          sub={k.applied_today >= k.daily_limit ? 'Daily goal reached' : `${k.daily_limit - k.applied_today} to go`} to="/apply" tone="teal">
          <div className="relative"><Ring value={k.applied_today} max={k.daily_limit} />
            <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold text-slate-600">{k.applied_today >= k.daily_limit ? <CheckCircle2 size={18} className="text-emerald-600" /> : `${pct(k.applied_today, k.daily_limit)}%`}</span></div>
        </Kpi>
        <Kpi icon={CalendarClock} label="Interviews" value={k.interviews} sub={`${k.response_rate}% replied · ${k.rejected} rejected${k.offers ? ` · ${k.offers} offer` : ''}`} to="/tracker" tone="violet" />
      </div>

      {/* next steps + funnel */}
      <div className="grid items-start gap-5 lg:grid-cols-3">
        <Card title="What to do next" icon={Sparkles} className="lg:col-span-2">
          {!steps.length ? <Empty title="All caught up">Nothing waiting for you right now.</Empty> : (
            <div className="divide-y divide-slate-100">
              {steps.map((s, i) => (
                <Link key={i} to={s.to} className="flex items-center gap-4 px-5 py-3.5 transition hover:bg-slate-50">
                  <div className={`rounded-lg p-2 ${s.tone}`}><s.icon size={16} /></div>
                  <div className="flex-1 text-sm text-slate-700">{s.text}</div>
                  <span className="inline-flex items-center gap-1 text-sm font-medium text-teal-600">{s.cta}<ArrowRight size={14} /></span>
                </Link>))}
            </div>)}
        </Card>
        <Card title="Your pipeline" icon={TrendingUp} action={<span className="text-xs muted">% = of the step before</span>}>
          <Funnel steps={data.funnel} />
        </Card>
      </div>

      {/* activity */}
      <Card title="Last 14 days" icon={TrendingUp}>
        <div className="grid gap-8 p-5 md:grid-cols-2">
          <DayBars title="New good matches per day" data={act} k="good" color="rgb(var(--c-teal-500))" unit="good matches" />
          <DayBars title="Applications per day" data={act} k="applied" color="rgb(var(--c-cyan-500))" unit="applications" />
        </div>
      </Card>

      {/* matches + inbox */}
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Top matches without a CV yet" icon={Target} className="lg:col-span-2"
          action={<Link to="/jobs" className="text-sm text-teal-600 hover:underline">Open job board →</Link>}>
          {!data.top.length ? <Empty title="No open matches">New good matches appear here after a search or a job-alert mail.</Empty> : (
            <div className="divide-y divide-slate-100">
              {data.top.map((j) => (
                <div key={j.id} className="flex cursor-pointer items-center gap-4 px-5 py-3 transition hover:bg-slate-50" onClick={() => nav(`/jobs/${j.id}`)}>
                  <ScoreDot score={j.score} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-semibold text-slate-800">{j.title}</div>
                    <div className="truncate text-xs text-slate-500">{[j.company, j.location, j.work_mode].filter(Boolean).join(' · ') || '–'}</div>
                    {(j.missing?.length > 0 || j.blockers?.length > 0) && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {j.blockers.map((b, i) => <span key={i} className={`chip text-[11px] ${b.hard ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-800'}`} title={b.text}>{b.hard ? '' : 'check: '}{b.text.length > 60 ? b.text.slice(0, 60) + '…' : b.text}</span>)}
                        {j.missing.map((m) => <span key={m} className="chip bg-slate-100 text-[11px] text-slate-500">missing: {m}</span>)}
                      </div>)}
                  </div>
                  <div className="hidden shrink-0 text-right sm:block">
                    <div className="text-xs text-slate-500">{j.job_type || '–'}</div>
                    <div className="mt-0.5 text-[11px] text-slate-400">{(j.sources || []).slice(0, 2).map((x) => SOURCE_LABEL[x] || x).join(', ')}</div>
                  </div>
                  <VerdictBadge verdict={j.verdict} />
                </div>))}
            </div>)}
        </Card>

        <Card title="Inbox" icon={Inbox} action={<Link to="/inbox" className="text-sm text-teal-600 hover:underline">Open →</Link>}>
          {!data.inbox.connected ? (
            <div className="p-5 text-sm text-slate-600">Connect Gmail and confirmations, rejections and interview invitations update your tracker by themselves.
              <Link to="/inbox" className="btn-teal mt-3 w-full justify-center"><Mail size={14} />Connect Gmail</Link></div>
          ) : (
            <div>
              <div className="grid grid-cols-4 border-b border-slate-100 text-center">
                {[['confirmation', 'Received'], ['rejection', 'Rejected'], ['interview', 'Interview'], ['job_alert', 'Alerts']].map(([kk, l]) => (
                  <div key={kk} className="px-2 py-3"><div className="text-lg font-bold tabular-nums text-navy">{(data.inbox.week[kk] || 0) + (kk === 'interview' ? data.inbox.week.assessment || 0 : 0)}</div>
                    <div className="text-[11px] text-slate-500">{l} · 7 days</div></div>))}
              </div>
              {data.inbox.todo.map((e) => (
                <Link key={e.id} to="/inbox" className="block border-b border-violet-100 bg-violet-50/60 px-5 py-3 hover:bg-violet-50">
                  <div className="flex items-center gap-2 text-sm font-semibold text-violet-900"><CalendarClock size={14} />{e.company}{e.role ? ` – ${e.role}` : ''}</div>
                  <div className="mt-0.5 text-xs text-violet-800">{e.action || e.summary}{e.when ? ` · ${e.when}` : ''}</div>
                </Link>))}
              <div className="divide-y divide-slate-100">
                {data.inbox.recent.filter((e) => !data.inbox.todo.find((t) => t.id === e.id)).slice(0, 5).map((e) => {
                  const [l, cls, Ic] = MAIL[e.kind] || ['Mail', 'bg-slate-100 text-slate-600', Mail]
                  return (
                    <div key={e.id} className="flex items-start gap-3 px-5 py-2.5">
                      <span className={`chip mt-0.5 shrink-0 ${cls}`}><Ic size={11} className="mr-1" />{l}</span>
                      <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium text-slate-700">{e.company}</div>
                        <div className="truncate text-xs text-slate-500">{e.role || e.summary}</div></div>
                      <span className="shrink-0 text-[11px] text-slate-400">{ago(e.at)}</span>
                    </div>)
                })}
                {!data.inbox.recent.length && <div className="px-5 py-4 text-sm muted">No company mails yet.</div>}
              </div>
            </div>)}
        </Card>
      </div>

      {/* ready to apply + sources + last search */}
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Ready to apply" icon={FileCheck2} action={<Link to="/apply" className="text-sm text-teal-600 hover:underline">Apply page →</Link>}>
          {!data.ready_list.length ? <Empty title="Nothing ready">Build CVs for your good matches first.</Empty> : (
            <div className="divide-y divide-slate-100">
              {data.ready_list.map((j) => (
                <div key={j.id} className="flex items-center gap-3 px-5 py-2.5">
                  <ScoreDot score={j.score} />
                  <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold text-slate-800">{j.title}</div>
                    <div className="truncate text-xs text-slate-500">{j.company || '–'}</div></div>
                </div>))}
              <div className="px-5 py-3"><Link to="/apply" className="btn-teal w-full justify-center"><Send size={14} />Apply to today's jobs</Link></div>
            </div>)}
        </Card>

        <Card title="Where your good matches come from" icon={Search}>
          <div className="space-y-5 p-5">
            {data.sources.length ? <HBars rows={data.sources} labels={{ ...SOURCE_LABEL, email: 'Job-alert mails' }} /> : <div className="text-sm muted">No good matches yet.</div>}
            {data.types.length > 0 && <div>
              <div className="label">Job types</div>
              <div className="flex flex-wrap gap-1.5">{data.types.map(([t, n]) => <span key={t} className="chip bg-slate-100 text-slate-700">{t} <b className="ml-1 tabular-nums">{n}</b></span>)}</div>
            </div>}
          </div>
        </Card>

        <Card title="Last search" icon={Clock} action={data.last_run && <Link to="/runs" className="text-sm text-teal-600 hover:underline">Runs →</Link>}>
          {!data.last_run ? <Empty title="No runs yet" /> : (
            <div className="space-y-4 p-5 text-sm">
              <div className="flex items-center justify-between"><StatusBadge status={data.last_run.status} /><span className="muted">{fmtDate(data.last_run.started_at)} {fmtTime(data.last_run.started_at)}</span></div>
              {data.last_run.counts && (
                <div className="grid grid-cols-2 gap-3">
                  {[['Found', data.last_run.counts.found], ['New', data.last_run.counts.new], ['Duplicates', data.last_run.counts.duplicates], ['Full-time excluded', data.last_run.counts.full_time_excluded]].map(([l, v]) => (
                    <div key={l} className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{l}</div>
                      <div className="text-lg font-bold tabular-nums text-navy">{v ?? '–'}</div></div>))}
                </div>)}
              {data.last_run.counts?.by_source && (
                <div className="flex flex-wrap gap-1">{Object.entries(data.last_run.counts.by_source).sort((a, b) => b[1] - a[1]).map(([kk, v]) => <span key={kk} className="chip bg-slate-100">{SOURCE_LABEL[kk] || kk} <b className="ml-1 tabular-nums">{v}</b></span>)}</div>)}
              {data.coverage_warning_count > 0 && (
                <Link to="/runs" className="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 hover:bg-amber-100">
                  <AlertTriangle size={14} className="shrink-0" /><span><b>{data.coverage_warning_count} searches not completed</b> (e.g. {SOURCE_LABEL[data.coverage_warnings[0].site] || data.coverage_warnings[0].site} – {data.coverage_warnings[0].status.replace('_', ' ')}). See coverage →</span>
                </Link>)}
            </div>)}
        </Card>
      </div>
    </div>
  )
}
