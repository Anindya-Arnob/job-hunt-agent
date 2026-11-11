import { useState } from 'react'
import { LogIn, Plus, RefreshCw, Trash2, ShieldCheck } from 'lucide-react'
import { api, fmtDate } from '../api'
import { StatusBadge, useAsync, useToast } from '../ui'

const ATS = ['greenhouse', 'lever', 'ashby', 'smartrecruiters', 'personio']

export default function Connections() {
  const conns = useAsync(() => api.get('/api/connections'))
  const comps = useAsync(() => api.get('/api/companies'))
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const [nc, setNc] = useState({ name: '', ats: 'greenhouse', board_id: '' })

  const login = async (site) => { await api.post(`/api/connections/${site}/login`); toast('A browser window opened. Log in there, then close it.'); setTimeout(conns.reload, 1500) }
  const verify = async (site) => { setBusy(site); try { const r = await api.post(`/api/connections/${site}/verify`); toast(`Status: ${r.status}`) } catch (e) { toast(e.message, 'error') } setBusy(''); conns.reload() }
  const disconnect = async (site) => { if (confirm('Remove the saved login for this site?')) { await api.del(`/api/connections/${site}`); conns.reload() } }
  const addCompany = async () => { if (!nc.name || !nc.board_id) return; await api.post('/api/companies', nc); setNc({ ...nc, name: '', board_id: '' }); comps.reload() }

  return (
    <div className="space-y-5">
      <h1 className="h1">Connections</h1>
      <div className="card flex gap-3 bg-navy-50 p-4 text-sm">
        <ShieldCheck className="shrink-0 text-teal" />
        <div>Agents always try <b>without login first</b>. Your saved login (use your second account) is only used for searches where guest access failed.
          You log in yourself in a normal browser window; the app keeps only the browser session, never your password. Agents are read-only: they cannot apply, message or connect.</div>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(conns.data || []).map((c) => (
          <div key={c.site} className="card p-4">
            <div className="flex items-center justify-between"><div className="font-semibold text-navy">{c.label}</div><StatusBadge status={c.status} /></div>
            <div className="mt-1 h-8 text-xs muted">{c.supports_login ? (c.last_verified_at ? `Checked ${fmtDate(c.last_verified_at)}` : 'Guest first; login used only as fallback') : 'Public pages or official feed – no login needed'}{c.note && <div className="text-amber-700">{c.note}</div>}</div>
            {c.supports_login && (
              <div className="mt-2 flex gap-2">
                <button className="btn-primary" onClick={() => login(c.site)}><LogIn size={14} />{c.status === 'connected' ? 'Reconnect' : 'Log in'}</button>
                {c.has_profile && <button className="btn-outline" disabled={busy === c.site} onClick={() => verify(c.site)}><RefreshCw size={14} className={busy === c.site ? 'animate-spin' : ''} />Check</button>}
                {c.has_profile && <button className="btn-danger" onClick={() => disconnect(c.site)}><Trash2 size={14} /></button>}
              </div>)}
          </div>))}
      </div>
      <div className="card">
        <div className="card-h">Target companies (official job feeds – no login, no account risk)</div>
        <div className="p-4">
          <p className="mb-3 text-sm muted">Add companies you want checked every day. The board id is in their careers URL, e.g. boards.greenhouse.io/<b>n26</b>, jobs.lever.co/<b>spotify</b>, <b>company</b>.jobs.personio.de.</p>
          <div className="mb-3 flex gap-2">
            <input className="input w-56" placeholder="Company name" value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} />
            <select className="input w-44" value={nc.ats} onChange={(e) => setNc({ ...nc, ats: e.target.value })}>{ATS.map((a) => <option key={a}>{a}</option>)}</select>
            <input className="input w-56" placeholder="Board id" value={nc.board_id} onChange={(e) => setNc({ ...nc, board_id: e.target.value })} />
            <button className="btn-teal" onClick={addCompany}><Plus size={14} />Add</button>
          </div>
          <table className="w-full text-sm"><tbody>
            {(comps.data || []).map((c) => (
              <tr key={c.id} className="border-t border-slate-100"><td className="py-1.5 font-medium">{c.name}</td><td className="muted">{c.ats}</td><td className="muted">{c.board_id}</td>
                <td className="text-right"><button className="btn-ghost" onClick={async () => { await api.del(`/api/companies/${c.id}`); comps.reload() }}><Trash2 size={14} /></button></td></tr>))}
            {!comps.data?.length && <tr><td className="py-2 muted">No companies yet.</td></tr>}
          </tbody></table>
        </div>
      </div>
    </div>
  )
}
