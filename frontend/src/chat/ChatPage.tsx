import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowUp, BookOpen, Brain, Check, ChevronDown, ChevronLeft, ChevronRight, Copy, FileSearch, LoaderCircle, MoreHorizontal, Plus, RefreshCw, Search, ShieldAlert, ThumbsDown, ThumbsUp, X } from 'lucide-react'
import { api, streamChat, type StreamEvent } from '../api/client'
import type { ChatMessage, CitationData, Conversation, Page } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { CitationMarkdown } from '../components/CitationMarkdown'
import { Modal } from '../components/Modal'
import { SourcesDrawer } from '../components/SourcesDrawer'
import { ThemeButton, useTheme } from '../components/ThemeToggle'

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
  const hasInlineCitation = /\[\d+\]/.test(target.content || '')
  if (hasInlineCitation) {
    const idx = allMessages.findIndex((m) => m.id === target.id)
    const startIdx = idx >= 0 ? idx - 1 : allMessages.length - 1
    for (let i = startIdx; i >= 0; i--) {
      const prev = allMessages[i]
      if (prev.sender === 'assistant') {
        const prevData = citationData(prev.citations)
        if (prevData && (prevData.citations?.length || prevData.evidence_chunks?.length || Object.keys(prevData.citation_map || {}).length)) {
          return prevData
        }
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

function AssistantMessage({ message, variants, allMessages, onOpenSources, onRegenerate, onFeedback, onSuggestion, currentFeedback, busy }: {
  message: UIMessage
  variants: UIMessage[]
  allMessages: UIMessage[]
  onOpenSources: (message: UIMessage, number?: number, customData?: CitationData | null) => void
  onRegenerate: (message: UIMessage) => void
  onFeedback: (message: UIMessage, positive: boolean) => void
  onSuggestion: (suggestion: string) => void
  currentFeedback?: { positive: boolean; category?: string; comment?: string }
  busy: boolean
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [variantIndex, setVariantIndex] = useState(Math.max(0, variants.findIndex((item) => item.id === message.id)))
  useEffect(() => setVariantIndex(Math.max(0, variants.findIndex((item) => item.id === message.id))), [variants.length, message.id])
  const allVariants = variants.length ? variants : [message]
  const active = allVariants[Math.min(variantIndex, allVariants.length - 1)] || message
  const sources = resolveMessageCitations(active, allMessages)
  const hasSources = Boolean(sources?.citations?.length || sources?.evidence_chunks?.length || Object.keys(sources?.citation_map || {}).length)
  const latestStep = active.steps && active.steps.length > 0 ? active.steps[active.steps.length - 1] : null
  const isCompacting = Boolean(active.streaming && latestStep?.stage === 'compacting')

  return <article className="assistant-message">
    <div className="assistant-avatar">c</div>
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
      {active.suggestions && active.suggestions.length > 0 && <div className="suggestion-row">{active.suggestions.map((suggestion) => <button key={suggestion} className="suggestion-pill" onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}</div>}
      {!active.streaming && <div className="message-toolbar">
        <div className="message-toolbar-left">
          <button className="message-action" onClick={() => void navigator.clipboard?.writeText(active.content)} title="Copy answer"><Copy size={14} /></button>
          <button className={`message-action like-btn ${currentFeedback?.positive === true ? 'active' : ''}`} onClick={() => onFeedback(active, true)} title={currentFeedback?.positive === true ? 'Undo helpful' : 'Helpful'} aria-label="Helpful"><ThumbsUp size={14} /></button>
          <button className={`message-action dislike-btn ${currentFeedback?.positive === false ? 'active' : ''}`} onClick={() => onFeedback(active, false)} title={currentFeedback?.positive === false ? 'Edit feedback' : 'Not helpful'} aria-label="Not helpful"><ThumbsDown size={14} /></button>
          {allVariants.length > 1 && <div className="variant-switcher"><button className="message-action" disabled={variantIndex === 0} onClick={() => setVariantIndex((value) => value - 1)}><ChevronLeft size={14} /></button><span>{active.variant_index || variantIndex + 1} / {allVariants.length}</span><button className="message-action" disabled={variantIndex >= allVariants.length - 1} onClick={() => setVariantIndex((value) => value + 1)}><ChevronRight size={14} /></button></div>}
        </div>
        <div className="message-menu-wrap">
          <button className="message-action" onClick={() => setMenuOpen((value) => !value)} aria-label="More answer actions"><MoreHorizontal size={17} /></button>
          {menuOpen && <><button className="menu-dismiss" onClick={() => setMenuOpen(false)} aria-label="Close actions" /><div className="message-menu">
            <button disabled={!hasSources} onClick={() => { onOpenSources(active, undefined, sources); setMenuOpen(false) }}><FileSearch size={15} /> View sources</button>
            <button disabled={busy} onClick={() => { onRegenerate(active); setMenuOpen(false) }}><RefreshCw size={15} /> Regenerate answer</button>
          </div></>}
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
  const [status, setStatus] = useState(draft.status || 'PENDING')
  const [busy, setBusy] = useState(false)
  async function act(action: 'confirm' | 'discard') {
    setBusy(true)
    try {
      const result = await api<any>(`/api/v1/actions/${draft.draft_id}/${action}`, { method: 'POST' })
      setStatus(result.status || (action === 'confirm' ? 'COMMITTED' : 'DISCARDED'))
    } catch (error) { setStatus(error instanceof Error ? error.message : 'Action failed') }
    finally { setBusy(false) }
  }
  return <div className="action-draft-card"><div className="action-draft-heading"><span className="action-draft-icon">A</span><div><small>PROPOSED WORKFLOW</small><strong>{draft.title || draft.action_type || 'Action draft'}</strong></div><span className="status-pill">{status}</span></div><p>{draft.description || 'Review the proposed action before committing it.'}</p>{!['COMMITTED', 'DISCARDED', 'EXPIRED'].includes(status) && <div className="action-draft-actions"><button className="secondary-button compact-button" disabled={busy} onClick={() => void act('discard')}>Discard</button><button className="primary-button compact-button" disabled={busy} onClick={() => void act('confirm')}>{busy ? 'Working…' : 'Confirm action'}</button></div>}</div>
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
        const mapped = result.items.map((item) => ({ ...item, interrupted: item.is_complete === false })) as UIMessage[]
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
      patchMessage(assistantId, { id: doneId, streaming: false })
      setIsStreaming(false)
      if (event.data?.conversation_id && !conversationId) navigate(`/chat/${event.data.conversation_id}`, { replace: true })
      if (event.data?.conversation_id) void loadMessages(event.data.conversation_id, 1)
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
    const assistantId = crypto.randomUUID()
    const userMessage: UIMessage = { id: crypto.randomUUID(), conversation_id: conversationId || '', sender: 'user', content: clean, variant_index: 1, created_at: now }
    const assistantMessage: UIMessage = { id: assistantId, conversation_id: conversationId || '', sender: 'assistant', content: '', variant_index: 1, created_at: now, streaming: true, steps: [] }
    setMessages((current) => [...current, userMessage, assistantMessage])
    setInput('')
    await startStream('/api/v1/chat/message', { message: clean, conversation_id: conversationId || null, message_id: assistantId, selected_model: selectedModel || null }, assistantId)
  }

  async function regenerate(message: UIMessage) {
    if (isStreaming) return
    const assistantId = crypto.randomUUID()
    const placeholder: UIMessage = { ...message, id: assistantId, content: '', streaming: true, error: undefined, interrupted: false, variant_index: message.variant_index + 1, steps: [], suggestions: undefined }
    setMessages((current) => [...current, placeholder])
    await startStream(`/api/v1/chat/messages/${message.id}/regenerate`, { message_id: assistantId, selected_model: selectedModel || null }, assistantId)
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

  function submit(event: FormEvent) { event.preventDefault(); void sendMessage() }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void sendMessage() }
  }

  const grouped = useMemo(() => groupMessages(messages), [messages])
  const isEmpty = messages.length === 0
  const firstName = user?.name.split(' ')[0] || 'there'

  return <div className="chat-page">
    <header className="chat-topbar"><div className="chat-topbar-left"><span className="topbar-context">WORKSPACE</span><span className="topbar-divider">/</span><span>{conversationId ? 'Conversation' : 'New conversation'}</span></div><div className="chat-topbar-right"><span className="secure-session"><span /> Private workspace</span><div className="model-picker-wrap"><button className="model-picker" onClick={() => setShowModelPicker((value) => !value)}><span className="model-indicator" />{models.find((item) => item.id === selectedModel)?.name || 'Default model'}<ChevronDown size={14} /></button>{showModelPicker && <><button className="menu-dismiss" onClick={() => setShowModelPicker(false)} aria-label="Close model picker" /><div className="model-menu">{models.map((model) => <button key={model.id} className={selectedModel === model.id ? 'selected' : ''} onClick={() => { setSelectedModel(model.id); setShowModelPicker(false) }}><span><strong>{model.name}</strong><small>{model.provider} · {model.badge}</small></span>{selectedModel === model.id && <Check size={15} />}</button>)}</div></>}</div><ThemeButton theme={theme} onToggle={toggleTheme} /></div></header>

    <div className="chat-scroll" ref={scrollRef}>
      {isEmpty ? <div className="chat-empty-state"><div className="greeting-mark">c</div><p className="eyebrow">YOUR COMPANY KNOWLEDGE, CONNECTED</p><h1>{greeting()}, {firstName}.</h1><p className="empty-subtitle">What would you like to understand or get done today?</p><div className="prompt-grid">
        <button onClick={() => void sendMessage('Summarize the most important company policies I should know.')}><span className="prompt-icon"><BookOpen size={17} /></span><strong>Get oriented</strong><small>Find the policies and processes that matter.</small></button>
        <button onClick={() => void sendMessage('What is the process for requesting leave?')}><span className="prompt-icon"><FileSearch size={17} /></span><strong>Find a process</strong><small>Get a clear answer with source references.</small></button>
        <button onClick={() => void sendMessage('What information do you have access to?')}><span className="prompt-icon"><Search size={17} /></span><strong>Explore knowledge</strong><small>Search across the information available to you.</small></button>
      </div></div> : <div className="message-thread">
        {historyPages > historyPage && <div className="older-messages"><button className="secondary-button" disabled={loadingHistory} onClick={() => conversationId && void loadMessages(conversationId, historyPage + 1, true)}>{loadingHistory ? 'Loading…' : 'Load earlier messages'}</button></div>}
        {grouped.map((group, index) => <div className="message-group" key={group.user?.id || group.standalone?.id || index}>
          {group.user && <div className="user-message"><div className="user-message-bubble">{group.user.content}</div></div>}
          {group.assistants.length > 0 && <AssistantMessage key={group.user?.id || group.assistants[0].id} message={group.assistants[group.assistants.length - 1]} variants={group.assistants} allMessages={messages} onOpenSources={(message, number, customData) => { setSourceMessage(message); setSourceNumber(number ?? null); setSourceData(customData || resolveMessageCitations(message, messages)) }} onRegenerate={regenerate} onFeedback={feedback} onSuggestion={(suggestion) => void sendMessage(suggestion)} currentFeedback={feedbackState[group.assistants[group.assistants.length - 1].id]} busy={isStreaming} />}
          {group.standalone && <AssistantMessage message={group.standalone} variants={[group.standalone]} allMessages={messages} onOpenSources={(message, number, customData) => { setSourceMessage(message); setSourceNumber(number ?? null); setSourceData(customData || resolveMessageCitations(message, messages)) }} onRegenerate={regenerate} onFeedback={feedback} onSuggestion={(suggestion) => void sendMessage(suggestion)} currentFeedback={feedbackState[group.standalone.id]} busy={isStreaming} />}
        </div>)}
      </div>}
    </div>

    <div className="composer-area"><form className="composer" onSubmit={submit}><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={handleKeyDown} placeholder="Ask about a policy, process, or anything in your workspace…" rows={1} disabled={isStreaming} /><div className="composer-footer"><span className="composer-hint">Answers are grounded in sources you can access.</span><div className="composer-actions"><span className="keyboard-hint">Shift + Enter for a new line</span><button className="send-button" type="submit" disabled={!input.trim() || isStreaming} aria-label="Send message">{isStreaming ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={18} />}</button></div></div></form><p className="composer-disclaimer">Cortex can make mistakes. Check the cited source before acting on important information.</p></div>

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
    {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
  </div>
}

