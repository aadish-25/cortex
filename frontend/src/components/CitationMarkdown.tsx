import React, { Children, cloneElement, isValidElement, useMemo, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function withCitationChips(children: ReactNode, onOpen: (number: number) => void): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === 'string') {
      return child.split(/(\[\d+\])/g).map((piece, index) => {
        const match = piece.match(/^\[(\d+)\]$/)
        if (!match) return piece
        const number = Number(match[1])
        return <button type="button" className="citation-chip" key={`${number}-${index}`} onClick={() => onOpen(number)} aria-label={`Open source ${number}`}>[{number}]</button>
      })
    }
    if (isValidElement<{ children?: ReactNode }>(child) && child.props.children) {
      return cloneElement(child, { children: withCitationChips(child.props.children, onOpen) })
    }
    return child
  })
}

export function CitationMarkdown({ content, onOpenSource }: { content: string; onOpenSource: (number: number) => void }) {
  const normalizedContent = useMemo(() => {
    if (!content) return ''
    // Strip trailing hallucinated Citation Map text
    const cleaned = content.replace(/\n\s*Citation Map:[\s\S]*$/i, '')
    // Normalize multi-bracket citations like [2, 4] or [2, 5, 8] into separate chips [2] [4]
    return cleaned.replace(/\[(\d+)(?:\s*,\s*(\d+))+\]/g, (match) => {
      const numbers = match.replace(/[\[\]]/g, '').split(/\s*,\s*/)
      return numbers.map((n) => `[${n}]`).join(' ')
    })
  }, [content])

  return <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    p: ({ children }) => <p>{withCitationChips(children, onOpenSource)}</p>,
    li: ({ children }) => <li>{withCitationChips(children, onOpenSource)}</li>,
    h1: ({ children }) => <h1>{withCitationChips(children, onOpenSource)}</h1>,
    h2: ({ children }) => <h2>{withCitationChips(children, onOpenSource)}</h2>,
    h3: ({ children }) => <h3>{withCitationChips(children, onOpenSource)}</h3>,
    blockquote: ({ children }) => <blockquote>{withCitationChips(children, onOpenSource)}</blockquote>,
    td: ({ children }) => <td>{withCitationChips(children, onOpenSource)}</td>,
    th: ({ children }) => <th>{withCitationChips(children, onOpenSource)}</th>,
    a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
    code: ({ children, className }) => <code className={className}>{children}</code>,
  }}>{normalizedContent}</ReactMarkdown></div>
}
