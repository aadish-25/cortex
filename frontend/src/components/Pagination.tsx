import { ChevronLeft, ChevronRight } from 'lucide-react'

export function Pagination({ page, totalPages, onChange }: { page: number; totalPages: number; onChange: (page: number) => void }) {
  if (totalPages <= 1) return null
  return <div className="pagination"><span>Page {page} of {totalPages}</span><div><button className="icon-button small" disabled={page <= 1} onClick={() => onChange(page - 1)} aria-label="Previous page"><ChevronLeft size={16} /></button><button className="icon-button small" disabled={page >= totalPages} onClick={() => onChange(page + 1)} aria-label="Next page"><ChevronRight size={16} /></button></div></div>
}
