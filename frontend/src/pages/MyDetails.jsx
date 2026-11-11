import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Pencil, Plus, Save, Trash2, X } from 'lucide-react'
import { api, fmtDate } from '../api'
import { useAsync, useToast } from '../ui'

const SECTIONS = [
  { title: 'Personal', fields: [
    ['first_name', 'First name'], ['last_name', 'Last name'], ['email', 'Email'], ['phone', 'Phone'],
    ['date_of_birth', 'Date of birth', 'Only used when a form asks'], ['nationality', 'Nationality'],
    ['gender', 'Gender', 'Empty = "prefer not to say" where a form offers it'], ['pronouns', 'Pronouns'],
  ] },
  { title: 'Address & links', fields: [
    ['street', 'Street and number'], ['postal_code', 'Postal code'], ['city', 'City'], ['country', 'Country'],
    ['linkedin', 'LinkedIn URL'], ['github', 'GitHub URL'], ['website', 'Website / portfolio'],
  ] },
  { title: 'Studies', fields: [
    ['university', 'University'], ['degree', 'Degree', 'e.g. M.Sc. Data Science'], ['field_of_study', 'Field of study'],
    ['study_start', 'Study start'], ['graduation', 'Expected graduation', 'e.g. 09/2027'],
    ['enrolled_student', 'Enrolled student?', 'Yes / No – matters for Werkstudent jobs'],
  ] },
  { title: 'Work permit & languages', fields: [
    ['work_authorized', 'Allowed to work in Germany?', 'Yes / No'], ['needs_sponsorship', 'Need visa sponsorship?', 'Yes / No'],
    ['work_permit', 'Work permit / residence status', 'e.g. Residence permit for study – working student allowed'],
    ['german_level', 'German level', 'A1 … C2 / native'], ['english_level', 'English level', 'A1 … C2 / native'],
    ['other_languages', 'Other languages'],
  ] },
  { title: 'Availability', fields: [
    ['earliest_start', 'Earliest start', 'e.g. immediately or 01.12.2026'], ['hours_per_week', 'Hours per week'],
    ['internship_duration', 'Internship duration', 'e.g. 6 months'], ['notice_period', 'Notice period'],
    ['willing_to_relocate', 'Willing to relocate?', 'Yes / No / within Germany'], ['remote_preference', 'Remote / hybrid / on-site'],
    ['drivers_license', "Driver's licence"], ['how_heard', '"How did you hear about us?"'],
  ] },
]
const SALARY = [
  ['working_student_hourly', 'Werkstudent – € per hour'], ['internship_monthly', 'Internship – € per month'],
  ['part_time_hourly', 'Part-time – € per hour'], ['full_time_yearly', 'Full-time – € per year'], ['note', 'Note', 'e.g. negotiable'],
]

function Input({ label, hint, value, onChange, missing }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1 text-xs font-semibold text-slate-600">{label}
        {missing && <span className="rounded bg-amber-100 px-1 text-[10px] font-semibold text-amber-800">often asked</span>}</span>
      <input className="input" value={value || ''} placeholder={hint || ''} onChange={(e) => onChange(e.target.value)} />
    </label>
  )
}

