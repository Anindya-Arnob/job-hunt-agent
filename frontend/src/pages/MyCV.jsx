import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Check, Download, GitCompare, History, Plus, Star, Trash2, Undo2, Upload, Pencil, Palette } from 'lucide-react'
import { api, fileUrl, fmtDate } from '../api'
import { Empty, Modal, PdfPages, useAsync, useToast } from '../ui'

const dot = (a) => (a || []).join(' · ')
const undot = (s) => s.split(/\s*[·•;]\s*/).map((x) => x.trim()).filter(Boolean)
const lines = (a) => (a || []).join('\n')
const unlines = (s) => s.split('\n').map((x) => x.trim()).filter(Boolean)

export default function MyCV() {
  const cv = useAsync(() => api.get('/api/cv'))
  const toast = useToast()
  const fileRef = useRef()
  const tplRef = useRef()
  const [review, setReview] = useState(null)      // {draft, sourcePdf, name, make_main}
  const [editing, setEditing] = useState(null)    // {pid, profile, note}
  const [versionsOf, setVersionsOf] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [delCv, setDelCv] = useState(null)

  const upload = async (file) => {
    if (!file) return
    setUploading(true)
    try {
      const draft = await api.upload('/api/cv/upload', file)
      const src = await api.post('/api/cv/source-pdf', { source_file: draft.source_file })
      setReview({ draft, profile: draft.profile, sourcePdf: src.pdf_path, name: file.name.replace(/\.(docx|pdf)$/i, ''), make_main: true })
    } catch (e) { toast(e.message, 'error') }
    setUploading(false)
  }
  const confirmUpload = async () => {
    try {
      await api.post('/api/cv/confirm', { profile: review.profile, source_file: review.draft.source_file, name: review.name, make_main: review.make_main })
      toast(review.make_main ? 'Saved as your main CV' : 'CV saved'); setReview(null); cv.reload(); askRescore()
    } catch (e) { toast(e.message, 'error') }
  }
  const askRescore = () => { if (confirm('Your main CV changed. Re-score the jobs on your board now?')) api.post('/api/runs/rescore', { scope: 'all' }).then((r) => toast(`Re-scored ${r.rescored} jobs`)) }
  const makeMain = async (pid) => { await api.post(`/api/cv/${pid}/main`); toast('Main CV changed'); cv.reload(); askRescore() }
  const del = (p) => { if (p.is_main) { toast('This is your main CV. Make another CV the main one first.', 'warn'); return } setDelCv(p) }
  const doDelete = async (permanent) => {
    try {
      await api.del(permanent ? `/api/cv/${delCv.id}/permanent` : `/api/cv/${delCv.id}`)
      toast(permanent ? `“${delCv.name}” deleted` : `“${delCv.name}” moved to the archive`); setDelCv(null); cv.reload()
    } catch (e) { toast(e.message, 'error') }
  }
  const deleteNow = async (p) => {
    if (!confirm(`Delete “${p.name}” and all its versions now? This can't be undone.`)) return
    try { await api.del(`/api/cv/${p.id}/permanent`); toast('Deleted'); cv.reload() } catch (e) { toast(e.message, 'error') }
  }
  const rename = async (p) => { const n = prompt('New name', p.name); if (n) { await api.post(`/api/cv/${p.id}/rename`, { name: n }); cv.reload() } }
  const startEdit = async (pid) => { const r = await api.get(`/api/cv/${pid}`); setEditing({ pid, profile: r.profile, note: '' }) }
  const saveEdit = async () => {
    try { await api.put(`/api/cv/${editing.pid}`, { profile: editing.profile, note: editing.note || 'Edited' }); toast('New version saved'); setEditing(null); cv.reload()
      const p = (cv.data?.profiles || []).find((x) => x.id === editing.pid); if (p?.is_main) askRescore() }
    catch (e) { toast(e.message, 'error') }
  }
  const uploadTemplate = async (f) => { if (!f) return; const r = await api.upload('/api/cv/template', f); toast('Template saved'); cv.setData({ ...cv.data, style: r.style }) }

  if (!cv.data) return <div className="muted">Loading…</div>
  const active = cv.data.profiles.filter((p) => !p.archived_at)
  const archived = cv.data.profiles.filter((p) => p.archived_at)
  const st = cv.data.style

  if (editing) return <Editor editing={editing} setEditing={setEditing} onSave={saveEdit} />

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="h1">My CV</h1>
        <div className="flex gap-2">
          <input ref={fileRef} type="file" accept=".docx,.pdf" className="hidden" onChange={(e) => { upload(e.target.files[0]); e.target.value = '' }} />
          <button className="btn-teal" disabled={uploading} onClick={() => fileRef.current.click()}><Upload size={14} />{uploading ? 'Reading…' : active.length ? 'Upload new CV' : 'Upload your CV'}</button>
        </div>
      </div>

      {!active.length && (
        <div className="card border-2 border-dashed p-10 text-center" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files[0]) }}>
          <Upload className="mx-auto mb-2 text-slate-400" /><div className="font-semibold">Drop your CV here (.docx works best, .pdf is fine)</div>
          <div className="text-sm muted">The app reads it into sections; you check everything before it is saved.</div>
        </div>)}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {active.map((p) => (
          <div key={p.id} className={`card p-4 ${p.is_main ? 'ring-2 ring-teal' : ''}`}>
            <div className="flex items-center gap-2"><div className="flex-1 font-semibold text-navy">{p.name}</div>
              {p.is_main && <span className="chip bg-teal text-white"><Star size={12} className="mr-1" />Main</span>}</div>
            <div className="mt-1 text-xs muted">v{p.versions[0]?.version} · {p.versions[0]?.note} · {fmtDate(p.versions[0]?.created_at)}</div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <button className="btn-primary" onClick={() => startEdit(p.id)}><Pencil size={14} />Edit</button>
              {!p.is_main && <button className="btn-outline" onClick={() => makeMain(p.id)}><Star size={14} />Make main</button>}
              <button className="btn-outline" onClick={() => setVersionsOf(p)}><History size={14} />Versions ({p.versions.length})</button>
              <button className="btn-ghost" onClick={() => rename(p)}>Rename</button>
              <button className="btn-danger" title={p.is_main ? 'Make another CV main first' : 'Delete'} onClick={() => del(p)}><Trash2 size={14} /></button>
            </div>
          </div>))}
      </div>

      <div className="card">
        <div className="card-h"><span className="flex items-center gap-2"><Palette size={16} />Layout template</span>
          <span><input ref={tplRef} type="file" accept=".docx" className="hidden" onChange={(e) => uploadTemplate(e.target.files[0])} />
            <button className="btn-outline" onClick={() => tplRef.current.click()}><Upload size={14} />Upload template</button></span></div>
        <div className="flex flex-wrap items-center gap-4 p-4 text-sm">
          {[['Header band', st.navy], ['Heading rule', st.teal], ['Text', st.text], ['Links', st.link]].map(([l, c]) => (
            <div key={l} className="flex items-center gap-2"><span className="h-6 w-6 rounded border" style={{ background: `#${c}` }} />{l}</div>))}
          <div className="muted">Font {st.font} · headings {st.heading_pt} pt · body {st.body_pt} pt. Your content and the layout are stored separately, so a new CV never breaks the formatting.</div>
        </div>
      </div>

      {archived.length > 0 && (
        <div className="card">
          <div className="card-h">Archive</div>
          {archived.map((p) => (
            <div key={p.id} className="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-sm">
              <span>{p.name} <span className="muted">· deleted for good on {fmtDate(p.delete_after)}</span></span>
              <span className="flex gap-2">
                <button className="btn-outline" onClick={async () => { await api.post(`/api/cv/${p.id}/restore`); cv.reload() }}><Undo2 size={14} />Restore</button>
                <button className="btn-danger" onClick={() => deleteNow(p)}><Trash2 size={14} />Delete now</button>
              </span>
            </div>))}
        </div>)}

      {review && (
        <Modal open wide title="Check what was read from your CV" onClose={() => setReview(null)}
          footer={<>
            <label className="mr-auto flex items-center gap-2 text-sm"><span className="shrink-0 muted">Name</span>
              <input className="input w-72" value={review.name} onChange={(e) => setReview({ ...review, name: e.target.value })} /></label>
            <label className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-sm"><input type="checkbox" checked={review.make_main} onChange={(e) => setReview({ ...review, make_main: e.target.checked })} />Make this my main CV</label>
            <button className="btn-outline" onClick={() => setReview(null)}>Cancel</button>
            <button className="btn-teal" onClick={confirmUpload}><Check size={14} />Confirm & save</button></>}>
          {review.profile.uncertain_fields?.length > 0 && <div className="mb-3 rounded-md bg-amber-50 p-2 text-sm text-amber-900">Please check: {review.profile.uncertain_fields.join(', ')}</div>}
          <div className="grid h-[68vh] grid-cols-2 gap-3 overflow-hidden">
            <div className="flex min-h-0 flex-col overflow-hidden"><span className="label">Your original</span>
              {review.sourcePdf ? <PdfPages path={review.sourcePdf} className="rounded border" /> : <div className="muted">Preview not available</div>}</div>
            <div className="flex min-h-0 flex-col overflow-hidden"><span className="label">What the app read (edit anything that's wrong)</span>
              <div className="min-h-0 flex-1 overflow-y-auto pr-1"><ProfileForm value={review.profile} onChange={(p) => setReview({ ...review, profile: p })} uncertain={review.profile.uncertain_fields || []} /></div></div>
          </div>
        </Modal>)}

      {versionsOf && <Versions profile={versionsOf} onClose={() => setVersionsOf(null)} onChanged={cv.reload} />}
      {delCv && (
        <Modal open title={`Delete “${delCv.name}”?`} onClose={() => setDelCv(null)}
          footer={<><button className="btn-outline" onClick={() => setDelCv(null)}>Cancel</button>
            <button className="btn-outline" onClick={() => doDelete(false)}>Move to archive</button>
            <button className="btn bg-rose-600 text-white hover:bg-rose-700" onClick={() => doDelete(true)}><Trash2 size={14} />Delete permanently</button></>}>
          <p className="text-sm"><b>Move to archive</b> keeps it for 30 days so you can restore it. <b>Delete permanently</b> removes this CV, all {delCv.versions.length} version(s) and the uploaded file now.</p>
          <p className="mt-2 text-xs muted">Tailored CVs already built from it are not touched.</p>
        </Modal>)}
    </div>
  )
}

