import { useEffect, useState } from 'react'
import { Filter, Search, ShieldCheck } from 'lucide-react'
import { api } from '../api/client'
import { Modal } from '../components/Modal'
import { Pagination } from '../components/Pagination'

interface AuditLog { id: string; event_tag: string; category: string; status_code: number; details?: string | null; user_id?: string | null; created_at: string }
interface AuditPage { total: number; total_returned: number; offset: number; limit: number; category_filter?: string | null; logs: AuditLog[] }
const categories = ['AUTH', 'DOCUMENT', 'QUERY', 'WORKFLOW', 'ADMIN']

export function AdminAudit() {
  const [data, setData] = useState<AuditPage | null>(null)
  const [category, setCategory] = useState('')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null)
  const pageSize = 25
  async function load(nextPage = 1, selectedCategory = category) {
    setLoading(true); setError('')
    const params = new URLSearchParams({ limit: String(pageSize), offset: String((nextPage - 1) * pageSize) })
    if (selectedCategory) params.set('category', selectedCategory)
    try { const result = await api<AuditPage>(`/api/v1/admin/audit-logs?${params}`); setData(result); setPage(nextPage) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not load audit logs.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load(1) }, [])
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / pageSize))
  return <div className="admin-page">
    <header className="page-header"><div><p className="eyebrow">SECURITY & COMPLIANCE</p><h1>Audit trail</h1><p className="page-subtitle">A chronological record of access, document and administrative events.</p></div><div className="audit-total"><ShieldCheck size={18} /><strong>{data?.total ?? '—'}</strong><small>records</small></div></header>
    <section className="panel data-panel"><div className="table-toolbar"><div className="toolbar-filters"><span className="filter-label"><Filter size={15} /> Category</span><select value={category} onChange={(e) => { const value = e.target.value; setCategory(value); void load(1, value) }}><option value="">All categories</option>{categories.map((item) => <option key={item}>{item}</option>)}</select></div><span className="count-label">Page {page} · {data?.total_returned ?? 0} shown · Click row to inspect</span></div>
      {error && <div className="inline-error table-error">{error}</div>}
      <div className="table-scroll"><table className="data-table audit-table"><thead><tr><th>Event</th><th>Category</th><th>Result</th><th>Details</th><th>Actor ID</th><th>Timestamp</th></tr></thead><tbody>
        {loading && <tr><td colSpan={6} className="table-loading">Loading audit trail…</td></tr>}
        {!loading && data?.logs.length === 0 && <tr><td colSpan={6} className="table-empty">No audit events found.</td></tr>}
        {!loading && data?.logs.map((log) => <tr key={log.id} onClick={() => setSelectedLog(log)} style={{ cursor: 'pointer' }} title="Click to view full event details"><td><strong className="event-code">{log.event_tag}</strong></td><td><span className="category-tag">{log.category}</span></td><td><span className={`http-status ${log.status_code >= 400 ? 'bad' : 'good'}`}>{log.status_code}</span></td><td className="audit-details" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{log.details || '—'}</td><td className="mono-cell">{log.user_id ? `${log.user_id.slice(0, 8)}…` : 'System'}</td><td>{new Date(log.created_at).toLocaleString()}</td></tr>)}
      </tbody></table></div>
      <Pagination page={page} totalPages={totalPages} onChange={(next) => void load(next)} />
    </section>

    {selectedLog && (
      <Modal title={`Audit Event · ${selectedLog.event_tag}`} onClose={() => setSelectedLog(null)} wide>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 8 }}>
            <div>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase' }}>EVENT TAG</span>
              <h3 style={{ margin: '2px 0 0', fontSize: 15, fontFamily: 'monospace' }}>{selectedLog.event_tag}</h3>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="category-tag">{selectedLog.category}</span>
              <span className={`http-status ${selectedLog.status_code >= 400 ? 'bad' : 'good'}`} style={{ padding: '3px 8px', borderRadius: 6, fontWeight: 700 }}>
                HTTP {selectedLog.status_code}
              </span>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={{ padding: '10px 12px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 8 }}>
              <small style={{ color: 'var(--muted)', display: 'block', fontSize: 10, textTransform: 'uppercase', fontWeight: 700 }}>Timestamp</small>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{new Date(selectedLog.created_at).toLocaleString()}</span>
            </div>
            <div style={{ padding: '10px 12px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 8 }}>
              <small style={{ color: 'var(--muted)', display: 'block', fontSize: 10, textTransform: 'uppercase', fontWeight: 700 }}>Actor / User ID</small>
              <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{selectedLog.user_id || 'System (automated)'}</span>
            </div>
          </div>

          <div style={{ padding: '12px 14px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 8 }}>
            <small style={{ color: 'var(--muted)', display: 'block', fontSize: 10, textTransform: 'uppercase', fontWeight: 700, marginBottom: 8 }}>Event Details & Payload</small>
            {selectedLog.details ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {selectedLog.details.split(';').map((part, idx) => {
                    const trimmed = part.trim()
                    if (!trimmed) return null
                    return (
                      <span key={idx} style={{ padding: '4px 8px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 11, fontFamily: 'monospace' }}>
                        {trimmed}
                      </span>
                    )
                  })}
                </div>
                <pre style={{ margin: '8px 0 0', padding: '10px 12px', background: 'var(--page-bg)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: 'monospace' }}>
                  {selectedLog.details}
                </pre>
              </div>
            ) : (
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>No additional details recorded for this event.</span>
            )}
          </div>

          <div className="modal-actions" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="primary-button compact-button" onClick={() => setSelectedLog(null)}>
              Got it
            </button>
          </div>
        </div>
      </Modal>
    )}
  </div>
}
