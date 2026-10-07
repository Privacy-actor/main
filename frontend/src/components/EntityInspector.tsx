import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, Plus, Search, Undo2 } from 'lucide-react'
import { api } from '../api'
import { ENTITY_LABEL, SEMANTIC_TYPES, STRATEGY_LABEL, STRENGTH_LABEL, entityStyle, isPending, sourceLabel, statusLabel, type Layer, spanLayers } from '../lib/entities'
import { revealInPane } from '../lib/scroll'
import { allEntityTypes, type EntityType, type Replacement, type Span, type Strategy, type TraceStep } from '../types'
import { Kbd } from './ui'
import './EntityInspector.css'

export type EntityFilter = 'all' | 'pending' | 'rejected' | 'human'

interface ListProps {
  spans: Span[]
  selectedId: string | null
  filter: EntityFilter
  onFilter: (filter: EntityFilter) => void
  layer: Layer | null
  query: string
  onQuery: (query: string) => void
  onSelect: (span: Span) => void
  onAdd: () => void
  busy?: boolean
}

export function filterSpans(spans: Span[], filter: EntityFilter, layer: Layer | null, query: string) {
  const needle = query.trim().toLocaleLowerCase()
  return spans.filter(span => {
    if (filter === 'pending' && !isPending(span)) return false
    if (filter === 'rejected' && span.status !== 'rejected') return false
    if (filter === 'human' && !span.sources.includes('HUMAN')) return false
    if (filter === 'all' && span.status === 'rejected') return false
    if (layer && !spanLayers(span).includes(layer)) return false
    if (needle && !`${span.text} ${ENTITY_LABEL[span.entity_type]}`.toLocaleLowerCase().includes(needle)) return false
    return true
  }).sort((a, b) => a.start - b.start)
}

export function EntityList({ spans, selectedId, filter, onFilter, layer, query, onQuery, onSelect, onAdd, busy }: ListProps) {
  const listRef = useRef<HTMLUListElement>(null)
  useEffect(() => {
    if (selectedId) revealInPane(listRef.current?.querySelector('.entity-item.is-selected'), 4)
  }, [selectedId, filter])
  const pending = spans.filter(isPending).length
  const rejected = spans.filter(span => span.status === 'rejected').length
  const human = spans.filter(span => span.sources.includes('HUMAN')).length
  const items = filterSpans(spans, filter, layer, query)
  const filters: Array<[EntityFilter, string, number]> = [['all', '全部', spans.length - rejected], ['pending', '待确认', pending], ['human', '人工', human], ['rejected', '已恢复', rejected]]
  return <div className="entity-list">
    <div className="entity-filters" role="tablist" aria-label="实体筛选">
      {filters.map(([key, label, count]) => <button type="button" role="tab" key={key} aria-selected={filter === key} className={filter === key ? 'is-active' : ''} onClick={() => onFilter(key)}>
        {label}<span className="num">{count}</span>
      </button>)}
    </div>
    <div className="entity-list-tools">
      <label className="entity-search"><Search size={14} aria-hidden="true"/><input value={query} onChange={event => onQuery(event.target.value)} placeholder="查找实体" aria-label="查找实体"/></label>
      <button type="button" className="btn btn-sm" onClick={onAdd} disabled={busy}><Plus size={14}/>补充遗漏</button>
    </div>
    <ul className="entity-items" ref={listRef}>
      {items.map(span => <li key={span.id}>
        <button type="button" className={`entity-item${span.id === selectedId ? ' is-selected' : ''}${span.status === 'rejected' ? ' is-rejected' : ''}`} style={entityStyle(span.entity_type)} onClick={() => onSelect(span)}>
          <span className="dot"/>
          <span className="entity-item-text">{span.text}</span>
          <span className="entity-item-type">{ENTITY_LABEL[span.entity_type]}</span>
          {isPending(span) && <span className="entity-item-flag" title="待确认">待确认</span>}
          {span.sources.includes('HUMAN') && !isPending(span) && span.status !== 'rejected' && <Check size={13} className="entity-item-human" aria-label="已人工确认"/>}
        </button>
      </li>)}
      {!items.length && <li className="entity-items-empty">{filter === 'pending' ? '没有待确认的实体' : filter === 'rejected' ? '没有被恢复的实体' : query ? '没有匹配的实体' : layer ? '这一层没有识别出实体' : '还没有识别出实体，可以手动补充'}</li>}
    </ul>
  </div>
}

