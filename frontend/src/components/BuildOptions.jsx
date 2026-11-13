import { useEffect, useState } from 'react'
import { Cpu, FolderGit2, Github, Loader2, Puzzle, RefreshCw, Save, ShieldCheck, Sparkles } from 'lucide-react'
import { api } from '../api'
import { useToast } from '../ui'
import { ClaudeLoginStatus } from './ClaudeLogin'

// Keys this card owns (the Settings form leaves them alone)
export const BUILD_KEYS = ['cv_model', 'letter_model', 'letter_language', 'cv_effort', 'cv_projects', 'github_projects', 'github_user', 'github_token',
  'ats_target', 'recruiter_review', 'humanize', 'state_availability', 'claude_lean', 'claude_skills', 'claude_plugins']

const MODELS = [
  { v: '', l: 'Default (Sonnet)' },
  { v: 'haiku', l: 'Haiku · fastest, fewest tokens' },
  { v: 'sonnet', l: 'Sonnet · balanced' },
  { v: 'opus', l: 'Opus · best writing, most tokens' },
  { v: 'fable', l: 'Fable · newest top model' },
]
const EFFORT = [{ v: 'low', l: 'Low – fast, fewest tokens (recommended)' }, { v: 'medium', l: 'Medium' }, { v: 'high', l: 'High – slow, many tokens' }]

const Field = ({ label, help, children }) => (
  <label className="block"><span className="label">{label}</span>{children}{help && <span className="mt-1 block text-xs muted">{help}</span>}</label>)

