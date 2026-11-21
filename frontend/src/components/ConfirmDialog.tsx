import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, Info, X } from 'lucide-react'

export interface ConfirmDialogProps {
  title: string
  message: ReactNode
  confirmText?: string
  cancelText?: string
  confirmVariant?: 'danger' | 'primary'
  isBusy?: boolean
  onConfirm: () => void | Promise<void>
  onCancel: () => void
}

export function ConfirmDialog({
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  confirmVariant = 'danger',
  isBusy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && !isBusy) {
        onCancel()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isBusy, onCancel])

  return (
    <div
      className="modal-overlay"
      onClick={() => {
        if (!isBusy) onCancel()
      }}
      role="presentation"
    >
      <div
        className="modal-card confirm-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {confirmVariant === 'danger' ? (
              <span style={{ color: 'var(--danger, #ef4444)', display: 'flex', flexShrink: 0 }}>
                <AlertTriangle size={18} />
              </span>
            ) : (
              <span style={{ color: 'var(--primary, #3b82f6)', display: 'flex', flexShrink: 0 }}>
                <Info size={18} />
              </span>
            )}
            <h3 id="confirm-dialog-title" style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
              {title}
            </h3>
          </div>
          <button
            type="button"
            className="icon-button"
            style={{ padding: 4, margin: -4 }}
            disabled={isBusy}
            onClick={onCancel}
            aria-label="Close dialog"
          >
            <X size={16} />
          </button>
        </div>

        <div style={{ margin: '0 0 18px', fontSize: 13, lineHeight: 1.5, color: 'var(--muted)' }}>
          {typeof message === 'string' ? <p style={{ margin: 0 }}>{message}</p> : message}
        </div>

        <div className="modal-actions" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button
            type="button"
            className="secondary-button"
            disabled={isBusy}
            onClick={onCancel}
          >
            {cancelText}
          </button>
          <button
            type="button"
            className={confirmVariant === 'danger' ? 'danger-button' : 'primary-button'}
            disabled={isBusy}
            onClick={() => void onConfirm()}
          >
            {isBusy ? 'Processing…' : confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
