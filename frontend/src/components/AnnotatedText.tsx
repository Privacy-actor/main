import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { ENTITY_LABEL, entityStyle } from '../lib/entities'
import { revealInPane } from '../lib/scroll'
import type { Span } from '../types'

interface AnnotatedTextProps {
  text: string
  spans: Span[]
  selectedId?: string | null
  linkedId?: string | null
  /** 只突出这些实体，其他实体变淡（按识别层筛选时使用） */
  focusIds?: Set<string> | null
  showRejected?: boolean
  showTags?: boolean
  compact?: boolean
  onSelect?: (span: Span) => void
  onHover?: (spanId: string | null) => void
}

/** 原文高亮。位置按 Unicode 码点计算，与后端 Python 一致。 */
export default function AnnotatedText({ text, spans, selectedId, linkedId, focusIds, showRejected = false, showTags = false, compact = false, onSelect, onHover }: AnnotatedTextProps) {
  const characters = useMemo(() => Array.from(text), [text])
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!selectedId || !root.current) return
    revealInPane(root.current.querySelector(`[data-span="${CSS.escape(selectedId)}"]`), 28)
  }, [selectedId])

  const nodes: ReactNode[] = []
  let cursor = 0
  const visible = spans
    .filter(span => showRejected || span.status !== 'rejected')
    .sort((a, b) => a.start - b.start || b.end - a.end)
  for (const span of visible) {
    if (span.start < cursor || characters.slice(span.start, span.end).join('') !== span.text) continue
    if (span.start > cursor) nodes.push(<span key={`t-${cursor}`}>{characters.slice(cursor, span.start).join('')}</span>)
    const classes = ['ent']
    if (span.status === 'rejected') classes.push('is-rejected')
    else if (span.status === 'pending' || span.conflict) classes.push('is-pending')
    if (span.id === selectedId) classes.push('is-selected')
    if (span.id === linkedId) classes.push('is-linked')
    if (focusIds && !focusIds.has(span.id)) classes.push('is-dim')
    // 用行内 span 而不是 button：长实体可以像普通文字一样跨行
    nodes.push(<span
      role="button" tabIndex={onSelect ? 0 : -1} key={span.id} data-span={span.id} data-label={ENTITY_LABEL[span.entity_type]} className={classes.join(' ')} style={entityStyle(span.entity_type)}
      aria-label={`${ENTITY_LABEL[span.entity_type]}：${span.text}`} aria-pressed={span.id === selectedId}
      onClick={() => onSelect?.(span)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect?.(span) } }}
      onMouseEnter={() => onHover?.(span.id)} onMouseLeave={() => onHover?.(null)}
      onFocus={() => onHover?.(span.id)} onBlur={() => onHover?.(null)}
    >{span.text}</span>)
    cursor = span.end
  }
  if (cursor < characters.length) nodes.push(<span key="tail">{characters.slice(cursor).join('')}</span>)
  return <div ref={root} className={`doc-text${compact ? ' is-compact' : ''}${showTags ? ' show-tags' : ''}`}>{nodes}</div>
}
