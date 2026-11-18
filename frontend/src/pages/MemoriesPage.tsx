import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Brain, CheckSquare, Plus, Search, Square, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import {
  ConfirmClearMemoriesModal,
  MEMORY_CATEGORIES,
  MemoryFormModal,
  formatCategory,
  type Memory,
} from '../components/memories/MemoryModals'
import { MemoryRow } from '../components/memories/MemoryRow'

export function MemoriesPage() {
  const navigate = useNavigate()
  const [memories, setMemories] = useState<Memory[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [loading, setLoading] = useState(true)

  const [categoryFilter, setCategoryFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState<'active' | 'superseded' | 'all'>('active')
  const [searchInput, setSearchInput] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')

  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [confirmClearAll, setConfirmClearAll] = useState(false)

  const [adding, setAdding] = useState(false)
  const [newText, setNewText] = useState('')
  const [newCategory, setNewCategory] = useState('preference')
  const [editing, setEditing] = useState<Memory | null>(null)
  const [editText, setEditText] = useState('')
  const [editCategory, setEditCategory] = useState('preference')
  const [saving, setSaving] = useState(false)

  const loadMemories = useCallback(async (next?: string | null) => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '25', status: statusFilter })
      if (next) params.set('cursor', next)
      if (categoryFilter) params.set('category', categoryFilter)
      if (appliedSearch.trim()) params.set('search', appliedSearch.trim())
      const result = await api<{ items: Memory[]; total: number; next_cursor: string | null }>(`/api/v1/chat/memories?${params}`)
      setMemories((current) => (next ? [...current, ...result.items] : result.items))
      setTotal(result.total)
      setNextCursor(result.next_cursor)
      if (!next) setSelectedIds([])
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load personal memories.')
    } finally {
      setLoading(false)
    }
  }, [categoryFilter, statusFilter, appliedSearch])

  useEffect(() => { void loadMemories() }, [loadMemories])

  function handleSearchSubmit(event: FormEvent) {
    event.preventDefault()
    setAppliedSearch(searchInput)
  }

  function toggleSelect(id: string) {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  function toggleSelectAll() {
    setSelectedIds(selectedIds.length === memories.length && memories.length > 0 ? [] : memories.map((m) => m.memory_id))
  }

  function openEdit(memory: Memory) {
    setEditing(memory)
    setEditText(memory.text)
    setEditCategory(memory.category || 'preference')
    setError('')
  }

  async function saveNewMemory(event: FormEvent) {
    event.preventDefault()
    if (!newText.trim()) return
    setSaving(true); setError('')
    try {
      const created = await api<Memory>('/api/v1/chat/memories', {
        method: 'POST',
        body: JSON.stringify({ text: newText.trim(), category: newCategory }),
      })
      if (!categoryFilter || categoryFilter === created.category) {
        setMemories((current) => [created, ...current])
        setTotal((value) => value + 1)
      }
      setAdding(false); setNewText(''); setNewCategory('preference')
      setToast('Memory saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this memory.')
    } finally {
      setSaving(false)
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault()
    if (!editing || !editText.trim()) return
    setSaving(true); setError('')
    try {
      const updated = await api<Memory>(`/api/v1/chat/memories/${encodeURIComponent(editing.memory_id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ text: editText.trim(), category: editCategory, expected_version: editing.version }),
      })
      setMemories((current) => current.map((item) => (item.memory_id === editing.memory_id ? { ...item, ...updated } : item)))
      setEditing(null)
      setToast('Memory updated.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update this memory.')
    } finally {
      setSaving(false)
    }
  }

  async function removeMemory(id: string) {
    try {
      await api(`/api/v1/chat/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
      setMemories((current) => current.filter((item) => item.memory_id !== id))
      setSelectedIds((current) => current.filter((item) => item !== id))
      setTotal((value) => Math.max(0, value - 1))
      setToast('Memory removed.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this memory.')
    }
  }

  async function removeSelected() {
    if (!selectedIds.length) return
    setBulkDeleting(true); setError('')
    try {
      const res = await api<{ deleted_count: number; deleted_ids: string[] }>('/api/v1/chat/memories/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ memory_ids: selectedIds }),
      })
      const removedSet = new Set(res.deleted_ids)
      setMemories((current) => current.filter((item) => !removedSet.has(item.memory_id)))
      setTotal((value) => Math.max(0, value - res.deleted_count))
      setSelectedIds([])
      setToast(`Deleted ${res.deleted_count} selected memor${res.deleted_count === 1 ? 'y' : 'ies'}.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete selected memories.')
    } finally {
      setBulkDeleting(false)
    }
  }

  async function clearScopeMemories() {
    setBulkDeleting(true); setError('')
    try {
      const body = categoryFilter ? { category: categoryFilter } : { delete_all: true }
      const res = await api<{ deleted_count: number }>('/api/v1/chat/memories/bulk-delete', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setConfirmClearAll(false)
      setToast(`Deleted ${res.deleted_count} memor${res.deleted_count === 1 ? 'y' : 'ies'}.`)
      await loadMemories()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete memories.')
    } finally {
      setBulkDeleting(false)
    }
  }

  const allSelected = memories.length > 0 && selectedIds.length === memories.length

  return (
    <div className="admin-page profile-page">
      <header className="page-header">
        <div>
          <button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={15} /> Back</button>
          <p className="eyebrow">PERSONAL CONTEXT</p>
          <h1>Memories</h1>
          <p className="page-subtitle">Facts and preferences Cortex retains across conversations to personalize answers.</p>
        </div>
        <div className="memory-header-actions">
          <span className="count-label">{total} saved</span>
          <button className="primary-button compact-button" onClick={() => { setError(''); setAdding(true) }}>
            <Plus size={14} /> Add memory
          </button>
        </div>
      </header>

      <section className="panel memories-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">PERSONAL CONTEXT</p>
            <h2><Brain size={19} /> Saved memories</h2>
            <p className="panel-subtitle">Filter, search, edit, or delete memories anytime. You can also manage them directly in chat.</p>
          </div>
          {total > 0 && (
            <button className="secondary-button compact-button danger-text" onClick={() => setConfirmClearAll(true)} disabled={bulkDeleting}>
              <Trash2 size={13} /> {categoryFilter ? `Clear ${formatCategory(categoryFilter)}` : 'Clear all'}
            </button>
          )}
        </div>

        <div className="memory-toolbar">
          <form className="search-field" onSubmit={handleSearchSubmit}>
            <Search size={14} />
            <input type="text" value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search memories by keyword or topic…" />
            {(searchInput || appliedSearch) && <button type="button" onClick={() => { setSearchInput(''); setAppliedSearch('') }}>Clear</button>}
            <button type="submit">Search</button>
          </form>

          <div className="toolbar-filters">
            <select aria-label="Filter by category" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
              <option value="">All categories</option>
              {MEMORY_CATEGORIES.map((cat) => <option key={cat} value={cat}>{formatCategory(cat)}</option>)}
            </select>
            <select aria-label="Filter by status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as 'active' | 'superseded' | 'all')}>
              <option value="active">Active only</option>
              <option value="all">Active + superseded</option>
              <option value="superseded">Superseded history</option>
            </select>
          </div>
        </div>

        {memories.length > 0 && (
          <div className="memory-bulk-bar">
            <button type="button" className="text-link muted-link" onClick={toggleSelectAll}>
              {allSelected ? <CheckSquare size={14} /> : <Square size={14} />}
              {allSelected ? 'Deselect all' : 'Select all'}
            </button>
            {selectedIds.length > 0 && (
              <button type="button" className="secondary-button compact-button danger-text" disabled={bulkDeleting} onClick={() => void removeSelected()}>
                <Trash2 size={13} /> {bulkDeleting ? 'Deleting…' : `Delete selected (${selectedIds.length})`}
              </button>
            )}
          </div>
        )}

        {error && <div className="inline-error">{error}</div>}
        {loading && <div className="table-loading">Loading memories…</div>}
        {!loading && memories.length === 0 && (
          <div className="empty-state compact">
            <Brain size={24} />
            <p>
              {appliedSearch || categoryFilter || statusFilter !== 'active'
                ? 'No memories match your current filter.'
                : 'No personal memories have been saved yet. Tell Cortex in chat (e.g., “Remember that I prefer concise answers”) or add one above.'}
            </p>
          </div>
        )}

        <div className="memory-list">
          {memories.map((memory) => (
            <MemoryRow
              key={memory.memory_id}
              memory={memory}
              selected={selectedIds.includes(memory.memory_id)}
              onToggleSelect={toggleSelect}
              onEdit={openEdit}
              onDelete={(id) => void removeMemory(id)}
            />
          ))}
        </div>

        {nextCursor && (
          <button className="secondary-button load-more-button" disabled={loading} onClick={() => void loadMemories(nextCursor)}>
            Load more memories
          </button>
        )}
      </section>

      {adding && (
        <MemoryFormModal
          title="Add memory"
          submitLabel="Save memory"
          text={newText}
          category={newCategory}
          minLength={4}
          saving={saving}
          placeholder="e.g., Prefers concise technical explanations with code snippets."
          onTextChange={setNewText}
          onCategoryChange={setNewCategory}
          onSubmit={saveNewMemory}
          onClose={() => setAdding(false)}
        />
      )}

      {editing && (
        <MemoryFormModal
          title="Edit memory"
          submitLabel="Save changes"
          text={editText}
          category={editCategory}
          minLength={2}
          saving={saving}
          onTextChange={setEditText}
          onCategoryChange={setEditCategory}
          onSubmit={saveEdit}
          onClose={() => setEditing(null)}
        />
      )}

      {confirmClearAll && (
        <ConfirmClearMemoriesModal
          categoryFilter={categoryFilter}
          busy={bulkDeleting}
          onConfirm={() => void clearScopeMemories()}
          onClose={() => setConfirmClearAll(false)}
        />
      )}

      {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
    </div>
  )
}
