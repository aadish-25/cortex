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
  const [uploadError, setUploadError] = useState('')
  const [toast, setToast] = useState<{ message: string; isError?: boolean } | null>(null)

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 4500)
      return () => clearTimeout(timer)
    }
  }, [toast])

  function showToast(message: string, isError = false) {
    setToast({ message, isError })
  }

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
  const uploadFileInputRef = useRef<HTMLInputElement>(null)
  const replaceTarget = useRef<DocumentRecord | null>(null)
  const pageRef = useRef(page)
  pageRef.current = page

  const load = async (
    nextPage = pageRef.current,
    options?: { search?: string; status?: string; department?: string; clearance?: string },
    silent = false
  ) => {
    if (!silent) setLoading(true)
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
      setResult(data)
      setPage(data.page)
    } catch (err) {
      if (!silent) showToast(err instanceof Error ? err.message : 'Could not load documents.', true)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => { void load(1) }, [])

  // Auto-poll quietly when any document is in UPDATING state so row updates live with zero table flicker
  useEffect(() => {
    const hasUpdating = result?.items.some((d) => d.status === 'UPDATING')
    if (!hasUpdating) return
    const timer = setInterval(() => {
      void load(pageRef.current, undefined, true)
    }, 2500)
    return () => clearInterval(timer)
  }, [result?.items])

  async function upload(event: FormEvent) {
    event.preventDefault()
    if (!uploadFile) return
    setUploading(true)
    setUploadError('')
    const form = new FormData()
    form.append('file', uploadFile)
    form.append('department', uploadForm.department)
    form.append('clearance_level', uploadForm.clearance_level)
    if (uploadForm.title.trim()) form.append('title', uploadForm.title.trim())
    try {
      await api('/api/v1/documents/upload', { method: 'POST', body: form })
      setUploadOpen(false)
      setUploadFile(null)
      setUploadForm({ department: 'Engineering', clearance_level: 'Internal', title: '' })
      showToast('Document uploaded and indexing started.')
      await load(1, undefined, true)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  async function documentAction(doc: DocumentRecord, action: 'reindex' | 'rollback' | 'delete') {
    if (action === 'delete' && !window.confirm(`Delete “${doc.title}” and its indexed content?`)) return
    setBusyId(doc.id)
    try {
      if (action === 'delete') {
        await api(`/api/v1/documents/${doc.id}`, { method: 'DELETE' })
        showToast('Document deleted.')
        await load(pageRef.current, undefined, true)
      } else if (action === 'reindex') {
        // Optimistically update only this row so the table does not blink or unmount
        setResult((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            items: prev.items.map((item) =>
              item.id === doc.id ? { ...item, status: 'UPDATING', is_live: false } : item
            ),
          }
        })
        const res = await api<any>(`/api/v1/admin/documents/${doc.id}/reindex`, { method: 'POST' })
        if (res.status === 'REINDEX_PENDING') {
          showToast('Reindex request queued; worker processing.')
        } else {
          showToast('Reindex request queued.')
        }
        await load(pageRef.current, undefined, true)
      } else if (action === 'rollback') {
        await api<any>(`/api/v1/admin/documents/${doc.id}/rollback`, { method: 'POST' })
        showToast('Previous document version restored.')
        await load(pageRef.current, undefined, true)
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Document action failed.'
      showToast(msg, true)
      await load(pageRef.current, undefined, true)
    } finally {
      setBusyId(null)
    }
  }

  async function replaceDocument(file: File) {
    const target = replaceTarget.current
    if (!target) return
    setBusyId(target.id)
    const form = new FormData()
    form.append('file', file)
    try {
      await api(`/api/v1/admin/documents/${target.id}/replace`, { method: 'POST', body: form })
      showToast('Replacement version staged for indexing.')
      await load(pageRef.current, undefined, true)
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Could not replace document.', true)
    } finally {
      setBusyId(null)
      replaceTarget.current = null
      if (replaceInput.current) replaceInput.current.value = ''
    }
  }

  async function openLogs(doc: DocumentRecord, nextPage = 1) {
    setLogsTarget(doc); setLogsLoading(true)
    try {
      const data = await api<{ items: typeof logs; total_pages: number }>(`/api/v1/admin/documents/${doc.id}/logs?page=${nextPage}&page_size=25`)
      setLogs(data.items); setLogsPage(nextPage); setLogsTotalPages(data.total_pages)
    } catch (err) { showToast(err instanceof Error ? err.message : "Could not load document logs.", true) }
    finally { setLogsLoading(false) }
  }

  return <div className="admin-page">
    <header className="page-header"><div><p className="eyebrow">KNOWLEDGE BASE</p><h1>Documents</h1><p className="page-subtitle">Manage source files, indexing state and active versions.</p></div><button className="primary-button" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add document</button></header>
    <section className="panel data-panel">
      <div className="table-toolbar"><div className="search-field"><Search size={16} /><input placeholder="Search title or filename" value={search} onChange={(e) => { const v = e.target.value; setSearch(v); if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current); searchDebounceRef.current = setTimeout(() => void load(1, { search: v }), 350) }} /></div><div className="toolbar-filters"><select value={department} onChange={(e) => { const value = e.target.value; setDepartment(value); setPage(1); void load(1, { department: value }) }}><option value="">All departments</option><option value="Engineering">Engineering</option><option value="HR">HR</option><option value="Finance">Finance</option><option value="Enterprise">Enterprise</option></select><select value={clearance} onChange={(e) => { const value = e.target.value; setClearance(value); setPage(1); void load(1, { clearance: value }) }}><option value="">All clearances</option><option value="Public">Public</option><option value="Internal">Internal</option><option value="Confidential">Confidential</option></select><select value={status} onChange={(e) => { const value = e.target.value; setStatus(value); setPage(1); void load(1, { status: value }) }}><option value="">All statuses</option><option value="ACTIVE">Active</option><option value="UPDATING">Updating</option><option value="UPDATE_FAILED">Update failed</option></select><span className="count-label">{result?.total ?? 0} documents</span></div></div>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Document</th><th>Department</th><th>Clearance</th><th>Version</th><th>Status</th><th>Uploaded</th><th className="actions-col">Actions</th></tr></thead><tbody>
        {loading && <tr><td colSpan={7} className="table-loading">Loading documents…</td></tr>}
        {!loading && result?.items.length === 0 && <tr><td colSpan={7} className="table-empty">No documents match these filters.</td></tr>}
        {!loading && result?.items.map((doc) => <tr key={doc.id}><td><div className="document-cell"><span className="file-icon"><FileText size={17} /></span><span><strong>{doc.title}</strong><small>{doc.filename}</small></span></div></td><td>{doc.department}</td><td><span className={`clearance-tag ${doc.clearance_level.toLowerCase()}`}>{doc.clearance_level}</span></td><td>v{doc.version}</td><td><span className={`status-pill ${doc.status.toLowerCase()}`}>{doc.status.replace('_', ' ')}</span>{!doc.is_live && <small className="status-subline">Not live</small>}</td><td>{new Date(doc.uploaded_at).toLocaleDateString()}</td><td><div className="row-actions"><button className="icon-button small" disabled={busyId === doc.id || doc.status === 'UPDATING' || !doc.is_latest || !doc.has_source_file} onClick={() => void documentAction(doc, 'reindex')} title="Reindex"><RefreshCw size={15} /></button><button className="icon-button small" disabled={busyId === doc.id || !doc.is_latest || doc.status === 'UPDATING'} onClick={() => { replaceTarget.current = doc; replaceInput.current?.click() }} title="Replace version"><UploadCloud size={15} /></button><button className="icon-button small" disabled={busyId === doc.id || !doc.is_latest} onClick={() => void documentAction(doc, 'rollback')} title="Restore previous version"><RotateCcw size={15} /></button><button className="icon-button small" onClick={() => void openLogs(doc)} title="Processing history"><ScrollText size={15} /></button><button className="icon-button small danger" disabled={busyId === doc.id} onClick={() => void documentAction(doc, 'delete')} title="Delete"><Trash2 size={15} /></button></div></td></tr>)}
      </tbody></table></div>
      {result && <Pagination page={result.page} totalPages={result.total_pages} onChange={(next) => void load(next)} />}
    </section>
    <input ref={replaceInput} type="file" accept=".pdf,.docx,.md,.txt" hidden onChange={(e) => { const file = e.target.files?.[0]; if (file) void replaceDocument(file) }} />
    {uploadOpen && (
      <Modal title="Add a source document" onClose={() => setUploadOpen(false)}>
        <form className="stack-form" onSubmit={upload}>
          <div>
            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6 }}>Source document file *</span>
            <input
              ref={uploadFileInputRef}
              type="file"
              accept=".pdf,.docx,.md,.txt"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0] || null
                setUploadFile(f)
                if (f && !uploadForm.title.trim()) {
                  const cleanName = f.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ')
                  setUploadForm((prev) => ({ ...prev, title: cleanName }))
                }
              }}
            />
            {!uploadFile ? (
              <div
                className="file-dropzone-button"
                onClick={() => uploadFileInputRef.current?.click()}
                style={{
                  border: '2px dashed var(--border-strong)',
                  borderRadius: 10,
                  padding: '24px 16px',
                  background: 'var(--surface-soft)',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8,
                  cursor: 'pointer',
                  transition: 'border-color 0.2s, background 0.2s',
                }}
              >
                <div style={{ width: 42, height: 42, borderRadius: 10, background: 'var(--primary-soft)', color: 'var(--primary)', display: 'grid', placeItems: 'center' }}>
                  <UploadCloud size={22} />
                </div>
                <strong style={{ fontSize: 13, color: 'var(--text)' }}>Choose file to upload</strong>
                <small style={{ color: 'var(--muted)', fontSize: 11 }}>PDF, DOCX, Markdown, or TXT documents</small>
              </div>
            ) : (
              <div
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 10,
                  padding: '12px 14px',
                  background: 'var(--surface)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 12,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 8, background: 'var(--primary-soft)', color: 'var(--primary)', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
                    <FileText size={18} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <strong style={{ fontSize: 12, color: 'var(--text)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {uploadFile.name}
                    </strong>
                    <small style={{ color: 'var(--muted)', fontSize: 11 }}>
                      {(uploadFile.size / 1024).toFixed(1)} KB
                    </small>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    className="secondary-button compact-button"
                    style={{ fontSize: 11, padding: '3px 8px', height: 26 }}
                    onClick={() => uploadFileInputRef.current?.click()}
                  >
                    Change
                  </button>
                  <button
                    type="button"
                    className="icon-button small danger"
                    style={{ width: 26, height: 26 }}
                    onClick={() => {
                      setUploadFile(null)
                      if (uploadFileInputRef.current) uploadFileInputRef.current.value = ''
                    }}
                    title="Remove file"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            )}
          </div>
          <label>Title<input value={uploadForm.title} onChange={(e) => setUploadForm((f) => ({ ...f, title: e.target.value }))} placeholder="Leave blank to use filename" /></label>
          <div className="form-grid"><label>Department<input value={uploadForm.department} onChange={(e) => setUploadForm((f) => ({ ...f, department: e.target.value }))} required /></label><label>Clearance<select value={uploadForm.clearance_level} onChange={(e) => setUploadForm((f) => ({ ...f, clearance_level: e.target.value }))}><option>Public</option><option>Internal</option><option>Confidential</option></select></label></div>
          <p className="form-help">The file is stored server-side so it can be safely reindexed later.</p>
          {uploadError && <div className="inline-error">{uploadError}</div>}
          <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setUploadOpen(false)}>Cancel</button><button type="submit" className="primary-button" disabled={!uploadFile || uploading}>{uploading ? 'Uploading…' : 'Upload and index'}</button></div>
        </form>
      </Modal>
    )}
    {logsTarget && <Modal title={`Processing history · ${logsTarget.title}`} onClose={() => setLogsTarget(null)} wide><div className="document-log-list">{logsLoading && <div className="table-loading">Loading processing history…</div>}{!logsLoading && logs.length === 0 && <div className="empty-state compact"><ScrollText size={22} /><p>No processing events have been recorded for this document.</p></div>}{!logsLoading && logs.map((log) => <div className="document-log-row" key={log.id}><span className={`log-status ${log.status.toLowerCase()}`} /><div><div className="document-log-meta"><strong>{log.stage.replace('_', ' ')}</strong><span>{log.status}</span><small>{new Date(log.created_at).toLocaleString()}</small></div><p>{log.message}</p></div></div>)}<Pagination page={logsPage} totalPages={logsTotalPages} onChange={(next) => void openLogs(logsTarget, next)} /></div></Modal>}
    {toast && (
      <div
        className="toast-message"
        style={{
          borderColor: toast.isError ? 'var(--danger)' : 'var(--border)',
          color: toast.isError ? 'var(--danger)' : 'var(--text)',
          background: 'var(--surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
        }}
      >
        {toast.isError && <span>⚠️</span>}
        <span>{toast.message}</span>
        <button onClick={() => setToast(null)} aria-label="Dismiss"><X size={14} /></button>
      </div>
    )}
  </div>
}
