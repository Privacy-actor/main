import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { RefreshCw, Search, Trash2 } from 'lucide-react'
import { api } from '../api'
import { ConfirmDialog, EmptyState, Spinner } from '../components/ui'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { ENTITY_LABEL, STRATEGY_SHORT, STRENGTH_LABEL } from '../lib/entities'
import { formatDateTime, formatTime, shortId } from '../lib/format'
import { operationLabel } from '../lib/report'
import type { AuditEntry, EntityType, HistoryItem, Strategy } from '../types'
import './History.css'

type Tab = 'tasks' | 'audits'
const PAGE_SIZE = 100

function auditSummary(item: AuditEntry) {
  const payload = item.payload as Record<string, unknown>
  if (item.operation === 'edit_text') return `保存为 v${payload.revision ?? ''}，改动约 ${payload.changed_characters ?? 0} 字`
  if (item.operation === 'change_type') return `${ENTITY_LABEL[payload.before as EntityType] || payload.before} 改为 ${ENTITY_LABEL[payload.after as EntityType] || payload.after}`
  if (item.operation === 'set_strategy' || item.operation === 'set_span_strategy') return `改为${STRATEGY_SHORT[payload.after as Strategy] || payload.after}`
  if (item.operation === 'set_strength') return `力度改为${STRENGTH_LABEL[Number(payload.after)] || payload.after}`
  if (item.operation === 'add_many') return `${(payload.spans as unknown[] | undefined)?.length || 0} 处`
  if (item.operation === 'accept_many' || item.operation === 'reject_many') return `${(payload.span_ids as unknown[] | undefined)?.length || 0} 处`
  if (item.operation === 'adjust_boundary') return `范围 ${payload.before} 改为 ${payload.after}`
  if (item.operation === 'restore') return `换回 ${payload.restored ?? 0} 处`
  return ''
}

