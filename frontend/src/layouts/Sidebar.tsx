import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, NavLink } from 'react-router-dom'
import { Activity, ChevronDown, FileText, LayoutDashboard, MessageSquare, Pencil, Plus, Search, ShieldCheck, Trash2, Users, X } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { Conversation, Page } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import CortexLogo from '../components/CortexLogo'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { UserMenu } from './UserMenu'

export function Sidebar({ mobileOpen, onNavigate }: { mobileOpen: boolean; onNavigate: () => void }) {
  const { user } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [search, setSearch] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  async function loadConversations(nextPage = 1, term = search) {
    const params = new URLSearchParams({ page: String(nextPage), page_size: '30' })
    if (term.trim()) params.set('search', term.trim())
    try {
      const result = await api<Page<Conversation>>(`/api/v1/chat/conversations?${params}`)
      setConversations((current) => nextPage === 1 ? result.items : [...current, ...result.items])
      setPage(result.page); setTotalPages(result.total_pages)
    } catch { /* The chat view remains usable if history is temporarily unavailable. */ }
  }

  async function saveRename(id: string) {
    if (!editTitle.trim()) { setEditingId(null); return }
    try {
      await api(`/api/v1/chat/conversations/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: editTitle.trim() }),
      })
      setConversations((prev) => prev.map((c) => c.id === id ? { ...c, title: editTitle.trim() } : c))
    } catch { /* toast or error */ }
    finally { setEditingId(null) }
  }

  async function confirmDelete() {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    const targetId = deleteTarget.id
    try {
      await api(`/api/v1/chat/conversations/${targetId}`, { method: 'DELETE' })
      setConversations((prev) => prev.filter((c) => c.id !== targetId))
      setDeleteTarget(null)
      if (location.pathname === `/chat/${targetId}` || location.pathname === `/chat/${targetId}/`) {
        navigate('/chat')
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Could not delete conversation.'
      setDeleteError(msg)
    } finally {
      setIsDeleting(false)
    }
  }

  useEffect(() => { void loadConversations(1, '') }, [])
  useEffect(() => {
    if (location.pathname.startsWith('/chat')) void loadConversations(1, '')
  }, [location.pathname])
  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current) }, [])

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        navigate('/chat')
        onNavigate()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate, onNavigate])

  function handleSearchChange(value: string) {
    setSearch(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => { void loadConversations(1, value) }, 300)
  }

  function newChat() { navigate('/chat'); onNavigate() }
  const isAdminArea = location.pathname.startsWith('/admin')

  return <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
    <div className="sidebar-brand-row">
      <NavLink to="/chat" className="brand-lockup" onClick={onNavigate}><CortexLogo size={27} /><span>cortex</span></NavLink>
      <span className="workspace-tag">WORKSPACE</span>
    </div>

    <div className="sidebar-primary-actions">
      <button className="new-chat-button" onClick={newChat}><Plus size={16} /><span>New conversation</span><kbd>⌘ K</kbd></button>
      <div className="sidebar-search-field">
        <Search size={14} />
        <input
          placeholder="Search conversations…"
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
          aria-label="Search conversations"
        />
        {search && <button className="search-clear-btn" onClick={() => handleSearchChange('')} aria-label="Clear search"><X size={13} /></button>}
      </div>
    </div>

    {user?.role === 'Admin' && <div className="sidebar-section admin-section">
      <p className="sidebar-section-label">ADMINISTRATION</p>
      <NavLink to="/admin/overview" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><LayoutDashboard size={15} /><span>Overview</span></NavLink>
      <NavLink to="/admin/documents" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><FileText size={15} /><span>Documents</span></NavLink>
      <NavLink to="/admin/users" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><Users size={15} /><span>People</span></NavLink>
      <NavLink to="/admin/quality" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><Activity size={15} /><span>Answer quality</span></NavLink>
      <NavLink to="/admin/audit" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><ShieldCheck size={15} /><span>Audit trail</span></NavLink>
    </div>}

    <div className="sidebar-history">
      <div className="sidebar-history-heading"><p className="sidebar-section-label">{isAdminArea ? 'RECENT CONVERSATIONS' : 'YOUR CONVERSATIONS'}</p><button className="subtle-icon" onClick={() => void loadConversations(1)} aria-label="Refresh conversations"><ChevronDown size={15} /></button></div>
      <div className="conversation-list">
        {conversations.length === 0 && <p className="sidebar-empty">Your conversations will appear here.</p>}
        {conversations.map((conversation) => (
          <div key={conversation.id} className={`conversation-item ${location.pathname === `/chat/${conversation.id}` ? 'active' : ''}`}>
            {editingId === conversation.id ? (
              <input
                className="conversation-rename-input"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void saveRename(conversation.id)
                  if (e.key === 'Escape') setEditingId(null)
                }}
                onBlur={() => void saveRename(conversation.id)}
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <button
                className="conversation-link"
                onClick={() => { navigate(`/chat/${conversation.id}`); onNavigate() }}
              >
                <MessageSquare size={14} className="conversation-icon" />
                <span className="conversation-title">{conversation.title || 'New conversation'}</span>
              </button>
            )}
            <div className="conversation-actions">
              <button
                className="conversation-action-btn"
                title="Rename"
                onClick={(e) => {
                  e.stopPropagation()
                  setEditingId(conversation.id)
                  setEditTitle(conversation.title || 'New conversation')
                }}
              >
                <Pencil size={12} />
              </button>
              <button
                className="conversation-action-btn delete"
                title="Delete"
                onClick={(e) => {
                  e.stopPropagation()
                  setDeleteTarget(conversation)
                }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          </div>
        ))}
        {page < totalPages && <button className="load-more-conversations" onClick={() => void loadConversations(page + 1)}>Load older conversations</button>}
      </div>
    </div>

    {deleteTarget && (
      <ConfirmDialog
        title="Delete conversation?"
        message={
          <div>
            <p style={{ margin: '0 0 8px' }}>
              This will permanently delete &ldquo;<strong>{deleteTarget.title || 'New conversation'}</strong>&rdquo; and its message history. This action cannot be undone.
            </p>
            {deleteError && (
              <div className="inline-error" style={{ marginTop: 8 }}>
                {deleteError}
              </div>
            )}
          </div>
        }
        confirmText="Delete"
        confirmVariant="danger"
        isBusy={isDeleting}
        onCancel={() => {
          if (!isDeleting) {
            setDeleteTarget(null)
            setDeleteError(null)
          }
        }}
        onConfirm={confirmDelete}
      />
    )}

    <div className="sidebar-user-anchor"><UserMenu /></div>
  </aside>
}
