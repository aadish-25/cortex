import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowUp, BookOpen, Brain, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Clock, Copy, ExternalLink, FileSearch, FileText, LoaderCircle, MoreHorizontal, Pencil, Plus, RefreshCw, Search, ShieldAlert, ThumbsDown, ThumbsUp, X } from 'lucide-react'
import { api, streamChat, type StreamEvent } from '../api/client'
import type { ChatMessage, CitationData, Conversation, Page } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { CitationMarkdown } from '../components/CitationMarkdown'
import CortexLogo from '../components/CortexLogo'
import { Modal } from '../components/Modal'
import { SourcesDrawer } from '../components/SourcesDrawer'
import { ThemeButton, useTheme } from '../components/ThemeToggle'
import { AdminNotificationBell } from '../admin/AdminNotificationBell'
import { safeRandomUUID } from '../lib/uuid'

interface MemoryEventData {
  actions: string[]
  saved?: Array<{ memory_id: string; text: string; category: string }>
  updated?: Array<{ memory_id: string; text: string; category: string }>
  deleted_count?: number
  retrieved_count?: number
  rejected?: Array<{ content: string; reason: string }>
}

interface UIMessage extends ChatMessage {
  streaming?: boolean
  error?: string
  interrupted?: boolean
  steps?: Array<{ stage: string; message: string }>
  suggestions?: string[]
  actionDraft?: any
  corpusGap?: any
  memoryEvent?: MemoryEventData
}

function greeting() {
  const hour = new Date().getHours()
  return hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
}

function citationData(value?: CitationData | string[] | null): CitationData | null {
  if (!value) return null
  if (Array.isArray(value)) return { citations: value, citation_map: Object.fromEntries(value.map((item, index) => [String(index + 1), item])), evidence_chunks: [] }
  return value
}

function resolveMessageCitations(target: UIMessage, allMessages: UIMessage[]): CitationData | null {
  const direct = citationData(target.citations)
  if (direct && (direct.citations?.length || direct.evidence_chunks?.length || Object.keys(direct.citation_map || {}).length)) {
    return direct
  }
  // Only look at immediate parent if this is an explicit variant; do NOT bleed citations across unrelated turns
  if (target.parent_message_id) {
    const parent = allMessages.find((m) => m.id === target.parent_message_id)
    if (parent) {
      const parentData = citationData(parent.citations)
      if (parentData && (parentData.citations?.length || parentData.evidence_chunks?.length || Object.keys(parentData.citation_map || {}).length)) {
        return parentData
      }
    }
  }
  return direct
}

