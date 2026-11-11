import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Download, FolderOpen, Pencil, Save, Trash2 } from 'lucide-react'
import { api, fileUrl, fmtDate } from '../api'
import { CheckList, Empty, Modal, PdfPages, useAsync, useToast } from '../ui'

export default function Documents() {
  const [params, setParams] = useSearchParams()
  const docs = useAsync(() => api.get('/api/documents'))
  const cv = useAsync(() => api.get('/api/cv'))
  const [bust, setBust] = useState(Date.now())
  const [view, setView] = useState('letter')   // right pane: letter | main
  const [mainPdf, setMainPdf] = useState(null)
  const [edit, setEdit] = useState(null)
  const toast = useToast()

  const groups = useMemo(() => {
    const m = {}
    for (const d of docs.data || []) (m[d.job_id] = m[d.job_id] || { job_id: d.job_id, company: d.company, title: d.title, built: d.built_at, docs: {} }).docs[d.kind] = d
    return Object.values(m).sort((a, b) => b.built.localeCompare(a.built))
  }, [docs.data])
  const jobId = +(params.get('job') || groups[0]?.job_id || 0)
  const g = groups.find((x) => x.job_id === jobId)

  useEffect(() => {
    if (view === 'main' && cv.data?.main && !mainPdf) api.post('/api/cv/preview', { profile: cv.data.main }).then((r) => setMainPdf(r.pdf_path))
  }, [view, cv.data]) // eslint-disable-line

  const openFolder = async (p) => { const r = await api.post('/api/files/open-folder', { path: p }); if (!r.ok) toast(`Folder: ${r.path}`) }
  const removeDocs = async (x) => {
    if (!confirm(`Delete the tailored CV and cover letter for ${x.company}? The .docx and .pdf files are removed from your Claude CV folder. The job stays on the board, so you can build again.`)) return
    try { const r = await api.del(`/api/documents/job/${x.job_id}?delete_files=true`); toast(`Deleted ${r.files_deleted} file(s)`); setParams({}); docs.reload() }
    catch (e) { toast(e.message, 'error') }
  }
  const save = async () => {
    try {
      const r = await api.put(`/api/documents/${edit.doc.id}`, { content: edit.content })
      toast(r.qa_passed ? 'Saved – all checks passed' : 'Saved – some checks failed', r.qa_passed ? 'info' : 'warn')
      setEdit(null); await docs.reload(); setBust(Date.now())
    } catch (e) { toast(e.message, 'error') }
  }

  if (!docs.data) return <div className="muted">Loading…</div>
  if (!groups.length) return <div><h1 className="h1 mb-4">Documents</h1><div className="card"><Empty title="No CVs built yet">Pick jobs on the job board or build queue.</Empty></div></div>

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-4">
      <div className="card w-72 shrink-0 overflow-y-auto">
        <div className="card-h">Documents ({groups.length})</div>
        {groups.map((x) => (
          <button key={x.job_id} onClick={() => setParams({ job: x.job_id })}
            className={`block w-full border-b border-slate-100 px-4 py-2.5 text-left text-sm ${x.job_id === jobId ? 'bg-teal-50' : 'hover:bg-slate-50'}`}>
            <div className="font-medium">{x.company}</div><div className="truncate text-xs muted">{x.title}</div>
            <div className="mt-0.5 text-xs">{Object.values(x.docs).some((d) => d.imported) ? <span className="text-slate-500">earlier build</span>
              : Object.values(x.docs).every((d) => d.qa_passed) ? <span className="text-emerald-700">✓ checks passed</span> : <span className="text-amber-700">⚠ needs attention</span>}
              {Object.values(x.docs).some((d) => d.outdated) && <span className="text-amber-700"> · older CV</span>}</div>
          </button>))}
      </div>
      {g && (
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="mr-auto"><div className="h1">{g.company}</div><div className="text-sm muted">{g.title} · built {fmtDate(g.built)}</div></div>
            {g.docs.cv && !g.docs.cv.imported && <button className="btn-outline" onClick={() => setEdit({ doc: g.docs.cv, content: structuredClone(g.docs.cv.content) })}><Pencil size={14} />Edit CV</button>}
            {g.docs.letter && !g.docs.letter.imported && <button className="btn-outline" onClick={() => setEdit({ doc: g.docs.letter, content: structuredClone(g.docs.letter.content) })}><Pencil size={14} />Edit letter</button>}
            {g.docs.cv && <DownloadPair label="CV" doc={g.docs.cv} />}
            {g.docs.letter && <DownloadPair label="Letter" doc={g.docs.letter} />}
            {g.docs.cv && <button className="btn-outline" onClick={() => openFolder(g.docs.cv.pdf_path)}><FolderOpen size={14} />Folder</button>}
            <button className="btn-danger" title="Delete this CV and letter" onClick={() => removeDocs(g)}><Trash2 size={14} />Delete</button>
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
            <div className="card flex flex-col overflow-hidden">
              <div className="card-h text-sm">Tailored CV</div>
              {g.docs.cv && <PdfPages path={g.docs.cv.pdf_path} bust={bust} />}
              {g.docs.cv && <div className="max-h-40 overflow-y-auto border-t border-slate-100 p-3">{g.docs.cv.imported
                ? <p className="text-sm muted">Built earlier, before this app (found in your Claude CV folder). Checks weren't run. Use Rebuild on the job board to make a checked, editable version.</p>
                : <CheckList checks={g.docs.cv.qa_results} />}</div>}
            </div>
            <div className="card flex flex-col overflow-hidden">
              <div className="card-h text-sm">
                <div className="flex gap-1">
                  <button className={`btn ${view === 'letter' ? 'bg-teal text-white shadow-glow' : 'btn-ghost'}`} onClick={() => setView('letter')}>Cover letter</button>
                  <button className={`btn ${view === 'main' ? 'bg-teal text-white shadow-glow' : 'btn-ghost'}`} onClick={() => setView('main')}>Your main CV</button>
                </div></div>
              {view === 'letter' && g.docs.letter && <PdfPages path={g.docs.letter.pdf_path} bust={bust} />}
              {view === 'main' && (mainPdf ? <PdfPages path={mainPdf} /> : <div className="p-4 muted">Rendering…</div>)}
              {view === 'letter' && g.docs.letter && <div className="max-h-40 overflow-y-auto border-t border-slate-100 p-3"><CheckList checks={g.docs.letter.qa_results} />
                {g.docs.letter.blockers_stated?.length > 0 && <div className="mt-1 text-xs text-amber-800">Gaps named honestly: {g.docs.letter.blockers_stated.join('; ')}</div>}</div>}
            </div>
          </div>
        </div>)}
      {edit && <EditModal edit={edit} setEdit={setEdit} onSave={save} />}
    </div>
  )
}

