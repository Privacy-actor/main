import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Undo2 } from 'lucide-react'
import { api } from '../api'
import { EmptyState, Kbd, Spinner } from '../components/ui'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { ENTITY_LABEL, entityStyle, sourceLabel } from '../lib/entities'
import { formatTime, shortId } from '../lib/format'
import { allEntityTypes, type EntityType, type ReviewQueueItem } from '../types'
import './Review.css'

const keyOf = (item: ReviewQueueItem) => `${item.task_id}:${item.span.id}`

function isTyping(target: EventTarget | null) {
  const element = target as HTMLElement | null
  return Boolean(element?.closest('input, textarea, select, [contenteditable="true"]'))
}

/** 复核队列中的一段上下文，按码点切分并高亮目标实体。 */
function Context({ item }: { item: ReviewQueueItem }) {
  const characters = Array.from(item.context)
  const start = Math.max(0, item.span.start - item.context_offset)
  const end = Math.min(characters.length, item.span.end - item.context_offset)
  const leading = item.context_offset > 0
  const trailing = item.text_length !== undefined ? item.context_offset + characters.length < item.text_length : false
  return <p className="review-context doc-text">
    {leading && <span className="muted">…</span>}{characters.slice(0, start).join('')}
    <mark style={entityStyle(item.span.entity_type)}>{characters.slice(start, end).join('')}</mark>
    {characters.slice(end).join('')}{trailing && <span className="muted">…</span>}
  </p>
}