function groupMessages(messages: UIMessage[]) {
  const assistantByParent = new Map<string, UIMessage[]>()
  for (const message of messages) {
    if (message.sender === 'assistant' && message.parent_message_id) {
      const group = assistantByParent.get(message.parent_message_id) || []
      group.push(message)
      assistantByParent.set(message.parent_message_id, group)
    }
  }
  const consumed = new Set<string>()
  const output: Array<{ user?: UIMessage; assistants: UIMessage[]; standalone?: UIMessage }> = []
  for (const message of messages) {
    if (message.sender === 'user') {
      const variants = (assistantByParent.get(message.id) || []).sort((a, b) => a.variant_index - b.variant_index)
      variants.forEach((item) => consumed.add(item.id))
      output.push({ user: message, assistants: variants })
    } else if (!consumed.has(message.id)) {
      output.push({ assistants: [], standalone: message })
    }
  }
  return output
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}m ${secs}s`
}

function CompactingTrace() {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const startedAt = Date.now()
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <div className="execution-trace compacting-trace" role="status" aria-live="polite">
      <span className="compacting-shimmer">Compacting</span>
      <span className="compacting-sep">·</span>
      <span className="compacting-elapsed">{formatElapsed(elapsed)}</span>
    </div>
  )
}

const FEEDBACK_CATEGORIES = [
  { id: 'VISUAL_BROKEN', label: 'Visual is unhelpful or broken' },
  { id: 'INCORRECT_INCOMPLETE', label: 'Incorrect or incomplete' },
  { id: 'NOT_WHAT_ASKED', label: 'Not what I asked for' },
  { id: 'SLOW_BUGGY', label: 'Slow or buggy' },
  { id: 'STYLE_TONE', label: 'Style or tone' },
  { id: 'SAFETY_LEGAL', label: 'Safety or legal concern' },
  { id: 'OTHER', label: 'Other' },
]

function formatMessageTime(createdAt?: string | Date): string {
  if (!createdAt) return 'Today'
  const date = new Date(createdAt)
  const isToday = new Date().toDateString() === date.toDateString()
  const timeStr = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return isToday ? `Today, ${timeStr}` : `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${timeStr}`
}

function AssistantMessage({ message, variants, allMessages, onOpenSources, onRegenerate, onFeedback, onSuggestion, feedbackState, busy }: {
  message: UIMessage
  variants: UIMessage[]
  allMessages: UIMessage[]
  onOpenSources: (message: UIMessage, number?: number, customData?: CitationData | null) => void
  onRegenerate: (message: UIMessage, customInstruction?: string) => void
  onFeedback: (message: UIMessage, positive: boolean) => void
  onSuggestion: (suggestion: string) => void
  feedbackState?: Record<string, { positive: boolean; category?: string; comment?: string }>
  busy: boolean
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [submenuOpen, setSubmenuOpen] = useState(false)
  const [customInstruction, setCustomInstruction] = useState('')
  const [variantIndex, setVariantIndex] = useState(Math.max(0, variants.findIndex((item) => item.id === message.id)))
  useEffect(() => setVariantIndex(Math.max(0, variants.findIndex((item) => item.id === message.id))), [variants.length, message.id])
  const allVariants = variants.length ? variants : [message]
  const active = allVariants[Math.min(variantIndex, allVariants.length - 1)] || message
  const sources = resolveMessageCitations(active, allMessages)
  const currentFeedback = feedbackState?.[active.id]
  const hasSources = Boolean(sources?.citations?.length || sources?.evidence_chunks?.length || Object.keys(sources?.citation_map || {}).length)
  const latestStep = active.steps && active.steps.length > 0 ? active.steps[active.steps.length - 1] : null
  const isCompacting = Boolean(active.streaming && latestStep?.stage === 'compacting')

  return <article className="assistant-message">
    <div className="assistant-avatar"><CortexLogo size={27} /></div>
    <div className="assistant-message-content">
      <div className="assistant-author-row"><strong>Cortex</strong><span className="assistant-meta">{isCompacting ? 'Compacting' : active.streaming ? 'Working' : active.interrupted ? 'Partial response' : 'Answer'}</span></div>
      {latestStep && active.streaming && (
        isCompacting
          ? <CompactingTrace />
          : <div className="execution-trace"><span className="trace-pulse" />{latestStep.message}</div>
      )}
      {active.content ? <CitationMarkdown content={active.content} onOpenSource={(number) => onOpenSources(active, number, sources)} /> : active.streaming ? <div className="typing-indicator"><i /><i /><i /></div> : null}
      {active.memoryEvent && <MemoryEventCard event={active.memoryEvent} />}
      {active.corpusGap && <div className="corpus-gap-card"><ShieldAlert size={17} /><div><strong>Knowledge gap detected</strong><p>{active.corpusGap.message}</p><small>Confidence: {active.corpusGap.confidence} · Score: {active.corpusGap.top_reranker_score ?? '—'}</small></div></div>}
      {active.actionDraft && <ActionDraftCard draft={active.actionDraft} />}
      {active.error && <div className="inline-error">{active.error}</div>}
      {active.interrupted && <div className="partial-note">This response was interrupted. The partial answer has been saved.</div>}
      {!active.actionDraft && active.suggestions && active.suggestions.length > 0 && <div className="suggestion-row">{active.suggestions.map((suggestion) => <button key={suggestion} className="suggestion-pill" onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}</div>}
      {!active.streaming && <div className="message-toolbar">
        <div className="message-toolbar-left">
          <button className={`message-action like-btn ${currentFeedback?.positive === true ? 'active' : ''}`} onClick={() => onFeedback(active, true)} title={currentFeedback?.positive === true ? 'Undo helpful' : 'Helpful'} aria-label="Helpful"><ThumbsUp size={14} /></button>
          <button className={`message-action dislike-btn ${currentFeedback?.positive === false ? 'active' : ''}`} onClick={() => onFeedback(active, false)} title={currentFeedback?.positive === false ? 'Edit feedback' : 'Not helpful'} aria-label="Not helpful"><ThumbsDown size={14} /></button>
          <button className="message-action" onClick={() => void navigator.clipboard?.writeText(active.content)} title="Copy answer"><Copy size={14} /></button>
          <div className="message-menu-wrap">
            <button className="message-action" onClick={() => { setMenuOpen((value) => !value); setSubmenuOpen(false) }} aria-label="More answer actions"><MoreHorizontal size={17} /></button>
            {menuOpen && <><button className="menu-dismiss" onClick={() => { setMenuOpen(false); setSubmenuOpen(false) }} aria-label="Close actions" /><div className="message-menu chatgpt-popover">
              <div className="message-menu-time">{formatMessageTime(active.created_at)}</div>
              <div className="menu-item-with-submenu" onMouseEnter={() => setSubmenuOpen(true)}>
                <button
                  type="button"
                  className={`menu-item has-arrow ${submenuOpen ? 'active' : ''}`}
                  onClick={() => setSubmenuOpen((v) => !v)}
                >
                  <span className="menu-item-left"><RefreshCw size={14} /> Regenerate response</span>
                  <ChevronRight size={14} className="menu-chevron" />
                </button>
                {submenuOpen && (
                  <div className="message-submenu">
                    <div className="regenerate-input-wrap">
                      <input
                        type="text"
                        className="regenerate-input"
                        placeholder="Ask to change response"
                        value={customInstruction}
                        onChange={(e) => setCustomInstruction(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault()
                            if (customInstruction.trim() && !busy) {
                              onRegenerate(active, customInstruction.trim())
                              setMenuOpen(false)
                              setSubmenuOpen(false)
                              setCustomInstruction('')
                            }
                          }
                        }}
                        autoFocus
                      />
                      <button
                        type="button"
                        className="regenerate-submit-btn"
                        disabled={!customInstruction.trim() || busy}
                        onClick={() => {
                          if (customInstruction.trim() && !busy) {
                            onRegenerate(active, customInstruction.trim())
                            setMenuOpen(false)
                            setSubmenuOpen(false)
                            setCustomInstruction('')
                          }
                        }}
                        title="Submit change request"
                      >
                        <ArrowUp size={12} />
                      </button>
                    </div>
                    <hr className="menu-divider" />
                    <button
                      type="button"
                      className="menu-item"
                      disabled={busy}
                      onClick={() => {
                        onRegenerate(active)
                        setMenuOpen(false)
                        setSubmenuOpen(false)
                      }}
                    >
                      <span className="menu-item-left"><RefreshCw size={14} /> Try again</span>
                    </button>
                  </div>
                )}
              </div>
              <button
                type="button"
                className="menu-item"
                disabled={!hasSources}
                onClick={() => {
                  onOpenSources(active, undefined, sources)
                  setMenuOpen(false)
                  setSubmenuOpen(false)
                }}
              >
                <span className="menu-item-left"><FileSearch size={14} /> View sources</span>
              </button>
            </div></>}
          </div>
          {allVariants.length > 1 && <div className="variant-switcher"><button className="message-action" disabled={variantIndex === 0} onClick={() => setVariantIndex((value) => value - 1)}><ChevronLeft size={14} /></button><span>{active.variant_index || variantIndex + 1} / {allVariants.length}</span><button className="message-action" disabled={variantIndex >= allVariants.length - 1} onClick={() => setVariantIndex((value) => value + 1)}><ChevronRight size={14} /></button></div>}
        </div>
      </div>}
    </div>
  </article>
}

function MemoryEventCard({ event }: { event: MemoryEventData }) {
  const parts: string[] = []
  if (event.saved?.length) parts.push(`Saved ${event.saved.length}`)
  if (event.updated?.length) parts.push(`Updated ${event.updated.length}`)
  if (event.deleted_count) parts.push(`Deleted ${event.deleted_count}`)
  if (event.rejected?.length) parts.push(`Blocked ${event.rejected.length} (privacy policy)`)
  if (!parts.length && event.retrieved_count !== undefined) parts.push(`Retrieved ${event.retrieved_count}`)
  if (!parts.length) return null

  return (
    <div className="memory-event-card">
      <Brain size={15} />
      <div className="memory-event-copy">
        <strong>Memory updated</strong>
        <span>{parts.join(' · ')}</span>
      </div>
      <Link className="text-link" to="/memories">Manage</Link>
    </div>
  )
}

function ActionDraftCard({ draft }: { draft: any }) {
  const { user } = useAuth()
  const [currentDraft, setCurrentDraft] = useState(draft)
  const [status, setStatus] = useState<string>(draft.status || 'PENDING')
  const [confirmedData, setConfirmedData] = useState<{ ticket_id?: string; jira_key?: string; jira_url?: string } | null>(
    draft.jira_key ? { jira_key: draft.jira_key, jira_url: draft.jira_url } : null
  )

  // Countdown timer hook (15-min TTL)
  const [secondsLeft, setSecondsLeft] = useState<number>(() => {
    if (currentDraft.expires_at) {
      const exp = new Date(currentDraft.expires_at).getTime()
      const now = Date.now()
      return Math.max(0, Math.floor((exp - now) / 1000))
    }
    return currentDraft.ttl_seconds_remaining ?? 900
  })

  const isConfirmed = status === 'CONFIRMED' || status === 'COMMITTED' || status === 'CREATED' || Boolean(confirmedData?.jira_key)

  useEffect(() => {
    if (isConfirmed || status === 'DISCARDED' || secondsLeft <= 0) return
    const interval = setInterval(() => {
      setSecondsLeft((prev) => {
        if (prev <= 1) {
          clearInterval(interval)
          setStatus('EXPIRED')
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [isConfirmed, status, secondsLeft])

  const formatTimer = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return `${m}:${s < 10 ? '0' : ''}${s}`
  }

  // Modals: Review/Edit Modal & View Confirmed Ticket Modal
  const [isReviewOpen, setIsReviewOpen] = useState(false)
  const [isViewOpen, setIsViewOpen] = useState(false)

  // Editable fields in Review Modal
  const [editTitle, setEditTitle] = useState(currentDraft.title || '')
  const [editDescription, setEditDescription] = useState(currentDraft.description || '')
  const [editPriority, setEditPriority] = useState(currentDraft.priority || 'MEDIUM')
  const [editNotes, setEditNotes] = useState(currentDraft.domain_fields?.notes || '')
  const [savingEdit, setSavingEdit] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [reviewError, setReviewError] = useState('')

  async function handleConfirmSubmit(e?: FormEvent) {
    if (e) e.preventDefault()
    setSubmitting(true)
    setReviewError('')
    try {
      // 1. If fields were edited, update draft first
      if (
        editTitle.trim() !== (currentDraft.title || '') ||
        editDescription.trim() !== (currentDraft.description || '') ||
        editPriority !== (currentDraft.priority || 'MEDIUM') ||
        editNotes.trim() !== (currentDraft.domain_fields?.notes || '')
      ) {
        const updated = await api<any>(`/api/v1/actions/${currentDraft.draft_id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            title: editTitle.trim(),
            description: editDescription.trim(),
            priority: editPriority,
            domain_fields: editNotes.trim() ? { notes: editNotes.trim() } : {},
          }),
        })
        setCurrentDraft((prev: any) => ({ ...prev, ...updated }))
      }

      // 2. Confirm and dispatch to Jira
      const result = await api<any>(`/api/v1/actions/${currentDraft.draft_id}/confirm`, { method: 'POST' })
      setStatus('CONFIRMED')
      setConfirmedData({
        ticket_id: result.ticket_id,
        jira_key: result.jira_key || result.ticket_id?.slice(0, 8),
        jira_url: result.jira_url,
      })
      setIsReviewOpen(false)
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : 'Could not submit Jira ticket.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDiscardDraft() {
    setSubmitting(true)
    setReviewError('')
    try {
      await api<any>(`/api/v1/actions/${currentDraft.draft_id}/discard`, { method: 'POST' })
      setStatus('DISCARDED')
      setIsReviewOpen(false)
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : 'Could not discard draft.')
    } finally {
      setSubmitting(false)
    }
  }

  const assignedKey = confirmedData?.jira_key || currentDraft.jira_key || `Jira-${currentDraft.project_key || 'ENG'}-17`

  return (
    <div className="action-draft-card">
      <div className="action-draft-heading">
        <div className="action-draft-badge-row">
          <span className="action-draft-icon">
            <FileText size={15} />
          </span>
          <div className="action-draft-meta-tags">
            <span className="workflow-tag">PROPOSED JIRA WORKFLOW</span>
            <span className="project-key-tag">[{currentDraft.project_key || 'ENG'}]</span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className={`priority-tag ${String(editPriority || currentDraft.priority || 'MEDIUM').toLowerCase()}`}>
            {editPriority || currentDraft.priority || 'MEDIUM'}
          </span>
          <span className={`status-pill ${isConfirmed ? 'active' : status === 'DISCARDED' ? 'inactive' : status === 'EXPIRED' ? 'inactive' : 'pending'}`}>
            {isConfirmed ? 'CONFIRMED' : status}
          </span>
        </div>
      </div>

      <div className="action-draft-content">
        <h4 className="ticket-title">{editTitle || currentDraft.title || 'Official Ticket Request'}</h4>
        <div className="ticket-meta-pills">
          <span><strong>Department:</strong> {currentDraft.department || 'Engineering'}</span>
          <span><strong>Category:</strong> {currentDraft.taxonomy_tier || 'Service Request'}</span>
          <span><strong>Requester:</strong> {user?.name || 'Employee'}</span>
        </div>
        <p className="ticket-description">{editDescription || currentDraft.description || 'No description provided.'}</p>
        {Boolean(editNotes || currentDraft.domain_fields?.notes) && (
          <div className="ticket-notes-box">
            <small>ATTACHED NOTES / DETAILS:</small>
            <p>{editNotes || currentDraft.domain_fields.notes}</p>
          </div>
        )}
      </div>

      {isConfirmed ? (
        <div className="ticket-confirmed-box">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <CheckCircle2 size={22} color="var(--success)" style={{ flexShrink: 0 }} />
            <div>
              <strong style={{ fontSize: 13, color: 'var(--text)' }}>Ticket Created Successfully</strong>
              <div style={{ fontSize: 12, color: 'var(--text-soft)', marginTop: 2 }}>
                Your ticket has been successfully raised. Please note that your ticket ID is <strong className="jira-key-badge">{assignedKey}</strong>.
              </div>
            </div>
          </div>
          <button
            type="button"
            className="primary-button compact-button"
            style={{ fontSize: 11, height: 28, padding: '0 12px', flexShrink: 0 }}
            onClick={() => setIsViewOpen(true)}
          >
            View ticket
          </button>
        </div>
      ) : status === 'DISCARDED' ? (
        <div className="ticket-discarded-box">
          <small>This draft was discarded and not submitted to Jira.</small>
        </div>
      ) : status === 'EXPIRED' ? (
        <div className="ticket-discarded-box">
          <small>This draft has expired (15-minute TTL exceeded). Please request a new ticket.</small>
        </div>
      ) : (
        <div className="action-draft-actions">
          <div className="action-draft-timer">
            <Clock size={12} />
            <span>Expires in {formatTimer(secondsLeft)}</span>
          </div>
          <button
            type="button"
            className="primary-button compact-button"
            disabled={secondsLeft <= 0}
            onClick={() => {
              setEditTitle(currentDraft.title || '')
              setEditDescription(currentDraft.description || '')
              setEditPriority(currentDraft.priority || 'MEDIUM')
              setEditNotes(currentDraft.domain_fields?.notes || '')
              setReviewError('')
              setIsReviewOpen(true)
            }}
          >
            <FileText size={12} /> Review draft
          </button>
        </div>
      )}

      {/* 1. Review & Submit Modal */}
      {isReviewOpen && (
        <Modal title="Review & Submit Jira Ticket" onClose={() => setIsReviewOpen(false)} wide>
          <form className="stack-form" onSubmit={handleConfirmSubmit}>
            <div style={{ padding: '10px 14px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 8, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, fontSize: 11 }}>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>TARGET KEY</small><strong>Jira-[{currentDraft.project_key || 'ENG'}]-PENDING</strong></div>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>PROJECT SPACE</small><strong>[{currentDraft.project_key || 'ENG'}]</strong></div>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>DEPARTMENT</small><strong>{currentDraft.department || 'Engineering'}</strong></div>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>REQUESTER</small><strong>{user?.name}</strong></div>
            </div>

            <label style={{ marginTop: 10 }}>
              Summary / Title *
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="Brief summary of the issue or request"
                required
              />
            </label>

            <label>
              Priority
              <select value={editPriority} onChange={(e) => setEditPriority(e.target.value)}>
                <option value="LOW">Low</option>
                <option value="MEDIUM">Medium</option>
                <option value="HIGH">High</option>
                <option value="CRITICAL">Critical</option>
              </select>
            </label>

            <label>
              Detailed Description *
              <textarea
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                rows={5}
                placeholder="Detailed explanation, impact, steps to reproduce, or justification"
                required
              />
            </label>

            <label>
              Additional Information / Attachment Notes (Optional)
              <input
                value={editNotes}
                onChange={(e) => setEditNotes(e.target.value)}
                placeholder="e.g. Receipt image link, serial number, cost center"
              />
            </label>

            {reviewError && <div className="inline-error">{reviewError}</div>}

            <div className="modal-actions" style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button
                type="button"
                className="secondary-button"
                style={{ color: 'var(--danger)', borderColor: 'var(--danger-soft)' }}
                onClick={() => void handleDiscardDraft()}
                disabled={submitting}
              >
                Discard Draft
              </button>
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="button" className="secondary-button" onClick={() => setIsReviewOpen(false)} disabled={submitting}>
                  Cancel
                </button>
                <button type="submit" className="primary-button" disabled={submitting || !editTitle.trim() || !editDescription.trim()}>
                  {submitting ? 'Submitting to Jira…' : 'Confirm & Submit Ticket'}
                </button>
              </div>
            </div>
          </form>
        </Modal>
      )}

      {/* 2. View Confirmed Ticket Modal */}
      {isViewOpen && (
        <Modal title={`Ticket Details — ${assignedKey}`} onClose={() => setIsViewOpen(false)} wide>
          <div className="stack-form">
            <div style={{ padding: '12px 14px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 8, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, fontSize: 11 }}>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>TICKET ID</small><strong className="jira-key-badge">{assignedKey}</strong></div>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>STATUS</small><strong style={{ color: 'var(--success)' }}>CREATED (Active)</strong></div>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>PRIORITY</small><strong>{editPriority || currentDraft.priority || 'MEDIUM'}</strong></div>
              <div><small style={{ color: 'var(--muted)', display: 'block' }}>DEPARTMENT</small><strong>{currentDraft.department || 'Engineering'}</strong></div>
            </div>

            <div style={{ marginTop: 10 }}>
              <small style={{ color: 'var(--muted)', display: 'block', marginBottom: 4, fontSize: 11, fontWeight: 700 }}>SUMMARY</small>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', padding: '8px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6 }}>
                {editTitle || currentDraft.title}
              </div>
            </div>

            <div>
              <small style={{ color: 'var(--muted)', display: 'block', marginBottom: 4, fontSize: 11, fontWeight: 700 }}>DESCRIPTION</small>
              <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--text)', padding: '10px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, whiteSpace: 'pre-wrap' }}>
                {editDescription || currentDraft.description}
              </div>
            </div>

            {Boolean(editNotes || currentDraft.domain_fields?.notes) && (
              <div>
                <small style={{ color: 'var(--muted)', display: 'block', marginBottom: 4, fontSize: 11, fontWeight: 700 }}>ATTACHED NOTES & ASSETS</small>
                <div style={{ fontSize: 12, color: 'var(--text)', padding: '8px 12px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 6 }}>
                  {editNotes || currentDraft.domain_fields?.notes}
                </div>
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 11, color: 'var(--muted)' }}>
              <div><strong>Requester:</strong> {user?.name} ({user?.email})</div>
              <div><strong>Category:</strong> {currentDraft.taxonomy_tier || 'Service Request'}</div>
            </div>

            <div className="modal-actions" style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              {confirmedData?.jira_url && (
                <a
                  href={confirmedData.jira_url}
                  target="_blank"
                  rel="noreferrer"
                  className="secondary-button"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                  Open in Jira Cloud <ExternalLink size={12} />
                </a>
              )}
              <button type="button" className="primary-button" onClick={() => setIsViewOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

export function ChatPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const { user, signOut } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const [messages, setMessages] = useState<UIMessage[]>([])
  const [input, setInput] = useState('')
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyPage, setHistoryPage] = useState(1)
  const [historyPages, setHistoryPages] = useState(1)
  const [isStreaming, setIsStreaming] = useState(false)
  const [selectedModel, setSelectedModel] = useState('')
  const [models, setModels] = useState<Array<{ id: string; name: string; provider: string; badge: string }>>([])
  const [sourceMessage, setSourceMessage] = useState<UIMessage | null>(null)
  const [sourceNumber, setSourceNumber] = useState<number | null>(null)
  const [sourceData, setSourceData] = useState<CitationData | null>(null)
  const [toast, setToast] = useState('')
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(''), 3500)
      return () => clearTimeout(timer)
    }
  }, [toast])
  const [feedbackState, setFeedbackState] = useState<Record<string, { positive: boolean; category?: string; comment?: string }>>({})
  const [dislikeModalTarget, setDislikeModalTarget] = useState<UIMessage | null>(null)
  const [dislikeCategory, setDislikeCategory] = useState<string>('')
  const [dislikeComment, setDislikeComment] = useState<string>('')
  const [submittingFeedback, setSubmittingFeedback] = useState<boolean>(false)
  const [showModelPicker, setShowModelPicker] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadMessages = useCallback(async (id: string, page = 1, prepend = false) => {
    setLoadingHistory(true)
    try {
      const result = await api<Page<ChatMessage>>(`/api/v1/chat/conversations/${id}/messages?page=${page}&page_size=40`)
      setMessages((current) => {
        const currentById = new Map(current.map((item) => [item.id, item]))
        const mapped = result.items.map((item) => {
          const prev = currentById.get(item.id)
          return {
            ...item,
            interrupted: item.is_complete === false,
            actionDraft: prev?.actionDraft,
            suggestions: prev?.suggestions,
            corpusGap: prev?.corpusGap,
            memoryEvent: prev?.memoryEvent,
            steps: prev?.steps,
          }
        }) as UIMessage[]
        if (!prepend || page === 1) return mapped
        const existing = new Set(current.map((item) => item.id))
        return [...mapped.filter((item) => !existing.has(item.id)), ...current]
      })
      setHistoryPage(result.page)
      setHistoryPages(result.total_pages)
    } catch (error) { setToast(error instanceof Error ? error.message : 'Could not load this conversation.') }
    finally { setLoadingHistory(false) }
  }, [])

  useEffect(() => {
    // Prevent flicker: if messages already exist for this active conversation (e.g. freshly streamed first turn),
    // retain them smoothly in memory while loadMessages syncs in the background.
    if (conversationId && messages.length > 0 && messages.some((m) => m.conversation_id === conversationId || !m.conversation_id)) {
      setMessages((current) => current.map((m) => (m.conversation_id ? m : { ...m, conversation_id: conversationId })))
      void loadMessages(conversationId, 1)
      return
    }

    abortRef.current?.abort()
    setIsStreaming(false)
    setInput('')
    if (conversationId) {
      setMessages([]); setHistoryPage(1); setHistoryPages(1)
      void loadMessages(conversationId, 1)
    } else {
      setMessages([]); setHistoryPage(1); setHistoryPages(1)
    }
  }, [conversationId, loadMessages])

  useEffect(() => {
    api<Array<{ id: string; name: string; provider: string; badge: string }>>('/api/v1/chat/available-models').then((items) => {
      setModels(items)
      if (items[0]) setSelectedModel((current) => current || items[0].id)
    }).catch(() => undefined)
  }, [])

  const lastMessageContent = messages[messages.length - 1]?.content || ''
  useEffect(() => {
    if (scrollRef.current && !loadingHistory && historyPage === 1) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages.length, lastMessageContent, isStreaming, loadingHistory, historyPage])

  function patchMessage(id: string, update: Partial<UIMessage>) {
    setMessages((current) => current.map((item) => item.id === id ? { ...item, ...update } : item))
  }

  function handleStreamEvent(assistantId: string, event: StreamEvent) {
    if (event.type === 'token') {
      setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, content: item.content + (event.data?.delta || '') } : item))
    } else if (event.type === 'step') {
      setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, steps: [...(item.steps || []), event.data] } : item))
    } else if (event.type === 'citations') {
      patchMessage(assistantId, { citations: event.data as CitationData })
    } else if (event.type === 'suggestions') {
      patchMessage(assistantId, { suggestions: event.data?.suggestions || [] })
    } else if (event.type === 'action_card') {
      patchMessage(assistantId, { actionDraft: event.data })
    } else if (event.type === 'memory_event') {
      patchMessage(assistantId, { memoryEvent: event.data as MemoryEventData })
    } else if (event.type === 'corpus_gap') {
      patchMessage(assistantId, { corpusGap: event.data })
    } else if (event.type === 'interrupted') {
      patchMessage(assistantId, { streaming: false, interrupted: true, id: event.data?.message_id || assistantId })
      setIsStreaming(false)
    } else if (event.type === 'error') {
      if (!event.data?.retryable) {
        patchMessage(assistantId, { streaming: false, error: event.data?.message || event.data?.error || 'The response could not be completed.' })
        setIsStreaming(false)
      }
    } else if (event.type === 'session_terminated') {
      void signOut().finally(() => navigate('/login', { replace: true }))
    } else if (event.type === 'done') {
      const doneId = event.data?.message_id || assistantId
      const newConvId = event.data?.conversation_id
      if (newConvId) {
        setMessages((current) => current.map((m) => (m.conversation_id ? m : { ...m, conversation_id: newConvId })))
      }
      patchMessage(assistantId, { id: doneId, streaming: false })
      setIsStreaming(false)
      if (newConvId && !conversationId) navigate(`/chat/${newConvId}`, { replace: true })
      if (newConvId) void loadMessages(newConvId, 1)
    }
  }

  async function startStream(path: string, body: Record<string, unknown>, assistantId: string) {
    const controller = new AbortController()
    abortRef.current = controller
    setIsStreaming(true)
    try {
      await streamChat(path, body, (event) => handleStreamEvent(assistantId, event), () => { void signOut().finally(() => navigate('/login', { replace: true })) }, controller.signal)
    } catch (error) {
      if (!controller.signal.aborted) {
        patchMessage(assistantId, { streaming: false, error: error instanceof Error ? error.message : 'The response stream failed.' })
        setIsStreaming(false)
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  async function sendMessage(text = input) {
    const clean = text.trim()
    if (!clean || isStreaming) return
    const now = new Date().toISOString()
    const assistantId = safeRandomUUID()
    const userMessage: UIMessage = { id: safeRandomUUID(), conversation_id: conversationId || '', sender: 'user', content: clean, variant_index: 1, created_at: now }
    const assistantMessage: UIMessage = { id: assistantId, conversation_id: conversationId || '', sender: 'assistant', content: '', variant_index: 1, created_at: now, streaming: true, steps: [] }
    setMessages((current) => [...current, userMessage, assistantMessage])
    setInput('')
    await startStream('/api/v1/chat/message', { message: clean, conversation_id: conversationId || null, message_id: assistantId, selected_model: selectedModel || null }, assistantId)
  }

  async function regenerate(message: UIMessage, customInstruction?: string) {
    if (isStreaming) return
    const assistantId = safeRandomUUID()
    const placeholder: UIMessage = { ...message, id: assistantId, content: '', streaming: true, error: undefined, interrupted: false, variant_index: message.variant_index + 1, steps: [], suggestions: undefined }
    setMessages((current) => [...current, placeholder])
    await startStream(`/api/v1/chat/messages/${message.id}/regenerate`, {
      message_id: assistantId,
      selected_model: selectedModel || null,
      custom_instruction: customInstruction || null,
    }, assistantId)
    if (conversationId) await loadMessages(conversationId, 1)
  }

  async function feedback(message: UIMessage, positive: boolean) {
    const existing = feedbackState[message.id]
    if (positive) {
      if (existing?.positive === true) {
        try {
          await api(`/api/v1/chat/feedback/${message.id}`, { method: 'DELETE' })
          setFeedbackState((current) => {
            const next = { ...current }
            delete next[message.id]
            return next
          })
          setToast('Feedback removed')
        } catch (error) {
          setToast(error instanceof Error ? error.message : 'Could not remove feedback')
        }
      } else {
        try {
          await api('/api/v1/chat/feedback', {
            method: 'POST',
            body: JSON.stringify({ message_id: message.id, is_thumbs_up: true }),
          })
          setFeedbackState((current) => ({
            ...current,
            [message.id]: { positive: true },
          }))
          setToast('Marked as helpful')
        } catch (error) {
          setToast(error instanceof Error ? error.message : 'Could not submit feedback')
        }
      }
    } else {
      setDislikeModalTarget(message)
      setDislikeCategory(existing?.positive === false ? (existing.category || '') : '')
      setDislikeComment(existing?.positive === false ? (existing.comment || '') : '')
    }
  }

  async function handleDislikeSubmit(event: FormEvent) {
    event.preventDefault()
    if (!dislikeModalTarget) return
    setSubmittingFeedback(true)
    try {
      await api('/api/v1/chat/feedback', {
        method: 'POST',
        body: JSON.stringify({
          message_id: dislikeModalTarget.id,
          is_thumbs_up: false,
          category: dislikeCategory || 'OTHER',
          comment: dislikeComment.trim() || null,
        }),
      })
      setFeedbackState((current) => ({
        ...current,
        [dislikeModalTarget.id]: {
          positive: false,
          category: dislikeCategory || 'OTHER',
          comment: dislikeComment.trim(),
        },
      }))
      setDislikeModalTarget(null)
      setToast('Feedback submitted')
    } catch (error) {
      setToast(error instanceof Error ? error.message : 'Could not submit feedback')
    } finally {
      setSubmittingFeedback(false)
    }
  }

  async function handleUndoDislike() {
    if (!dislikeModalTarget) return
    try {
      await api(`/api/v1/chat/feedback/${dislikeModalTarget.id}`, { method: 'DELETE' })
      setFeedbackState((current) => {
        const next = { ...current }
        delete next[dislikeModalTarget.id]
        return next
      })
      setDislikeModalTarget(null)
      setToast('Feedback removed')
    } catch (error) {
      setToast(error instanceof Error ? error.message : 'Could not remove feedback')
    }
  }

  function submit(event: FormEvent) { event.preventDefault(); if (!isStreaming) void sendMessage() }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!isStreaming) void sendMessage() }
  }

  const grouped = useMemo(() => groupMessages(messages), [messages])
  const isEmpty = !conversationId && messages.length === 0 && !isStreaming
  const firstName = user?.name.split(' ')[0] || 'there'

  return <div className="chat-page">
    <header className="chat-topbar"><div className="chat-topbar-left"><span className="topbar-context">WORKSPACE</span><span className="topbar-divider">/</span><span>{conversationId ? 'Conversation' : 'New conversation'}</span></div><div className="chat-topbar-right"><span className="secure-session"><span /> Private workspace</span><AdminNotificationBell /><ThemeButton theme={theme} onToggle={toggleTheme} /></div></header>

    <div className="chat-scroll" ref={scrollRef}>
      {isEmpty ? <div className="chat-empty-state"><div className="greeting-mark">c</div><p className="eyebrow">YOUR COMPANY KNOWLEDGE, CONNECTED</p><h1>{greeting()}, {firstName}.</h1><p className="empty-subtitle">What would you like to understand or get done today?</p><div className="prompt-grid">
        <button onClick={() => void sendMessage('Summarize the most important company policies I should know.')}><span className="prompt-icon"><BookOpen size={17} /></span><strong>Get oriented</strong><small>Find the policies and processes that matter.</small></button>
        <button onClick={() => void sendMessage('What is the process for requesting leave?')}><span className="prompt-icon"><FileSearch size={17} /></span><strong>Find a process</strong><small>Get a clear answer with source references.</small></button>
        <button onClick={() => void sendMessage('What information do you have access to?')}><span className="prompt-icon"><Search size={17} /></span><strong>Explore knowledge</strong><small>Search across the information available to you.</small></button>
      </div></div> : <div className="message-thread">
        {historyPages > historyPage && <div className="older-messages"><button className="secondary-button" disabled={loadingHistory} onClick={() => conversationId && void loadMessages(conversationId, historyPage + 1, true)}>{loadingHistory ? 'Loading…' : 'Load earlier messages'}</button></div>}
        {grouped.map((group, index) => <div className="message-group" key={group.user?.id || group.standalone?.id || index}>
          {group.user && <div className="user-message"><div className="user-message-bubble">{group.user.content}</div></div>}
          {group.assistants.length > 0 && <AssistantMessage key={group.user?.id || group.assistants[0].id} message={group.assistants[group.assistants.length - 1]} variants={group.assistants} allMessages={messages} onOpenSources={(message, number, customData) => { setSourceMessage(message); setSourceNumber(number ?? null); setSourceData(customData || resolveMessageCitations(message, messages)) }} onRegenerate={regenerate} onFeedback={feedback} onSuggestion={(suggestion) => void sendMessage(suggestion)} feedbackState={feedbackState} busy={isStreaming} />}
          {group.standalone && <AssistantMessage message={group.standalone} variants={[group.standalone]} allMessages={messages} onOpenSources={(message, number, customData) => { setSourceMessage(message); setSourceNumber(number ?? null); setSourceData(customData || resolveMessageCitations(message, messages)) }} onRegenerate={regenerate} onFeedback={feedback} onSuggestion={(suggestion) => void sendMessage(suggestion)} feedbackState={feedbackState} busy={isStreaming} />}
        </div>)}
      </div>}
    </div>

    <div className="composer-area"><form className="composer" onSubmit={submit}><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={handleKeyDown} placeholder="Ask about a policy, process, or anything in your workspace…" rows={2} /><div className="composer-footer"><div className="composer-footer-left"><div className="model-picker-wrap"><button type="button" className="model-picker" onClick={() => setShowModelPicker((value) => !value)}><span className="model-indicator" />{models.find((item) => item.id === selectedModel)?.name || 'Default model'}<ChevronDown size={14} /></button>{showModelPicker && <><button type="button" className="menu-dismiss" onClick={() => setShowModelPicker(false)} aria-label="Close model picker" /><div className="model-menu composer-model-menu">{models.map((model) => <button type="button" key={model.id} className={selectedModel === model.id ? 'selected' : ''} onClick={() => { setSelectedModel(model.id); setShowModelPicker(false) }}><span><strong>{model.name}</strong><small>{model.provider} · {model.badge}</small></span>{selectedModel === model.id && <Check size={15} />}</button>)}</div></>}</div><span className="composer-hint">Answers are grounded in sources you can access.</span></div><div className="composer-actions"><span className="keyboard-hint">Shift + Enter for a new line</span><button className="send-button" type="submit" disabled={!input.trim() || isStreaming} aria-label="Send message">{isStreaming ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={18} />}</button></div></div></form><p className="composer-disclaimer">Cortex can make mistakes. Check the cited source before acting on important information.</p></div>

    {sourceMessage && <SourcesDrawer open={Boolean(sourceMessage)} onClose={() => { setSourceMessage(null); setSourceNumber(null); setSourceData(null) }} citationValue={sourceData || resolveMessageCitations(sourceMessage, messages)} focusNumber={sourceNumber} />}
    {dislikeModalTarget && <Modal title="Share feedback" onClose={() => setDislikeModalTarget(null)}><form onSubmit={handleDislikeSubmit}>
      <div className="feedback-categories">
        {FEEDBACK_CATEGORIES.map((cat) => <button type="button" key={cat.id} className={`feedback-pill ${dislikeCategory === cat.id ? 'selected' : ''}`} onClick={() => setDislikeCategory(dislikeCategory === cat.id ? '' : cat.id)}>{cat.label}</button>)}
      </div>
      <textarea className="feedback-textarea" placeholder="Share details (optional)" value={dislikeComment} onChange={(e) => setDislikeComment(e.target.value)} />
      <p className="feedback-notice">Your conversation will be included with your feedback to help improve Cortex.</p>
      <div className="modal-actions">
        {feedbackState[dislikeModalTarget.id]?.positive === false && <button type="button" className="secondary-button" onClick={() => void handleUndoDislike()}>Undo feedback</button>}
        <button type="button" className="secondary-button" onClick={() => setDislikeModalTarget(null)}>Cancel</button>
        <button type="submit" className="primary-button" disabled={submittingFeedback}>{submittingFeedback ? 'Submitting…' : 'Submit'}</button>
      </div>
    </form></Modal>}
    {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
  </div>
}

