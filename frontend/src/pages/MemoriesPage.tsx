import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Brain, Pencil, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Modal } from '../components/Modal'

interface Memory { memory_id: string; text: string; category: string; created_at?: string | null }

const CATEGORIES = ['preference', 'tech_stack', 'project_context', 'location', 'general']

export function MemoriesPage() {
  const navigate = useNavigate()
  const [memories, setMemories] = useState<Memory[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Memory | null>(null)
  const [editText, setEditText] = useState('')
  const [editCategory, setEditCategory] = useState('preference')
  const [saving, setSaving] = useState(false)

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

  function openEdit(memory: Memory) {
    setEditing(memory)
    setEditText(memory.text)
    setEditCategory(memory.category || 'preference')
    setError('')
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault()
    if (!editing || !editText.trim()) return
    setSaving(true); setError('')
    try {
      const updated = await api<Memory>(`/api/v1/chat/memories/${encodeURIComponent(editing.memory_id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ text: editText.trim(), category: editCategory }),
      })
      setMemories((current) => current.map((item) => item.memory_id === editing.memory_id ? { ...item, ...updated } : item))
      setEditing(null)
      setToast('Memory updated.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not update this memory.') }
    finally { setSaving(false) }
  }

  async function removeMemory(id: string) {
    try {
      await api(`/api/v1/chat/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
      setMemories((current) => current.filter((item) => item.memory_id !== id))
      setTotal((value) => Math.max(0, value - 1))
      setToast('Memory removed.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not delete this memory.') }
  }

  return <div className="admin-page profile-page">
    <header className="page-header"><div><button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={15} /> Back</button><p className="eyebrow">PERSONAL CONTEXT</p><h1>Memories</h1><p className="page-subtitle">Facts and preferences Cortex has retained to personalize future answers.</p></div><span className="count-label">{total} saved</span></header>
    <section className="panel memories-panel">
      <div className="panel-heading"><div><p className="eyebrow">PERSONAL CONTEXT</p><h2><Brain size={19} /> Saved memories</h2><p className="panel-subtitle">You can edit or remove any item at any time.</p></div></div>
      {error && <div className="inline-error">{error}</div>}
      {loading && <div className="table-loading">Loading memories…</div>}
      {!loading && memories.length === 0 && <div className="empty-state compact"><Brain size={24} /><p>No personal memories have been saved yet. Tell Cortex about your preferences in chat (for example, “I prefer concise answers with code snippets”).</p></div>}
      <div className="memory-list">
        {memories.map((memory) => (
          <div className="memory-row" key={memory.memory_id}>
            <div className="memory-dot" />
            <div className="memory-copy">
              <strong>{memory.text}</strong>
              <small>{memory.category} {memory.created_at ? `· ${new Date(memory.created_at).toLocaleDateString()}` : ''}</small>
            </div>
            <div className="row-actions">
              <button className="icon-button small" onClick={() => openEdit(memory)} aria-label="Edit memory" title="Edit memory"><Pencil size={15} /></button>
              <button className="icon-button small danger" onClick={() => void removeMemory(memory.memory_id)} aria-label="Delete memory" title="Delete memory"><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
      {nextCursor && <button className="secondary-button load-more-button" disabled={loading} onClick={() => void loadMemories(nextCursor)}>Load more memories</button>}
    </section>

    {editing && (
      <Modal title="Edit memory" onClose={() => setEditing(null)}>
        <form className="stack-form" onSubmit={saveEdit}>
          <label>
            Memory text
            <textarea className="memory-textarea" rows={3} value={editText} onChange={(e) => setEditText(e.target.value)} required minLength={2} />
          </label>
          <label>
            Category
            <select value={editCategory} onChange={(e) => setEditCategory(e.target.value)}>
              {CATEGORIES.map((cat) => <option key={cat} value={cat}>{cat.replace('_', ' ')}</option>)}
            </select>
          </label>
          <div className="modal-actions">
            <button type="button" className="secondary-button" onClick={() => setEditing(null)}>Cancel</button>
            <button type="submit" className="primary-button" disabled={saving || !editText.trim()}>{saving ? 'Saving…' : 'Save changes'}</button>
          </div>
        </form>
      </Modal>
    )}
    {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
  </div>
}