interface DetailProps {
  span: Span
  replacement?: Replacement
  sameText: Span[]
  index: number
  total: number
  strength: number
  llmStatus?: TraceStep['status']
  /** 实体所在句子的语言，决定知识图谱层级显示中文还是英文 */
  lang?: 'zh' | 'en'
  /** 自动采纳的置信度阈值（立项书：90%） */
  threshold?: number
  busy?: boolean
  onPrev: () => void
  onNext: () => void
  onDecide: (status: 'accepted' | 'rejected', all: boolean) => void
  onChangeType: (type: EntityType) => void
  onSetStrategy: (strategy: Strategy) => void
  onSetReplacement: (value: string) => void
  onAdjust: () => void
}

function routingNote(span: Span, threshold: number, llmStatus?: TraceStep['status']) {
  if (span.sources.includes('HUMAN')) return span.status === 'rejected' ? '你已将它恢复为原文' : '你已人工确认'
  if (span.metadata.propagated_from) return '与文中另一处同名实体一起处理'
  if (span.conflict) return '多个识别层的判断不一致，需要人工确认'
  if (span.entity_type === 'ROLE' && !span.sources.includes('LLM')) return '职务加部门可能间接指向具体的人（隐性隐私），请确认是否需要处理'
  const score = span.score ?? 0
  const certainty = span.metadata.llm_certainty || span.metadata.certainty
  if (span.sources.includes('LLM')) {
    if (span.metadata.llm_addition) return `大模型补充的遗漏项（把握：${certainty === 'high' ? '高' : certainty === 'low' ? '低' : '中'}）`
    return `大模型已复核（把握：${certainty === 'high' ? '高' : certainty === 'low' ? '低' : '中'}）`
  }
  const percent = `${Math.round(threshold * 100)}%`
  if (score >= threshold) return `置信度达到 ${percent}，直接采纳`
  if (llmStatus === 'done') return `置信度低于 ${percent}，已送大模型核查，仍请人工确认`
  return `置信度低于 ${percent}，大模型未参与，请人工确认`
}

