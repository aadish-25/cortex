import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, Database, Gauge, MessageSquareText, ShieldCheck, ThumbsUp, Zap } from 'lucide-react'
import { api } from '../api/client'
import type { AdminHealth } from '../api/types'

interface Quality { total_queries: number; fallback_queries: number; fallback_rate_percent: number; low_confidence_queries: number; average_reranker_score: number | null }
interface QueryStats { total_queries: number; daily_counts: Array<{ day: string; query_count: number }> }
interface Feedback { thumbs_up: number; thumbs_down: number; total_feedback: number; satisfaction_percent: number | null }
interface Tokens { grand_total_tokens: number; grand_total_cost_usd: number; by_provider_model: Array<{ provider: string; model: string; total_tokens: number; request_count: number }> }

const SESSION_CACHE_KEY = 'cortex_admin_overview_cache'

function loadSavedCache(): {
  health?: AdminHealth
  quality?: Quality
  queries?: QueryStats
  feedback?: Feedback
  tokens?: Tokens
  loaded?: boolean
} {
  try {
    const raw = sessionStorage.getItem(SESSION_CACHE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

// True stale-while-revalidate cache backed by sessionStorage: renders instantly on every revisit / refresh.
const _cache = loadSavedCache()

function saveCache() {
  try { sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(_cache)) } catch {}
}

function buildWeeklyChartData(dailyCounts: Array<{ day: string; query_count: number }> = []) {
  const countMap = new Map<string, number>()
  for (const item of dailyCounts) {
    const key = (item.day || '').split('T')[0]
    if (key) countMap.set(key, item.query_count)
  }

  const result: Array<{ day: string; label: string; count: number; isToday: boolean }> = []
  const today = new Date()

  // Always start week from Monday (Sunday is 0, Monday is 1, ..., Saturday is 6)
  const currentDay = today.getDay()
  const diffToMonday = currentDay === 0 ? -6 : 1 - currentDay
  const monday = new Date(today)
  monday.setDate(today.getDate() + diffToMonday)

  for (let i = 0; i < 7; i++) {
    const d = new Date(monday)
    d.setDate(monday.getDate() + i)
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const key = `${yyyy}-${mm}-${dd}`
    const count = countMap.get(key) || 0
    const label = d.toLocaleDateString(undefined, { weekday: 'short' })
    const isToday = d.toDateString() === today.toDateString()
    result.push({ day: key, label, count, isToday })
  }
  return result
}

export function AdminOverview() {
  const [health, setHealth] = useState<AdminHealth | null>(_cache.health ?? null)
  const [quality, setQuality] = useState<Quality | null>(_cache.quality ?? null)
  const [queries, setQueries] = useState<QueryStats | null>(_cache.queries ?? null)
  const [feedback, setFeedback] = useState<Feedback | null>(_cache.feedback ?? null)
  const [tokens, setTokens] = useState<Tokens | null>(_cache.tokens ?? null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(!_cache.loaded)
  const mountedRef = useRef(true)

  const refreshAll = useCallback(async () => {
    let failures = 0
    const handle = <T,>(promise: Promise<T>, apply: (val: T) => void) =>
      promise
        .then((val) => {
          if (mountedRef.current) {
            apply(val)
            saveCache()
            setLoading(false)
          }
        })
        .catch(() => { failures += 1 })

    await Promise.all([
      handle(api<AdminHealth>('/api/v1/admin/health'), (v) => { _cache.health = v; setHealth(v) }),
      handle(api<Quality>('/api/v1/admin/analytics/quality?days=7'), (v) => { _cache.quality = v; setQuality(v) }),
      handle(api<QueryStats>('/api/v1/admin/analytics/queries?days=14'), (v) => { _cache.queries = v; setQueries(v) }),
      handle(api<Feedback>('/api/v1/admin/analytics/feedback?days=7'), (v) => { _cache.feedback = v; setFeedback(v) }),
      handle(api<Tokens>('/api/v1/admin/analytics/tokens?days=7'), (v) => { _cache.tokens = v; setTokens(v) }),
    ])

    if (!mountedRef.current) return
    _cache.loaded = true
    saveCache()
    setLoading(false)
    if (failures === 5) setError('Could not load admin telemetry. Check that the backend services are running.')
    else setError('')
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void refreshAll()
  }, [refreshAll])

  useEffect(() => {
    // If any service is WAITING or CHECKING, poll every 3s so the UI automatically flips to UP
    const hasWaiting = Object.values(health?.services ?? {}).some(
      (s) => s.status === 'WAITING' || s.status === 'CHECKING'
    )
    const intervalMs = hasWaiting ? 3_000 : 30_000
    const timer = setInterval(() => { void refreshAll() }, intervalMs)
    return () => clearInterval(timer)
  }, [refreshAll, health?.services])

  const metrics = [
    { label: 'Queries · 7 days', value: quality?.total_queries ?? queries?.total_queries ?? '—', note: 'Recorded chat turns', icon: MessageSquareText },
    { label: 'Average reranker score', value: quality?.average_reranker_score != null ? quality.average_reranker_score.toFixed(3) : '—', note: 'Top retrieved evidence score', icon: Gauge },
    { label: 'Fallback rate', value: quality ? `${quality.fallback_rate_percent}%` : '—', note: `${quality?.fallback_queries ?? 0} fallback answers`, icon: Activity },
    { label: 'Answer satisfaction', value: feedback?.satisfaction_percent == null ? '—' : `${feedback.satisfaction_percent}%`, note: `${feedback?.total_feedback ?? 0} ratings submitted`, icon: ThumbsUp },
  ]
  const weeklyData = useMemo(() => buildWeeklyChartData(queries?.daily_counts), [queries?.daily_counts])
  const maxCount = Math.max(1, ...weeklyData.map((item) => item.count))
  const scaleMax = Math.max(10, Math.ceil(maxCount * 1.25))
  const serviceEntries = Object.entries(health?.services ?? {}) as Array<[string, AdminHealth['services'][string]]>

  function serviceBadgeClass(svc: AdminHealth['services'][string]) {
    const s = svc.status.toLowerCase()
    if (s === 'up') return 'up'
    if (s === 'waiting' || s === 'checking') return 'waiting'
    if (s === 'down') return 'down'
    return 'degraded'
  }

  return <div className="admin-page">
    <header className="page-header overview-header"><div><p className="eyebrow">CONTROL ROOM</p><h1>Good afternoon, Admin.</h1><p className="page-subtitle">A clear view of Cortex operations, retrieval quality and usage.</p></div><div className="header-status"><span className={`health-dot ${health?.overall === 'UP' ? 'up' : 'warn'}`} />{health?.overall || 'Checking'}<small>System status</small></div></header>
    {error && <div className="inline-error">{error}</div>}
    <section className="metric-grid">{metrics.map((metric) => { const Icon = metric.icon; return <article className="metric-card" key={metric.label}><div className="metric-card-top"><span>{metric.label}</span><Icon size={17} /></div><strong>{loading && metric.value === '—' ? '—' : metric.value}</strong><small>{metric.note}</small></article> })}</section>
    <div className="overview-grid">
      <section className="panel health-panel"><div className="panel-heading"><div><p className="eyebrow">INFRASTRUCTURE</p><h2>Service health</h2></div><span className="live-label"><span /> Live checks</span></div>
        <div className="service-list">{serviceEntries.map(([name, service]) => <div className="service-row" key={name}><div className="service-name"><span className={`service-status ${serviceBadgeClass(service)}`} /><strong>{name.toUpperCase()}</strong></div><span className={`service-badge ${serviceBadgeClass(service)}`}>{service.status}</span><small>{service.detail}</small></div>)}{!health && <div className="table-loading">{loading ? 'Checking services…' : 'Health data unavailable'}</div>}</div>
        {health?.llm_providers && <div className="provider-health"><p className="eyebrow">MODEL PROVIDER KEYS</p><div>{Object.entries(health.llm_providers).map(([provider, status]) => <span key={provider} className={status === 'CONFIGURED' ? 'provider-configured' : 'provider-missing'}>{provider} · {status === 'CONFIGURED' ? 'Configured' : 'Not configured'}</span>)}</div></div>}
      </section>
      <section className="panel query-panel">
        <div className="panel-heading">
          <div><p className="eyebrow">ACTIVITY</p><h2>Query volume</h2></div>
          <span className="panel-period">This week</span>
        </div>
        <div className="bar-chart-container">
          <div className="bar-chart-grid">
            <div className="grid-line" style={{ top: '15%' }}><span>{Math.round(scaleMax * 0.85)}</span></div>
            <div className="grid-line" style={{ top: '50%' }}><span>{Math.round(scaleMax * 0.5)}</span></div>
            <div className="grid-line" style={{ top: '85%' }}><span>{Math.round(scaleMax * 0.15)}</span></div>
          </div>
          <div className="bar-chart">
            {weeklyData.map((item) => {
              const heightPct = item.count > 0 ? Math.min(100, Math.max(6, (item.count / scaleMax) * 100)) : 0
              return (
                <div className={`bar-chart-col ${item.isToday ? 'is-today' : ''}`} key={item.day}>
                  <span className="bar-count">{item.count > 0 ? item.count : '0'}</span>
                  <div className="bar-slot">
                    {item.count > 0 ? (
                      <div
                        className="bar-column"
                        style={{ height: `${heightPct}%` }}
                        title={`${item.count} queries on ${item.label}`}
                      />
                    ) : (
                      <div className="bar-column-zero" title="0 queries" />
                    )}
                  </div>
                  <small className="bar-day">{item.label}</small>
                </div>
              )
            })}
          </div>
        </div>
      </section>
    </div>
    <div className="overview-bottom-grid">
      <section className="panel compact-panel"><div className="panel-heading"><div><p className="eyebrow">RETRIEVAL QUALITY</p><h2>Knowledge gaps</h2></div><ShieldCheck size={18} /></div><div className="quality-stat"><strong>{quality?.low_confidence_queries ?? '—'}</strong><span>Low-confidence or fuzzy queries</span></div><Link className="text-link" to="/admin/quality">Review answer quality <ArrowUpRight size={14} /></Link></section>
      <section className="panel compact-panel"><div className="panel-heading"><div><p className="eyebrow">MODEL USAGE</p><h2>Token consumption</h2></div><Zap size={18} /></div><div className="quality-stat"><strong>{tokens?.grand_total_tokens?.toLocaleString() ?? '—'}</strong><span>Tokens · 7 days</span></div><div className="provider-list">{tokens?.by_provider_model.slice(0, 3).map((item) => <div key={`${item.provider}-${item.model}`}><span>{item.provider} · {item.model}</span><strong>{item.total_tokens.toLocaleString()}</strong></div>)}</div></section>
      <section className="panel compact-panel"><div className="panel-heading"><div><p className="eyebrow">FEEDBACK</p><h2>Employee signals</h2></div><Database size={18} /></div><div className="feedback-split"><div><strong>{feedback?.thumbs_up ?? 0}</strong><small>Helpful</small></div><div><strong>{feedback?.thumbs_down ?? 0}</strong><small>Not helpful</small></div></div><span className="panel-footnote">Ratings are collected directly on assistant answers.</span></section>
    </div>
  </div>
}
