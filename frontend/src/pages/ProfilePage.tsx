import { useEffect, useState } from 'react'
import { ArrowLeft, Brain, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useTheme } from '../components/ThemeToggle'

interface Memory { memory_id: string; text: string; category: string; created_at?: string | null }

export function ProfilePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [memories, setMemories] = useState<Memory[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const { theme, toggleTheme } = useTheme()

  async function loadMemories(next?: string | null) {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '25' })
      if (next) params.set('cursor', next)
      const result = await api<{ items: Memory[]; total: number; next_cursor: string | null }>(`/api/v1/chat/memories?${params}`)
      setMemories((current) => next ? [...current, ...result.items] : result.items)
      setTotal(result.total); setNextCursor(result.next_cursor); setError('')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not load personal memories.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void loadMemories() }, [])

  async function removeMemory(id: string) {
    try {
      await api(`/api/v1/chat/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
      setMemories((current) => current.filter((item) => item.memory_id !== id))
      setTotal((value) => Math.max(0, value - 1))
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not delete this memory.') }
  }

  if (!user) return null
  const initials = user.name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()
  return <div className="admin-page profile-page">
    <header className="page-header"><div><button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={15} /> Back</button><p className="eyebrow">ACCOUNT</p><h1>Your profile</h1><p className="page-subtitle">Your account details and personal context stored by Cortex.</p></div></header>
    <div className="profile-grid">
      <section className="panel profile-card"><div className="profile-hero"><span className="user-avatar profile-avatar">{initials}</span><div><h2>{user.name}</h2><p>{user.job_title}</p><span className="role-pill">{user.role}</span></div></div><div className="profile-details"><div><small>EMAIL</small><strong>{user.email}</strong></div><div><small>USERNAME</small><strong>{user.username}</strong></div><div><small>DEPARTMENT</small><strong>{user.department}</strong></div><div><small>CLEARANCE</small><strong>{user.clearance_level}</strong></div></div></section>
      <section className="panel preferences-card"><div className="panel-heading"><div><p className="eyebrow">PREFERENCES</p><h2>Appearance</h2></div></div><div className="preference-row"><div><strong>Theme</strong><small>Choose a light or dark workspace.</small></div><button className="secondary-button" onClick={toggleTheme}>{theme === 'light' ? 'Switch to dark' : 'Switch to light'}</button></div><div className="preference-row"><div><strong>Session access</strong><small>Access is controlled by your role and clearance.</small></div><span className="status-pill">{user.is_active ? 'Active' : 'Inactive'}</span></div></section>
    </div>
    <section className="panel memories-panel"><div className="panel-heading"><div><p className="eyebrow">PERSONAL CONTEXT</p><h2><Brain size={19} /> Memories</h2><p className="panel-subtitle">Facts Cortex has retained to make future conversations more useful. You can remove any item.</p></div><span className="count-label">{total} saved</span></div>
      {error && <div className="inline-error">{error}</div>}
      {loading && <div className="table-loading">Loading memories…</div>}
      {!loading && memories.length === 0 && <div className="empty-state compact"><Brain size={24} /><p>No personal memories have been saved yet.</p></div>}
      <div className="memory-list">{memories.map((memory) => <div className="memory-row" key={memory.memory_id}><div className="memory-dot" /><div className="memory-copy"><strong>{memory.text}</strong><small>{memory.category} {memory.created_at ? `· ${new Date(memory.created_at).toLocaleDateString()}` : ''}</small></div><button className="icon-button danger" onClick={() => void removeMemory(memory.memory_id)} aria-label="Delete memory"><Trash2 size={15} /></button></div>)}</div>
      {nextCursor && <button className="secondary-button load-more-button" disabled={loading} onClick={() => void loadMemories(nextCursor)}>Load more memories</button>}
    </section>
  </div>
}
