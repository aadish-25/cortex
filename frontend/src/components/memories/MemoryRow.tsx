import { CheckSquare, Pencil, Square, Trash2 } from 'lucide-react'
import { formatCategory, formatDate, type Memory } from './MemoryModals'

export function MemoryRow({
  memory,
  selected,
  onToggleSelect,
  onEdit,
  onDelete,
}: {
  memory: Memory
  selected: boolean
  onToggleSelect: (id: string) => void
  onEdit: (memory: Memory) => void
  onDelete: (id: string) => void
}) {
  const isSuperseded = memory.status === 'superseded'
  const created = formatDate(memory.created_at)
  const updated = formatDate(memory.updated_at)
  const showUpdated = updated && updated !== created

  return (
    <div className={`memory-row ${isSuperseded ? 'superseded' : ''}`}>
      <button
        type="button"
        className="memory-select-box"
        onClick={() => onToggleSelect(memory.memory_id)}
        aria-label={selected ? 'Deselect memory' : 'Select memory'}
      >
        {selected ? <CheckSquare size={15} /> : <Square size={15} />}
      </button>
      <div className="memory-copy">
        <strong>{memory.text}</strong>
        <div className="memory-meta-row">
          <span className="category-tag">{formatCategory(memory.category)}</span>
          <span className={`status-pill ${isSuperseded ? 'updating' : 'active'}`}>
            {isSuperseded ? 'Superseded' : 'Active'}
          </span>
          {created && <small>Created {created}</small>}
          {showUpdated && <small>· Updated {updated}</small>}
        </div>
      </div>
      <div className="row-actions">
        {!isSuperseded && (
          <button
            className="icon-button small"
            onClick={() => onEdit(memory)}
            aria-label="Edit memory"
            title="Edit memory"
          >
            <Pencil size={15} />
          </button>
        )}
        <button
          className="icon-button small danger"
          onClick={() => onDelete(memory.memory_id)}
          aria-label="Delete memory"
          title="Delete memory"
        >
          <Trash2 size={15} />
        </button>
      </div>
    </div>
  )
}