// ------------------------------------------------------------- editor ---
function Editor({ editing, setEditing, onSave }) {
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef()
  useEffect(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      setBusy(true)
      try { setPreview(await api.post('/api/cv/preview', { profile: editing.profile })) } catch { /* ignore */ }
      setBusy(false)
    }, 900)
    return () => clearTimeout(timer.current)
  }, [editing.profile])
  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-3">
      <div className="flex items-center gap-2">
        <h1 className="h1 mr-auto">Edit CV</h1>
        <input className="input w-72" placeholder="What changed? e.g. added LangGraph project" value={editing.note} onChange={(e) => setEditing({ ...editing, note: e.target.value })} />
        {preview?.docx_path && <a className="btn-outline" href={fileUrl(preview.docx_path, true)}><Download size={14} />.docx</a>}
        {preview?.pdf_path && <a className="btn-outline" href={fileUrl(preview.pdf_path, true)}><Download size={14} />.pdf</a>}
        <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
        <button className="btn-teal" onClick={onSave}><Check size={14} />Save as new version</button>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-4">
        <div className="overflow-y-auto pr-2"><ProfileForm value={editing.profile} onChange={(p) => setEditing({ ...editing, profile: p })} uncertain={[]} /></div>
        <div className="card flex flex-col overflow-hidden">
          <div className="card-h text-sm">Live preview {busy ? <span className="muted text-xs">updating…</span> : preview?.pages && <span className="text-xs font-normal muted">{preview.pages} page{preview.pages > 1 ? 's' : ''} (full CV; tailored CVs are cut to 1 page)</span>}</div>
          {preview?.pdf_path ? <PdfPages path={preview.pdf_path} /> : <div className="p-4 muted">{preview?.error || 'Rendering…'}</div>}
        </div>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, warn, area, rows = 3, mono }) {
  const cls = `input ${warn ? 'border-amber-400 bg-amber-50' : ''} ${mono ? 'font-mono text-xs' : ''}`
  return (
    <label className="block"><span className="label">{label}</span>
      {area ? <textarea className={cls} rows={rows} value={value || ''} onChange={(e) => onChange(e.target.value)} />
        : <input className={cls} value={value || ''} onChange={(e) => onChange(e.target.value)} />}</label>
  )
}

