import { X } from 'lucide-react'
import type { ReactNode } from 'react'

export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true"><header className="modal-header"><h3>{title}</h3><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></header><div className="modal-content">{children}</div></section></div>
}
