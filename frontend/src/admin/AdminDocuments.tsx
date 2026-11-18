import { useEffect, useRef, useState, type FormEvent } from 'react'
import { FileText, Plus, RefreshCw, RotateCcw, ScrollText, Search, Trash2, UploadCloud, X } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { DocumentRecord, Page } from '../api/types'
import { Modal } from '../components/Modal'
import { Pagination } from '../components/Pagination'

export function AdminDocuments() {
  const [result, setResult] = useState<Page<DocumentRecord> | null>(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [department, setDepartment] = useState('')
  const [clearance, setClearance] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(''), 3500)
      return () => clearTimeout(timer)
    }
  }, [toast])
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadForm, setUploadForm] = useState({ department: 'Engineering', clearance_level: 'Internal', title: '' })
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [logsTarget, setLogsTarget] = useState<DocumentRecord | null>(null)
  const [logs, setLogs] = useState<Array<{ id: string; stage: string; status: string; message: string; created_at: string }>>([])
  const [logsPage, setLogsPage] = useState(1)
  const [logsTotalPages, setLogsTotalPages] = useState(1)
  const [logsLoading, setLogsLoading] = useState(false)
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const replaceInput = useRef<HTMLInputElement>(null)
  const replaceTarget = useRef<DocumentRecord | null>(null)

  const load = async (nextPage = page, options?: { search?: string; status?: string; department?: string; clearance?: string }) => {
    setLoading(true); setError('')
    const searchTerm = options?.search ?? search
    const statusFilter = options?.status ?? status
    const deptFilter = options?.department ?? department
    const clearanceFilter = options?.clearance ?? clearance
    try {
      const params = new URLSearchParams({ page: String(nextPage), page_size: '20' })
      if (searchTerm.trim()) params.set('search', searchTerm.trim())
      if (statusFilter) params.set('status', statusFilter)
      if (deptFilter) params.set('department', deptFilter)
      if (clearanceFilter) params.set('clearance_level', clearanceFilter)
      const data = await api<Page<DocumentRecord>>(`/api/v1/documents/?${params}`)
      setResult(data); setPage(data.page)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not load documents.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load(1) }, [])

  async function upload(event: FormEvent) {
    event.preventDefault()
    if (!uploadFile) return
    setUploading(true); setError('')
    const form = new FormData()
    form.append('file', uploadFile); form.append('department', uploadForm.department); form.append('clearance_level', uploadForm.clearance_level)
    if (uploadForm.title.trim()) form.append('title', uploadForm.title.trim())
    try {
      await api('/api/v1/documents/upload', { method: 'POST', body: form })
      setUploadOpen(false); setUploadFile(null); setUploadForm({ department: 'Engineering', clearance_level: 'Internal', title: '' }); setToast('Document uploaded and indexed.'); await load(1)
    } catch (err) { setError(err instanceof Error ? err.message : 'Upload failed.') }
    finally { setUploading(false) }
  }

  async function documentAction(doc: DocumentRecord, action: 'reindex' | 'rollback' | 'delete') {
    if (action === 'delete' && !window.confirm(`Delete “${doc.title}” and its indexed content?`)) return
    setBusyId(doc.id); setError('')
    try {
      if (action === 'delete') await api(`/api/v1/documents/${doc.id}`, { method: 'DELETE' })
      else {
        const result = await api<any>(`/api/v1/admin/documents/${doc.id}/${action}`, { method: 'POST' })
        if (action === 'reindex' && result.status === 'REINDEX_PENDING') setToast('Reindex is pending; the worker queue is unavailable.')
        else setToast(action === 'reindex' ? 'Reindex request queued.' : 'Previous version restored.')
      }
      if (action === 'delete') setToast('Document deleted.')
      await load(page)
    } catch (err) { setError(err instanceof ApiError ? err.message : 'Document action failed.') }
    finally { setBusyId(null) }
  }

  async function replaceDocument(file: File) {
    const target = replaceTarget.current
    if (!target) return
    setBusyId(target.id)
    const form = new FormData(); form.append('file', file)
    try {
      await api(`/api/v1/admin/documents/${target.id}/replace`, { method: 'POST', body: form })
      setToast('Replacement version staged for indexing.'); await load(page)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not replace document.') }
    finally { setBusyId(null); replaceTarget.current = null; if (replaceInput.current) replaceInput.current.value = '' }
  }

  async function openLogs(doc: DocumentRecord, nextPage = 1) {
    setLogsTarget(doc); setLogsLoading(true)
    try {
      const data = await api<{ items: typeof logs; total_pages: number }>(`/api/v1/admin/documents/${doc.id}/logs?page=${nextPage}&page_size=25`)
      setLogs(data.items); setLogsPage(nextPage); setLogsTotalPages(data.total_pages)
    } catch (err) { setError(err instanceof Error ? err.message : "Could not load document logs.") }
    finally { setLogsLoading(false) }
  }

  return <div className="admin-page">
    <header className="page-header"><div><p className="eyebrow">KNOWLEDGE BASE</p><h1>Documents</h1><p className="page-subtitle">Manage source files, indexing state and active versions.</p></div><button className="primary-button" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add document</button></header>
    <section className="panel data-panel">
      <div className="table-toolbar"><div className="search-field"><Search size={16} /><input placeholder="Search title or filename" value={search} onChange={(e) => { const v = e.target.value; setSearch(v); if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current); searchDebounceRef.current = setTimeout(() => void load(1, { search: v }), 350) }} /></div><div className="toolbar-filters"><select value={department} onChange={(e) => { const value = e.target.value; setDepartment(value); setPage(1); void load(1, { department: value }) }}><option value="">All departments</option><option value="Engineering">Engineering</option><option value="HR">HR</option><option value="Finance">Finance</option><option value="Enterprise">Enterprise</option></select><select value={clearance} onChange={(e) => { const value = e.target.value; setClearance(value); setPage(1); void load(1, { clearance: value }) }}><option value="">All clearances</option><option value="Public">Public</option><option value="Internal">Internal</option><option value="Confidential">Confidential</option></select><select value={status} onChange={(e) => { const value = e.target.value; setStatus(value); setPage(1); void load(1, { status: value }) }}><option value="">All statuses</option><option value="ACTIVE">Active</option><option value="UPDATING">Updating</option><option value="UPDATE_FAILED">Update failed</option></select><span className="count-label">{result?.total ?? 0} documents</span></div></div>
      {error && <div className="inline-error table-error">{error}</div>}
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Document</th><th>Department</th><th>Clearance</th><th>Version</th><th>Status</th><th>Uploaded</th><th className="actions-col">Actions</th></tr></thead><tbody>
        {loading && <tr><td colSpan={7} className="table-loading">Loading documents…</td></tr>}
        {!loading && result?.items.length === 0 && <tr><td colSpan={7} className="table-empty">No documents match these filters.</td></tr>}
        {!loading && result?.items.map((doc) => <tr key={doc.id}><td><div className="document-cell"><span className="file-icon"><FileText size={17} /></span><span><strong>{doc.title}</strong><small>{doc.filename}</small></span></div></td><td>{doc.department}</td><td><span className={`clearance-tag ${doc.clearance_level.toLowerCase()}`}>{doc.clearance_level}</span></td><td>v{doc.version}</td><td><span className={`status-pill ${doc.status.toLowerCase()}`}>{doc.status.replace('_', ' ')}</span>{!doc.is_live && <small className="status-subline">Not live</small>}</td><td>{new Date(doc.uploaded_at).toLocaleDateString()}</td><td><div className="row-actions"><button className="icon-button small" disabled={busyId === doc.id || doc.status === 'UPDATING' || !doc.is_latest || !doc.has_source_file} onClick={() => void documentAction(doc, 'reindex')} title="Reindex"><RefreshCw size={15} /></button><button className="icon-button small" disabled={busyId === doc.id || !doc.is_latest || doc.status === 'UPDATING'} onClick={() => { replaceTarget.current = doc; replaceInput.current?.click() }} title="Replace version"><UploadCloud size={15} /></button><button className="icon-button small" disabled={busyId === doc.id || !doc.is_latest} onClick={() => void documentAction(doc, 'rollback')} title="Restore previous version"><RotateCcw size={15} /></button><button className="icon-button small" onClick={() => void openLogs(doc)} title="Processing history"><ScrollText size={15} /></button><button className="icon-button small danger" disabled={busyId === doc.id} onClick={() => void documentAction(doc, 'delete')} title="Delete"><Trash2 size={15} /></button></div></td></tr>)}
      </tbody></table></div>
      {result && <Pagination page={result.page} totalPages={result.total_pages} onChange={(next) => void load(next)} />}
    </section>
    <input ref={replaceInput} type="file" accept=".pdf,.docx,.md,.txt" hidden onChange={(e) => { const file = e.target.files?.[0]; if (file) void replaceDocument(file) }} />
    {uploadOpen && <Modal title="Add a source document" onClose={() => setUploadOpen(false)}><form className="stack-form" onSubmit={upload}><label>Source file<input type="file" accept=".pdf,.docx,.md,.txt" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} required /></label><label>Title<input value={uploadForm.title} onChange={(e) => setUploadForm((f) => ({ ...f, title: e.target.value }))} placeholder="Leave blank to use filename" /></label><div className="form-grid"><label>Department<input value={uploadForm.department} onChange={(e) => setUploadForm((f) => ({ ...f, department: e.target.value }))} required /></label><label>Clearance<select value={uploadForm.clearance_level} onChange={(e) => setUploadForm((f) => ({ ...f, clearance_level: e.target.value }))}><option>Public</option><option>Internal</option><option>Confidential</option></select></label></div><p className="form-help">The file is stored server-side so it can be safely reindexed later.</p><div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setUploadOpen(false)}>Cancel</button><button type="submit" className="primary-button" disabled={!uploadFile || uploading}>{uploading ? 'Uploading…' : 'Upload and index'}</button></div></form></Modal>}
    {logsTarget && <Modal title={`Processing history · ${logsTarget.title}`} onClose={() => setLogsTarget(null)} wide><div className="document-log-list">{logsLoading && <div className="table-loading">Loading processing history…</div>}{!logsLoading && logs.length === 0 && <div className="empty-state compact"><ScrollText size={22} /><p>No processing events have been recorded for this document.</p></div>}{!logsLoading && logs.map((log) => <div className="document-log-row" key={log.id}><span className={`log-status ${log.status.toLowerCase()}`} /><div><div className="document-log-meta"><strong>{log.stage.replace('_', ' ')}</strong><span>{log.status}</span><small>{new Date(log.created_at).toLocaleString()}</small></div><p>{log.message}</p></div></div>)}<Pagination page={logsPage} totalPages={logsTotalPages} onChange={(next) => void openLogs(logsTarget, next)} /></div></Modal>}
    {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
  </div>
}