function ListSection({ title, items, onChange, render, blank }) {
  const move = (i, d) => { const a = [...items]; const [x] = a.splice(i, 1); a.splice(i + d, 0, x); onChange(a) }
  return (
    <div className="card p-3">
      <div className="mb-2 flex items-center justify-between"><span className="font-semibold text-navy">{title}</span>
        <button className="btn-ghost" onClick={() => onChange([...items, structuredClone(blank)])}><Plus size={14} />Add</button></div>
      <div className="space-y-3">
        {items.map((it, i) => (
          <div key={i} className="rounded-md border border-slate-200 p-2.5">
            <div className="mb-1 flex justify-end gap-1">
              <button className="btn-ghost p-1" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={13} /></button>
              <button className="btn-ghost p-1" disabled={i === items.length - 1} onClick={() => move(i, 1)}><ArrowDown size={13} /></button>
              <button className="btn-ghost p-1 text-rose-600" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={13} /></button>
            </div>
            {render(it, (patch) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x))), i)}
          </div>))}
      </div>
    </div>
  )
}

export function ProfileForm({ value: p, onChange, uncertain }) {
  const set = (patch) => onChange({ ...p, ...patch })
  const w = (path) => uncertain.some((u) => u.startsWith(path))
  return (
    <div className="space-y-3">
      <div className="card grid gap-2 p-3 md:grid-cols-2">
        <Field label="Name" value={p.name} onChange={(v) => set({ name: v })} />
        <Field label="Tagline" value={p.tagline} onChange={(v) => set({ tagline: v })} />
        {['location', 'phone', 'email', 'linkedin', 'github'].map((k) => (
          <Field key={k} label={k} value={p.contact?.[k]} onChange={(v) => set({ contact: { ...p.contact, [k]: v } })} />))}
        <div className="md:col-span-2"><Field label="Summary" area rows={4} value={p.summary} onChange={(v) => set({ summary: v })} warn={w('summary')} /></div>
      </div>
      <ListSection title="Skills" items={p.skills} blank={{ category: '', items: [] }} onChange={(v) => set({ skills: v })}
        render={(g, up) => <div className="grid grid-cols-3 gap-2"><Field label="Category" value={g.category} onChange={(v) => up({ category: v })} />
          <div className="col-span-2"><Field label="Items (· separated)" value={dot(g.items)} onChange={(v) => up({ items: undot(v) })} /></div></div>} />
      <ListSection title="Experience" items={p.experience} blank={{ title: '', org: '', meta: '', dates: '', stack: [], bullets: [] }} onChange={(v) => set({ experience: v })}
        render={(e, up, i) => <div className="grid grid-cols-2 gap-2">
          <Field label="Title" value={e.title} onChange={(v) => up({ title: v })} /><Field label="Organisation" value={e.org} onChange={(v) => up({ org: v })} />
          <Field label="Type · place" value={e.meta} onChange={(v) => up({ meta: v })} /><Field label="Dates" value={e.dates} onChange={(v) => up({ dates: v })} warn={w(`experience[${i}].dates`)} />
          <div className="col-span-2"><Field label="Stack (· separated)" value={dot(e.stack)} onChange={(v) => up({ stack: undot(v) })} /></div>
          <div className="col-span-2"><Field label="Bullets (one per line)" area rows={4} value={lines(e.bullets)} onChange={(v) => up({ bullets: unlines(v) })} /></div></div>} />
      <ListSection title="Projects" items={p.projects} blank={{ name: '', link: '', note: '', dates: '', stack: [], bullets: [] }} onChange={(v) => set({ projects: v })}
        render={(e, up, i) => <div className="grid grid-cols-2 gap-2">
          <Field label="Name" value={e.name} onChange={(v) => up({ name: v })} /><Field label="Link" value={e.link} onChange={(v) => up({ link: v })} />
          <Field label="Note e.g. (Ongoing)" value={e.note} onChange={(v) => up({ note: v })} /><Field label="Dates" value={e.dates} onChange={(v) => up({ dates: v })} warn={w(`projects[${i}].dates`)} />
          <div className="col-span-2"><Field label="Stack (· separated)" value={dot(e.stack)} onChange={(v) => up({ stack: undot(v) })} /></div>
          <div className="col-span-2"><Field label="Bullets (one per line)" area rows={4} value={lines(e.bullets)} onChange={(v) => up({ bullets: unlines(v) })} /></div></div>} />
      <ListSection title="Education" items={p.education} blank={{ degree: '', school: '', dates: '', bullets: [] }} onChange={(v) => set({ education: v })}
        render={(e, up) => <div className="grid grid-cols-2 gap-2">
          <Field label="Degree" value={e.degree} onChange={(v) => up({ degree: v })} /><Field label="School" value={e.school} onChange={(v) => up({ school: v })} />
          <Field label="Dates" value={e.dates} onChange={(v) => up({ dates: v })} /><div />
          <div className="col-span-2"><Field label="Bullets (one per line)" area rows={2} value={lines(e.bullets)} onChange={(v) => up({ bullets: unlines(v) })} /></div></div>} />
      <ListSection title="Certifications" items={p.certifications} blank={{ name: '', issuer: '', dates: '', link: '', bullets: [] }} onChange={(v) => set({ certifications: v })}
        render={(e, up) => <div className="grid grid-cols-2 gap-2">
          <Field label="Name" value={e.name} onChange={(v) => up({ name: v })} /><Field label="Issuer" value={e.issuer} onChange={(v) => up({ issuer: v })} />
          <Field label="Dates" value={e.dates} onChange={(v) => up({ dates: v })} /><Field label="Link" value={e.link} onChange={(v) => up({ link: v })} />
          <div className="col-span-2"><Field label="Bullets" area rows={2} value={lines(e.bullets)} onChange={(v) => up({ bullets: unlines(v) })} /></div></div>} />
      <ListSection title="Languages" items={p.languages} blank={{ language: '', level: '' }} onChange={(v) => set({ languages: v })}
        render={(e, up) => <div className="grid grid-cols-2 gap-2"><Field label="Language" value={e.language} onChange={(v) => up({ language: v })} />
          <Field label="Level e.g. Elementary (A2)" value={e.level} onChange={(v) => up({ level: v })} /></div>} />
    </div>
  )
}

