import { useMemo, useState } from 'react'
import { Minus, Plus } from 'lucide-react'
import { ENTITY_LABEL, entityStyle } from '../lib/entities'
import { findOccurrences } from '../lib/text'
import { allEntityTypes, type EntityType, type Span } from '../types'
import { Dialog, Spinner } from './ui'
import './EntityRangeEditor.css'

// 兼容旧的测试与调用方
export { findOccurrences as findEntityTextMatches } from '../lib/text'

export function validateEntityRange(start: string, end: string, length: number): string {
  if (length === 0) return '原文为空，无法标记。'
  if (!start.trim() || !end.trim()) return '请填写完整的起止位置。'
  const from = Number(start)
  const to = Number(end)
  if (!Number.isInteger(from) || !Number.isInteger(to)) return '起止位置需要是整数。'
  if (from < 0 || to > length) return `位置需要在 0 到 ${length} 之间。`
  if (from >= to) return '结束位置需要大于起始位置。'
  return ''
}

function overlaps(start: number, end: number, spans: Span[], ignoreId?: string) {
  return spans.some(span => span.id !== ignoreId && span.status !== 'rejected' && start < span.end && end > span.start)
}

interface AddProps {
  open: boolean
  text: string
  spans: Span[]
  initialQuery?: string
  busy?: boolean
  onClose: () => void
  onSubmit: (ranges: Array<{ start: number; end: number }>, type: EntityType) => Promise<boolean>
}

