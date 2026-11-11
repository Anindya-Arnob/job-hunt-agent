import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarClock, CheckCircle2, ExternalLink, KeyRound, Loader2, Mail, RefreshCw, Unplug } from 'lucide-react'
import { api, fmtDateTime } from '../api'
import { useOnEvent } from '../events'
import { Empty, StatusBadge, Tabs, useAsync, useToast } from '../ui'

const KIND = {
  interview: ['Interview', 'bg-violet-100 text-violet-800'],
  assessment: ['Test', 'bg-fuchsia-100 text-fuchsia-800'],
  offer: ['Offer', 'bg-emerald-100 text-emerald-800'],
  rejection: ['Rejection', 'bg-rose-100 text-rose-700'],
  confirmation: ['Received', 'bg-blue-100 text-blue-800'],
  update: ['Update', 'bg-sky-100 text-sky-800'],
  recruiter: ['Recruiter', 'bg-amber-100 text-amber-800'],
  job_alert: ['Job alert', 'bg-teal-50 text-teal-700'],
  account: ['Account', 'bg-slate-100 text-slate-500'],
}

const TABS = [
  { id: 'todo', label: 'To do', kinds: 'todo' },
  { id: 'all', label: 'All', kinds: '' },
  { id: 'interview', label: 'Interviews & tests', kinds: 'interview,assessment' },
  { id: 'offer', label: 'Offers', kinds: 'offer' },
  { id: 'rejection', label: 'Rejections', kinds: 'rejection' },
  { id: 'confirmation', label: 'Received', kinds: 'confirmation,update' },
  { id: 'job_alert', label: 'New jobs', kinds: 'job_alert,recruiter' },
  { id: 'account', label: 'Accounts', kinds: 'account' },
]

function Connect({ st, onSaved }) {
  const toast = useToast()
  const [address, setAddress] = useState(st?.address || '')
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (st?.address && !address) setAddress(st.address) }, [st]) // eslint-disable-line
  const save = async () => {
    setBusy(true)
    try { await api.put('/api/mail/settings', { address, app_password: pw, enabled: true }); setPw(''); toast('Gmail connected – first check starts now'); await api.post('/api/mail/check'); onSaved() }
    catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }
  return (
    <div className="glass rounded-2xl p-5 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-semibold text-navy"><KeyRound size={16} className="text-teal-600" />Connect your Gmail (once, about 2 minutes)</div>
      <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm text-slate-600">
        <li>Google needs <b>2-Step Verification</b> on your account (Google Account → Security). Most accounts already have it.</li>
        <li>Open <a className="font-medium text-teal-600 hover:underline" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">myaccount.google.com/apppasswords</a>, type the name <b>Job Hunt Agent</b> and press <b>Create</b>.</li>
        <li>Copy the 16-letter password Google shows and paste it below. It is stored only on this PC and can only read mail; you can delete it at Google any time.</li>
      </ol>
      <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
        <input className="input" placeholder="you@gmail.com" value={address} onChange={(e) => setAddress(e.target.value)} />
        <input className="input font-mono" type="password" placeholder="app password (xxxx xxxx xxxx xxxx)" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" />
        <button className="btn-primary" disabled={busy || !address || pw.replace(/\s/g, '').length < 16} onClick={save}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}Connect & check</button>
      </div>
      <p className="mt-2 text-xs muted">The app reads your inbox only (nothing is marked as read, deleted or sent). Only job mails are opened; everything else is skipped after reading the subject line. Passwords and codes inside mails are never stored.</p>
    </div>
  )
}

