import { useEffect, useState } from 'react'
import { useLocation, useNavigate, NavLink } from 'react-router-dom'
import { Activity, BookOpen, ChevronDown, CircleHelp, FileText, FolderKanban, LayoutDashboard, MessageSquare, Plus, Search, ShieldCheck, Users } from 'lucide-react'
import { api } from '../api/client'
import type { Conversation, Page } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { UserMenu } from './UserMenu'

export function Sidebar({ mobileOpen, onNavigate }: { mobileOpen: boolean; onNavigate: () => void }) {
  const { user } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)

  async function loadConversations(nextPage = 1, term = search) {
    const params = new URLSearchParams({ page: String(nextPage), page_size: '30' })
    if (term.trim()) params.set('search', term.trim())
    try {
      const result = await api<Page<Conversation>>(`/api/v1/chat/conversations?${params}`)
      setConversations((current) => nextPage === 1 ? result.items : [...current, ...result.items])
      setPage(result.page); setTotalPages(result.total_pages)
    } catch { /* The chat view remains usable if history is temporarily unavailable. */ }
  }

  useEffect(() => { void loadConversations(1, '') }, [])
  useEffect(() => {
    if (location.pathname.startsWith('/chat')) void loadConversations(1, '')
  }, [location.pathname])

  function newChat() { navigate('/chat'); onNavigate() }
  const isAdminArea = location.pathname.startsWith('/admin')

  return <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
    <div className="sidebar-brand-row">
      <NavLink to="/chat" className="brand-lockup" onClick={onNavigate}><span className="brand-mark">c</span><span>Cortex</span></NavLink>
      <span className="workspace-tag">WORKSPACE</span>
    </div>

    <div className="sidebar-primary-actions">
      <button className="new-chat-button" onClick={newChat}><Plus size={17} /><span>New conversation</span><kbd>⌘ K</kbd></button>
      <button className={`sidebar-search-button ${searchOpen ? 'active' : ''}`} onClick={() => setSearchOpen((value) => !value)}><Search size={16} /><span>Search conversations</span></button>
      {searchOpen && <input className="sidebar-search-input" autoFocus placeholder="Search titles…" value={search} onChange={(event) => { setSearch(event.target.value); void loadConversations(1, event.target.value) }} />}
    </div>

    {user?.role === 'Admin' && <div className="sidebar-section admin-section">
      <p className="sidebar-section-label">ADMINISTRATION</p>
      <NavLink to="/admin/overview" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><LayoutDashboard size={16} /><span>Overview</span></NavLink>
      <NavLink to="/admin/documents" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><FileText size={16} /><span>Documents</span></NavLink>
      <NavLink to="/admin/users" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><Users size={16} /><span>People</span></NavLink>
      <NavLink to="/admin/quality" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><Activity size={16} /><span>Answer quality</span></NavLink>
      <NavLink to="/admin/audit" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={onNavigate}><ShieldCheck size={16} /><span>Audit trail</span></NavLink>
    </div>}

    <div className="sidebar-history">
      <div className="sidebar-history-heading"><p className="sidebar-section-label">{isAdminArea ? 'RECENT CONVERSATIONS' : 'YOUR CONVERSATIONS'}</p><button className="subtle-icon" onClick={() => void loadConversations(1)} aria-label="Refresh conversations"><ChevronDown size={15} /></button></div>
      <div className="conversation-list">
        {conversations.length === 0 && <p className="sidebar-empty">Your conversations will appear here.</p>}
        {conversations.map((conversation) => <button key={conversation.id} className={`conversation-link ${location.pathname === `/chat/${conversation.id}` ? 'active' : ''}`} onClick={() => { navigate(`/chat/${conversation.id}`); onNavigate() }}><MessageSquare size={15} /><span>{conversation.title || 'New conversation'}</span></button>)}
        {page < totalPages && <button className="load-more-conversations" onClick={() => void loadConversations(page + 1)}>Load older conversations</button>}
      </div>
    </div>

    <div className="sidebar-help"><CircleHelp size={15} /><span>Need help?</span><span className="help-shortcut">?</span></div>
    <div className="sidebar-user-anchor"><UserMenu /></div>
  </aside>
}
