import { useEffect, useState } from 'react'
import { Activity, CheckCheck, CircleAlert, Gauge, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import { Pagination } from '../components/Pagination'

interface QualityStats { total_queries: number; fallback_queries: number; fallback_rate_percent: number; low_confidence_queries: number; average_reranker_score: number | null }
interface QualityRecord { id: string; query_preview: string; confidence: 'LOW' | 'FUZZY'; top_reranker_score: number | null; is_fallback: boolean; created_at: string; reviewed_at: string | null }
interface QualityPage { items: QualityRecord[]; page: number; page_size: number; total: number; total_pages: number }

export function AdminQuality() {
  const [stats, setStats] = useState<QualityStats | null>(null)
  const [result, setResult] = useState<QualityPage | null>(null)
  const [page, setPage] = useState(1)
  const [reviewed, setReviewed] = useState('false')
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState('')

  async function load(nextPage = 1, reviewedFilter = reviewed) {
    setLoading(true); setError('')
    try {
      const [metrics, records] = await Promise.all([
        api<QualityStats>('/api/v1/admin/analytics/quality?days=30'),
        api<QualityPage>(`/api/v1/admin/quality/low-confidence?page=${nextPage}&page_size=25&reviewed=${reviewedFilter}`),
      ])
      setStats(metrics); setResult(records); setPage(records.page)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not load quality data.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load(1) }, [])

  async function markReviewed(record: QualityRecord) {
    setBusyId(record.id)
    try { await api(`/api/v1/admin/quality/low-confidence/${record.id}/review`, { method: 'PATCH' }); await load(page) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not mark this query reviewed.') }
    finally { setBusyId(null) }
  }

  return <div className="admin-page">
    <header className="page-header"><div><p className="eyebrow">RETRIEVAL OBSERVABILITY</p><h1>Answer quality</h1><p className="page-subtitle">Review weak retrievals, monitor fallback behaviour and improve source coverage.</p></div><button className="secondary-button" onClick={() => void load(page)}><RefreshCw size={15} /> Refresh</button></header>
    <section className="metric-grid quality-metrics">
      <article className="metric-card"><div className="metric-card-top"><span>Average reranker score</span><Gauge size={17} /></div><strong>{stats?.average_reranker_score?.toFixed(3) ?? '—'}</strong><small>Across the last 30 days</small></article>
      <article className="metric-card"><div className="metric-card-top"><span>Fallback rate</span><Activity size={17} /></div><strong>{stats ? `${stats.fallback_rate_percent}%` : '—'}</strong><small>{stats?.fallback_queries ?? 0} low-confidence or fallback answers</small></article>
      <article className="metric-card"><div className="metric-card-top"><span>Review queue</span><CircleAlert size={17} /></div><strong>{stats?.low_confidence_queries ?? '—'}</strong><small>Low or fuzzy retrieval confidence</small></article>
    </section>
    <section className="panel data-panel"><div className="panel-heading quality-list-heading"><div><p className="eyebrow">MANUAL REVIEW</p><h2>Low-confidence queries</h2><p className="panel-subtitle">Query previews are de-identified before they are stored for review.</p></div><select value={reviewed} onChange={(e) => { setReviewed(e.target.value); void load(1, e.target.value) }}><option value="false">Needs review</option><option value="true">Reviewed</option></select></div>
      {error && <div className="inline-error table-error">{error}</div>}
      <div className="quality-query-list">{loading && <div className="table-loading">Loading review queue…</div>}{!loading && result?.items.length === 0 && <div className="empty-state compact"><CheckCheck size={24} /><p>No queries in this review queue.</p></div>}
        {!loading && result?.items.map((record) => <article className="quality-query-row" key={record.id}><div className="quality-query-main"><div className="quality-query-tags"><span className={`confidence-tag ${record.confidence.toLowerCase()}`}>{record.confidence}</span>{record.is_fallback && <span className="fallback-tag">Fallback</span>}<small>{new Date(record.created_at).toLocaleString()}</small></div><p>{record.query_preview || 'No de-identified query preview available.'}</p><div className="quality-score">Top reranker score: <strong>{record.top_reranker_score?.toFixed(4) ?? 'No score'}</strong></div></div><button className="secondary-button compact-button" disabled={busyId === record.id || Boolean(record.reviewed_at)} onClick={() => void markReviewed(record)}>{busyId === record.id ? 'Saving…' : record.reviewed_at ? 'Reviewed' : 'Mark reviewed'}</button></article>)}
      </div>
      {result && <Pagination page={result.page} totalPages={result.total_pages} onChange={(next) => void load(next)} />}
    </section>
  </div>
}