const Toggle = ({ on, onChange, title, help, disabled }) => (
  <label className={`flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 ${disabled ? 'opacity-50' : 'cursor-pointer hover:border-slate-300'}`}>
    <input type="checkbox" className="mt-0.5" checked={!!on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    <span><span className="block text-sm font-medium text-slate-900">{title}</span>{help && <span className="block text-xs muted">{help}</span>}</span>
  </label>)

function useSettings() {
  const [f, setF] = useState(null)
  const [dirty, setDirty] = useState(false)
  useEffect(() => { api.get('/api/settings').then(setF) }, [])
  const set = (k, v) => { setF((x) => ({ ...x, [k]: v })); setDirty(true) }
  const save = async (keys) => {
    const body = Object.fromEntries(keys.filter((k) => k in f).map((k) => [k, f[k]]))
    const r = await api.put('/api/settings', body); setF(r); setDirty(false); return r
  }
  return { f, set, save, dirty, setF }
}

// ----------------------------------------------------------------------------- CV building ---
export default function BuildOptions({ compact = false }) {
  const { f, set, save, dirty } = useSettings()
  const toast = useToast()
  const [token, setToken] = useState('')
  const [gh, setGh] = useState(null)
  const [ghBusy, setGhBusy] = useState(false)
  if (!f) return <div className="card p-4 text-sm muted">Loading build options…</div>

  const doSave = async () => {
    try {
      const keys = BUILD_KEYS.filter((k) => k !== 'github_token')
      if (token) { f.github_token = token; keys.push('github_token') }
      await save(keys); setToken(''); toast('Build options saved')
    } catch (e) { toast(e.message, 'error') }
  }
  const checkGithub = async (refresh = false) => {
    setGhBusy(true)
    try { if (dirty) await doSave(); setGh(await api.get(`/api/github/repos${refresh ? '?refresh=true' : ''}`)) }
    catch (e) { toast(e.message, 'error') } finally { setGhBusy(false) }
  }
  const removeToken = async () => { await api.put('/api/settings', { github_token: '__clear__' }); toast('GitHub token removed'); location.reload() }

  return (
    <div className="card">
      <div className="card-h">
        <span className="flex items-center gap-2"><Sparkles size={16} className="text-teal" />CV building</span>
        <button className="btn-teal" disabled={!dirty && !token} onClick={doSave}><Save size={14} />Save</button>
      </div>
      <div className="grid gap-5 p-5 lg:grid-cols-2">
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500"><Cpu size={14} />Model</div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="CV writer"><select className="input" value={f.cv_model} onChange={(e) => set('cv_model', e.target.value)}>
              {MODELS.map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}</select></Field>
            <Field label="Cover letter"><select className="input" value={f.letter_model} onChange={(e) => set('letter_model', e.target.value)}>
              <option value="">Same as CV</option>{MODELS.slice(1).map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}</select></Field>
            <Field label="Cover letter language" help="English by default, also for German job ads. A German letter suggests you are fluent in German."><select className="input" value={f.letter_language || 'english'} onChange={(e) => set('letter_language', e.target.value)}>
              <option value="english">English (always)</option><option value="ad">Same as the job ad</option><option value="german">German (always)</option></select></Field>
            <Field label="Effort" help="How long Claude thinks before writing. High used up to 17,000 tokens and 4-5 minutes for one CV; low is usually enough because the app checks every CV."><select className="input" value={f.cv_effort || 'low'} onChange={(e) => set('cv_effort', e.target.value)}>
              {EFFORT.map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}</select></Field>
            <Field label="Projects on the CV" help="2-6. Many projects get shorter bullets to stay on one page.">
              <input type="number" min={2} max={6} className="input" value={f.cv_projects} onChange={(e) => set('cv_projects', Math.max(2, Math.min(6, +e.target.value || 3)))} /></Field>
          </div>
          <div className="flex items-center gap-2 pt-1 text-xs font-semibold uppercase tracking-wider text-slate-500"><ShieldCheck size={14} />Hard to reject</div>
          <div className="grid gap-2">
            <Field label="ATS target score" help="Below this, the writer adds the posting's exact words for skills you really have, then checks again.">
              <input type="number" min={50} max={100} className="input w-32" value={f.ats_target} onChange={(e) => set('ats_target', +e.target.value || 75)} /></Field>
            <Toggle on={f.recruiter_review} onChange={(v) => set('recruiter_review', v)} title="Recruiter review"
              help="One extra pass reads the CV like a recruiter, names knock-outs (e.g. German C1) and applies true fixes. Costs one more call." />
            <Toggle on={f.humanize} onChange={(v) => set('humanize', v)} title="Human-sounding writing"
              help="Built-in rules against AI-sounding phrases, for the CV and the letter. No plugin needed." />
            <Toggle on={f.state_availability} onChange={(v) => set('state_availability', v)} title="Availability in the cover letter"
              help="A sentence in the cover letter from My details: hours per week or internship length, start date, work permit. Never in the CV: its summary is about your studies, skills and experience." />
          </div>
        </div>
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500"><FolderGit2 size={14} />Projects from GitHub</div>
          <Toggle on={f.github_projects} onChange={(v) => set('github_projects', v)} title="Look at my GitHub too"
            help="Reads your repositories' READMEs and picks the projects closest to each job, from GitHub and your main CV. A project only on GitHub is described with its README facts only." />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="GitHub user" help="Empty = the GitHub link on your CV.">
              <input className="input" placeholder="github.com/your-name" value={f.github_user} onChange={(e) => set('github_user', e.target.value)} /></Field>
            <Field label="Token (optional)" help={f.has_github_token ? <span>Saved. <button className="text-teal underline" onClick={removeToken}>Remove</button></span> : 'For private repos and a higher limit.'}>
              <input type="password" className="input" placeholder={f.has_github_token ? '••••••••' : 'ghp_…'} value={token} onChange={(e) => setToken(e.target.value)} /></Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-outline" onClick={() => checkGithub(false)} disabled={ghBusy}>{ghBusy ? <Loader2 size={14} className="animate-spin" /> : <Github size={14} />}Check my GitHub</button>
            {gh && <button className="btn-ghost" onClick={() => checkGithub(true)} disabled={ghBusy} title="Read GitHub again now (otherwise once a day)"><RefreshCw size={14} />Read again</button>}
          </div>
          {gh && <GithubList gh={gh} compact={compact} />}
        </div>
      </div>
    </div>
  )
}

function GithubList({ gh, compact }) {
  if (gh.error) return <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{gh.error}</div>
  return (
    <div className="rounded-xl border border-slate-200">
      <div className="border-b border-slate-100 px-3 py-2 text-xs muted">{gh.repos.length} repositories of <b>{gh.user}</b> · {gh.repos.filter((r) => r.usable).length} usable{gh.cached && ' · read today'}</div>
      <div className={`${compact ? 'max-h-48' : 'max-h-72'} divide-y divide-slate-100 overflow-y-auto`}>
        {gh.repos.map((r) => (
          <div key={r.repo} className="px-3 py-2 text-sm">
            <div className="flex items-center gap-2">
              <a href={r.url} target="_blank" rel="noreferrer" className="font-medium hover:text-teal">{r.name}</a>
              {r.on_cv ? <span className="chip bg-teal-50 text-teal-700">on your CV</span> : r.usable ? <span className="chip bg-sky-50 text-sky-700">GitHub only</span> : <span className="chip bg-slate-100 text-slate-500">no README</span>}
              {r.language && <span className="ml-auto text-xs muted">{r.language}</span>}
            </div>
            {r.stack?.length > 0 && <div className="mt-0.5 truncate text-xs muted">{r.stack.join(' · ')}</div>}
          </div>))}
      </div>
    </div>)
}

// ----------------------------------------------------------------------------- Claude Code extras ---
export function ClaudeExtras() {
  const { f, set, save, dirty } = useSettings()
  const [x, setX] = useState(null)
  const toast = useToast()
  const load = () => api.get('/api/claude/extras').then(setX)
  useEffect(() => { load() }, [])
  if (!f || !x) return <div className="card p-4 text-sm muted">Loading…</div>
  let choice = {}
  try { choice = JSON.parse(f.claude_plugins || '{}') } catch { choice = {} }
  const lean = !!f.claude_lean
  const setPlugin = (name, on) => set('claude_plugins', JSON.stringify({ ...choice, [name]: on }))
  const doSave = async () => { try { await save(['claude_lean', 'claude_skills', 'claude_plugins']); await load(); toast('Saved') } catch (e) { toast(e.message, 'error') } }
  const calls = x.recent_calls || []
  return (
    <div className="card">
      <div className="card-h"><span className="flex items-center gap-2"><Puzzle size={16} className="text-teal" />Claude Code: plugins and tokens</span>
        <button className="btn-teal" disabled={!dirty} onClick={doSave}><Save size={14} />Save</button></div>
      <div className="space-y-3 p-5">
        {!x.claude_code && <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">Claude Code was not found on this computer.</div>}
        <ClaudeLoginStatus />
        <Toggle on={lean} onChange={(v) => set('claude_lean', v)} title="Lean mode (saves the most tokens)"
          help="Each call sends only the app's own instructions: no Claude Code system prompt, tools, skills, plugins or MCP servers. About 3,000 instead of about 36,000 input tokens per call." />
        <div className={lean ? 'pointer-events-none opacity-50' : ''}>
          <Toggle on={f.claude_skills} onChange={(v) => set('claude_skills', v)} disabled={lean} title={`Load my skills (${x.skills.length})`}
            help={x.skills.length ? x.skills.slice(0, 12).join(', ') + (x.skills.length > 12 ? ` and ${x.skills.length - 12} more` : '') : 'No personal skills found.'} />
          <div className="mt-2 rounded-xl border border-slate-200">
            <div className="border-b border-slate-100 px-3 py-2 text-xs muted">Plugins ({x.plugins.length}) · on/off only for this app, your Claude Code stays as it is</div>
            {!x.plugins.length && <div className="px-3 py-2 text-sm muted">No plugins installed.</div>}
            {x.plugins.map((p) => (
              <label key={p.name} className="flex items-center gap-3 border-b border-slate-100 px-3 py-2 text-sm last:border-0">
                <input type="checkbox" disabled={lean} checked={!lean && (choice[p.name] ?? p.in_claude_code)} onChange={(e) => setPlugin(p.name, e.target.checked)} />
                <span className="font-medium">{p.name.split('@')[0]}</span><span className="text-xs muted">{p.name.split('@')[1]}</span>
                {!p.in_claude_code && <span className="ml-auto text-xs muted">off in Claude Code</span>}
              </label>))}
          </div>
        </div>
        <p className="text-xs muted">Inside the app Claude Code answers in one turn with its tools off, so skills and plugins (ponytail, a humanizer and so on) cannot actually run there; they only add their descriptions to every call. Human-sounding writing is built in under CV building.</p>
        {calls.length > 0 && (
          <div className="rounded-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2 text-xs muted">Last Claude Code calls<button className="text-teal" onClick={load}><RefreshCw size={12} /></button></div>
            <table className="w-full text-xs"><tbody>
              {calls.slice().reverse().map((c, i) => (
                <tr key={i} className="border-b border-slate-100 last:border-0"><td className="px-3 py-1.5 font-medium">{c.node}</td>
                  <td className="px-3 text-right tabular-nums">{c.input_tokens.toLocaleString()} in</td><td className="px-3 text-right tabular-nums">{c.output_tokens.toLocaleString()} out</td>
                  <td className="px-3 text-right muted">{c.lean ? 'lean' : 'full'}</td></tr>))}
            </tbody></table>
          </div>)}
      </div>
    </div>)
}