/** 补充遗漏：输入原文中的文字，一次标记它在全文的所有出现位置。 */
export function AddEntityDialog({ open, text, spans, initialQuery = '', busy, onClose, onSubmit }: AddProps) {
  const characters = useMemo(() => Array.from(text), [text])
  const [query, setQuery] = useState(initialQuery)
  const [type, setType] = useState<EntityType>('PERSON')
  const [unchecked, setUnchecked] = useState<Set<number>>(new Set())
  const [error, setError] = useState('')
  const occurrences = useMemo(() => findOccurrences(text, query.trim() ? query : ''), [text, query])
  const available = occurrences.map((item, index) => ({ ...item, index, covered: overlaps(item.start, item.end, spans) }))
  const chosen = available.filter(item => !item.covered && !unchecked.has(item.index))

  async function submit() {
    if (!chosen.length) return
    setError('')
    const ok = await onSubmit(chosen.map(({ start, end }) => ({ start, end })), type)
    if (!ok) setError('没有保存成功，内容已保留，可以检查后再试。')
  }

  return <Dialog open={open} onClose={onClose} title="补充遗漏的敏感信息" description="输入原文里的文字，系统会找到它在全文的每一处出现，一并标记。" size="md"
    footer={<><button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn btn-primary" disabled={busy || !chosen.length} onClick={submit}>{busy && <Spinner size={14}/>}{chosen.length ? `标记 ${chosen.length} 处` : '标记'}</button></>}>
    <div className="range-editor">
      <label className="field">
        <span className="field-label">要标记的文字</span>
        <input className="input" data-autofocus value={query} onChange={event => { setQuery(event.target.value); setUnchecked(new Set()); setError('') }} placeholder="例如：星舟、王小明、内部编号"/>
      </label>
      <div className="field">
        <span className="field-label">类型</span>
        <div className="range-types">
          {allEntityTypes.map(item => <button type="button" key={item} className={`type-chip ${type === item ? 'is-on' : ''}`} style={entityStyle(item)} aria-pressed={type === item} onClick={() => setType(item)}><span className="dot"/>{ENTITY_LABEL[item]}</button>)}
        </div>
      </div>
      <div className="range-occurrences">
        {!query.trim() ? <p className="muted">输入文字后，这里会列出它在原文中的位置。</p>
          : !occurrences.length ? <p className="range-error">原文中没有找到“{query}”，请核对文字、空格和标点。</p>
          : <>
            <p className="range-summary">找到 <b className="num">{occurrences.length}</b> 处{available.some(item => item.covered) ? `，其中 ${available.filter(item => item.covered).length} 处已被标记` : ''}</p>
            <ul>
              {available.map(item => <li key={item.index} className={item.covered ? 'is-covered' : ''}>
                <label className="check">
                  <input type="checkbox" disabled={item.covered} checked={!item.covered && !unchecked.has(item.index)} onChange={event => setUnchecked(current => { const next = new Set(current); if (event.target.checked) next.delete(item.index); else next.add(item.index); return next })}/>
                  <span className="range-context">
                    {item.start > 14 && '…'}{characters.slice(Math.max(0, item.start - 14), item.start).join('')}
                    <mark style={entityStyle(type)}>{characters.slice(item.start, item.end).join('')}</mark>
                    {characters.slice(item.end, item.end + 14).join('')}{item.end + 14 < characters.length && '…'}
                  </span>
                  {item.covered && <span className="chip">已标记</span>}
                </label>
              </li>)}
            </ul>
          </>}
      </div>
      {error && <p className="range-error" role="alert">{error}</p>}
    </div>
  </Dialog>
}

interface AdjustProps {
  open: boolean
  text: string
  span: Span | null
  spans: Span[]
  busy?: boolean
  onClose: () => void
  onSubmit: (start: number, end: number) => Promise<boolean>
}

/** 调整范围：逐字扩展或收缩实体的左右边界。 */
export function AdjustRangeDialog({ open, text, span, spans, busy, onClose, onSubmit }: AdjustProps) {
  const characters = useMemo(() => Array.from(text), [text])
  const [start, setStart] = useState(span?.start ?? 0)
  const [end, setEnd] = useState(span?.end ?? 0)
  const [error, setError] = useState('')
  if (!span) return null
  const invalid = validateEntityRange(String(start), String(end), characters.length)
  const conflict = !invalid && overlaps(start, end, spans, span.id) ? '新的范围与其他实体重叠，请先恢复那个实体。' : ''
  const message = invalid || conflict
  const contextStart = Math.max(0, Math.min(start, span.start) - 24)
  const contextEnd = Math.min(characters.length, Math.max(end, span.end) + 24)

  async function submit() {
    if (message) return
    setError('')
    const ok = await onSubmit(start, end)
    if (!ok) setError('没有保存成功，请检查后再试。')
  }

  const nudge = (edge: 'start' | 'end', delta: number) => {
    if (edge === 'start') setStart(value => Math.max(0, Math.min(end - 1, value + delta)))
    else setEnd(value => Math.min(characters.length, Math.max(start + 1, value + delta)))
  }

  return <Dialog open={open} onClose={onClose} title="调整标记范围" description="逐字扩展或收缩，确认选中的文字正好是需要脱敏的部分。" size="md"
    footer={<><button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn btn-primary" disabled={busy || Boolean(message) || (start === span.start && end === span.end)} onClick={submit}>{busy && <Spinner size={14}/>}保存范围</button></>}>
    <div className="range-editor">
      <p className="range-preview doc-text is-compact">
        {contextStart > 0 && '…'}{characters.slice(contextStart, start).join('')}
        <mark style={entityStyle(span.entity_type)}>{characters.slice(start, end).join('')}</mark>
        {characters.slice(end, contextEnd).join('')}{contextEnd < characters.length && '…'}
      </p>
      <div className="range-nudges">
        <div><span className="field-label">左边界</span><div className="range-nudge-btns">
          <button type="button" className="btn btn-sm" onClick={() => nudge('start', -1)} disabled={start <= 0}><Plus size={13}/>向左多一字</button>
          <button type="button" className="btn btn-sm" onClick={() => nudge('start', 1)} disabled={start >= end - 1}><Minus size={13}/>去掉首字</button>
        </div></div>
        <div><span className="field-label">右边界</span><div className="range-nudge-btns">
          <button type="button" className="btn btn-sm" onClick={() => nudge('end', -1)} disabled={end <= start + 1}><Minus size={13}/>去掉末字</button>
          <button type="button" className="btn btn-sm" onClick={() => nudge('end', 1)} disabled={end >= characters.length}><Plus size={13}/>向右多一字</button>
        </div></div>
      </div>
      <p className="range-summary">选中 <b className="num">{Math.max(0, end - start)}</b> 个字符：<span className="range-selected">{characters.slice(start, end).join('')}</span></p>
      {(message || error) && <p className="range-error" role="alert">{message || error}</p>}
    </div>
  </Dialog>
}