export default function Review() {
  const toast = useToast()
  const { refreshStats } = useApp()
  const [items, setItems] = useState<ReviewQueueItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [loadFailed, setLoadFailed] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<EntityType | 'all'>('all')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(0)

  const load = useCallback(async () => {
    setError('')
    try {
      const { items: queue, total: count } = await api.reviewQueue()
      setItems(queue)
      setTotal(count ?? queue.length)
      setLoadFailed(false)
      setSelected(current => current && queue.some(item => keyOf(item) === current) ? current : queue[0] ? keyOf(queue[0]) : null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '复核队列加载失败')
      setLoadFailed(true)
      setItems(current => current || [])
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const visible = useMemo(() => (items || []).filter(item => typeFilter === 'all' || item.span.entity_type === typeFilter), [items, typeFilter])
  const groups = useMemo(() => {
    const map = new Map<string, ReviewQueueItem[]>()
    for (const item of visible) map.set(item.task_id, [...(map.get(item.task_id) || []), item])
    return [...map.entries()]
  }, [visible])
  const typeCounts = useMemo(() => {
    const counts = new Map<EntityType, number>()
    for (const item of items || []) counts.set(item.span.entity_type, (counts.get(item.span.entity_type) || 0) + 1)
    return allEntityTypes.filter(type => counts.has(type)).map(type => [type, counts.get(type) || 0] as const)
  }, [items])
  const current = visible.find(item => keyOf(item) === selected) || visible[0] || null
  const index = current ? visible.indexOf(current) : -1
  const sameTask = current ? visible.filter(item => item.task_id === current.task_id) : []

  function move(step: number) {
    if (!visible.length) return
    const next = visible[(Math.max(0, index) + step + visible.length) % visible.length]
    setSelected(keyOf(next))
  }

  async function decide(operation: 'accept' | 'reject' | 'change_type', type?: EntityType) {
    if (!current || busy) return
    setBusy(true)
    const target = current
    const following = visible[index + 1] || visible[index - 1] || null
    try {
      await api.review({
        task_id: target.task_id, span_id: target.span.id, operation,
        before: operation === 'change_type' ? target.span.entity_type : target.span.status,
        after: operation === 'change_type' ? type : operation === 'accept' ? 'accepted' : 'rejected',
      })
      setItems(list => (list || []).filter(item => keyOf(item) !== keyOf(target)))
      setTotal(count => Math.max(0, count - 1))
      setSelected(following ? keyOf(following) : null)
      setDone(count => count + 1)
      void refreshStats()
      toast(operation === 'accept' ? `已确认脱敏「${target.span.text}」` : operation === 'reject' ? `已恢复原文「${target.span.text}」` : `已改为${ENTITY_LABEL[type as EntityType]}并确认`, 'success')
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : '操作没有保存', 'error')
      void load()
    } finally { setBusy(false) }
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (document.body.classList.contains('has-dialog') || isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
      const key = event.key.toLowerCase()
      if (key === 'j') { event.preventDefault(); move(1) }
      else if (key === 'k') { event.preventDefault(); move(-1) }
      else if (key === 'a') { event.preventDefault(); void decide('accept') }
      else if (key === 'r') { event.preventDefault(); void decide('reject') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return <div className="page review">
    <header className="page-head">
      <div>
        <h1 className="page-title">人工复核</h1>
        <p className="page-desc">置信度不足或几个识别层判断不一致的实体集中在这里。每次确认都会同步到对应任务，并留下操作记录。</p>
      </div>
      <div className="page-actions">
        <button type="button" className="btn" onClick={() => void load()}><RefreshCw size={15}/>刷新</button>
      </div>
    </header>

    {error && !(loadFailed && !items?.length) && <div className="notice notice-bad review-error">{error}</div>}

    {!items ? <div className="review-loading"><Spinner size={20}/></div>
      : loadFailed && !items.length ? <div className="panel"><EmptyState title="暂时读不到复核队列" illustration="queue"
          action={<button type="button" className="btn btn-primary" onClick={() => void load()}><RefreshCw size={15}/>重试</button>}>
          {error || '处理服务没有响应'}。确认后端窗口仍在运行后重试。
        </EmptyState></div>
      : !items.length ? <div className="panel"><EmptyState title={done ? `这一轮复核完成，共处理 ${done} 处` : '没有待确认的实体'} illustration="queue"
          action={<Link className="btn btn-primary" to="/workbench">去工作台处理文本</Link>}>
          工作台和批量处理中置信度不足的实体会自动出现在这里。
        </EmptyState></div>
      : <>
        <div className="review-bar">
          <p className="num"><strong>{total}</strong> 处待确认，来自 {new Set(items.map(item => item.task_id)).size} 个任务{total > items.length ? `（先列出最近的 ${items.length} 处，处理完后刷新可看到更多）` : ''}{done ? `，本次已处理 ${done} 处` : ''}</p>
          <div className="review-types" role="group" aria-label="按类型筛选">
            <button type="button" className={`type-chip ${typeFilter === 'all' ? 'is-on' : ''}`} style={{ ['--c' as string]: 'var(--ink)' }} aria-pressed={typeFilter === 'all'} onClick={() => setTypeFilter('all')}>全部</button>
            {typeCounts.map(([type, count]) => <button type="button" key={type} className={`type-chip ${typeFilter === type ? 'is-on' : ''}`} style={entityStyle(type)} aria-pressed={typeFilter === type} onClick={() => setTypeFilter(type)}>
              <span className="dot"/>{ENTITY_LABEL[type]}<span className="num">{count}</span>
            </button>)}
          </div>
        </div>

        <div className="review-grid">
          <nav className="review-queue panel" aria-label="待确认的实体">
            {groups.map(([taskId, list]) => <section key={taskId} className="queue-group">
              <header className="queue-group-head">
                <span className="num">任务 {shortId(taskId)}</span>
                <span className="muted">{list[0].created_at ? formatTime(list[0].created_at) : ''}</span>
              </header>
              <ul>
                {list.map(item => <li key={keyOf(item)}>
                  <button type="button" className={`queue-item${current && keyOf(current) === keyOf(item) ? ' is-selected' : ''}`} style={entityStyle(item.span.entity_type)} onClick={() => setSelected(keyOf(item))}>
                    <span className="dot"/>
                    <span className="queue-item-text">{item.span.text}</span>
                    <span className="queue-item-type">{ENTITY_LABEL[item.span.entity_type]}</span>
                  </button>
                </li>)}
              </ul>
            </section>)}
            {!visible.length && <p className="queue-empty">这个类型没有待确认的实体</p>}
          </nav>

          {current ? <article className="review-focus sheet" style={entityStyle(current.span.entity_type)} aria-live="polite">
            <header className="focus-head">
              <div className="focus-nav">
                <button type="button" className="icon-btn" aria-label="上一处" onClick={() => move(-1)} disabled={visible.length < 2}><ChevronLeft size={18}/></button>
                <span className="num">{index + 1} / {visible.length}</span>
                <button type="button" className="icon-btn" aria-label="下一处" onClick={() => move(1)} disabled={visible.length < 2}><ChevronRight size={18}/></button>
              </div>
              <Link className="btn btn-sm btn-quiet" to={`/workbench?task=${encodeURIComponent(current.task_id)}&span=${encodeURIComponent(current.span.id)}`}><ExternalLink size={14}/>在工作台打开</Link>
            </header>

            <div className="focus-body">
              <p className="focus-reason">{current.reason === '识别器冲突' ? '几个识别层对这里的判断不一致' : `置信度 ${Math.round((current.span.score ?? 0) * 100)}%，低于自动采纳的阈值`}</p>
              <Context item={current}/>
              <dl className="focus-facts">
                <div><dt>实体</dt><dd className="focus-entity">{current.span.text}</dd></div>
                <div><dt>类型</dt><dd>
                  <select className="select input-sm focus-type" value={current.span.entity_type} disabled={busy} aria-label="实体类型"
                    onChange={event => void decide('change_type', event.target.value as EntityType)}>
                    {allEntityTypes.map(type => <option key={type} value={type}>{ENTITY_LABEL[type]}{type === current.span.entity_type ? '' : '（改为此类并确认）'}</option>)}
                  </select>
                </dd></div>
                <div><dt>来源</dt><dd className="focus-sources">{current.span.sources.map(source => <span key={source} className={`source-badge${source === 'LLM' ? ' is-llm' : ''}`}>{sourceLabel(source)}</span>)}</dd></div>
                {sameTask.length > 1 && <div><dt>同一任务</dt><dd>还有 {sameTask.length - 1} 处待确认</dd></div>}
              </dl>
            </div>

            <footer className="focus-actions">
              <button type="button" className="btn btn-lg" disabled={busy} onClick={() => void decide('reject')}><Undo2 size={16}/>不是敏感信息，恢复原文<Kbd>R</Kbd></button>
              <button type="button" className="btn btn-ink btn-lg" disabled={busy} onClick={() => void decide('accept')}>{busy ? <Spinner size={16}/> : <Check size={16}/>}确认脱敏<Kbd>A</Kbd></button>
            </footer>
            <p className="focus-keys"><Kbd>J</Kbd><Kbd>K</Kbd>上一处、下一处</p>
          </article> : <div className="review-focus sheet"><EmptyState title="选择一处实体" illustration="queue">从左侧列表中选择。</EmptyState></div>}
        </div>
      </>}
  </div>
}
