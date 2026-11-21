import { useMemo, useState } from 'react'
import {
  CheckCircle2,
  Clock,
  ExternalLink,
  GitBranch,
  Layers,
  Sparkles,
  X,
  Zap,
  ArrowRight,
  Database,
  Search,
  Code,
  FileText,
  FileCheck,
  Shield,
  MessageSquare,
  Activity,
  AlertTriangle,
} from 'lucide-react'
import { Modal } from './Modal'

export interface DagNodeDetail {
  status: 'completed' | 'skipped' | 'failed' | 'active'
  latency_ms: number
  input_summary: string
  output_summary: string
}

export interface DagTraceData {
  nodes: Array<{
    id: string
    label: string
    category: string
    description: string
  }>
  edges: Array<{
    source: string
    target: string
    active: boolean
  }>
  executed_node_ids: string[]
  node_details: Record<string, DagNodeDetail>
  total_latency_ms: number
  confidence: string
}

interface DagVisualizerModalProps {
  dagTrace: DagTraceData
  onClose: () => void
}

function getNodeIcon(id: string) {
  switch (id) {
    case 'user_query':
      return <MessageSquare size={16} />
    case 'memory_context':
      return <Sparkles size={16} />
    case 'planner':
      return <GitBranch size={16} />
    case 'internal_rag':
      return <Database size={16} />
    case 'crag_recovery':
      return <RefreshCwIcon size={16} />
    case 'reranker':
      return <Layers size={16} />
    case 'github_search':
      return <Code size={16} />
    case 'drive_search':
      return <FileText size={16} />
    case 'sql_lookup':
      return <Database size={16} />
    case 'web_search':
      return <Search size={16} />
    case 'action_workflow':
      return <FileCheck size={16} />
    case 'admin_ops':
      return <Shield size={16} />
    case 'direct_chat':
      return <MessageSquare size={16} />
    case 'synthesis':
      return <Zap size={16} />
    default:
      return <Activity size={16} />
  }
}

function RefreshCwIcon({ size = 16 }: { size?: number }) {
  return <Activity size={size} />
}

