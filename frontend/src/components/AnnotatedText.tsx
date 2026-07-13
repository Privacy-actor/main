import type { Span } from '../types'

const LABELS: Record<string, string> = { PERSON:'姓名', ORG:'机构', LOCATION:'地点', ADDRESS:'地址', PHONE:'电话', EMAIL:'邮箱', ID_CARD:'证件', BANK_CARD:'银行卡' }

export default function AnnotatedText({ text, spans, selected, onSelect }: { text:string; spans:Span[]; selected?:string; onSelect:(span:Span)=>void }) {
  const accepted = spans.filter(s => s.status !== 'rejected').sort((a,b) => a.start-b.start)
  const nodes: React.ReactNode[] = []; let cursor = 0
  accepted.forEach(span => {
    if (span.start < cursor || text.slice(span.start, span.end) !== span.text) return
    if (span.start > cursor) nodes.push(<span key={`t-${cursor}`}>{text.slice(cursor, span.start)}</span>)
    nodes.push(<button type="button" key={span.id} className={`entity entity-${span.entity_type} ${selected === span.id ? 'selected' : ''}`} onClick={() => onSelect(span)}>
      {span.text}<em>{LABELS[span.entity_type]}</em>
    </button>); cursor = span.end
  })
  if (cursor < text.length) nodes.push(<span key="tail">{text.slice(cursor)}</span>)
  return <div className="annotated-text">{nodes}</div>
}
