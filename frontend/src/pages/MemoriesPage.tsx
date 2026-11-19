import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Brain, CheckSquare, Plus, Search, Square, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Modal } from '../components/Modal'
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
  const [deleteTarget, setDeleteTarget] = useState<Memory | null>(null)
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false)

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(''), 3500)
      return () => clearTimeout(timer)
    }
  }, [toast])

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
    const textToSave = newText.trim()
    const catToSave = newCategory
    if (!textToSave) return

    // Immediately close modal and clear inputs: optimistic update ensures zero waiting time
    setAdding(false)
    setNewText('')
    setNewCategory('preference')
    setError('')

    const tempId = `temp_${Date.now()}`
    const optimisticMemory: Memory = {
      memory_id: tempId,
      text: textToSave,
      category: catToSave,
      status: 'active',
      version: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }

    if (!categoryFilter || categoryFilter === catToSave) {
      setMemories((current) => [optimisticMemory, ...current])
      setTotal((value) => value + 1)
    }
    setToast('Memory saved.')

    try {
      const created = await api<Memory>('/api/v1/chat/memories', {
        method: 'POST',
        body: JSON.stringify({ text: textToSave, category: catToSave }),
      })
      // Replace optimistic placeholder with authoritative server-generated record
      setMemories((current) => current.map((item) => (item.memory_id === tempId ? created : item)))
    } catch (err) {
      // Revert optimistic addition if failed
      setMemories((current) => current.filter((item) => item.memory_id !== tempId))
      setTotal((value) => Math.max(0, value - 1))
      setError(err instanceof Error ? err.message : 'Could not save this memory.')
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault()
    if (!editing || !editText.trim()) return

    const targetId = editing.memory_id
    const originalMemory = editing
    const updatedText = editText.trim()
    const updatedCategory = editCategory

    // Immediately close modal: optimistic update ensures user never waits
    setEditing(null)
    setError('')

    const optimisticUpdated: Memory = {
      ...originalMemory,
      text: updatedText,
      category: updatedCategory,
      updated_at: new Date().toISOString(),
    }

    setMemories((current) =>
      current.map((item) => (item.memory_id === targetId ? optimisticUpdated : item))
    )
    setToast('Memory updated.')

    try {
      const updated = await api<Memory>(`/api/v1/chat/memories/${encodeURIComponent(targetId)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          text: updatedText,
          category: updatedCategory,
          expected_version: originalMemory.version,
        }),
      })
      setMemories((current) =>
        current.map((item) => (item.memory_id === targetId ? { ...item, ...updated } : item))
      )
    } catch (err) {
      // Rollback to original memory
      setMemories((current) =>
        current.map((item) => (item.memory_id === targetId ? originalMemory : item))
      )
      setError(err instanceof Error ? err.message : 'Could not update this memory.')
    }
  }

  async function confirmSingleDelete() {
    if (!deleteTarget) return
    const target = deleteTarget
    const id = target.memory_id
    const prevMemories = memories
    const prevSelected = selectedIds

    // Immediately close modal and update state optimistically
    setDeleteTarget(null)
    setMemories((current) => current.filter((item) => item.memory_id !== id))
    setSelectedIds((current) => current.filter((item) => item !== id))
    setTotal((value) => Math.max(0, value - 1))
    setToast('Memory deleted.')

    try {
      await api(`/api/v1/chat/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
    } catch (err) {
      // Rollback
      setMemories(prevMemories)
      setSelectedIds(prevSelected)
      setTotal((value) => value + 1)
      setError(err instanceof Error ? err.message : 'Could not delete this memory.')
    }
  }

  async function removeSelected() {
    if (!selectedIds.length) return
    const idsToDelete = [...selectedIds]
    const count = idsToDelete.length
    const idSet = new Set(idsToDelete)
    const prevMemories = memories
    const prevSelected = selectedIds

    // Immediately close modal and remove optimistically
    setConfirmBulkDelete(false)
    setMemories((current) => current.filter((item) => !idSet.has(item.memory_id)))
    setSelectedIds([])
    setTotal((value) => Math.max(0, value - count))
    setToast(`Deleted ${count} selected memor${count === 1 ? 'y' : 'ies'}.`)
    setBulkDeleting(true)
    setError('')

    try {
      const res = await api<{ deleted_count: number; deleted_ids: string[] }>('/api/v1/chat/memories/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ memory_ids: idsToDelete }),
      })
      const removedSet = new Set(res.deleted_ids)
      setMemories((current) => current.filter((item) => !removedSet.has(item.memory_id)))
    } catch (err) {
      // Rollback
      setMemories(prevMemories)
      setSelectedIds(prevSelected)
      setTotal((value) => value + count)
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
            Add memory <Plus size={14} />
          </button>
        </div>
      </header>

      <section className="panel memories-panel">
        <div className="panel-heading">
          <div>
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
            <button
              type="button"
              className={`memory-select-all-btn ${allSelected ? 'active' : ''}`}
              onClick={toggleSelectAll}
              aria-label={allSelected ? 'Deselect all memories' : 'Select all memories'}
            >
              {allSelected ? <CheckSquare size={15} color="#2563eb" /> : <Square size={15} />}
              <span>{allSelected ? 'Deselect all' : 'Select all'}</span>
            </button>
            <div className="memory-bulk-actions">
              <button
                type="button"
                className="secondary-button compact-button danger-text"
                style={{
                  opacity: selectedIds.length > 0 ? 1 : 0,
                  pointerEvents: selectedIds.length > 0 ? 'auto' : 'none',
                  transform: selectedIds.length > 0 ? 'scale(1)' : 'scale(0.96)',
                  transition: 'opacity 0.15s ease, transform 0.15s ease',
                }}
                disabled={bulkDeleting || selectedIds.length === 0}
                onClick={() => setConfirmBulkDelete(true)}
              >
                <Trash2 size={13} /> {bulkDeleting ? 'Deleting…' : `Delete selected (${selectedIds.length})`}
              </button>
            </div>
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
              onDelete={(mem) => setDeleteTarget(mem)}
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

      {deleteTarget && (
        <Modal title="Delete memory?" onClose={() => setDeleteTarget(null)}>
          <div className="stack-form">
            <p className="help-copy">
              This will permanently remove &ldquo;<strong>{deleteTarget.text}</strong>&rdquo; from your saved profile and memory search index.
            </p>
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="primary-button danger-button"
                onClick={() => void confirmSingleDelete()}
              >
                Delete
              </button>
            </div>
          </div>
        </Modal>
      )}

      {confirmBulkDelete && (
        <Modal
          title={`Delete ${selectedIds.length} memor${selectedIds.length === 1 ? 'y' : 'ies'}?`}
          onClose={() => setConfirmBulkDelete(false)}
        >
          <div className="stack-form">
            <p className="help-copy">
              This will permanently delete the {selectedIds.length} selected memor{selectedIds.length === 1 ? 'y' : 'ies'} from your profile and semantic search index.
            </p>
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={() => setConfirmBulkDelete(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="primary-button danger-button"
                disabled={bulkDeleting}
                onClick={() => void removeSelected()}
              >
                {bulkDeleting ? 'Deleting…' : 'Delete selected'}
              </button>
            </div>
          </div>
        </Modal>
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