export function DagVisualizerModal({ dagTrace, onClose }: DagVisualizerModalProps) {
  const executedSet = useMemo(() => new Set(dagTrace?.executed_node_ids || []), [dagTrace])
  const [selectedNodeId, setSelectedNodeId] = useState<string>(() => {
    // Default to the first interesting executed tool, or planner, or user_query
    const tool = dagTrace?.executed_node_ids?.find((id) =>
      ['internal_rag', 'github_search', 'drive_search', 'sql_lookup', 'action_workflow', 'web_search', 'planner'].includes(id)
    )
    return tool || dagTrace?.executed_node_ids?.[0] || 'user_query'
  })

  const selectedNode = useMemo(() => {
    return dagTrace?.nodes?.find((n) => n.id === selectedNodeId) || null
  }, [dagTrace, selectedNodeId])

  const selectedDetail = useMemo(() => {
    return dagTrace?.node_details?.[selectedNodeId] || null
  }, [dagTrace, selectedNodeId])

  // Group nodes into logical DAG layers for intuitive visual pipeline layout
  const layers = useMemo(() => {
    const layerDefs = [
      { id: 'input', label: '1. Ingestion', nodeIds: ['user_query'] },
      { id: 'context', label: '2. Context & Memories', nodeIds: ['memory_context'] },
      { id: 'orchestration', label: '3. Task Planning', nodeIds: ['planner'] },
      {
        id: 'execution',
        label: '4. Distributed Execution & Connectors',
        nodeIds: [
          'internal_rag',
          'crag_recovery',
          'reranker',
          'github_search',
          'drive_search',
          'sql_lookup',
          'web_search',
          'action_workflow',
          'admin_ops',
          'direct_chat',
        ],
      },
      { id: 'synthesis', label: '5. Grounded Synthesis', nodeIds: ['synthesis'] },
    ]

    return layerDefs.map((layer) => ({
      ...layer,
      nodes: (dagTrace?.nodes || []).filter((n) => layer.nodeIds.includes(n.id)),
    }))
  }, [dagTrace])

  return (
    <Modal title="Interactive Execution DAG Visualizer" onClose={onClose} wide>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Top Telemetry Strip */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
            padding: '12px 16px',
            background: 'var(--bg-secondary, #f8fafc)',
            borderRadius: 10,
            border: '1px solid var(--border-color, #e2e8f0)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <Clock size={15} style={{ color: '#6366f1' }} />
              <span>Total Latency:</span>
              <strong style={{ color: '#0f172a' }}>{dagTrace.total_latency_ms} ms</strong>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <Zap size={15} style={{ color: '#10b981' }} />
              <span>Executed:</span>
              <strong style={{ color: '#0f172a' }}>
                {dagTrace.executed_node_ids.length} of {dagTrace.nodes.length} nodes
              </strong>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <span>Confidence:</span>
              <span
                style={{
                  padding: '2px 8px',
                  borderRadius: 12,
                  fontSize: 11,
                  fontWeight: 600,
                  background:
                    dagTrace.confidence === 'HIGH'
                      ? 'rgba(16, 185, 129, 0.1)'
                      : dagTrace.confidence === 'FUZZY'
                      ? 'rgba(245, 158, 11, 0.1)'
                      : 'rgba(239, 68, 68, 0.1)',
                  color:
                    dagTrace.confidence === 'HIGH'
                      ? '#10b981'
                      : dagTrace.confidence === 'FUZZY'
                      ? '#f59e0b'
                      : '#ef4444',
                }}
              >
                {dagTrace.confidence}
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#10b981' }} />
              Executed
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#94a3b8' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#cbd5e1' }} />
              Skipped
            </span>
          </div>
        </div>

        {/* Split View: Left DAG Canvas, Right Node Inspector */}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.8fr) minmax(280px, 1.2fr)', gap: 16 }}>
          {/* DAG Pipeline Flow Canvas */}
          <div
            style={{
              background: 'var(--bg-primary, #ffffff)',
              border: '1px solid var(--border-color, #e2e8f0)',
              borderRadius: 12,
              padding: 16,
              maxHeight: 520,
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {layers.map((layer, layerIdx) => (
                <div key={layer.id} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      color: 'var(--text-secondary, #64748b)',
                    }}
                  >
                    {layer.label}
                  </div>

                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: layer.id === 'execution' ? 'repeat(auto-fill, minmax(140px, 1fr))' : 'repeat(auto-fill, minmax(200px, 1fr))',
                      gap: 10,
                    }}
                  >
                    {layer.nodes.map((node) => {
                      const isExecuted = executedSet.has(node.id)
                      const isSelected = selectedNodeId === node.id
                      const detail = dagTrace.node_details?.[node.id]

                      return (
                        <div
                          key={node.id}
                          onClick={() => setSelectedNodeId(node.id)}
                          style={{
                            padding: '10px 12px',
                            borderRadius: 10,
                            cursor: 'pointer',
                            transition: 'all 0.15s ease',
                            border: isSelected
                              ? '2px solid #6366f1'
                              : isExecuted
                              ? '1px solid #10b981'
                              : '1px dashed #cbd5e1',
                            background: isSelected
                              ? 'rgba(99, 102, 241, 0.08)'
                              : isExecuted
                              ? 'rgba(16, 185, 129, 0.04)'
                              : 'rgba(241, 245, 249, 0.4)',
                            opacity: isExecuted ? 1 : 0.6,
                            boxShadow: isExecuted ? '0 1px 3px rgba(0,0,0,0.04)' : 'none',
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: isExecuted ? '#0f172a' : '#64748b' }}>
                              {getNodeIcon(node.id)}
                              <span style={{ fontSize: 13, fontWeight: isExecuted ? 600 : 500 }}>{node.label}</span>
                            </div>
                            {isExecuted && <CheckCircle2 size={13} style={{ color: '#10b981', flexShrink: 0 }} />}
                          </div>

                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: '#64748b', marginTop: 4 }}>
                            <span>{node.category}</span>
                            {isExecuted && detail && (
                              <span style={{ fontWeight: 600, color: '#0f172a' }}>{detail.latency_ms} ms</span>
                            )}
                            {!isExecuted && <span style={{ color: '#94a3b8' }}>Skipped</span>}
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  {layerIdx < layers.length - 1 && (
                    <div style={{ display: 'flex', justifyContent: 'center', padding: '2px 0' }}>
                      <ArrowRight size={14} style={{ color: '#94a3b8', transform: 'rotate(90deg)' }} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Right Inspector Drawer */}
          <div
            style={{
              background: 'var(--bg-secondary, #f8fafc)',
              border: '1px solid var(--border-color, #e2e8f0)',
              borderRadius: 12,
              padding: 16,
              display: 'flex',
              flexDirection: 'column',
              maxHeight: 520,
              overflowY: 'auto',
            }}
          >
            {selectedNode && selectedDetail ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {/* Node Header */}
                <div style={{ borderBottom: '1px solid var(--border-color, #e2e8f0)', paddingBottom: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <div style={{ padding: 6, background: '#e0e7ff', color: '#4338ca', borderRadius: 6 }}>
                      {getNodeIcon(selectedNode.id)}
                    </div>
                    <div>
                      <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: '#0f172a' }}>
                        {selectedNode.label}
                      </h3>
                      <span style={{ fontSize: 11, fontFamily: 'monospace', color: '#64748b' }}>
                        id: {selectedNode.id}
                      </span>
                    </div>
                  </div>
                  <p style={{ margin: '6px 0 0', fontSize: 12, color: '#64748b', lineHeight: 1.4 }}>
                    {selectedNode.description}
                  </p>
                </div>

                {/* Node Status & Latency Badges */}
                <div style={{ display: 'flex', gap: 8 }}>
                  <span
                    style={{
                      padding: '3px 8px',
                      borderRadius: 6,
                      fontSize: 11,
                      fontWeight: 600,
                      background: selectedDetail.status === 'completed' ? '#dcfce7' : '#f1f5f9',
                      color: selectedDetail.status === 'completed' ? '#15803d' : '#64748b',
                    }}
                  >
                    STATUS: {selectedDetail.status.toUpperCase()}
                  </span>
                  {selectedDetail.latency_ms > 0 && (
                    <span
                      style={{
                        padding: '3px 8px',
                        borderRadius: 6,
                        fontSize: 11,
                        fontWeight: 600,
                        background: '#e0e7ff',
                        color: '#4338ca',
                      }}
                    >
                      LATENCY: {selectedDetail.latency_ms} ms
                    </span>
                  )}
                </div>

                {/* Input Telemetry */}
                <div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', display: 'block', marginBottom: 4 }}>
                    Input Parameters & Query
                  </span>
                  <div
                    style={{
                      background: '#ffffff',
                      border: '1px solid #e2e8f0',
                      borderRadius: 8,
                      padding: 10,
                      fontSize: 12,
                      fontFamily: 'monospace',
                      color: '#0f172a',
                      maxHeight: 120,
                      overflowY: 'auto',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {selectedDetail.input_summary || 'No explicit parameters passed.'}
                  </div>
                </div>

                {/* Output Telemetry */}
                <div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', display: 'block', marginBottom: 4 }}>
                    Node Output / Result Summary
                  </span>
                  <div
                    style={{
                      background: '#ffffff',
                      border: '1px solid #e2e8f0',
                      borderRadius: 8,
                      padding: 10,
                      fontSize: 12,
                      fontFamily: 'monospace',
                      color: '#0f172a',
                      maxHeight: 160,
                      overflowY: 'auto',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {selectedDetail.output_summary || 'No output recorded.'}
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '36px 0', color: '#94a3b8', fontSize: 13 }}>
                Click on any node in the graph to inspect its telemetry, inputs, and outputs.
              </div>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}