function Row({ e, onChanged }) {
  const toast = useToast()
  const [k, cls] = KIND[e.kind] || [e.kind, 'bg-slate-100 text-slate-600']
  const actionable = ['interview', 'assessment', 'offer'].includes(e.kind)
  const done = async () => { try { await api.post(`/api/mail/events/${e.id}/done?done=${!e.done}`); onChanged() } catch (er) { toast(er.message, 'error') } }
  return (
    <div className={`grid grid-cols-[92px_minmax(0,1fr)_auto] gap-4 px-5 py-3 ${actionable && !e.done ? 'bg-violet-50/50' : ''}`}>
      <div><span className={`chip ${cls}`}>{k}</span><div className="mt-1 text-[11px] muted">{fmtDateTime(e.received_at)}</div></div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-slate-800">{e.company || e.sender_name || e.sender}{e.role ? <span className="font-normal text-slate-500"> – {e.role}</span> : ''}</div>
        <div className="truncate text-xs text-slate-500" title={e.subject}>{e.subject}</div>
        {e.summary && e.kind !== 'job_alert' && <div className="mt-1 text-sm text-slate-600">{e.summary}</div>}
        {e.kind === 'job_alert' && e.matches?.length > 0 && <div className="mt-1.5 space-y-1">
          {e.matches.slice(0, 6).map((j) => <div key={j.id} className="flex items-center gap-2 text-sm">
            <span className={`w-9 rounded px-1 text-center text-xs font-semibold tabular-nums ${j.score >= 75 ? 'bg-emerald-100 text-emerald-800' : 'bg-teal-50 text-teal-700'}`}>{j.score ?? '–'}</span>
            <span className="truncate text-slate-700">{j.title}<span className="text-slate-400"> · {j.company}</span></span></div>)}
          {e.matches.length > 6 && <div className="text-xs muted">+ {e.matches.length - 6} more</div>}</div>}
        {e.action && <div className="mt-1 flex items-start gap-1.5 text-sm font-medium text-violet-800"><CalendarClock size={14} className="mt-0.5 shrink-0" />{e.action}{e.when_text ? ` · ${e.when_text}` : ''}</div>}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
          {e.job && <Link to="/tracker" className="inline-flex items-center gap-1 text-teal-600 hover:underline">{e.job.company} – {e.job.title}<StatusBadge status={e.job.status} /></Link>}
          {e.effect && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">→ {e.effect}</span>}
          {e.kind === 'job_alert' && e.matches?.length > 0 && <Link to="/jobs" className="text-teal-600 hover:underline">Job board → From email</Link>}
          {!e.job && ['confirmation', 'rejection', 'interview', 'assessment', 'offer', 'update'].includes(e.kind) && <span className="muted">not linked to a job</span>}
        </div>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <a href={e.gmail} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-teal-600"><ExternalLink size={12} />Open in Gmail</a>
        {actionable && <button className={`btn py-1 text-xs ${e.done ? 'border border-slate-200 bg-slate-50 text-slate-500' : 'bg-violet-600 text-white hover:bg-violet-700'}`} onClick={done}>
          <CheckCircle2 size={12} />{e.done ? 'Done' : 'Mark done'}</button>}
      </div>
    </div>
  )
}

export default function Inbox() {
  const toast = useToast()
  const st = useAsync(() => api.get('/api/mail/status'))
  const [tab, setTab] = useState('todo')
  const kinds = TABS.find((t) => t.id === tab).kinds
  const ev = useAsync(() => api.get(`/api/mail/events?kind=${kinds}`), [kinds])
  const [showSettings, setShowSettings] = useState(false)
  useOnEvent((e) => { if (e.type === 'mail') { st.reload(); if (e.result || e.kind) ev.reload() } }, [kinds])
  useEffect(() => { if (st.data && st.data.todo === 0 && tab === 'todo' && st.data.has_password) setTab('all') }, [st.data?.has_password]) // eslint-disable-line
  const s = st.data
  const connected = s?.has_password
  const counts = s?.counts || {}
  const tabCount = (t) => t.id === 'todo' ? s?.todo : t.id === 'all' ? Object.values(counts).reduce((a, b) => a + b, 0)
    : t.kinds.split(',').reduce((a, k) => a + (counts[k] || 0), 0)
  const check = async () => { try { await api.post('/api/mail/check'); st.reload(); toast('Checking your Gmail…') } catch (e) { toast(e.message, 'error') } }
  const saveOpt = async (patch) => { try { await api.put('/api/mail/settings', patch); st.reload() } catch (e) { toast(e.message, 'error') } }
  const disconnect = async () => { try { await api.post('/api/mail/disconnect'); st.reload(); toast('Gmail disconnected – delete the app password at Google too if you like') } catch (e) { toast(e.message, 'error') } }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h1 className="text-xl font-semibold text-navy">Inbox</h1>
          <p className="mt-1 max-w-3xl text-sm muted">Claude reads your job mails: confirmations mark a job <b>Applied</b>, rejections <b>Rejected</b>, invitations <b>Interview</b> (and land in <b>To do</b>), offers <b>Offer</b>. Jobs in job alerts (StepStone, Glassdoor, XING, LinkedIn, company talent pools) are scored against your CV; good matches go to <b>Job board → From email</b>.</p></div>
        {connected && <div className="text-right text-xs muted">
          <div className="flex items-center justify-end gap-2">
            <span className={`inline-block h-2 w-2 rounded-full ${s.enabled ? 'bg-emerald-500' : 'bg-slate-300'}`} />{s.address}{s.enabled ? ` · every ${s.interval_min} min` : ' · paused'}</div>
          <div className="mt-0.5">{s.running ? 'checking now…' : s.last_check ? `last check ${fmtDateTime(s.last_check)}` : 'not checked yet'}
            {s.older_waiting > 0 && ` · ${s.older_waiting} older mails still to read`}</div>
          <div className="mt-2 flex justify-end gap-2">
            <button className="btn-outline py-1 text-xs" disabled={s.running} onClick={check}>{s.running ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Check now</button>
            <button className="btn-ghost py-1 text-xs" onClick={() => setShowSettings((v) => !v)}>Settings</button>
          </div></div>}
      </div>

      {s && !connected && <Connect st={s} onSaved={() => { st.reload(); ev.reload() }} />}
      {s?.last_error && <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{s.last_error}</div>}
      {connected && showSettings && (
        <div className="flex flex-wrap items-center gap-5 glass rounded-2xl px-5 py-3 text-sm shadow-sm">
          <label className="flex items-center gap-2"><input type="checkbox" className="accent-teal" checked={s.enabled} onChange={(e) => saveOpt({ enabled: e.target.checked })} />Check automatically</label>
          <label className="flex items-center gap-2">every<select className="input w-24 py-1" value={s.interval_min} onChange={(e) => saveOpt({ interval_min: +e.target.value })}>
            {[5, 10, 15, 30, 60].map((n) => <option key={n} value={n}>{n} min</option>)}</select></label>
          <label className="flex items-center gap-2"><input type="checkbox" className="accent-teal" checked={s.add_jobs} onChange={(e) => saveOpt({ add_jobs: e.target.checked })} />Add jobs from job-alert mails</label>
          <label className="flex items-center gap-2" title="Jobs from mails below this CV match score go to Excluded instead of your Job board">only if CV score ≥
            <select className="input w-20 py-1" value={s.min_score} onChange={(e) => saveOpt({ min_score: +e.target.value })}>
              {[40, 50, 55, 60, 65, 70, 75, 80].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <span className="muted">The app checks while it is open.</span>
          <button className="btn-ghost ml-auto text-xs text-rose-700" onClick={disconnect}><Unplug size={12} />Disconnect</button>
        </div>)}

      {connected && (
        <div className="overflow-hidden glass rounded-2xl">
          <div className="px-5 pt-2"><Tabs value={tab} onChange={setTab} tabs={TABS.map((t) => ({ id: t.id, label: t.label, count: tabCount(t) || 0 }))} /></div>
          {ev.loading && !ev.data ? <div className="p-6 text-sm muted">Loading…</div>
            : !(ev.data || []).length ? <Empty title={tab === 'todo' ? 'Nothing to do' : 'No mails here yet'}>
                {tab === 'todo' ? 'Interview invitations, tests and offers show up here.' : s.running ? 'The first check is running – this can take a few minutes.' : 'New job mails appear here after the next check.'}</Empty>
            : <div className="divide-y divide-slate-100">{ev.data.map((e) => <Row key={e.id} e={e} onChanged={() => { ev.reload(); st.reload() }} />)}</div>}
        </div>)}
    </div>
  )
}
