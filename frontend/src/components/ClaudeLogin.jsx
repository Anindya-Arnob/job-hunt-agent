import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { KeyRound, Loader2, LogIn, RefreshCw, X } from 'lucide-react'
import { api } from '../api'
import { useOnEvent } from '../events'
import { useToast } from '../ui'

// Is Claude Code logged in on this PC? One state for the whole app: the banner, the sidebar dot, Settings.
const Ctx = createContext(null)
const BAD = ['expired', 'logged_out', 'missing']

export function ClaudeLoginProvider({ children }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const notified = useRef(false)
  const toast = useToast()
  const load = (q = '') => api.get(`/api/claude/login${q}`).then(setSt).catch(() => {})
  useEffect(() => { load(); const t = setInterval(() => load(), 5 * 60 * 1000); return () => clearInterval(t) }, [])
  useOnEvent((e) => { if (e.type === 'claude_login') setSt((x) => ({ ...(x || {}), state: e.state, message: e.message })) }, [])

  // a desktop notification once, when the login goes bad (the app may be in a background tab)
  useEffect(() => {
    if (!st || !BAD.includes(st.state)) { notified.current = false; return }
    if (notified.current || st.state === 'missing') return
    notified.current = true
    try {
      const show = () => new Notification('Job Hunt Agent: Claude login', { body: st.message, tag: 'claude-login' })
      if (Notification.permission === 'granted') show()
      else if (Notification.permission === 'default') Notification.requestPermission().then((p) => p === 'granted' && show())
    } catch (e) { /* no notifications in this browser */ }
  }, [st?.state]) // eslint-disable-line

  const check = async () => {
    setBusy(true)
    try { const r = await api.get('/api/claude/login?probe=true'); setSt(r); toast(r.state === 'ok' ? 'Claude Code is logged in' : r.message, r.state === 'ok' ? 'ok' : 'warn') }
    catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }
  const login = async () => {
    try {
      const r = await api.post('/api/claude/login/open')
      toast(r.how, r.opened ? 'ok' : 'warn')
      // look again a few times while you log in (each look is one tiny Claude call)
      setBusy(true)
      for (let i = 0; i < 8; i++) {
        await new Promise((ok) => setTimeout(ok, 20000))
        const s = await api.get('/api/claude/login?probe=true').catch(() => null)
        if (s) setSt(s)
        if (s?.state === 'ok') { toast('Claude Code is logged in again'); break }
      }
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }
  return <Ctx.Provider value={{ st, busy, check, login, reload: load }}>{children}</Ctx.Provider>
}

export const useClaudeLogin = () => useContext(Ctx) || {}

// the red bar on top of every page
export function ClaudeLoginBanner() {
  const { st, busy, check, login } = useClaudeLogin()
  const [hidden, setHidden] = useState('')
  if (!st || !BAD.includes(st.state) || hidden === st.state) return null
  return (
    <div className="glass mb-4 flex flex-wrap items-center gap-3 rounded-2xl border-rose-300/40 bg-rose-500/10 px-4 py-3 text-sm" role="alert">
      <KeyRound size={18} className="shrink-0 text-rose-500" />
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-slate-900">{st.state === 'missing' ? 'Claude Code is not installed' : st.state === 'expired' ? 'Claude login expired' : 'Claude is not logged in'}</div>
        <div className="text-slate-600">{st.message}{st.state !== 'missing' && ' Web search, CV writing and the recruiter review wait until you log in.'}</div>
      </div>
      {st.state !== 'missing' && <>
        <button className="btn-primary" onClick={login} disabled={busy}>{busy ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}Log in again</button>
        <button className="btn-outline" onClick={check} disabled={busy}><RefreshCw size={14} />Check again</button>
      </>}
      <button className="btn-ghost px-2" title="Hide until it changes" onClick={() => setHidden(st.state)}><X size={14} /></button>
    </div>)
}

// small status line (sidebar / Settings)
export function ClaudeLoginStatus({ compact = false }) {
  const { st, busy, check, login } = useClaudeLogin()
  if (!st) return null
  const ok = st.state === 'ok' || st.state === 'api'
  const dot = ok ? 'bg-emerald-400' : st.state === 'unknown' ? 'bg-slate-400' : 'bg-rose-500'
  const label = { ok: 'Claude: logged in', api: 'Claude: API key', expired: 'Claude: login expired', logged_out: 'Claude: logged out',
    missing: 'Claude Code missing', unknown: 'Claude: not checked yet' }[st.state] || st.state
  if (compact) {
    return (
      <button onClick={ok ? check : login} disabled={busy} title={ok ? 'Check the Claude Code login now' : 'Log in to Claude Code again'}
        className="flex w-full items-center gap-2 rounded-xl px-3 py-1.5 text-left text-[12px] text-slate-600 hover:bg-slate-100">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <span className={`h-2 w-2 rounded-full ${dot}`} />}
        <span className="flex-1 truncate">{label}</span>
        {!ok && st.state !== 'missing' && <span className="text-[11px] font-semibold text-rose-500">Log in</span>}
      </button>)
  }
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm">
      <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
      <span className="flex-1"><b>{label}</b>{st.method && ok && <span className="muted"> · {st.method === 'oauth_token' ? 'your Claude subscription' : st.method}</span>}
        {st.checked > 0 && <span className="muted"> · checked {new Date(st.checked * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}</span>
      <button className="btn-outline py-1 text-xs" onClick={check} disabled={busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Check now</button>
      {st.state !== 'missing' && <button className={ok ? 'btn-ghost py-1 text-xs' : 'btn-primary py-1 text-xs'} onClick={login} disabled={busy}><LogIn size={12} />{ok ? 'Log in again' : 'Log in'}</button>}
    </div>)
}
