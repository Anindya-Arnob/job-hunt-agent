import { useEffect, useState } from 'react'
import { Play, RefreshCw } from 'lucide-react'
import { api, fmtDate, fmtTime, SOURCE_LABEL } from '../api'
import { Empty, StatusBadge, useAsync, useToast } from '../ui'
import { useOnEvent } from '../events'

export default function Runs() {
  const runs = useAsync(() => api.get('/api/runs?limit=100'))
  const insights = useAsync(() => api.get('/api/insights/missing-keywords'))
  const [sel, setSel] = useState(null)
  const [detail, setDetail] = useState(null)
  const toast = useToast()
  useOnEvent((ev) => { if (['run_done', 'run_failed', 'run_started'].includes(ev.type)) runs.reload() }, [])
  useEffect(() => { if (!sel && runs.data?.length) setSel((runs.data.find((r) => r.kind === 'search') || runs.data[0]).id) }, [runs.data]) // eslint-disable-line
  useEffect(() => { if (sel) api.get(`/api/runs/${sel}`).then(setDetail) }, [sel, runs.data])

  const resume = async (id) => { try { await api.post(`/api/runs/${id}/resume`); toast('Resuming where it stopped'); runs.reload() } catch (e) { toast(e.message, 'error') } }
  const rescore = async (scope) => { toast('Re-scoring…'); try { const r = await api.post('/api/runs/rescore', { scope }); toast(`Re-scored ${r.rescored} jobs`); runs.reload() } catch (e) { toast(e.message, 'error') } }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between"><h1 className="h1">Runs & coverage</h1>
        <div className="flex gap-2"><button className="btn-outline" onClick={() => rescore('outdated')}><RefreshCw size={14} />Re-score outdated</button>
          <button className="btn-outline" onClick={() => rescore('all')}><RefreshCw size={14} />Re-score all</button></div></div>
      <div className="grid gap-5 xl:grid-cols-4">
        <div className="card max-h-[75vh] overflow-y-auto">
          <div className="card-h">History</div>
          {(runs.data || []).map((r) => (
            <button key={r.id} onClick={() => setSel(r.id)} className={`block w-full border-b border-slate-100 px-4 py-2 text-left text-sm ${sel === r.id ? 'bg-teal-50' : 'hover:bg-slate-50'}`}>
              <div className="flex items-center justify-between"><span className="font-medium capitalize">{r.kind} #{r.id}</span><StatusBadge status={r.status} /></div>
              <div className="text-xs muted">{fmtDate(r.started_at)} {fmtTime(r.started_at)}{r.cost_usd ? ` · $${r.cost_usd.toFixed(2)}` : ''}</div>
            </button>))}
          {!runs.data?.length && <Empty title="No runs yet" />}
        </div>
        <div className="space-y-5 xl:col-span-3">
          {detail && (
            <div className="card">
              <div className="card-h"><span className="capitalize">{detail.run.kind} #{detail.run.id}</span>
                <span className="flex items-center gap-2">{detail.run.status === 'failed' && detail.run.kind === 'search' && <button className="btn-teal" onClick={() => resume(detail.run.id)}><Play size={14} />Resume</button>}<StatusBadge status={detail.run.status} /></span></div>
              <div className="space-y-4 p-4">
                {detail.run.counts && Object.keys(detail.run.counts).length > 0 && (
                  <div className="flex flex-wrap gap-2 text-sm">
                    {Object.entries(detail.run.counts).filter(([, v]) => typeof v !== 'object').map(([k, v]) => <span key={k} className="chip bg-slate-100">{k.replace(/_/g, ' ')}: <b className="ml-1">{v}</b></span>)}
                  </div>)}
                {detail.run.counts?.by_role && <div className="text-sm"><span className="label">Jobs per role</span>{Object.entries(detail.run.counts.by_role).map(([k, v]) => <span key={k} className="chip mr-1 bg-teal-50 text-teal-600">{k}: {v}</span>)}</div>}
                {detail.run.errors?.length > 0 && <div className="rounded-md bg-rose-50 p-2 text-xs text-rose-800">{detail.run.errors.slice(0, 8).map((e, i) => <div key={i}>{e}</div>)}</div>}
                {detail.coverage.length > 0 && (
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500"><tr><th className="px-3 py-2">Site</th><th>Searches</th><th>Worked without login</th><th>Needed login</th><th title="Searches done through Claude's web search, and the jobs they found">Web search</th><th>Split (hit limit)</th><th>Human check</th><th>Failed</th><th>Jobs collected</th></tr></thead>
                    <tbody>{detail.coverage.map((c) => (
                      <tr key={c.site} className="border-t border-slate-100"><td className="px-3 py-1.5 font-medium">{SOURCE_LABEL[c.site] || c.site}</td><td>{c.searches}</td><td>{c.guest_ok}</td><td>{c.needed_login}</td><td>{c.web ? <span title={`${c.web} web searches`}>{c.web} · <b>{c.web_jobs}</b> jobs</span> : 0}</td><td>{c.split}</td>
                        <td className={c.human_check ? 'font-semibold text-amber-700' : ''}>{c.human_check || 0}</td>
                        <td className={c.failed ? 'font-semibold text-rose-700' : ''}>{c.failed}{c.failed ? ' ⚠️' : ''}</td><td>{c.collected}</td></tr>))}</tbody>
                  </table>)}
                {detail.searches.some((x) => x.status === 'manual') && (
                  <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    <span>🛡️</span><div><b>{[...new Set(detail.searches.filter((x) => x.status === 'manual').map((x) => SOURCE_LABEL[x.site] || x.site))].join(', ')}</b> blocks automated browsers – it refused the app even after the “verify you are human” check was passed.
                      The app no longer searches it by itself, so it stops slowing down your runs. Instead, when you press Run on the <b>Search</b> screen, it opens a tab in your own Chrome: do the check or log in there once, and the extension collects the jobs by itself (or press <b>Collect now</b> under Sources).</div></div>)}
                {!detail.searches.some((x) => x.status === 'manual') && detail.coverage.some((c) => c.human_check) && (
                  <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    <span>🛡️</span><div><b>{detail.coverage.filter((c) => c.human_check).map((c) => SOURCE_LABEL[c.site] || c.site).join(', ')}</b> showed a “verify you are human” check.
                      From the next run the app opens a normal browser window for this site. When the check appears there, solve it once – the search then continues by itself and the window remembers it for later runs.
                      Keep the Search screen open so you see the prompt.</div></div>)}
                {detail.searches.filter((s) => !['ok', 'split'].includes(s.status)).length > 0 && (
                  <div><span className="label">Searches not completed (retried next run)</span>
                    <div className="max-h-56 overflow-y-auto text-xs">{detail.searches.filter((s) => !['ok', 'split'].includes(s.status)).map((s) => (
                      <div key={s.id} className="border-b border-slate-100 py-1">{SOURCE_LABEL[s.site] || s.site} · “{s.query}” {s.location && `in ${s.location}`} · <b>{s.status.replace('_', ' ')}</b> {s.error && <span className="muted">{s.error.slice(0, 120)}</span>}</div>))}</div></div>)}
                <div><span className="label">Log</span>
                  <div className="max-h-60 overflow-y-auto rounded-md bg-slate-50 p-2 font-mono text-xs">{detail.events.map((e, i) => <div key={i}><span className="text-slate-400">{fmtTime(e.ts)}</span> {e.message}</div>)}
                    {!detail.events.length && <span className="muted">Log is kept while the app is open.</span>}</div></div>
              </div>
            </div>)}
          <div className="card">
            <div className="card-h">Most frequently missing skills across all jobs</div>
            <div className="flex flex-wrap gap-2 p-4">{(insights.data || []).map((k) => <span key={k.keyword} className="chip bg-rose-50 text-rose-800">{k.keyword} <b className="ml-1">{k.jobs}</b></span>)}
              {!insights.data?.length && <span className="muted text-sm">Appears after jobs are scored.</span>}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