export default function History() {
  const toast = useToast()
  const { stats, refreshStats, projects } = useApp()
  const [params, setParams] = useSearchParams()
  const tab: Tab = params.get('tab') === 'audits' ? 'audits' : 'tasks'
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [audits, setAudits] = useState<AuditEntry[]>([])
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [onlyPending, setOnlyPending] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)
  const request = useRef(0)
  const [deleting, setDeleting] = useState<HistoryItem | null>(null)
  const [purging, setPurging] = useState(false)
  const [purgeDays, setPurgeDays] = useState(30)
  const [busy, setBusy] = useState(false)

  // 输入停顿 300 毫秒后再查，查找在服务端进行，覆盖全部任务（含原文）
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  const load = useCallback(async (offset = 0) => {
    const id = ++request.current
    try {
      const data = await api.history({ limit: PAGE_SIZE, auditLimit: 300, offset, q: search, pending: onlyPending })
      if (id !== request.current) return
      setItems(current => offset ? [...(current || []), ...data.items] : data.items)
      setTotal(data.total ?? data.items.length)
      setAudits(data.audits)
      setLoadError('')
    } catch (caught) {
      if (id !== request.current) return
      setLoadError(caught instanceof Error ? caught.message : '任务记录加载失败')
      setItems(current => current || [])
    }
  }, [search, onlyPending])

  useEffect(() => { void load(0) }, [load])
  useEffect(() => { void refreshStats() }, [refreshStats])

  async function loadMore() {
    setLoadingMore(true)
    await load(items?.length || 0)
    setLoadingMore(false)
  }

  const projectName = (id?: string | null) => id ? projects.find(project => project.id === id)?.name || '' : ''
  const visible = items || []
  const filtered = Boolean(search || onlyPending)

  async function removeTask() {
    if (!deleting) return
    setBusy(true)
    try {
      await api.deleteTask(deleting.id)
      setItems(list => list?.filter(item => item.id !== deleting.id) || list)
      setTotal(count => Math.max(0, count - 1))
      void refreshStats()
      toast('任务已删除', 'success')
      setDeleting(null)
    } catch (caught) { toast(caught instanceof Error ? caught.message : '删除失败', 'error') }
    finally { setBusy(false) }
  }

  async function purge() {
    setBusy(true)
    try {
      const { deleted } = await api.purgeTasks(purgeDays)
      toast(deleted ? `已清理 ${deleted} 个任务` : `没有 ${purgeDays} 天前的任务`, 'success')
      setPurging(false)
      void load(0); void refreshStats()
    } catch (caught) { toast(caught instanceof Error ? caught.message : '清理失败', 'error') }
    finally { setBusy(false) }
  }

  return <div className="page history">
    <header className="page-head">
      <div>
        <h1 className="page-title">任务记录</h1>
        <p className="page-desc">每次识别都会保存为一个任务，可以重新打开继续复核和修改。列表里只显示脱敏后的内容。</p>
      </div>
      <div className="page-actions"><button type="button" className="btn" onClick={() => setPurging(true)}><Trash2 size={15}/>清理旧任务</button></div>
    </header>

    <dl className="stat-line history-stats">
      <div><dt>任务</dt><dd>{stats?.tasks ?? '—'}</dd></div>
      <div><dt>已识别实体</dt><dd>{stats?.entities ?? '—'}</dd></div>
      <div><dt>待确认</dt><dd>{stats?.pending ?? '—'}</dd></div>
      <div><dt>人工修订过的任务</dt><dd>{stats?.edited_tasks ?? '—'}</dd></div>
      <div><dt>批处理</dt><dd>{stats?.jobs ?? '—'}</dd></div>
    </dl>

    <div className="tabs" role="tablist" aria-label="任务记录">
      <button type="button" role="tab" aria-selected={tab === 'tasks'} className={tab === 'tasks' ? 'is-active' : ''} onClick={() => setParams({})}>任务<span className="num">{items ? total : ''}</span></button>
      <button type="button" role="tab" aria-selected={tab === 'audits'} className={tab === 'audits' ? 'is-active' : ''} onClick={() => setParams({ tab: 'audits' })}>操作记录<span className="num">{audits.length || ''}</span></button>
    </div>

    {tab === 'tasks' && <>
      <div className="history-tools">
        <label className="history-search"><Search size={15} aria-hidden="true"/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="按原文、结果或任务编号查找" aria-label="查找任务"/></label>
        <label className="check"><input type="checkbox" checked={onlyPending} onChange={event => setOnlyPending(event.target.checked)}/>只看有待确认实体的任务</label>
      </div>
      {!items ? <div className="table-wrap"><div className="history-loading"><Spinner/></div></div>
        : loadError && !items.length ? <div className="panel"><EmptyState title="暂时读不到任务记录" illustration="document" action={<button type="button" className="btn btn-primary" onClick={() => void load(0)}><RefreshCw size={15}/>重试</button>}>{loadError}。确认后端窗口仍在运行后重试。</EmptyState></div>
        : !items.length && !filtered ? <div className="panel"><EmptyState title="还没有任务" illustration="document" action={<Link className="btn btn-primary" to="/workbench">去工作台</Link>}>在工作台识别一段文本，或者做一次批量处理。</EmptyState></div>
        : <div className="table-wrap">
          <table className="table history-table">
            <thead><tr><th>时间</th><th>内容</th><th className="is-num">实体</th><th>方式</th><th>状态</th><th aria-label="操作"/></tr></thead>
            <tbody>
              {visible.map(item => <tr key={item.id}>
                <td className="num history-time" title={formatDateTime(item.created_at)}>{formatTime(item.created_at)}</td>
                <td className="history-preview">
                  <Link to={`/workbench?task=${encodeURIComponent(item.id)}`}>{item.preview || '（空白）'}</Link>
                  <span className="muted num">任务 {shortId(item.id)}{projectName(item.project_id) ? `，项目 ${projectName(item.project_id)}` : ''}{item.text_length ? `，${item.text_length.toLocaleString()} 字` : ''}</span>
                </td>
                <td className="is-num">{item.entity_count}</td>
                <td className="history-strategy">{item.use_policies ? '按类型' : item.strategy ? STRATEGY_SHORT[item.strategy] : '—'}</td>
                <td>{(item.pending || 0) > 0 ? <span className="chip chip-warn">{item.pending} 处待确认</span> : item.has_manual_edits ? <span className="chip chip-cobalt">已修订 v{item.final_revision}</span> : <span className="chip chip-ok">已完成</span>}</td>
                <td className="history-actions">
                  <Link className="btn btn-sm btn-quiet" to={`/workbench?task=${encodeURIComponent(item.id)}`}>打开</Link>
                  <button type="button" className="icon-btn" aria-label="删除任务" title="删除" onClick={() => setDeleting(item)}><Trash2 size={15}/></button>
                </td>
              </tr>)}
              {!visible.length && <tr><td colSpan={6} className="history-none">没有符合条件的任务</td></tr>}
            </tbody>
          </table>
          {visible.length < total && <button type="button" className="btn btn-quiet btn-block history-more" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore && <Spinner size={14}/>}再显示 {Math.min(PAGE_SIZE, total - visible.length)} 个（共 {total} 个）</button>}
        </div>}
    </>}

    {tab === 'audits' && (!audits.length ? <div className="panel"><EmptyState title="还没有人工操作" illustration="queue">在工作台或人工复核中确认、恢复、修改实体后，会在这里留下记录。</EmptyState></div>
      : <div className="table-wrap">
        <table className="table">
          <thead><tr><th>时间</th><th>任务</th><th>操作</th><th>说明</th></tr></thead>
          <tbody>
            {audits.map(item => <tr key={item.id}>
              <td className="num history-time">{formatDateTime(item.created_at)}</td>
              <td><Link className="num" to={`/workbench?task=${encodeURIComponent(item.task_id)}`}>{shortId(item.task_id)}</Link></td>
              <td>{operationLabel(item.operation)}</td>
              <td className="muted">{auditSummary(item)}</td>
            </tr>)}
          </tbody>
        </table>
        <p className="history-note">操作记录不保存敏感原文，只保留实体长度和哈希，便于核对。</p>
      </div>)}

    <ConfirmDialog open={Boolean(deleting)} title="删除这个任务？" confirmLabel="删除" busy={busy} onClose={() => setDeleting(null)} onConfirm={() => void removeTask()}
      description="任务的原文、识别结果、最终稿和操作记录会一起删除，无法恢复。"/>
    <ConfirmDialog open={purging} title="清理旧任务" confirmLabel="清理" busy={busy} onClose={() => setPurging(false)} onConfirm={() => void purge()}
      description="删除创建时间早于所选天数的任务及其操作记录，无法恢复。">
      <label className="history-purge">
        <span>删除</span>
        <select className="select input-sm" value={purgeDays} onChange={event => setPurgeDays(Number(event.target.value))} aria-label="保留天数">
          {[1, 7, 30, 90, 180, 365].map(days => <option key={days} value={days}>{days} 天前</option>)}
        </select>
        <span>的任务</span>
      </label>
    </ConfirmDialog>
  </div>
}