export function EntityDetail({ span, replacement, sameText, index, total, strength, llmStatus, lang = 'zh', threshold = 0.9, busy, onPrev, onNext, onDecide, onChangeType, onSetStrategy, onSetReplacement, onAdjust }: DetailProps) {
  const [levels, setLevels] = useState<string[] | null>(null)
  const [levelSource, setLevelSource] = useState('')
  const [custom, setCustom] = useState('')
  const [applyAll, setApplyAll] = useState(true)
  const semantic = SEMANTIC_TYPES.includes(span.entity_type)

  useEffect(() => {
    setCustom(typeof span.metadata.custom_replacement === 'string' ? span.metadata.custom_replacement : '')
  }, [span.id, span.metadata.custom_replacement])

  useEffect(() => {
    let cancelled = false
    setLevels(null)
    if (!semantic) return
    const embedded = lang === 'en' ? span.metadata.knowledge_levels_en : span.metadata.knowledge_levels
    if (Array.isArray(embedded) && embedded.length >= 3) {
      setLevels(embedded.slice(0, 3).map(String))
      setLevelSource(String(span.metadata.knowledge_source || ''))
      return
    }
    api.knowledgeLookup(span.text, span.entity_type, true, lang).then(result => {
      if (!cancelled) { setLevels(result.levels.slice(0, 3)); setLevelSource(result.source) }
    }).catch(() => undefined)
    return () => { cancelled = true }
  }, [span.id, span.text, span.entity_type, semantic, lang, span.metadata.knowledge_levels, span.metadata.knowledge_levels_en, span.metadata.knowledge_source])

  const score = span.score ?? 0
  const pending = isPending(span)
  const others = sameText.length - 1
  const mask = replacement && replacement.strategy === 'mask' && /^【.+】$/.test(replacement.replacement)

  return <div className="entity-detail" style={entityStyle(span.entity_type)}>
    <div className="detail-body">
    <div className="detail-top">
      <div className="detail-title">
        <span className="detail-text">{span.text}</span>
        <span className="detail-meta">
          <select className="detail-type" value={span.entity_type} aria-label="实体类型" disabled={busy} onChange={event => onChangeType(event.target.value as EntityType)}>
            {allEntityTypes.map(type => <option key={type} value={type}>{ENTITY_LABEL[type]}</option>)}
          </select>
          <span className={`detail-status${pending ? ' is-pending' : span.status === 'rejected' ? ' is-rejected' : ''}`}>{statusLabel(span)}</span>
        </span>
      </div>
      <div className="detail-nav">
        <button type="button" className="icon-btn" aria-label="上一个实体" onClick={onPrev} disabled={total < 2}><ChevronLeft size={17}/></button>
        <span className="num">{index >= 0 && total ? `${index + 1}/${total}` : ''}</span>
        <button type="button" className="icon-btn" aria-label="下一个实体" onClick={onNext} disabled={total < 2}><ChevronRight size={17}/></button>
      </div>
    </div>

    <dl className="detail-facts">
      <div><dt>来源</dt><dd className="detail-sources">{span.sources.map(source => <span key={source} className={`source-badge${source === 'LLM' ? ' is-llm' : source === 'HUMAN' ? ' is-human' : ''}`}>{sourceLabel(source)}</span>)}</dd></div>
      <div><dt>置信度</dt><dd>
        <span className="confidence"><span className="confidence-bar"><i style={{ width: `${Math.round(score * 100)}%` }}/></span><span className="num">{Math.round(score * 100)}%</span></span>
        <span className="detail-note">{routingNote(span, threshold, llmStatus)}</span>
      </dd></div>
      <div><dt>替换为</dt><dd>
        {span.status === 'rejected' ? <span className="muted">保留原文</span> : replacement ? <span className="detail-replacement">{mask ? <span className="tok-mask">{replacement.replacement.slice(1, -1)}</span> : <span className={replacement.strategy === 'generalize' ? 'tok-gen' : 'tok-swap'}>{replacement.replacement}</span>}</span> : <span className="muted">{pending ? '确认后替换' : '—'}</span>}
        {span.status !== 'rejected' && <div className="detail-strategy">
          <select className="select input-sm" value={span.strategy} aria-label="此实体的脱敏方式" disabled={busy} onChange={event => onSetStrategy(event.target.value as Strategy)}>
            {(Object.keys(STRATEGY_LABEL) as Strategy[]).map(strategy => <option key={strategy} value={strategy}>{STRATEGY_LABEL[strategy]}</option>)}
          </select>
        </div>}
      </dd></div>
      {span.status !== 'rejected' && <div><dt>自定义</dt><dd>
        <form className="detail-custom" onSubmit={event => { event.preventDefault(); onSetReplacement(custom.trim()) }}>
          <input className="input input-sm" value={custom} maxLength={200} placeholder="指定替换词，留空则按方式生成" onChange={event => setCustom(event.target.value)} aria-label="自定义替换词"/>
          <button type="submit" className="btn btn-sm" disabled={busy || custom.trim() === (typeof span.metadata.custom_replacement === 'string' ? span.metadata.custom_replacement : '')}>应用</button>
        </form>
      </dd></div>}
      {semantic && <div><dt>知识图谱</dt><dd>
        {levels ? <ol className="kg-chain">
          <li className="kg-node is-origin">{span.text}</li>
          {levels.map((level, levelIndex) => <li key={`${level}-${levelIndex}`} className={`kg-node${span.strategy === 'generalize' && strength === levelIndex + 1 ? ' is-current' : ''}`} title={`力度${STRENGTH_LABEL[levelIndex + 1]}时使用`}>{level}</li>)}
        </ol> : <span className="muted">正在查询层级…</span>}
        <span className="detail-note">{levelSource === 'local_exact' ? '命中内置知识图谱' : levelSource === 'remote' ? '来自远程知识图谱' : levelSource === 'type_fallback' ? '按实体类型的通用层级' : levelSource ? '依据名称特征推断' : ''}{span.strategy === 'generalize' ? `，当前力度取第 ${strength} 级` : ''}</span>
      </dd></div>}
    </dl>

    </div>

    <div className="detail-bottom">
    {others > 0 && <label className="check detail-all"><input type="checkbox" checked={applyAll} onChange={event => setApplyAll(event.target.checked)}/>同时处理文中全部 {sameText.length} 处“{span.text}”</label>}
    <div className="detail-actions">
      <button type="button" className="btn" disabled={busy || span.status === 'rejected'} onClick={() => onDecide('rejected', applyAll && others > 0)}><Undo2 size={15}/>恢复原文<Kbd>R</Kbd></button>
      <button type="button" className="btn btn-ink" disabled={busy || (span.status === 'accepted' && span.sources.includes('HUMAN') && !span.conflict)} onClick={() => onDecide('accepted', applyAll && others > 0)}><Check size={15}/>确认脱敏<Kbd>A</Kbd></button>
    </div>
    <div className="detail-foot">
      <button type="button" className="link-btn" onClick={onAdjust} disabled={busy}>调整标记范围</button>
      <span className="detail-keys"><Kbd>J</Kbd><Kbd>K</Kbd>切换实体</span>
    </div>
    </div>
  </div>
}

export function useSameText(spans: Span[], span: Span | null) {
  return useMemo(() => span ? spans.filter(item => item.text === span.text && item.entity_type === span.entity_type) : [], [spans, span])
}
