import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, Database, Gauge, MessageSquareText, ShieldCheck, ThumbsUp, Zap } from 'lucide-react'
import { api } from '../api/client'
import type { AdminHealth } from '../api/types'

interface Quality { total_queries: number; fallback_queries: number; fallback_rate_percent: number; low_confidence_queries: number; average_reranker_score: number | null }
interface QueryStats { total_queries: number; daily_counts: Array<{ day: string; query_count: number }> }
interface Feedback { thumbs_up: number; thumbs_down: number; total_feedback: number; satisfaction_percent: number | null }
interface Tokens { grand_total_tokens: number; grand_total_cost_usd: number; by_provider_model: Array<{ provider: string; model: string; total_tokens: number; request_count: number }> }

export function AdminOverview() {
  const [health, setHealth] = useState<AdminHealth | null>(null)
  const [quality, setQuality] = useState<Quality | null>(null)
  const [queries, setQueries] = useState<QueryStats | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [tokens, setTokens] = useState<Tokens | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.allSettled([
      api<AdminHealth>('/api/v1/admin/health'),
      api<Quality>('/api/v1/admin/analytics/quality?days=7'),
      api<QueryStats>('/api/v1/admin/analytics/queries?days=7'),
      api<Feedback>('/api/v1/admin/analytics/feedback?days=7'),
      api<Tokens>('/api/v1/admin/analytics/tokens?days=7'),
    ]).then((results) => {
      if (results[0].status === 'fulfilled') setHealth(results[0].value)
      if (results[1].status === 'fulfilled') setQuality(results[1].value)
      if (results[2].status === 'fulfilled') setQueries(results[2].value)
      if (results[3].status === 'fulfilled') setFeedback(results[3].value)
      if (results[4].status === 'fulfilled') setTokens(results[4].value)
      if (results.every((item) => item.status === 'rejected')) setError('Could not load admin telemetry. Check that the backend services are running.')
    }).finally(() => setLoading(false))
  }, [])

  const metrics = [
    { label: 'Queries · 7 days', value: quality?.total_queries ?? queries?.total_queries ?? '—', note: 'Recorded chat turns', icon: MessageSquareText },
    { label: 'Average reranker score', value: quality?.average_reranker_score?.toFixed(3) ?? '—', note: 'Top retrieved evidence score', icon: Gauge },
    { label: 'Fallback rate', value: quality ? `${quality.fallback_rate_percent}%` : '—', note: `${quality?.fallback_queries ?? 0} fallback answers`, icon: Activity },
    { label: 'Answer satisfaction', value: feedback?.satisfaction_percent == null ? '—' : `${feedback.satisfaction_percent}%`, note: `${feedback?.total_feedback ?? 0} ratings submitted`, icon: ThumbsUp },
  ]
  const maxCount = Math.max(1, ...(queries?.daily_counts || []).map((item) => item.query_count))
  const serviceEntries = Object.entries(health?.services ?? {}) as Array<[string, AdminHealth['services'][string]]>

  return <div className="admin-page">
    <header className="page-header overview-header"><div><p className="eyebrow">CONTROL ROOM</p><h1>Good afternoon, Admin.</h1><p className="page-subtitle">A clear view of Cortex operations, retrieval quality and usage.</p></div><div className="header-status"><span className={`health-dot ${health?.overall === 'UP' ? 'up' : 'warn'}`} />{health?.overall || 'Checking'}<small>System status</small></div></header>
    {error && <div className="inline-error">{error}</div>}
    <section className="metric-grid">{metrics.map((metric) => { const Icon = metric.icon; return <article className="metric-card" key={metric.label}><div className="metric-card-top"><span>{metric.label}</span><Icon size={17} /></div><strong>{loading ? '—' : metric.value}</strong><small>{metric.note}</small></article> })}</section>
    <div className="overview-grid">
      <section className="panel health-panel"><div className="panel-heading"><div><p className="eyebrow">INFRASTRUCTURE</p><h2>Service health</h2></div><span className="live-label"><span /> Live checks</span></div>
        <div className="service-list">{serviceEntries.map(([name, service]) => <div className="service-row" key={name}><div className="service-name"><span className={`service-status ${service.status.toLowerCase()}`} /><strong>{name.toUpperCase()}</strong></div><span className={`service-badge ${service.status.toLowerCase()}`}>{service.status}</span><small>{service.detail}</small></div>)}{!health && <div className="table-loading">{loading ? 'Checking services…' : 'Health data unavailable'}</div>}</div>
        {health?.llm_providers && <div className="provider-health"><p className="eyebrow">MODEL PROVIDER KEYS</p><div>{Object.entries(health.llm_providers).map(([provider, status]) => <span key={provider} className={status === 'CONFIGURED' ? 'provider-configured' : 'provider-missing'}>{provider} · {status === 'CONFIGURED' ? 'Configured' : 'Not configured'}</span>)}</div></div>}
      </section>
      <section className="panel query-panel"><div className="panel-heading"><div><p className="eyebrow">ACTIVITY</p><h2>Query volume</h2></div><span className="panel-period">Last 7 days</span></div>
        <div className="bar-chart">{(queries?.daily_counts || []).map((item) => <div className="bar-chart-col" key={item.day}><span className="bar-count">{item.query_count}</span><div className="bar-track"><div className="bar-fill" style={{ height: `${Math.max(4, (item.query_count / maxCount) * 100)}%` }} /></div><small>{new Date(item.day).toLocaleDateString(undefined, { weekday: 'short' })}</small></div>)}{!queries?.daily_counts.length && <div className="chart-empty">Query activity will appear here.</div>}</div>
      </section>
    </div>
    <div className="overview-bottom-grid">
      <section className="panel compact-panel"><div className="panel-heading"><div><p className="eyebrow">RETRIEVAL QUALITY</p><h2>Knowledge gaps</h2></div><ShieldCheck size={18} /></div><div className="quality-stat"><strong>{quality?.low_confidence_queries ?? '—'}</strong><span>Low-confidence or fuzzy queries</span></div><Link className="text-link" to="/admin/quality">Review answer quality <ArrowUpRight size={14} /></Link></section>
      <section className="panel compact-panel"><div className="panel-heading"><div><p className="eyebrow">MODEL USAGE</p><h2>Token consumption</h2></div><Zap size={18} /></div><div className="quality-stat"><strong>{tokens?.grand_total_tokens?.toLocaleString() ?? '—'}</strong><span>Tokens · 7 days</span></div><div className="provider-list">{tokens?.by_provider_model.slice(0, 3).map((item) => <div key={`${item.provider}-${item.model}`}><span>{item.provider} · {item.model}</span><strong>{item.total_tokens.toLocaleString()}</strong></div>)}</div></section>
      <section className="panel compact-panel"><div className="panel-heading"><div><p className="eyebrow">FEEDBACK</p><h2>Employee signals</h2></div><Database size={18} /></div><div className="feedback-split"><div><strong>{feedback?.thumbs_up ?? 0}</strong><small>Helpful</small></div><div><strong>{feedback?.thumbs_down ?? 0}</strong><small>Not helpful</small></div></div><span className="panel-footnote">Ratings are collected directly on assistant answers.</span></section>
    </div>
  </div>
}