function DownloadPair({ label, doc }) {
  return (
    <span className="inline-flex overflow-hidden rounded-full border border-slate-200 bg-slate-50 p-0.5 text-sm">
      <span className="flex items-center gap-1.5 border-r border-slate-200 px-2.5 text-slate-600"><Download size={14} />{label}</span>
      <a className="px-2.5 py-1.5 font-medium hover:bg-slate-50" href={fileUrl(doc.pdf_path, true)} title={`Download ${label} as PDF`}>PDF</a>
      <a className="border-l border-slate-200 px-2.5 py-1.5 font-medium hover:bg-slate-50" href={fileUrl(doc.docx_path, true)} title={`Download ${label} as Word`}>Word</a>
    </span>
  )
}

const lines = (a) => (a || []).join('\n')
const unlines = (s) => s.split('\n').map((x) => x.trim()).filter(Boolean)

function EditModal({ edit, setEdit, onSave }) {
  const c = edit.content
  const upd = (fn) => { const n = structuredClone(c); fn(n); setEdit({ ...edit, content: n }) }
  const isCV = edit.doc.kind === 'cv'
  return (
    <Modal open wide title={isCV ? 'Edit tailored CV' : 'Edit cover letter'} onClose={() => setEdit(null)}
      footer={<><button className="btn-outline" onClick={() => setEdit(null)}>Cancel</button><button className="btn-teal" onClick={onSave}><Save size={14} />Save & re-check</button></>}>
      {isCV ? (
        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2"><span className="label">Tagline</span><input className="input" value={c.tagline} onChange={(e) => upd((n) => { n.tagline = e.target.value })} /></div>
          <div className="md:col-span-2"><span className="label">Summary</span><textarea className="input h-20" value={c.summary} onChange={(e) => upd((n) => { n.summary = e.target.value })} /></div>
          <div className="md:col-span-2"><span className="label">Skills (one row per line: Category: item · item)</span>
            <textarea className="input h-32 font-mono text-xs" value={c.skills.map((g) => `${g.category}: ${g.items.join(' · ')}`).join('\n')}
              onChange={(e) => upd((n) => { n.skills = unlines(e.target.value).map((l) => { const [cat, ...rest] = l.split(':'); return { category: cat.trim(), items: rest.join(':').split('·').map((x) => x.trim()).filter(Boolean) } }) })} /></div>
          {c.experience.map((x, i) => (
            <div key={`e${i}`} className="md:col-span-2"><span className="label">{x.title} – {x.org} (one bullet per line)</span>
              <textarea className="input h-24" value={lines(x.bullets)} onChange={(e) => upd((n) => { n.experience[i].bullets = unlines(e.target.value) })} /></div>))}
          {c.projects.map((x, i) => (
            <div key={`p${i}`}><span className="label">{x.name}</span>
              <textarea className="input h-28" value={lines(x.bullets)} onChange={(e) => upd((n) => { n.projects[i].bullets = unlines(e.target.value) })} /></div>))}
          <p className="text-xs muted md:col-span-2">Only tools and numbers from your main CV pass the checks. Saving re-renders the .docx and .pdf in place.</p>
        </div>
      ) : (
        <div className="space-y-3">
          <div><span className="label">Subject</span><input className="input" value={c.subject} onChange={(e) => upd((n) => { n.subject = e.target.value })} /></div>
          <div><span className="label">Greeting</span><input className="input" value={c.greeting} onChange={(e) => upd((n) => { n.greeting = e.target.value })} /></div>
          {c.paragraphs.map((p, i) => <div key={i}><span className="label">Paragraph {i + 1}</span>
            <textarea className="input h-24" value={p} onChange={(e) => upd((n) => { n.paragraphs[i] = e.target.value })} /></div>)}
        </div>
      )}
    </Modal>
  )
}
