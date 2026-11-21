import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CreditCard,
  DollarSign,
  Layers,
  RefreshCw,
  TrendingUp,
  Cpu,
  Building2,
  Info,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react'
import { api } from '../api/client'

interface ProviderModelUsage {
  provider: string
  model: string
  total_tokens: number
  estimated_cost_usd: number
  request_count: number
}

interface DepartmentUsage {
  department: string
  total_tokens: number
  estimated_cost_usd: number
  request_count: number
}

interface TokenAnalyticsResponse {
  period_days: number
  since: string
  grand_total_tokens: number
  grand_total_cost_usd: number
  by_provider_model: ProviderModelUsage[]
  by_department?: DepartmentUsage[]
}

const SESSION_CACHE_KEY = 'cortex_admin_billing_cache'

function loadSavedCache(): TokenAnalyticsResponse | null {
  try {
    const raw = sessionStorage.getItem(SESSION_CACHE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

let _billingCache: TokenAnalyticsResponse | null = loadSavedCache()

function saveCache(data: TokenAnalyticsResponse) {
  try {
    _billingCache = data
    sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(data))
  } catch {}
}

const USD_TO_INR_ESTIMATE = 83.5

export function AdminBilling() {
  const [data, setData] = useState<TokenAnalyticsResponse | null>(_billingCache)
  const [lookbackDays, setLookbackDays] = useState<number>(7)
  const [loading, setLoading] = useState<boolean>(!_billingCache)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const fetchData = useCallback(async (days: number) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api<TokenAnalyticsResponse>(`/api/v1/admin/analytics/tokens?days=${days}`)
      if (mountedRef.current) {
        setData(res)
        saveCache(res)
      }
    } catch (err: any) {
      if (mountedRef.current) {
        setError(err?.message || 'Failed to fetch billing & token usage data.')
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void fetchData(lookbackDays)
    return () => {
      mountedRef.current = false
    }
  }, [fetchData, lookbackDays])

  const totalTokens = data?.grand_total_tokens || 0
  const totalCostUsd = data?.grand_total_cost_usd || 0
  const totalCostInr = totalCostUsd * USD_TO_INR_ESTIMATE
  const totalRequests = useMemo(() => {
    if (!data?.by_provider_model) return 0
    return data.by_provider_model.reduce((acc, row) => acc + row.request_count, 0)
  }, [data])

  const avgCostPer1k = totalTokens > 0 ? (totalCostUsd / totalTokens) * 1000 : 0

  return (
    <div className="admin-page-container">
      {/* Header Row */}
      <div className="admin-page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <h1 className="admin-page-title" style={{ fontSize: 24, fontWeight: 700, margin: 0 }}>
            Billing & Token Consumption
          </h1>
          <p className="admin-page-subtitle" style={{ color: 'var(--text-secondary, #6b7280)', marginTop: 4, fontSize: 14 }}>
            Monitor multi-provider token expenditure, model efficiency metrics, and department cost allocations.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Days Filter */}
          <div style={{ display: 'flex', background: 'var(--bg-secondary, #f3f4f6)', borderRadius: 8, padding: 3 }}>
            {[7, 14, 30, 90].map((days) => (
              <button
                key={days}
                onClick={() => setLookbackDays(days)}
                style={{
                  padding: '6px 12px',
                  fontSize: 13,
                  fontWeight: lookbackDays === days ? 600 : 500,
                  borderRadius: 6,
                  border: 'none',
                  cursor: 'pointer',
                  background: lookbackDays === days ? 'var(--bg-primary, #ffffff)' : 'transparent',
                  color: lookbackDays === days ? 'var(--text-primary, #111827)' : 'var(--text-secondary, #6b7280)',
                  boxShadow: lookbackDays === days ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                  transition: 'all 0.15s ease',
                }}
              >
                {days}d
              </button>
            ))}
          </div>

          {/* Refresh Button */}
          <button
            onClick={() => void fetchData(lookbackDays)}
            disabled={loading}
            className="secondary-btn"
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', fontSize: 13, cursor: 'pointer' }}
            title="Refresh billing telemetry"
          >
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="inline-error" style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, color: '#991b1b' }}>
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* KPI Cards Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginBottom: 24 }}>
        {/* Total Cost Card */}
        <div className="admin-stat-card" style={{ padding: 20, borderRadius: 12, border: '1px solid var(--border-color, #e5e7eb)', background: 'var(--bg-card, #ffffff)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary, #6b7280)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Estimated Cost</span>
            <span style={{ padding: 6, background: 'rgba(16, 185, 129, 0.1)', color: '#10b981', borderRadius: 8 }}><DollarSign size={16} /></span>
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary, #111827)' }}>
            ${totalCostUsd.toFixed(4)}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary, #6b7280)', marginTop: 4 }}>
            ≈ ₹{totalCostInr.toFixed(2)} INR
          </div>
        </div>

        {/* Total Tokens Card */}
        <div className="admin-stat-card" style={{ padding: 20, borderRadius: 12, border: '1px solid var(--border-color, #e5e7eb)', background: 'var(--bg-card, #ffffff)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary, #6b7280)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Token Volume</span>
            <span style={{ padding: 6, background: 'rgba(59, 130, 246, 0.1)', color: '#3b82f6', borderRadius: 8 }}><Layers size={16} /></span>
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary, #111827)' }}>
            {totalTokens.toLocaleString()}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary, #6b7280)', marginTop: 4 }}>
            Prompt & completion tokens
          </div>
        </div>

        {/* Requests Card */}
        <div className="admin-stat-card" style={{ padding: 20, borderRadius: 12, border: '1px solid var(--border-color, #e5e7eb)', background: 'var(--bg-card, #ffffff)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary, #6b7280)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>LLM Invocations</span>
            <span style={{ padding: 6, background: 'rgba(139, 92, 246, 0.1)', color: '#8b5cf6', borderRadius: 8 }}><Cpu size={16} /></span>
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary, #111827)' }}>
            {totalRequests.toLocaleString()}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary, #6b7280)', marginTop: 4 }}>
            Invocations in past {lookbackDays} days
          </div>
        </div>

        {/* Unit Cost Efficiency */}
        <div className="admin-stat-card" style={{ padding: 20, borderRadius: 12, border: '1px solid var(--border-color, #e5e7eb)', background: 'var(--bg-card, #ffffff)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary, #6b7280)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Cost / 1k Tokens</span>
            <span style={{ padding: 6, background: 'rgba(245, 158, 11, 0.1)', color: '#f59e0b', borderRadius: 8 }}><TrendingUp size={16} /></span>
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary, #111827)' }}>
            ${avgCostPer1k.toFixed(5)}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary, #6b7280)', marginTop: 4 }}>
            Aggregated cost efficiency
          </div>
        </div>
      </div>

      {/* Provider & Model Matrix Section */}
      <div style={{ background: 'var(--bg-card, #ffffff)', borderRadius: 12, border: '1px solid var(--border-color, #e5e7eb)', padding: 20, marginBottom: 24 }}>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <CreditCard size={18} style={{ color: 'var(--primary-color, #4f46e5)' }} />
          <span>Provider & Model Consumption Matrix</span>
        </h2>

        {!data?.by_provider_model || data.by_provider_model.length === 0 ? (
          <div style={{ padding: '36px 0', textAlign: 'center', color: 'var(--text-secondary, #6b7280)', fontSize: 14 }}>
            No token activity recorded in the selected {lookbackDays}-day window.
          </div>
        ) : (
          <div className="table-wrapper" style={{ overflowX: 'auto' }}>
            <table className="admin-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color, #e5e7eb)', textAlign: 'left', color: 'var(--text-secondary, #6b7280)' }}>
                  <th style={{ padding: '10px 12px', fontWeight: 600 }}>Provider</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600 }}>Model</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600, textAlign: 'right' }}>Requests</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600, textAlign: 'right' }}>Tokens</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600, textAlign: 'right' }}>Share (%)</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600, textAlign: 'right' }}>Cost (USD)</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600, textAlign: 'right' }}>Cost (INR)</th>
                  <th style={{ padding: '10px 12px', fontWeight: 600 }}>Tier Badge</th>
                </tr>
              </thead>
              <tbody>
                {data.by_provider_model.map((row, idx) => {
                  const sharePct = totalTokens > 0 ? ((row.total_tokens / totalTokens) * 100).toFixed(1) : '0.0'
                  const costInr = (row.estimated_cost_usd * USD_TO_INR_ESTIMATE).toFixed(2)
                  const providerLower = (row.provider || '').toLowerCase()
                  const isGroq = providerLower.includes('groq')
                  const isOpenRouter = providerLower.includes('openrouter')
                  const isGemini = providerLower.includes('gemini')

                  return (
                    <tr key={`${row.provider}-${row.model}-${idx}`} style={{ borderBottom: '1px solid var(--border-color, #f3f4f6)' }}>
                      <td style={{ padding: '12px' }}>
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '3px 8px',
                            borderRadius: 6,
                            fontSize: 12,
                            fontWeight: 600,
                            background: isGroq ? 'rgba(234, 88, 12, 0.1)' : isOpenRouter ? 'rgba(99, 102, 241, 0.1)' : 'rgba(16, 185, 129, 0.1)',
                            color: isGroq ? '#ea580c' : isOpenRouter ? '#6366f1' : '#10b981',
                          }}
                        >
                          {row.provider}
                        </span>
                      </td>
                      <td style={{ padding: '12px', fontFamily: 'monospace', fontSize: 13, fontWeight: 500 }}>
                        {row.model}
                      </td>
                      <td style={{ padding: '12px', textAlign: 'right' }}>{row.request_count.toLocaleString()}</td>
                      <td style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>{row.total_tokens.toLocaleString()}</td>
                      <td style={{ padding: '12px', textAlign: 'right' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 6 }}>
                          <span>{sharePct}%</span>
                          <div style={{ width: 48, height: 6, background: 'var(--bg-secondary, #e5e7eb)', borderRadius: 3, overflow: 'hidden' }}>
                            <div style={{ width: `${sharePct}%`, height: '100%', background: 'var(--primary-color, #4f46e5)' }} />
                          </div>
                        </div>
                      </td>
                      <td style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>${row.estimated_cost_usd.toFixed(4)}</td>
                      <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-secondary, #6b7280)' }}>₹{costInr}</td>
                      <td style={{ padding: '12px' }}>
                        <span
                          style={{
                            padding: '2px 8px',
                            borderRadius: 12,
                            fontSize: 11,
                            fontWeight: 500,
                            background: isGroq ? '#fef3c7' : '#ecfdf5',
                            color: isGroq ? '#92400e' : '#065f46',
                            border: `1px solid ${isGroq ? '#fde68a' : '#a7f3d0'}`,
                          }}
                        >
                          {isGroq ? 'Fast Inference Tier' : isOpenRouter ? 'Enterprise API' : 'Direct API'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Department Allocation Breakdown Section */}
      <div style={{ background: 'var(--bg-card, #ffffff)', borderRadius: 12, border: '1px solid var(--border-color, #e5e7eb)', padding: 20, marginBottom: 24 }}>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Building2 size={18} style={{ color: 'var(--primary-color, #4f46e5)' }} />
          <span>Department Usage & Cost Allocation</span>
        </h2>

        {!data?.by_department || data.by_department.length === 0 ? (
          <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-secondary, #6b7280)', fontSize: 14 }}>
            Department breakdown will populate as employees execute queries across Engineering, HR, Finance, and Enterprise.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
            {data.by_department.map((dept) => {
              const deptPct = totalTokens > 0 ? ((dept.total_tokens / totalTokens) * 100).toFixed(1) : '0.0'
              const deptCostInr = (dept.estimated_cost_usd * USD_TO_INR_ESTIMATE).toFixed(2)

              return (
                <div
                  key={dept.department}
                  style={{
                    padding: 16,
                    borderRadius: 10,
                    border: '1px solid var(--border-color, #e5e7eb)',
                    background: 'var(--bg-secondary, #fafafa)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>{dept.department}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--primary-color, #4f46e5)' }}>{deptPct}% of total</span>
                  </div>

                  <div style={{ width: '100%', height: 6, background: '#e5e7eb', borderRadius: 3, overflow: 'hidden', marginBottom: 12 }}>
                    <div style={{ width: `${deptPct}%`, height: '100%', background: 'var(--primary-color, #4f46e5)' }} />
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary, #6b7280)' }}>Tokens:</span>
                    <span style={{ fontWeight: 600 }}>{dept.total_tokens.toLocaleString()}</span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary, #6b7280)' }}>Cost:</span>
                    <span style={{ fontWeight: 600 }}>${dept.estimated_cost_usd.toFixed(4)} (₹{deptCostInr})</span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                    <span style={{ color: 'var(--text-secondary, #6b7280)' }}>Queries:</span>
                    <span>{dept.request_count.toLocaleString()}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Observability & Pricing Guardrail Notes */}
      <div style={{ padding: 16, borderRadius: 10, background: 'rgba(99, 102, 241, 0.05)', border: '1px solid rgba(99, 102, 241, 0.15)', display: 'flex', gap: 12 }}>
        <Info size={20} style={{ color: '#6366f1', flexShrink: 0, marginTop: 2 }} />
        <div style={{ fontSize: 13, color: 'var(--text-secondary, #4b5563)', lineHeight: 1.5 }}>
          <strong style={{ color: 'var(--text-primary, #111827)' }}>Enterprise Pricing & Observability Note:</strong>
          <br />
          Cortex runs dual-tier model orchestration: Fast routing and classification models execute with minimal latency, while heavy synthesis models execute over Groq / OpenRouter with token metering.
          Local ONNX FastEmbed retrieval and PostgreSQL vector lookups execute with zero API cost ($0.00). All estimates reflect standard published API rate cards.
        </div>
      </div>
    </div>
  )
}