export default function MyDetails() {
  const data = useAsync(() => api.get('/api/apply/profile'))
  const answers = useAsync(() => api.get('/api/apply/answers'))
  const [p, setP] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [edit, setEdit] = useState(null)        // {id, answer}
  const [add, setAdd] = useState(null)          // {question, answer}
  const toast = useToast()
  useEffect(() => { if (data.data && !p) setP(data.data.profile) }, [data.data]) // eslint-disable-line
  if (!p) return <div className="muted">Loading…</div>
  const missing = data.data?.missing || []
  const set = (k) => (v) => { setP({ ...p, [k]: v }); setDirty(true) }
  const setSal = (k) => (v) => { setP({ ...p, salary: { ...p.salary, [k]: v } }); setDirty(true) }
  const save = async () => {
    try { const r = await api.put('/api/apply/profile', p); data.setData(r); setP(r.profile); setDirty(false); toast('Details saved') }
    catch (e) { toast(e.message, 'error') }
  }
  const labelMissing = (label) => missing.some((m) => label.toLowerCase().startsWith(m.toLowerCase().split(' ')[0]))

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="h1">My details</h1>
          <p className="mt-1 text-sm muted">Everything application forms ask. Filled in once, used for every application. Started from your main CV.</p>
        </div>
        <button className="btn-teal" disabled={!dirty} onClick={save}><Save size={14} />{dirty ? 'Save changes' : 'Saved'}</button>
      </div>
      {missing.length > 0 && (
        <div className="flex gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div>Most forms ask for these, still empty: <b>{missing.join(', ')}</b>. Empty fields are left for you on every form.</div>
        </div>)}

      <div className="grid gap-5 lg:grid-cols-2">
        {SECTIONS.map((sec) => (
          <div key={sec.title} className="glass rounded-2xl p-5">
            <div className="mb-3 text-sm font-semibold text-navy">{sec.title}</div>
            <div className="grid gap-3 sm:grid-cols-2">
              {sec.fields.map(([k, label, hint]) => <Input key={k} label={label} hint={hint} value={p[k]} onChange={set(k)} missing={!p[k] && labelMissing(label)} />)}
            </div>
          </div>))}
        <div className="glass rounded-2xl p-5">
          <div className="mb-1 text-sm font-semibold text-navy">Expected salary</div>
          <p className="mb-3 text-xs muted">Numbers only. The app converts to what a form asks (hour / month / year, using your hours per week). Empty = salary questions are left for you.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {SALARY.map(([k, label, hint]) => <Input key={k} label={label} hint={hint} value={p.salary?.[k]} onChange={setSal(k)} />)}
          </div>
        </div>
      </div>

      <div className="glass rounded-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <div><div className="text-sm font-semibold text-navy">Saved answers ({answers.data?.length || 0})</div>
            <div className="text-xs muted">Every question you answered on a form. The same question on another form is filled with your answer.</div></div>
          <button className="btn-outline" onClick={() => setAdd({ question: '', answer: '' })}><Plus size={14} />Add answer</button>
        </div>
        {add && (
          <div className="grid gap-2 border-b border-slate-100 bg-slate-50 px-5 py-3 md:grid-cols-[1fr_1fr_auto]">
            <input className="input" placeholder="Question, e.g. Why do you want to work with us?" value={add.question} onChange={(e) => setAdd({ ...add, question: e.target.value })} />
            <input className="input" placeholder="Your answer" value={add.answer} onChange={(e) => setAdd({ ...add, answer: e.target.value })} />
            <div className="flex gap-1">
              <button className="btn-teal" disabled={!add.question || !add.answer} onClick={async () => { await api.post('/api/apply/answers', add); setAdd(null); answers.reload() }}><Check size={14} /></button>
              <button className="btn-ghost" onClick={() => setAdd(null)}><X size={14} /></button></div>
          </div>)}
        {!answers.data?.length ? <div className="px-5 py-6 text-sm muted">None yet – they appear after your first applications.</div> : (
          <div className="divide-y divide-slate-100">
            {answers.data.map((a) => (
              <div key={a.id} className="grid gap-3 px-5 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_auto] md:items-start">
                <div className="text-sm font-medium text-slate-700">{a.question}
                  <div className="mt-0.5 text-xs font-normal text-slate-400">{a.source === 'claude' ? 'Claude draft you kept' : 'your answer'} · used {a.uses}× · {fmtDate(a.updated_at)}</div></div>
                {edit?.id === a.id
                  ? <textarea className="input h-20" value={edit.answer} onChange={(e) => setEdit({ ...edit, answer: e.target.value })} />
                  : <div className="whitespace-pre-wrap text-sm text-slate-600">{a.answer}</div>}
                <div className="flex gap-1">
                  {edit?.id === a.id
                    ? <><button className="btn-teal" title="Save" onClick={async () => { await api.put(`/api/apply/answers/${a.id}`, { answer: edit.answer }); setEdit(null); answers.reload() }}><Check size={14} /></button>
                      <button className="btn-ghost" onClick={() => setEdit(null)}><X size={14} /></button></>
                    : <><button className="btn-ghost" title="Edit" onClick={() => setEdit({ id: a.id, answer: a.answer })}><Pencil size={14} /></button>
                      <button className="btn-ghost text-rose-600" title="Delete this answer" onClick={async () => { await api.del(`/api/apply/answers/${a.id}`); answers.reload() }}><Trash2 size={14} /></button></>}
                </div>
              </div>))}
          </div>)}
      </div>
    </div>
  )
}
