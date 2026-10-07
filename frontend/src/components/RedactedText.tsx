import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { ENTITY_LABEL, entityStyle } from '../lib/entities'
import { revealInPane } from '../lib/scroll'
import type { Replacement, Span } from '../types'

interface RedactedTextProps {
  text: string
  replacements?: Replacement[]
  spansById?: Map<string, Span>
  selectedId?: string | null
  linkedId?: string | null
  revealing?: boolean
  compact?: boolean
  onSelect?: (spanId: string) => void
  onHover?: (spanId: string | null) => void
}

/** 脱敏结果。掩码显示为墨条，替换和泛化的词带下划线；悬停时和原文联动。 */
export default function RedactedText({ text, replacements = [], spansById, selectedId, linkedId, revealing = false, compact = false, onSelect, onHover }: RedactedTextProps) {
  const characters = useMemo(() => Array.from(text), [text])
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!selectedId || !root.current) return
    revealInPane(root.current.querySelector(`[data-span="${CSS.escape(selectedId)}"]`), 28)
  }, [selectedId])

  const nodes: ReactNode[] = []
  let cursor = 0
  const ordered = [...replacements].sort((a, b) => a.out_start - b.out_start)
  ordered.forEach((item, index) => {
    if (item.out_start < cursor || characters.slice(item.out_start, item.out_end).join('') !== item.replacement) return
    if (item.out_start > cursor) nodes.push(<span key={`t-${cursor}`}>{characters.slice(cursor, item.out_start).join('')}</span>)
    const span = spansById?.get(item.span_id)
    const pending = span ? span.status === 'pending' || span.conflict : false
    const classes = ['tok']
    if (item.span_id === selectedId) classes.push('is-selected')
    if (item.span_id === linkedId) classes.push('is-linked')
    if (pending) classes.push('is-pending')
    const label = `${ENTITY_LABEL[item.entity_type]}已替换为 ${item.replacement}`
    const isMask = item.strategy === 'mask' && /^【.+】$/.test(item.replacement)
    const inner = isMask
      ? <span className="tok-mask"><span className="bracket">【</span>{item.replacement.slice(1, -1)}<span className="bracket">】</span></span>
      : <span className={item.strategy === 'generalize' ? 'tok-gen' : 'tok-swap'}>{item.replacement}</span>
    nodes.push(<span
      role={onSelect ? 'button' : undefined} tabIndex={onSelect ? 0 : undefined} key={`${item.span_id}-${item.out_start}`} data-span={item.span_id} className={classes.join(' ')}
      style={{ ...entityStyle(item.entity_type), ['--i' as string]: index }} aria-label={onSelect ? label : undefined} title={label}
      onClick={() => onSelect?.(item.span_id)} onKeyDown={event => { if (onSelect && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onSelect(item.span_id) } }}
      onMouseEnter={() => onHover?.(item.span_id)} onMouseLeave={() => onHover?.(null)}
      onFocus={() => onHover?.(item.span_id)} onBlur={() => onHover?.(null)}
    >{inner}</span>)
    cursor = item.out_end
  })
  if (cursor < characters.length) nodes.push(<span key="tail">{characters.slice(cursor).join('')}</span>)
  return <div ref={root} className={`doc-text${compact ? ' is-compact' : ''}${revealing ? ' is-revealing' : ''}`}>{nodes}</div>
}
