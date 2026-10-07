import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Download, FileJson, FileSpreadsheet, FileUp, FolderUp, ListChecks, RefreshCw, Trash2, X } from 'lucide-react'
import { api } from '../api'
import ConfigPanel from '../components/ConfigPanel'
import RedactedText from '../components/RedactedText'
import { ConfirmDialog, EmptyState, MenuButton, Progress, RedactionSkeleton, Spinner } from '../components/ui'
import { loadProcessingConfig, saveProcessingConfig } from '../configStore'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { ENTITY_LABEL, STRATEGY_LABEL, STRENGTH_LABEL, entityStyle } from '../lib/entities'
import { saveBlob, toCsv } from '../lib/download'
import { formatBytes, formatTime, shortId, uid } from '../lib/format'
import { normalizeConfig, type BatchJob, type DetectResult, type EntityType, type ProcessingConfig } from '../types'
import './Batch.css'

type QueuedFile = { id: string; file: File; name: string; status: 'reading' | 'ready' | 'error'; records: Array<{ row: number; text: string }>; error?: string }
type Sample = { key: string; file: string; row: number; text: string; excerpt: boolean; result?: DetectResult; error?: string }

const ACCEPT = /\.(txt|md|csv|json|docx|pdf)$/i
const MAX_BYTES = 20_000_000
const MAX_RECORDS = 2000
const SAMPLE_SIZE = 3
const SAMPLE_CHARS = 600

export const JOB_STATUS: Record<BatchJob['status'], string> = {
  queued: '排队中', running: '处理中', completed: '已完成', completed_with_errors: '部分失败', failed: '失败',
}

const isRunning = (job: BatchJob | null) => Boolean(job && (job.status === 'queued' || job.status === 'running'))

const ROW_STATE: Record<string, string> = { needs_review: '待复核', completed: '已完成', deleted: '任务已删除' }

/** 结果表格 CSV：只含脱敏后的最终稿，不含原文。 */
export function resultsCsv(job: BatchJob) {
  return toCsv(['文件', '段落', '任务', '状态', '实体数', '待确认', '最终稿版本', '最终稿'], (job.payload.results || []).map(row => [
    row.file, row.row, row.task_id, ROW_STATE[row.status] || '已完成', row.entity_count, row.pending_count, row.final_revision, row.final_text ?? row.redacted_text,
  ]))
}

function jobSummary(job: BatchJob) {
  const results = job.payload.results || []
  // 在任务记录里删掉的段落仍列出来，但不计入统计
  const live = results.filter(row => row.status !== 'deleted')
  const byType = new Map<EntityType, number>()
  for (const row of live) for (const [type, count] of Object.entries(row.by_type || {})) byType.set(type as EntityType, (byType.get(type as EntityType) || 0) + count)
  return {
    results,
    failures: job.payload.failures || [],
    entities: live.reduce((sum, row) => sum + row.entity_count, 0),
    pending: live.reduce((sum, row) => sum + row.pending_count, 0),
    needsReview: live.filter(row => row.status === 'needs_review').length,
    byType: [...byType.entries()].sort((a, b) => b[1] - a[1]),
  }
}

/** 试跑只看效果：长段落取开头一节，在句末截断。 */
function excerpt(text: string) {
  const characters = Array.from(text)
  if (characters.length <= SAMPLE_CHARS) return { text, cut: false }
  const head = characters.slice(0, SAMPLE_CHARS).join('')
  const stop = Math.max(...['。', '！', '？', '\n', '. '].map(mark => head.lastIndexOf(mark)))
  return { text: stop > SAMPLE_CHARS / 2 ? head.slice(0, stop + 1) : head, cut: true }
}

function describeFiles(job: BatchJob) {
  const files = job.payload.files || [...new Set((job.payload.results || []).map(row => row.file))]
  if (!files.length) return '—'
  return files.length === 1 ? files[0] : `${files[0]} 等 ${files.length} 个文件`
}

