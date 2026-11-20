import { useRef, useState } from 'react'
import { Download, ExternalLink, FileText, Image as ImageIcon, Trash2, Upload } from 'lucide-react'
import { api } from '../api'
import { Empty, useAsync, useToast } from '../ui'

// Your certificates and other papers, uploaded once. When you apply, the Chrome helper puts them into the form's
// upload fields, and Apply can merge them into one PDF.
export default function MyDocuments() {
  const docs = useAsync(() => api.get('/api/attachments'))
  const toast = useToast()
  const ref = useRef()
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)

  const upload = async (files) => {
    if (!files?.length) return
    setBusy(true)
    let n = 0
    for (const f of files) {
      try { await api.upload('/api/attachments', f); n++ } catch (e) { toast(`${f.name}: ${e.message}`, 'error') }
    }
    setBusy(false)
    if (n) { toast(n === 1 ? 'Document added' : `${n} documents added`); docs.reload() }
  }
  const save = async (d, patch) => {
    try {
      const r = await api.put(`/api/attachments/${d.id}`, patch)
      docs.setData({ ...docs.data, items: docs.data.items.map((x) => (x.id === d.id ? r : x)) })
    } catch (e) { toast(e.message, 'error') }
  }
  const remove = async (d) => {
    if (!confirm(`Delete “${d.name}” from the app? The file on your PC is not touched.`)) return
    await api.del(`/api/attachments/${d.id}`); toast('Deleted'); docs.reload()
  }

  if (!docs.data) return <div className="muted">Loading…</div>
  const { items, kinds } = docs.data
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div><h1 className="h1">My documents</h1>
          <p className="text-sm muted">Certificates, transcripts, references: add them once, then they are ready whenever you apply.</p></div>
        <span><input ref={ref} type="file" multiple accept=".pdf,.jpg,.jpeg,.png" className="hidden" data-testid="docs-input"
          onChange={(e) => { upload([...e.target.files]); e.target.value = '' }} />
          <button className="btn-teal" disabled={busy} onClick={() => ref.current.click()}><Upload size={14} />{busy ? 'Adding…' : 'Add documents'}</button></span>
      </div>

      <div className={`card border-2 border-dashed p-6 text-center text-sm ${drag ? 'border-teal bg-teal/5' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload([...e.dataTransfer.files]) }}>
        <Upload className="mx-auto mb-1 text-slate-400" size={20} />
        Drop PDF, JPG or PNG files here (several at once is fine). <span className="muted">The type is guessed from the file name; change it below if needed.</span>
      </div>

      {!items.length ? (
        <Empty icon={FileText} title="No documents yet">Add your internship and work references, degree certificate, transcript of records, enrolment certificate and language certificates.</Empty>
      ) : (
        <div className="card divide-y divide-slate-100" data-testid="docs-list">
          {items.map((d) => (
            <div key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
              <a href={`/api/attachments/${d.id}/file`} target="_blank" rel="noreferrer" title="Open" className="shrink-0">
                {d.is_image
                  ? <img src={`/api/attachments/${d.id}/file`} alt="" className="h-14 w-11 rounded border border-slate-200 object-cover" />
                  : <span className="flex h-14 w-11 flex-col items-center justify-center rounded border border-slate-200 text-rose-600"><FileText size={18} /><span className="text-[10px] font-semibold">PDF</span></span>}
              </a>
              <div className="min-w-[14rem] flex-1 space-y-1">
                <input className="input font-medium" defaultValue={d.name} aria-label="Document name"
                  onBlur={(e) => e.target.value.trim() && e.target.value !== d.name && save(d, { name: e.target.value })} />
                <div className="text-xs muted">{d.original_name} · {d.pages} page{d.pages === 1 ? '' : 's'} · {d.size < 1024 * 1024 ? `${Math.max(1, Math.round(d.size / 1024))} KB` : `${(d.size / 1024 / 1024).toFixed(1)} MB`}</div>
              </div>
              <select className="input w-72" value={d.kind} aria-label="Type" onChange={(e) => save(d, { kind: e.target.value })}>
                {kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select>
              <input className="input w-28" placeholder="YYYY-MM" defaultValue={d.issued} aria-label="Issued" title="When it was issued: the one-PDF application puts the newest first"
                onBlur={(e) => e.target.value !== d.issued && save(d, { issued: e.target.value })} />
              <label className="flex items-center gap-1.5 whitespace-nowrap" title="Offered to application forms and put in the one-PDF application">
                <input type="checkbox" checked={d.use} onChange={(e) => save(d, { use: e.target.checked })} />Use when applying</label>
              <span className="flex gap-1">
                <a className="btn-ghost p-1.5" href={`/api/attachments/${d.id}/file`} target="_blank" rel="noreferrer" title="Open"><ExternalLink size={14} /></a>
                <a className="btn-ghost p-1.5" href={`/api/attachments/${d.id}/file?download=true`} title="Download"><Download size={14} /></a>
                <button className="btn-ghost p-1.5 text-rose-600" title="Delete" onClick={() => remove(d)}><Trash2 size={14} /></button></span>
            </div>))}
        </div>)}

      <div className="card p-4 text-sm">
        <div className="mb-1 flex items-center gap-2 font-semibold"><ImageIcon size={15} />How they are used</div>
        <ul className="list-disc space-y-1 pl-5 muted">
          <li>The Chrome helper puts the right one into a form's upload field: "Transcript" gets your transcript, "Language certificate" your language certificate, "Zeugnisse" or "Other documents" all ticked ones as one PDF.</li>
          <li>Several files of one type (e.g. two internship references) are joined into one PDF for that field.</li>
          <li>Dates (YYYY-MM) put the newest first, as German recruiters expect. The app never submits an application for you.</li>
        </ul>
      </div>
    </div>
  )
}
