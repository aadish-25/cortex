import { type FormEvent } from 'react'
import { Modal } from '../Modal'

export interface Memory {
  memory_id: string
  text: string
  category: string
  status?: 'active' | 'superseded' | 'deleted'
  version?: number
  source_type?: string | null
  supersedes_memory_id?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export const MEMORY_CATEGORIES = [
  'preference',
  'tech_stack',
  'project_context',
  'goal',
  'workflow',
  'location',
  'general',
] as const

export function formatCategory(cat: string) {
  return cat.replace(/_/g, ' ')
}

export function formatDate(iso?: string | null) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return ''
  }
}

export function MemoryFormModal({
  title,
  submitLabel,
  text,
  category,
  minLength,
  saving,
  placeholder,
  onTextChange,
  onCategoryChange,
  onSubmit,
  onClose,
}: {
  title: string
  submitLabel: string
  text: string
  category: string
  minLength: number
  saving: boolean
  placeholder?: string
  onTextChange: (value: string) => void
  onCategoryChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
  onClose: () => void
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <form className="stack-form" onSubmit={onSubmit}>
        <label>
          Memory text
          <textarea
            className="memory-textarea"
            rows={3}
            value={text}
            onChange={(e) => onTextChange(e.target.value)}
            placeholder={placeholder}
            required
            minLength={minLength}
          />
        </label>
        <label>
          Category
          <select value={category} onChange={(e) => onCategoryChange(e.target.value)}>
            {MEMORY_CATEGORIES.map((cat) => (
              <option key={cat} value={cat}>{formatCategory(cat)}</option>
            ))}
          </select>
        </label>
        <div className="modal-actions">
          <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary-button" disabled={saving || text.trim().length < minLength}>
            {saving ? 'Saving…' : submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export function ConfirmClearMemoriesModal({
  categoryFilter,
  busy,
  onConfirm,
  onClose,
}: {
  categoryFilter: string
  busy: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Modal
      title={categoryFilter ? `Delete ${formatCategory(categoryFilter)} memories?` : 'Delete all memories?'}
      onClose={onClose}
    >
      <div className="stack-form">
        <p className="help-copy">
          {categoryFilter
            ? `This will permanently remove all saved memories in the "${formatCategory(categoryFilter)}" category from your account and semantic search index.`
            : 'This will permanently remove all of your saved long-term memories from your account and semantic search index.'}
        </p>
        <div className="modal-actions">
          <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
          <button type="button" className="primary-button danger-button" disabled={busy} onClick={onConfirm}>
            {busy ? 'Deleting…' : 'Confirm delete'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