/** 在多个文件之间轮流取段落，让试跑样本覆盖不同来源。 */
function pickSamples(queue: QueuedFile[], round: number): Sample[] {
  const ready = queue.filter(item => item.status === 'ready')
  const pool: Sample[] = []
  const longest = Math.max(0, ...ready.map(item => item.records.length))
  for (let index = 0; index < longest; index += 1) {
    for (const item of ready) {
      const record = item.records[index]
      if (!record?.text.trim()) continue
      const part = excerpt(record.text)
      pool.push({ key: `${item.id}-${record.row}`, file: item.name, row: record.row, text: part.text, excerpt: part.cut })
    }
  }
  if (pool.length <= SAMPLE_SIZE) return pool
  const start = (round * SAMPLE_SIZE) % pool.length
  return Array.from({ length: SAMPLE_SIZE }, (_, offset) => pool[(start + offset) % pool.length])
}

export default function Batch() {
  const toast = useToast()
  const { currentProject, currentProjectId, refreshStats, selectProject, projects, projectsLoaded } = useApp()
  const [params, setParams] = useSearchParams()
  const jobParam = params.get('job')
  const projectParam = params.get('project')

  const [queue, setQueue] = useState<QueuedFile[]>([])
  const [dragging, setDragging] = useState(false)
  const [config, setConfigState] = useState<ProcessingConfig>(loadProcessingConfig)
  const [samples, setSamples] = useState<Sample[]>([])
  const [sampleConfig, setSampleConfig] = useState('')
  const [sampleRound, setSampleRound] = useState(0)
  const [sampling, setSampling] = useState(false)
  const [jobs, setJobs] = useState<BatchJob[] | null>(null)
  const [job, setJob] = useState<BatchJob | null>(null)
  const [starting, setStarting] = useState(false)
  const [deleting, setDeleting] = useState<BatchJob | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const resultRef = useRef<HTMLElement>(null)

  const setConfig = useCallback((next: ProcessingConfig) => { setConfigState(next); saveProcessingConfig(next) }, [])

  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '')
    folderInput.current?.setAttribute('directory', '')
    const onConfig = (event: Event) => setConfigState(normalizeConfig((event as CustomEvent).detail))
    window.addEventListener('privshield-config', onConfig)
    return () => window.removeEventListener('privshield-config', onConfig)
  }, [])

  // /batch?project=ID：从项目页或外部链接进入时，切换到该项目并套用它的方案
  useEffect(() => {
    if (!projectParam || !projectsLoaded) return
    if (projects.some(item => item.id === projectParam) && projectParam !== currentProjectId) selectProject(projectParam)
    const next = new URLSearchParams(params); next.delete('project'); setParams(next, { replace: true })
  }, [projectParam, projectsLoaded, projects, currentProjectId, selectProject, params, setParams])

  // 最近的批处理按当前项目隔离；切换项目时先清空，避免短暂显示别的项目的记录
  const loadJobs = useCallback(async () => {
    try { setJobs((await api.jobs(currentProjectId || null)).items) } catch { setJobs(current => current || []) }
  }, [currentProjectId])
  useEffect(() => { setJobs(null); void loadJobs() }, [loadJobs])

  // 刚删除的批处理：地址栏更新前不再去取它，也不再轮询（否则会报“没有找到这次批处理”）
  const deletedJobs = useRef(new Set<string>())
  useEffect(() => {
    if (!jobParam || job?.id === jobParam || deletedJobs.current.has(jobParam)) return
    api.job(jobParam).then(setJob).catch(() => { toast('没有找到这次批处理，可能已被删除', 'error'); setParams({}, { replace: true }) })
  }, [jobParam, job?.id, toast, setParams])

  // 处理中每秒刷新一次进度
  useEffect(() => {
    if (!job || !isRunning(job)) return
    const id = job.id
    const timer = window.setInterval(async () => {
      if (deletedJobs.current.has(id)) return
      try {
        const next = await api.job(id)
        if (deletedJobs.current.has(id)) return
        setJob(current => current?.id === id ? next : current)
        setJobs(current => current ? current.map(item => item.id === id ? next : item) : current)
        if (!isRunning(next)) {
          void loadJobs(); void refreshStats()
          toast(next.status === 'completed' ? `批处理完成，共 ${next.total} 条` : `批处理结束，${next.failed} 条失败`, next.status === 'completed' ? 'success' : 'error')
        }
      } catch { /* 网络抖动时下一轮再取 */ }
    }, 900)
    return () => window.clearInterval(timer)
  }, [job, loadJobs, refreshStats, toast])

  const ready = queue.filter(item => item.status === 'ready')
  const recordCount = ready.reduce((sum, item) => sum + item.records.length, 0)
  const reading = queue.some(item => item.status === 'reading')
  const tooMany = recordCount > MAX_RECORDS
  const sampleStale = samples.length > 0 && sampleConfig !== JSON.stringify(config)

  function addFiles(list: FileList | File[] | null | undefined) {
    if (!list) return
    const incoming = Array.from(list)
    const supported = incoming.filter(file => ACCEPT.test(file.name))
    const oversized = supported.filter(file => file.size > MAX_BYTES)
    const entries: QueuedFile[] = supported.filter(file => file.size <= MAX_BYTES)
      .map(file => ({ id: uid(), file, name: file.webkitRelativePath || file.name, status: 'reading', records: [] }))
    const fresh = entries.filter(entry => !queue.some(item => item.name === entry.name && item.file.size === entry.file.size))
    setQueue(current => [...current, ...fresh])
    const notes: string[] = []
    if (incoming.length > supported.length) notes.push(`${incoming.length - supported.length} 个文件格式不支持`)
    if (oversized.length) notes.push(`${oversized.length} 个文件超过 20 MB`)
    if (entries.length > fresh.length) notes.push(`${entries.length - fresh.length} 个文件已在列表中`)
    if (notes.length) toast(`已跳过：${notes.join('，')}`, 'info')
    for (const entry of fresh) {
      api.extract([entry.file]).then(data => {
        const records = data.records.filter(record => record.text.trim()).map(record => ({ row: record.row, text: record.text }))
        setQueue(current => current.map(item => item.id === entry.id ? { ...item, status: records.length ? 'ready' : 'error', records, error: records.length ? undefined : '没有可处理的文字' } : item))
      }).catch(caught => {
        setQueue(current => current.map(item => item.id === entry.id ? { ...item, status: 'error', error: caught instanceof Error ? caught.message : '读取失败' } : item))
      })
    }
    if (fileInput.current) fileInput.current.value = ''
    if (folderInput.current) folderInput.current.value = ''
  }

  function removeFile(id: string) {
    setQueue(current => current.filter(item => item.id !== id))
    setSamples(current => current.filter(sample => !sample.key.startsWith(`${id}-`)))
  }

  async function runSamples(round = sampleRound) {
    const picked = pickSamples(queue, round)
    if (!picked.length) return
    setSampling(true)
    setSamples(picked)
    const snapshot = JSON.stringify(config)
    const results = await Promise.all(picked.map(async sample => {
      try { return { ...sample, result: await api.detect(sample.text, config, currentProjectId || null, false) } }
      catch (caught) { return { ...sample, error: caught instanceof Error ? caught.message : '试跑失败' } }
    }))
    setSamples(results)
    setSampleConfig(snapshot)
    setSampling(false)
  }

  async function start() {
    if (!ready.length || starting || tooMany) return
    setStarting(true)
    try {
      const created = await api.batch(ready.map(item => item.file), config, currentProjectId || null)
      setJob(created)
      setParams({ job: created.id }, { replace: true })
      void loadJobs()
      window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : '批处理没有启动', 'error')
    } finally { setStarting(false) }
  }

  async function removeJob() {
    if (!deleting) return
    setDeleteBusy(true)
    deletedJobs.current.add(deleting.id)
    try {
      await api.deleteJob(deleting.id)
      if (job?.id === deleting.id) { setJob(null); setParams({}, { replace: true }) }
      toast(isRunning(deleting) ? '已停止并删除这次批处理' : '已删除这次批处理及其任务', 'success')
      void loadJobs(); void refreshStats()
      setDeleting(null)
    } catch (caught) {
      deletedJobs.current.delete(deleting.id)
      toast(caught instanceof Error ? caught.message : '删除失败', 'error')
    } finally { setDeleteBusy(false) }
  }

  function openJob(target: BatchJob) {
    setJob(target)
    setParams({ job: target.id }, { replace: true })
    window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
  }

  const planLabel = config.use_policies ? '按实体类型分别设置的方式' : `${STRATEGY_LABEL[config.strategy]}${config.strategy === 'mask' ? '' : `（力度${STRENGTH_LABEL[config.privacy_strength]}）`}`

  return <div className="page batch">
    <header className="page-head">
      <div>
        <h1 className="page-title">批量处理</h1>
        <p className="page-desc">一次处理多个文件。先用几段样本试跑，确认方案合适后再全部处理，结果可以逐条复核、整体导出。</p>
      </div>
    </header>

    <section className="batch-step">
      <header className="step-head">
        <span className="step-no" aria-hidden="true">1</span>
        <div><h2>选择文件</h2><p>支持 TXT、Markdown、CSV、JSON、Word、PDF，单个文件不超过 20 MB。文档整份处理（很长时按段切开）；CSV 和 JSON 每行（条）一段，所有列都参与识别，导出时还原成原来的列。</p></div>
      </header>
      <div className={`dropzone${dragging ? ' is-dragging' : ''}${queue.length ? ' has-files' : ''}`}
        onDragOver={(event: DragEvent) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)}
        onDrop={(event: DragEvent) => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files) }}>
        <input ref={fileInput} type="file" multiple hidden accept=".txt,.md,.csv,.json,.docx,.pdf" onChange={event => addFiles(event.target.files)}/>
        <input ref={folderInput} type="file" multiple hidden onChange={event => addFiles(event.target.files)}/>
        <p className="dropzone-copy">把文件或文件夹拖到这里</p>
        <div className="dropzone-actions">
          <button type="button" className="btn" onClick={() => fileInput.current?.click()}><FileUp size={15}/>选择文件</button>
          <button type="button" className="btn" onClick={() => folderInput.current?.click()}><FolderUp size={15}/>选择文件夹</button>
        </div>
      </div>
      {queue.length > 0 && <div className="table-wrap batch-files">
        <table className="table">
          <thead><tr><th>文件</th><th className="is-num">大小</th><th className="is-num">段落</th><th>状态</th><th aria-label="操作"/></tr></thead>
          <tbody>
            {queue.map(item => <tr key={item.id}>
              <td className="batch-file-name">{item.name}</td>
              <td className="is-num">{formatBytes(item.file.size)}</td>
              <td className="is-num">{item.status === 'ready' ? item.records.length : '—'}</td>
              <td>{item.status === 'reading' ? <span className="batch-file-state"><Spinner size={13}/>读取中</span> : item.status === 'ready' ? <span className="chip chip-ok">可以处理</span> : <span className="chip chip-bad" title={item.error}>{item.error || '读取失败'}</span>}</td>
              <td className="batch-file-remove"><button type="button" className="icon-btn" aria-label={`移除 ${item.name}`} onClick={() => removeFile(item.id)}><X size={15}/></button></td>
            </tr>)}
          </tbody>
          <tfoot><tr>
            <td colSpan={2}>{ready.length} 个文件可以处理</td>
            <td className="is-num">{recordCount}</td>
            <td colSpan={2}>{tooMany ? <span className="batch-limit">单次最多 {MAX_RECORDS} 段，请分批处理</span> : <button type="button" className="link-btn" onClick={() => { setQueue([]); setSamples([]) }}>清空列表</button>}</td>
          </tr></tfoot>
        </table>
      </div>}
    </section>

    <section className="batch-step">
      <header className="step-head">
        <span className="step-no" aria-hidden="true">2</span>
        <div><h2>设置方案并试跑</h2><p>方案和工作台共用{currentProject ? `，当前来自项目「${currentProject.name}」` : ''}。试跑只做预览，不会保存到任务记录。</p></div>
      </header>
      <div className="batch-plan">
        <div className="batch-config"><ConfigPanel value={config} onChange={setConfig} variant="batch"/></div>
        <div className="batch-samples sheet">
          <header className="batch-samples-head">
            <div><h3>样本试跑</h3><p>{samples.length ? `从已选文件中取 ${samples.length} 段` : '从已选文件中取 3 段，用当前方案处理'}</p></div>
            <div className="batch-samples-actions">
              {samples.length > 0 && <button type="button" className="btn btn-sm btn-quiet" disabled={sampling || recordCount <= SAMPLE_SIZE} onClick={() => { const next = sampleRound + 1; setSampleRound(next); void runSamples(next) }}><RefreshCw size={14}/>换几段</button>}
              <button type="button" className={`btn btn-sm${sampleStale ? ' btn-primary' : ''}`} disabled={sampling || !ready.length} onClick={() => void runSamples()}>{sampling && <Spinner size={13}/>}{samples.length ? (sampleStale ? '方案已改动，重新试跑' : '重新试跑') : '试跑'}</button>
            </div>
          </header>
          {!ready.length ? <EmptyState title="先选择文件" illustration="document">文件读取完成后，可以在这里预览方案的效果。</EmptyState>
            : !samples.length ? <EmptyState title="还没有试跑" illustration="document" action={<button type="button" className="btn btn-primary" onClick={() => void runSamples()} disabled={sampling}>用当前方案试跑</button>}>先看几段的效果，再决定是否调整方案。</EmptyState>
            : <ol className="sample-list">
              {samples.map(sample => <li key={sample.key} className="sample">
                <div className="sample-meta"><span className="sample-source" title={sample.file}>{sample.file}</span><span className="num">第 {sample.row} 段{sample.excerpt ? '（节选开头）' : ''}</span>
                  {sample.result && <span className="sample-counts num">{sample.result.spans.filter(span => span.status !== 'rejected').length} 处实体{sample.result.summary.pending ? `，${sample.result.summary.pending} 处待确认` : ''}</span>}
                </div>
                {sample.error ? <p className="range-error">{sample.error}</p>
                  : sample.result ? <RedactedText text={sample.result.redacted_text} replacements={sample.result.replacements} spansById={new Map(sample.result.spans.map(span => [span.id, span]))} compact/>
                  : <RedactionSkeleton lines={3}/>}
              </li>)}
            </ol>}
        </div>
      </div>
    </section>

    <section className="batch-step" ref={resultRef}>
      <header className="step-head">
        <span className="step-no" aria-hidden="true">3</span>
        <div><h2>处理与导出</h2><p>每段生成一个任务，可在工作台中逐条修改；有待确认实体的段落会进入人工复核。</p></div>
      </header>
      <div className="batch-run sheet">
        <p className="batch-run-copy">{ready.length ? <>将用<strong>{planLabel}</strong>处理 <strong className="num">{recordCount}</strong> 段文字，来自 {ready.length} 个文件。</> : '还没有可以处理的文件。'}</p>
        <button type="button" className="btn btn-primary btn-lg" disabled={!ready.length || reading || starting || tooMany || isRunning(job)} onClick={() => void start()}>
          {starting && <Spinner size={16}/>}开始批量处理
        </button>
      </div>
      {job && <JobPanel key={job.id} job={job} onDelete={() => setDeleting(job)}/>}
    </section>

    <section className="section">
      <div className="section-head"><h2 className="section-title">最近的批处理</h2>{jobs && jobs.length > 0 && <span className="section-note">{currentProject ? `项目「${currentProject.name}」的记录，` : ''}保留最近 30 次</span>}</div>
      {!jobs ? <div className="table-wrap"><div className="batch-loading"><Spinner/></div></div>
        : !jobs.length ? <div className="table-wrap"><EmptyState title="还没有批处理记录" illustration="folder">完成第一次批量处理后，会在这里留下记录，方便再次下载。</EmptyState></div>
        : <div className="table-wrap">
          <table className="table">
            <thead><tr><th>开始时间</th><th>文件</th><th className="is-num">段落</th><th>状态</th><th className="is-num">实体</th><th aria-label="操作"/></tr></thead>
            <tbody>
              {jobs.map(item => {
                const summary = jobSummary(item)
                return <tr key={item.id} className={job?.id === item.id ? 'is-current' : ''}>
                  <td className="num">{formatTime(item.created_at)}</td>
                  <td className="batch-file-name">{describeFiles(item)}</td>
                  <td className="is-num">{item.total}</td>
                  <td><JobStatus job={item}/></td>
                  <td className="is-num">{summary.entities}{summary.pending ? <span className="muted">（{summary.pending} 待确认）</span> : ''}</td>
                  <td className="batch-row-actions">
                    <button type="button" className="btn btn-sm btn-quiet" onClick={() => openJob(item)}>查看</button>
                    <button type="button" className="icon-btn" aria-label="删除这次批处理" title="删除" onClick={() => setDeleting(item)}><Trash2 size={15}/></button>
                  </td>
                </tr>
              })}
            </tbody>
          </table>
        </div>}
    </section>

    <ConfirmDialog open={Boolean(deleting)} title={isRunning(deleting) ? '停止并删除这次批处理？' : '删除这次批处理？'} confirmLabel={isRunning(deleting) ? '停止并删除' : '删除'} busy={deleteBusy} onClose={() => setDeleting(null)} onConfirm={() => void removeJob()}
      description={deleting ? (isRunning(deleting) ? '处理会立即停止，已经生成的任务和复核记录一并删除，无法恢复。' : `会同时删除它生成的 ${deleting.payload.results?.length || 0} 个任务和复核记录，删除后无法恢复。`) : ''}/>
  </div>
}

