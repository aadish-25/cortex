import { useEffect, useState } from 'react'
import { Filter, Search, ShieldCheck } from 'lucide-react'
import { api } from '../api/client'
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
  const pageSize = 50
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
    <section className="panel data-panel"><div className="table-toolbar"><div className="toolbar-filters"><span className="filter-label"><Filter size={15} /> Category</span><select value={category} onChange={(e) => { const value = e.target.value; setCategory(value); void load(1, value) }}><option value="">All categories</option>{categories.map((item) => <option key={item}>{item}</option>)}</select></div><span className="count-label">Page {page} · {data?.total_returned ?? 0} shown</span></div>
      {error && <div className="inline-error table-error">{error}</div>}
      <div className="table-scroll"><table className="data-table audit-table"><thead><tr><th>Event</th><th>Category</th><th>Result</th><th>Details</th><th>Actor ID</th><th>Timestamp</th></tr></thead><tbody>
        {loading && <tr><td colSpan={6} className="table-loading">Loading audit trail…</td></tr>}
        {!loading && data?.logs.length === 0 && <tr><td colSpan={6} className="table-empty">No audit events found.</td></tr>}
        {!loading && data?.logs.map((log) => <tr key={log.id}><td><strong className="event-code">{log.event_tag}</strong></td><td><span className="category-tag">{log.category}</span></td><td><span className={`http-status ${log.status_code >= 400 ? 'bad' : 'good'}`}>{log.status_code}</span></td><td className="audit-details">{log.details || '—'}</td><td className="mono-cell">{log.user_id ? `${log.user_id.slice(0, 8)}…` : 'System'}</td><td>{new Date(log.created_at).toLocaleString()}</td></tr>)}
      </tbody></table></div>
      <Pagination page={page} totalPages={totalPages} onChange={(next) => void load(next)} />
    </section>
  </div>
}
