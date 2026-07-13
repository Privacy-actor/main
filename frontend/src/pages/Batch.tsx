import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, ExternalLink, Eye, FileSpreadsheet, FolderOpen, LoaderCircle, RotateCcw, UploadCloud, XCircle } from 'lucide-react'
import { api } from '../api'
import { loadProcessingConfig, loadProjectId } from '../configStore'
import type { BatchJob, DetectResult, ProcessingConfig } from '../types'

function csvCell(value: unknown) { return `"${String(value ?? '').replaceAll('"', '""')}"` }
function saveBlob(name: string, content: BlobPart, type?: string) {
  const blob = content instanceof Blob ? content : new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function Batch() {
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [config, setConfig] = useState<ProcessingConfig>(loadProcessingConfig())
  const [projectId] = useState(loadProjectId())
  const [job, setJob] = useState<BatchJob | null>(null)
  const [recentJobs, setRecentJobs] = useState<BatchJob[]>([])
  const [preview, setPreview] = useState<DetectResult | null>(null)
  const [running, setRunning] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '')
    folderInput.current?.setAttribute('directory', '')
    api.jobs().then(({ items }) => setRecentJobs(items)).catch(caught => setError(caught instanceof Error ? caught.message : '历史批处理加载失败'))
  }, [])

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return
    const timer = window.setInterval(() => api.job(job.id).then(next => { setJob(next); setRecentJobs(current => [next, ...current.filter(item => item.id !== next.id)]) }).catch(caught => setError(caught.message)), 700)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  function updateConfig(next: ProcessingConfig) {
    setConfig(next)
    setPreview(null)
  }

  function choose(list: FileList | null) {
    if (!list) return
    const supported = Array.from(list).filter(file => /\.(txt|md|csv|json|docx|pdf)$/i.test(file.name))
    const tooLarge = supported.find(file => file.size > 20 * 1024 * 1024)
    if (tooLarge) { setError(`${tooLarge.name} 超过 20 MB，请拆分后上传。`); return }
    setFiles(supported)
    setJob(null)
    setPreview(null)
    setError(supported.length ? '' : '未找到支持的文本或文档文件。')
  }

  async function runPreview() {
    if (!files.length) return
    setPreviewing(true)
    setError('')
    try {
      const extracted = await api.extract([files[0]])
      const sample = extracted.records[0]?.text || extracted.text
      if (!sample.trim()) throw new Error('首个文件没有可预检的文本。')
      setPreview(await api.detect(sample.slice(0, 100000), config, projectId || null))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '样本预检失败')
    } finally { setPreviewing(false) }
  }

  async function run() {
    if (!files.length) return
    setRunning(true)
    setError('')
    try {
      const created = await api.batch(files, config, projectId || null)
      setJob(created)
      setRecentJobs(current => [created, ...current.filter(item => item.id !== created.id)])
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : '批处理启动失败') }
    finally { setRunning(false) }
  }

  async function freshJob() {
    if (!job) throw new Error('没有可导出的批处理任务')
    const fresh = await api.job(job.id)
    setJob(fresh)
    setRecentJobs(current => [fresh, ...current.filter(item => item.id !== fresh.id)])
    return fresh
  }

  async function downloadResults() {
    try {
      const current = await freshJob()
      const rows = current.payload.results || []
      const csv = ['file,row,task_id,status,entity_count,pending_count,final_revision,final_text', ...rows.map(row => [row.file, row.row, row.task_id, row.status, row.entity_count, row.pending_count, row.final_revision, row.final_text || row.redacted_text].map(csvCell).join(','))].join('\n')
      saveBlob(`${current.id}-results.csv`, '\ufeff' + csv, 'text/csv;charset=utf-8')
    } catch (caught) { setError(caught instanceof Error ? caught.message : '结果 CSV 下载失败') }
  }

  function downloadFailures() {
    if (!job) return
    const csv = ['file,row,error,text_length,text_hash', ...(job.payload.failures || []).map(row => [row.file, row.row, row.error, row.text_length, row.text_hash].map(csvCell).join(','))].join('\n')
    saveBlob(`${job.id}-failures.csv`, '\ufeff' + csv, 'text/csv;charset=utf-8')
  }

  async function downloadJson() {
    try {
      const current = await freshJob()
      saveBlob(`${current.id}.json`, JSON.stringify(current, null, 2), 'application/json;charset=utf-8')
    } catch (caught) { setError(caught instanceof Error ? caught.message : '完整 JSON 下载失败') }
  }

  async function openJob(id: string) {
    try {
      const fresh = await api.job(id)
      setJob(fresh)
      setRecentJobs(current => [fresh, ...current.filter(item => item.id !== id)])
      setError('')
    } catch (caught) { setError(caught instanceof Error ? caught.message : '批处理任务刷新失败') }
  }

  async function removeJob(id: string) {
    if (!window.confirm('\u5220\u9664\u6279\u5904\u7406\u4efb\u52a1\u4f1a\u540c\u65f6\u5220\u9664\u5176\u9010\u6761\u4efb\u52a1\u4e0e\u5ba1\u8ba1\u8bb0\u5f55\uff0c\u4e14\u4e0d\u53ef\u6062\u590d\u3002\u662f\u5426\u7ee7\u7eed\uff1f')) return
    try {
      await api.deleteJob(id)
      setRecentJobs(current => current.filter(item => item.id !== id))
      if (job?.id === id) setJob(null)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '删除批处理任务失败') }
  }

  async function downloadZip() {
    if (!job) return
    setDownloading(true)
    setError('')
    try { saveBlob(`${job.id}-redacted.zip`, await api.downloadJob(job.id)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : '结果包下载失败') }
    finally { setDownloading(false) }
  }

  const finished = job && !['queued', 'running'].includes(job.status)

  return <div className="page">
    <header className="page-header"><div><div className="eyebrow">PERSISTENT BATCH PROCESSING</div><h1>文件与文件夹批量处理</h1><p>先用代表性样本预检，再提交后台批次；完成后可逐条复核并导出最终结果包。</p></div>{job && <button className="btn ghost" onClick={() => { setJob(null); setFiles([]); setPreview(null) }}><RotateCcw size={16}/>新建任务</button>}</header>
    {error && <div className="error-banner"><AlertTriangle/>{error}</div>}
    {!job ? <div className="batch-grid">
      <section className="panel upload-panel"><div className="section-heading"><div><span>STEP 01</span><h2>选择文件或整个文件夹</h2></div></div><div className="batch-source-buttons"><button className="drop-zone" onClick={() => fileInput.current?.click()} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); choose(event.dataTransfer.files) }}><UploadCloud/><strong>选择多个文件或拖放到此处</strong><span>TXT / MD / CSV / JSON / DOCX / PDF</span></button><button className="drop-zone folder-zone" onClick={() => folderInput.current?.click()}><FolderOpen/><strong>选择文件夹</strong><span>自动收集目录内支持的文件</span></button></div><input ref={fileInput} type="file" multiple hidden accept=".txt,.md,.csv,.json,.docx,.pdf" onChange={event => choose(event.target.files)}/><input ref={folderInput} type="file" multiple hidden accept=".txt,.md,.csv,.json,.docx,.pdf" {...({ webkitdirectory: '', directory: '' } as Record<string, string>)} onChange={event => choose(event.target.files)}/>{files.length > 0 && <div className="selected-files"><div><strong>已选择 {files.length} 个文件</strong><span>{(files.reduce((sum, file) => sum + file.size, 0) / 1024).toFixed(1)} KB</span></div><ul>{files.slice(0, 8).map(file => <li key={`${file.webkitRelativePath || file.name}-${file.size}`}><FileSpreadsheet/>{file.webkitRelativePath || file.name}</li>)}</ul>{files.length > 8 && <small>另有 {files.length - 8} 个文件</small>}</div>}</section>
      <section className="panel batch-config"><div className="section-heading"><div><span>STEP 02</span><h2>预检并确认运行配置</h2></div></div><label><span>策略</span><select value={config.strategy} onChange={event => updateConfig({ ...config, strategy: event.target.value as ProcessingConfig['strategy'] })}><option value="mask">一致性掩码</option><option value="pseudonymize">语义伪名</option><option value="generalize">层级泛化</option></select></label><label><span>保护强度</span><select value={config.privacy_strength} onChange={event => updateConfig({ ...config, privacy_strength: Number(event.target.value) })}><option value="1">低</option><option value="2">中</option><option value="3">高</option></select></label><label className="toggle-inline"><input type="checkbox" checked={config.use_llm} onChange={event => updateConfig({ ...config, use_llm: event.target.checked })}/><span>启用 14B 核验与补漏</span></label><div className="batch-config-summary"><span>项目：{projectId || '临时配置'}</span><span>实体类型：{config.enabled_entity_types.length} 类</span><span>模式：{config.deployment_mode === 'local' ? '本地' : '云端兼容'}</span></div><div className="batch-run-actions"><button className="btn ghost" disabled={!files.length || previewing} onClick={runPreview}>{previewing ? <LoaderCircle className="spin"/> : <Eye/>}{previewing ? '正在预检' : '用首个文件预检'}</button><button className="btn primary run-batch" disabled={!files.length || running} onClick={run}>{running ? <LoaderCircle className="spin"/> : <UploadCloud/>}{running ? '正在创建任务' : '确认配置并启动批处理'}</button></div>{preview && <div className="sample-preview"><div><strong>样本预检结果</strong><span>{preview.summary.total} 个实体 · {preview.summary.pending} 个待复核</span></div><p>{preview.redacted_text}</p><a href={`/workbench?task=${encodeURIComponent(preview.task_id)}`}>进入完整人工复核 <ExternalLink/></a></div>}</section>
    </div> : <section className="panel job-panel"><div className="job-heading"><div>{finished ? job.failed ? <AlertTriangle className="warning"/> : <CheckCircle2/> : <LoaderCircle className="spin"/>}<span><small>{job.id}</small><h2>{finished ? job.failed ? '处理完成，部分记录失败' : '批处理已完成' : '后台处理中'}</h2></span></div><strong>{job.progress}%</strong></div><div className="progress-track"><i style={{ width: `${job.progress}%` }}/></div><div className="job-stats"><div><b>{job.total}</b><span>总记录</span></div><div><b>{job.processed}</b><span>已处理</span></div><div><b>{job.payload.results?.length || 0}</b><span>成功</span></div><div className={job.failed ? 'danger' : ''}><b>{job.failed}</b><span>失败</span></div></div>{job.payload.results?.length > 0 && <div className="batch-preview"><h3>结果预览与逐条复核</h3>{job.payload.results.map(row => <div key={`${row.file}-${row.row}`}><span>{row.file} · {row.row}</span><p>{row.final_text || row.redacted_text}</p><b>{row.entity_count} 个实体{row.has_manual_edits ? ` · 已人工修订 v${row.final_revision}` : ""}</b><a href={`/workbench?task=${encodeURIComponent(row.task_id)}`}>人工复核 <ExternalLink/></a></div>)}</div>}{job.payload.failures?.length > 0 && <div className="failure-preview"><h3><XCircle/>失败记录</h3>{job.payload.failures.slice(0, 5).map(row => <div key={`${row.file}-${row.row}`}><span>{row.file} · {row.row}</span><code>{row.error}</code></div>)}</div>}<div className="job-downloads"><button className="btn primary" disabled={!finished || downloading} onClick={downloadZip}><Download/>{downloading ? '打包中' : '下载最终文件 ZIP'}</button><button className="btn ghost" disabled={!finished} onClick={downloadResults}><Download/>结果 CSV</button><button className="btn ghost" disabled={!job.failed} onClick={downloadFailures}><Download/>失败记录</button><button className="btn ghost" onClick={downloadJson}><Download/>完整 JSON</button></div></section>}
    {recentJobs.length > 0 && <section className="panel recent-jobs"><div className="section-heading"><div><span>PERSISTENT JOBS</span><h2>历史批处理任务</h2></div><small>刷新或离开页面后仍可继续查看和下载</small></div><div className="recent-job-list">{recentJobs.map(item => <div className={job?.id === item.id ? 'recent-job active' : 'recent-job'} key={item.id}><button onClick={() => openJob(item.id)}><strong>{item.id}</strong><span>{item.status} · {item.processed}/{item.total} · {item.progress}%</span><small>{new Date(item.updated_at).toLocaleString()}</small></button><button className="icon-delete" aria-label={`删除 ${item.id}`} onClick={() => removeJob(item.id)}>×</button></div>)}</div></section>}
  </div>
}
