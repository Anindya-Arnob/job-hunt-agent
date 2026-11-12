import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { LayoutDashboard, Search, Table2, Hammer, FileText, ListChecks, PlugZap, IdCard, Activity, Send, UserRound, Inbox as InboxIcon, Settings as SettingsIcon, Moon, Sun, BriefcaseBusiness, ChevronRight } from 'lucide-react'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import './index.css'
import { EventsProvider, useEvents, useOnEvent } from './events'
import { api } from './api'
import { ToastProvider } from './ui'
import { ClaudeLoginBanner, ClaudeLoginProvider, ClaudeLoginStatus } from './components/ClaudeLogin'
import Dashboard from './pages/Dashboard'
import SearchPage from './pages/Search'
import JobBoard from './pages/JobBoard'
import BuildQueue from './pages/BuildQueue'
import Documents from './pages/Documents'
import Tracker from './pages/Tracker'
import Connections from './pages/Connections'
import MyCV from './pages/MyCV'
import Runs from './pages/Runs'
import Settings from './pages/Settings'
import Apply from './pages/Apply'
import MyDetails from './pages/MyDetails'
import Inbox from './pages/Inbox'

const NAV = [
  { section: 'Work' },
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/jobs', label: 'Job board', icon: Table2, badge: 'new' },
  { to: '/build', label: 'Build queue', icon: Hammer },
  { to: '/documents', label: 'Documents', icon: FileText },
  { section: 'Apply' },
  { to: '/apply', label: 'Apply', icon: Send },
  { to: '/tracker', label: 'Tracker', icon: ListChecks },
  { to: '/inbox', label: 'Inbox', icon: InboxIcon, badge: 'todo' },
  { section: 'Setup' },
  { to: '/connections', label: 'Connections', icon: PlugZap },
  { to: '/cv', label: 'My CV', icon: IdCard },
  { to: '/details', label: 'My details', icon: UserRound },
  { to: '/runs', label: 'Runs & coverage', icon: Activity },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
]

// dark glass by default; the choice is remembered in this browser
function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('jha-theme', theme) } catch (e) { /* private mode */ }
  }, [theme])
  return [theme, setTheme]
}

function Switch({ on, onChange, label }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className={`relative h-[22px] w-10 shrink-0 rounded-full transition ${on ? 'bg-emerald-500 shadow-[0_0_12px_rgb(16_185_129/.45)]' : 'bg-slate-300'}`}>
      <span className={`absolute top-[3px] h-4 w-4 rounded-full bg-white shadow transition-all ${on ? 'left-[21px]' : 'left-[3px]'}`} />
    </button>
  )
}

function useBadges() {
  const [b, setB] = useState({})
  const load = () => Promise.all([
    api.get('/api/jobs/counts').catch(() => ({})),
    api.get('/api/mail/status').catch(() => ({})),
  ]).then(([c, m]) => setB({ new: c.new || 0, todo: m.todo || 0 }))
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [])
  useOnEvent((e) => { if (['run_done', 'mail', 'apply'].includes(e.type) || e.result) load() }, [])
  return b
}

function Sidebar() {
  const { connected } = useEvents()
  const [theme, setTheme] = useTheme()
  const badges = useBadges()
  const [me, setMe] = useState(null)
  useEffect(() => { api.get('/api/apply/profile').then((r) => setMe(r.profile)).catch(() => {}) }, [])
  const name = [me?.first_name, me?.last_name].filter(Boolean).join(' ')
  const initials = (name || 'You').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
  return (
    <aside className="glass relative z-10 m-3 mr-0 flex w-[236px] shrink-0 flex-col rounded-[26px]">
      <div className="flex items-center gap-3 px-4 pb-4 pt-5">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-blue-400 to-teal-500 text-white shadow-glow">
          <BriefcaseBusiness size={18} />
        </div>
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold leading-tight text-navy">Job Hunt Agent</div>
          <div className="truncate text-[11px] text-slate-500">AI · ML · Data roles</div>
        </div>
      </div>
      <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-3 pb-3">
        {NAV.map((n, i) => n.section
          ? <div key={i} className="px-3 pb-1.5 pt-4 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">{n.section}</div>
          : (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              className={({ isActive }) => `group flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] font-medium transition ${isActive
                ? 'bg-teal text-white shadow-glow'
                : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'}`}>
              {({ isActive }) => (<>
                <n.icon size={16} className={isActive ? '' : 'text-slate-500 group-hover:text-slate-800'} />
                <span className="flex-1 truncate">{n.label}</span>
                {n.badge && badges[n.badge] > 0 && (
                  <span className={`min-w-[20px] rounded-full px-1.5 text-center text-[10px] font-semibold leading-[18px] tabular-nums ${isActive ? 'bg-white/25 text-white' : n.badge === 'todo' ? 'bg-violet-500 text-white' : 'bg-slate-200 text-slate-700'}`}>
                    {badges[n.badge] > 999 ? '999+' : badges[n.badge]}</span>)}
                {isActive && <ChevronRight size={14} className="opacity-80" />}
              </>)}
            </NavLink>))}
      </nav>
      <div className="space-y-2 border-t border-slate-100 p-3">
        <ClaudeLoginStatus compact />
        <div className="flex items-center gap-2.5 rounded-xl px-3 py-1.5 text-[13px] text-slate-600">
          {theme === 'dark' ? <Moon size={15} className="text-slate-500" /> : <Sun size={15} className="text-amber-500" />}
          <span className="flex-1">Dark mode</span>
          <Switch on={theme === 'dark'} label="Dark mode" onChange={(on) => setTheme(on ? 'dark' : 'light')} />
        </div>
        <div className="flex items-center gap-2.5 rounded-2xl bg-slate-50 px-2.5 py-2">
          <div className="relative grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-amber-300 to-orange-500 text-[11px] font-bold text-white">
            {initials}
            <span title={connected ? 'Live updates on' : 'Reconnecting…'} className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--app-base)] ${connected ? 'bg-emerald-400' : 'bg-rose-400'}`} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12px] font-semibold text-slate-800">{name || 'Your profile'}</div>
            <div className="truncate text-[11px] text-slate-500">{connected ? (me?.email || 'Live updates on') : 'Reconnecting…'}</div>
          </div>
        </div>
      </div>
    </aside>
  )
}

function Glow() {
  return <div className="app-glow" aria-hidden="true"><i className="g-blue" /><i className="g-cyan" /><i className="g-teal" /><i className="g-green" /><span className="orb" /></div>
}

function App() {
  const { pathname } = useLocation()
  useEffect(() => { document.getElementById('main-scroll')?.scrollTo(0, 0) }, [pathname])
  return (
    <div className="relative flex h-full">
      <Glow />
      <Sidebar />
      <main id="main-scroll" className="relative z-10 min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1720px] px-6 pb-10 pt-5">
          <ClaudeLoginBanner />
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/jobs" element={<JobBoard />} />
            <Route path="/jobs/:id" element={<JobBoard />} />
            <Route path="/build" element={<BuildQueue />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/tracker" element={<Tracker />} />
            <Route path="/apply" element={<Apply />} />
            <Route path="/details" element={<MyDetails />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/cv" element={<MyCV />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <EventsProvider>
          <ClaudeLoginProvider>
            <App />
          </ClaudeLoginProvider>
        </EventsProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
