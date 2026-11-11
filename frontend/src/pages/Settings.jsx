import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { api } from '../api'
import { useAsync, useToast } from '../ui'
import BuildOptions, { BUILD_KEYS, ClaudeExtras } from '../components/BuildOptions'

const Row = ({ label, help, children }) => (
  <div className="grid grid-cols-3 items-start gap-4 border-b border-slate-100 py-3 last:border-0"><div><div className="font-medium">{label}</div>{help && <div className="text-xs muted">{help}</div>}</div><div className="col-span-2">{children}</div></div>)

export default function Settings() {
  const { data } = useAsync(() => api.get('/api/settings'))
  const [f, setF] = useState(null)
  const toast = useToast()
  useEffect(() => { if (data) setF(data) }, [data])
  if (!f) return <div className="muted">Loading…</div>
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const save = async () => {
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([k]) => !BUILD_KEYS.includes(k)))   // the cards below save their own
      setF(await api.put('/api/settings', body)); toast('Settings saved')
    } catch (e) { toast(e.message, 'error') }
  }
  return (
    <div className="max-w-5xl space-y-5">
      <div className="flex items-center justify-between"><h1 className="h1">Settings</h1><button className="btn-teal" onClick={save}><Save size={14} />Save</button></div>
      <div className="card px-4">
        <Row label="AI mode" help={f.has_api_key ? 'API key found in .env' : 'No ANTHROPIC_API_KEY in .env – offline mode only'}>
          <select className="input" value={f.llm_mode || ''} onChange={set('llm_mode')}>
            <option value="">Automatic ({f.has_api_key ? 'Claude API' : 'offline'})</option><option value="anthropic">Claude API</option><option value="fake">Offline (no AI, heuristics only)</option></select>
          <div className="mt-1 text-xs muted">Currently: <b>{f.effective_llm_mode === 'fake' ? 'offline' : 'Claude API'}</b></div></Row>
        <Row label="Fast model" help="Page reading, classification"><input className="input" value={f.model_fast} onChange={set('model_fast')} /></Row>
        <Row label="Smart model" help="Scoring and checks with an API key. The CV and letter model is under CV building."><input className="input" value={f.model_smart} onChange={set('model_smart')} /></Row>
        <Row label="Daily budget (USD)" help="Runs stop before going over"><input type="number" step="0.5" className="input w-32" value={f.daily_budget_usd} onChange={set('daily_budget_usd')} /></Row>
      </div>
      <BuildOptions />
      <ClaudeExtras />
      <div className="card px-4">
        <Row label="Verdict: Skip below"><input type="number" className="input w-32" value={f.verdict_skip_below} onChange={set('verdict_skip_below')} /></Row>
        <Row label="Verdict: Apply from" help="Between the two = Apply after CV tailoring"><input type="number" className="input w-32" value={f.verdict_apply_from} onChange={set('verdict_apply_from')} /></Row>
        <Row label="Build CVs from score"><input type="number" className="input w-32" value={f.build_min_score} onChange={set('build_min_score')} /></Row>
        <Row label="Writer attempts" help="Writer → checker → QA retries"><input type="number" className="input w-32" value={f.max_build_attempts} onChange={set('max_build_attempts')} /></Row>
      </div>
      <div className="card px-4">
        <Row label="Morning run" help="Search + score with the default filter profile. Runs only while the app is open."><label className="flex items-center gap-2"><input type="checkbox" checked={!!f.schedule_enabled} onChange={set('schedule_enabled')} />Enabled at <input type="time" className="input w-32" value={f.schedule_time} onChange={set('schedule_time')} /></label></Row>
        <Row label="Seconds between pages" help="Human pacing protects your accounts"><input type="number" step="0.5" className="input w-32" value={f.page_delay_seconds} onChange={set('page_delay_seconds')} /></Row>
        <Row label="Result pages per search" help="Searches that hit the limit are split automatically"><input type="number" className="input w-32" value={f.max_pages_per_search} onChange={set('max_pages_per_search')} /></Row>
        <Row label="Guest browser hidden"><input type="checkbox" checked={!!f.headless_guest} onChange={set('headless_guest')} /></Row>
        <Row label="Job folder"><code className="text-xs">{f.job_dir}</code></Row>
      </div>
      <DeletedJobs />
      <div className="card px-4">
        <Row label="Earlier Excel files" help="Jobs_*.xlsx, Job_Match_Summary_*.xlsx and _Report_*.xlsx from your job folder. Jobs in them are treated as already seen, so they are never rebuilt.">
          <button className="btn-outline" onClick={async () => { const r = await api.post('/api/import/previous'); toast(r.map((x) => `${x.file}: ${x.imported ?? 0} new, ${x.updated ?? 0} updated${x.error ? ' (' + x.error + ')' : ''}`).join(' · ') || 'No files found') }}>Import earlier files</button>
        </Row>
      </div>
    </div>
  )
}

function DeletedJobs() {
  const { data, reload } = useAsync(() => api.get('/api/jobs/deleted'))
  const [open, setOpen] = useState(false)
  if (!data) return null
  return (
    <div className="card px-4">
      <Row label="Deleted jobs" help="Jobs you deleted are skipped by future searches. Undo to allow one to be found again.">
        <button className="btn-outline" onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'} ({data.length})</button>
        {open && (
          <div className="mt-2 max-h-72 overflow-y-auto text-sm">
            {data.map((d) => (
              <div key={d.id} className="flex items-center justify-between border-b border-slate-100 py-1">
                <span>{d.title} <span className="muted">· {d.company}</span></span>
                <button className="btn-ghost text-xs" onClick={async () => { await api.del(`/api/jobs/deleted/${d.id}`); reload() }}>Undo</button>
              </div>))}
            {!data.length && <div className="muted">None.</div>}
          </div>)}
      </Row>
    </div>
  )
}