function JobStatus({ job }: { job: BatchJob }) {
  if (isRunning(job)) return <span className="job-status is-running"><Spinner size={13}/>{JOB_STATUS[job.status]} <span className="num">{job.processed}/{job.total}</span></span>
  const tone = job.status === 'completed' ? 'ok' : job.status === 'failed' ? 'bad' : 'warn'
  return <span className={`chip chip-${tone}`}>{JOB_STATUS[job.status]}</span>
}

function JobPanel({ job, onDelete }: { job: BatchJob; onDelete: () => void }) {
  const toast = useToast()
  const [showAll, setShowAll] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const running = isRunning(job)
  const summary = useMemo(() => jobSummary(job), [job])
  const percent = job.total ? (job.processed / job.total) * 100 : 0
  const maxType = summary.byType[0]?.[1] || 1
  const rows = showAll ? summary.results : summary.results.slice(0, 50)

  async function downloadZip() {
    setDownloading(true)
    try {
      const { blob, filename } = await api.downloadJob(job.id)
      saveBlob(filename, blob)
    } catch (caught) { toast(caught instanceof Error ? caught.message : '下载失败', 'error') }
    finally { setDownloading(false) }
  }

  function downloadJson() {
    const report = {
      id: job.id, created_at: job.created_at, status: job.status, total: job.total, processed: job.processed, failed: job.failed,
      config: job.payload.config,
      results: summary.results.map(({ file, row, task_id, status, entity_count, pending_count, by_type, final_revision, final_text }) => ({ file, row, task_id, status, entity_count, pending_count, by_type, final_revision, final_text })),
      failures: summary.failures,
    }
    saveBlob(`${job.id}-report.json`, JSON.stringify(report, null, 2), 'application/json;charset=utf-8')
  }

  return <div className="job sheet" aria-live="polite">
    <header className="job-head">
      <div>
        <h3>{running ? '正在处理' : JOB_STATUS[job.status]}</h3>
        <p className="num">批处理 {shortId(job.id)}，{describeFiles(job)}</p>
      </div>
      {running && <div className="job-actions"><button type="button" className="btn" onClick={onDelete}><X size={15}/>停止并删除</button></div>}
      {!running && <div className="job-actions">
        {summary.needsReview > 0 && <Link className="btn" to="/review"><ListChecks size={15}/>去复核 {summary.pending} 处</Link>}
        <MenuButton className="btn btn-primary" label="导出" icon={<Download size={15}/>} items={[
          { label: '全部最终稿 ZIP', description: '每个文件一份脱敏文本，附处理清单', icon: <Download size={15}/>, onSelect: () => void downloadZip(), disabled: downloading || !summary.results.length },
          { label: '结果表 CSV', description: '每段一行，含实体数和最终稿', icon: <FileSpreadsheet size={15}/>, onSelect: () => saveBlob(`${job.id}-results.csv`, resultsCsv(job), 'text/csv;charset=utf-8'), disabled: !summary.results.length },
          { label: '处理清单 JSON', description: '方案、统计和失败明细，不含原文', icon: <FileJson size={15}/>, onSelect: downloadJson },
        ]}/>
      </div>}
    </header>

    <div className="job-progress">
      <Progress value={percent} tone={running ? 'cobalt' : job.failed ? 'warn' : 'ok'} label="批处理进度"/>
      <p className="num">已处理 {job.processed} / {job.total} 段{job.failed ? `，${job.failed} 段失败` : ''}</p>
    </div>

    {summary.results.length > 0 && <div className="job-overview">
      <dl className="job-figures">
        <div><dt>识别实体</dt><dd className="num">{summary.entities}</dd></div>
        <div><dt>待确认</dt><dd className="num">{summary.pending}</dd></div>
        <div><dt>需要复核的段落</dt><dd className="num">{summary.needsReview}</dd></div>
      </dl>
      {summary.byType.length > 0 && <ul className="type-bars" aria-label="实体类型分布">
        {summary.byType.map(([type, count]) => <li key={type} style={entityStyle(type)}>
          <span className="type-bars-label">{ENTITY_LABEL[type] || type}</span>
          <span className="type-bars-track"><i style={{ width: `${(count / maxType) * 100}%` }}/></span>
          <span className="type-bars-value num">{count}</span>
        </li>)}
      </ul>}
    </div>}

    {summary.results.length > 0 && <div className="job-table">
      <table className="table">
        <thead><tr><th>文件</th><th className="is-num">段落</th><th>脱敏结果</th><th className="is-num">实体</th><th>状态</th><th aria-label="操作"/></tr></thead>
        <tbody>
          {rows.map(row => <tr key={row.task_id}>
            <td className="batch-file-name">{row.file}</td>
            <td className="is-num">{row.row}</td>
            <td className="job-preview">{(row.final_text ?? row.redacted_text).slice(0, 90)}</td>
            <td className="is-num">{row.entity_count}</td>
            <td>{row.status === 'deleted' ? <span className="chip">任务已删除</span> : row.status === 'needs_review' ? <span className="chip chip-warn">{row.pending_count} 处待确认</span> : row.has_manual_edits ? <span className="chip chip-cobalt">已修订 v{row.final_revision}</span> : <span className="chip chip-ok">已完成</span>}</td>
            <td>{row.status !== 'deleted' && <Link className="btn btn-sm btn-quiet" to={`/workbench?task=${encodeURIComponent(row.task_id)}`}>打开</Link>}</td>
          </tr>)}
        </tbody>
      </table>
      {summary.results.length > rows.length && <button type="button" className="btn btn-quiet btn-block job-more" onClick={() => setShowAll(true)}>显示全部 {summary.results.length} 段</button>}
    </div>}

    {summary.failures.length > 0 && <div className="job-failures">
      <h4>失败的段落</h4>
      <ul>{summary.failures.map(item => <li key={`${item.file}-${item.row}`}><span className="batch-file-name">{item.file} 第 {item.row} 段</span><span className="muted">{item.error}</span></li>)}</ul>
    </div>}
  </div>
}