function Versions({ profile, onClose, onChanged }) {
  const toast = useToast()
  const [a, setA] = useState(profile.versions[1]?.id || null)
  const [b, setB] = useState(profile.versions[0]?.id || null)
  const [diff, setDiff] = useState(null)
  const compare = async () => setDiff(await api.get(`/api/cv/compare/${a}/${b}`))
  const restore = async (vid) => { await api.post(`/api/cv/versions/${vid}/restore`); toast('Restored as a new version'); onChanged(); onClose() }
  const removeVersion = async (v) => {
    if (!confirm(`Delete version v${v.version}? This can't be undone.`)) return
    try { await api.del(`/api/cv/versions/${v.id}`); toast(`v${v.version} deleted`); onChanged(); onClose() } catch (e) { toast(e.message, 'error') }
  }
  return (
    <Modal open wide title={`Versions – ${profile.name}`} onClose={onClose}>
      <table className="mb-4 w-full text-sm"><tbody>
        {profile.versions.map((v) => (
          <tr key={v.id} className="border-b border-slate-100">
            <td className="py-1.5 font-semibold">v{v.version}</td><td>{v.note}</td><td className="muted">{fmtDate(v.created_at)}</td><td className="muted">{v.source_file}</td>
            <td><label className="mr-2 text-xs"><input type="radio" checked={a === v.id} onChange={() => setA(v.id)} /> A</label>
              <label className="text-xs"><input type="radio" checked={b === v.id} onChange={() => setB(v.id)} /> B</label></td>
            <td className="text-right">{v.id !== profile.versions[0].id ? (
              <span className="inline-flex gap-1"><button className="btn-outline" onClick={() => restore(v.id)}><Undo2 size={13} />Restore</button>
                <button className="btn-danger" title="Delete this version" onClick={() => removeVersion(v)}><Trash2 size={13} /></button></span>)
              : <span className="text-xs muted">current</span>}</td>
          </tr>))}
      </tbody></table>
      <button className="btn-primary" disabled={!a || !b || a === b} onClick={compare}><GitCompare size={14} />Compare A → B</button>
      {diff && (diff.length === 0 ? <Empty title="No differences" /> : (
        <div className="mt-3 space-y-3">{diff.map((d) => (
          <div key={d.section}><div className="label">{d.section}</div>
            <div className="rounded-md bg-slate-50 p-2 font-mono text-xs">{d.changes.map((c, i) => <div key={i} className={c.startsWith('+') ? 'text-emerald-700' : 'text-rose-700'}>{c}</div>)}</div></div>))}</div>))}
    </Modal>
  )
}
