import { useEffect, useMemo, useState } from 'react'
import { BookOpen, ChevronRight, FileText, LoaderCircle, X } from 'lucide-react'
import { api } from '../api/client'
import type { CitationData, EvidenceRef } from '../api/types'

interface EvidenceContext { target: { text: string; citation: string; section: string }; preceding?: { text: string; citation: string; section: string } | null; succeeding?: { text: string; citation: string; section: string } | null }

function normalizeCitationData(value?: CitationData | string[] | null): CitationData {
  if (!value) return { citations: [], citation_map: {}, evidence_chunks: [] }
  if (Array.isArray(value)) return { citations: value, citation_map: Object.fromEntries(value.map((item, index) => [String(index + 1), item])), evidence_chunks: [] }
  return value
}

export function SourcesDrawer({ open, onClose, citationValue, focusNumber }: { open: boolean; onClose: () => void; citationValue?: CitationData | string[] | null; focusNumber?: number | null }) {
  const data = useMemo(() => normalizeCitationData(citationValue), [citationValue])
  const [selected, setSelected] = useState<EvidenceRef | null>(null)
  const [context, setContext] = useState<EvidenceContext | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) { setSelected(null); setContext(null); setError(''); return }
    const match = focusNumber
      ? data.evidence_chunks.find((item) => item.anchor === focusNumber)
      : data.evidence_chunks[0]
    if (match) void selectEvidence(match)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, focusNumber, data])

  async function selectEvidence(item: EvidenceRef) {
    setSelected(item); setContext(null); setError(''); setLoading(true)
    try { setContext(await api<EvidenceContext>(`/api/v1/chat/evidence/${encodeURIComponent(item.evidence_id)}`)) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not load source context.') }
    finally { setLoading(false) }
  }

  if (!open) return null
  const entries = data.citations.map((citation, index) => {
    const label = data.citation_map[String(index + 1)] || citation
    const urlMatch = label.match(/https?:\/\/[^\s]+/)
    return { number: index + 1, citation: label, evidence: data.evidence_chunks.find((item) => item.anchor === index + 1), url: urlMatch?.[0].replace(/[),.;]+$/, '') }
  })

  return <>
    <button className="drawer-scrim" onClick={onClose} aria-label="Close sources panel" />
    <aside className="sources-drawer" aria-label="Sources">
      <header className="drawer-header"><div><p className="eyebrow">ANSWER REFERENCES</p><h3>Sources</h3></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
      <div className="drawer-body">
        {entries.length === 0 && <div className="empty-state compact"><BookOpen size={22} /><p>No source references were attached to this answer.</p></div>}
        <div className="source-list">{entries.map((entry) => entry.url && !entry.evidence ? <a key={entry.number} className="source-entry" href={entry.url} target="_blank" rel="noreferrer">
          <span className="source-number">{entry.number}</span><span className="source-entry-copy"><strong>{entry.citation || `Source ${entry.number}`}</strong><small>External reference · Opens in a new tab</small></span><ChevronRight size={16} />
        </a> : <button key={entry.number} className={`source-entry ${selected?.anchor === entry.number ? 'selected' : ''}`} onClick={() => entry.evidence && void selectEvidence(entry.evidence)} disabled={!entry.evidence}>
          <span className="source-number">{entry.number}</span><span className="source-entry-copy"><strong>{entry.citation || `Source ${entry.number}`}</strong><small>{entry.evidence?.section || 'Reference'}</small></span><ChevronRight size={16} />
        </button>)}</div>
        {selected && <section className="evidence-panel"><div className="evidence-panel-title"><FileText size={16} /><span>{selected.section || 'Cited passage'}</span></div>
          {loading && <div className="loading-row"><LoaderCircle className="spin" size={17} /> Loading cited passage…</div>}
          {error && <p className="inline-error">{error}</p>}
          {context && <div className="evidence-stack">
            {context.preceding && <div className="evidence-context"><small>PRECEDING CONTEXT</small><p>{context.preceding.text}</p></div>}
            <div className="evidence-target"><small>PASSAGE CITED IN THE ANSWER</small><p>{context.target.text}</p></div>
            {context.succeeding && <div className="evidence-context"><small>FOLLOWING CONTEXT</small><p>{context.succeeding.text}</p></div>}
          </div>}
        </section>}
      </div>
      <footer className="drawer-footer">Source passages are shown with adjacent context. Access is checked again when a source is opened.</footer>
    </aside>
  </>
}
