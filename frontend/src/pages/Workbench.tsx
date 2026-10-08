import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Link, useBlocker, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Columns3, Download, FileJson, FileText, FileUp, History as HistoryIcon, Plus, Printer, ShieldCheck, ShieldAlert } from 'lucide-react'
import { api } from '../api'
import { loadDraft, loadProcessingConfig, saveDraft, saveProcessingConfig } from '../configStore'
import AnnotatedText from '../components/AnnotatedText'
import ConfigPanel from '../components/ConfigPanel'
import { AdjustRangeDialog, AddEntityDialog } from '../components/EntityRangeEditor'
import { EntityDetail, EntityList, filterSpans, useSameText, type EntityFilter } from '../components/EntityInspector'
import FinalTextEditor, { type SaveState } from '../components/FinalTextEditor'
import PipelineTrace from '../components/PipelineTrace'
import RecheckDialog from '../components/RecheckDialog'
import RedactedText from '../components/RedactedText'
import RestorePanel from '../components/RestorePanel'
import StrategyCompare from '../components/StrategyCompare'
import { Dialog, EmptyState, Kbd, MenuButton, RedactionSkeleton, Segmented, Spinner } from '../components/ui'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { ENTITY_LABEL, STRATEGY_SHORT, STRENGTH_LABEL, entityStyle, isPending, type Layer, spanLayers } from '../lib/entities'
import { saveBlob } from '../lib/download'
import { formatDateTime, sha256, shortId, uid } from '../lib/format'
import { buildTaskReport, operationLabel } from '../lib/report'
import { contextLanguage, countCharacters, describeLanguage } from '../lib/text'
import { normalizeConfig, type AuditEntry, type DetectResult, type EntityType, type ProcessingConfig, type RecheckFinding, type RecheckResult, type Span, type Strategy } from '../types'
import './Workbench.css'

export const SAMPLES = [
  {
    key: 'interview', label: '中英混合访谈',
    text: '【虚构演示资料】\n访谈员：请先介绍一下你的学习经历。\n受访者：我叫林若宁，目前在星河大学读研究生，导师是陈晓峰教授，他也是计算机系主任。平时可以通过 lin.rn@example.com 或 13912345678 联系我。\nInterviewer: Who supervised your summer project?\nParticipant: Dr. Alice Morgan from Northbridge Institute. I also worked with Kevin Zhang on the survey, and 林若宁 presented it in Shanghai last July.',
  },
  {
    key: 'service', label: '客服记录',
    text: '【虚构演示资料】\n联系人：李明，电话13800138000，邮箱 liming@example.com。\n客户反馈：上周在上海市浦东新区世纪大道88号的门店办理业务，银行卡 6222021001116248 被重复扣款。\n处理结果：款项已原路退回。项目内部代号为星舟，请勿外传。回访时李明表示满意。',
  },
  {
    key: 'email', label: '英文邮件',
    text: '[Fictional demonstration]\nHi team,\nI am Sarah Johnson from Northbridge University in London. Please send the signed form to sarah.j@example.com or call +1 415-555-0136 before Friday, and copy Michael Chen on the reply.\nPassport number for the visa letter: E12345678.\nBest,\nSarah Johnson',
  },
]

type ExportFormat = 'txt' | 'docx' | 'report' | 'json'
const EXPORT_LABEL: Record<ExportFormat, string> = { txt: ' TXT', docx: ' Word', report: '报告', json: '审计记录' }

/**
 * 重新打开任务时恢复的方案：用用户提交时的原始方案（applied_config.requested）。
 * 旧任务没有这一项时，去掉由自然语言要求解析出来的保留词，避免它们被写回方案、带到下一段文本里。
 */
export function requestedPlan(applied: Record<string, unknown>): Record<string, unknown> {
  const requested = applied.requested
  if (requested && typeof requested === 'object') return requested as Record<string, unknown>
  const derived = new Set(((applied.instruction_plan as { preserve_terms?: unknown[] } | null)?.preserve_terms || []).map(String))
  if (!derived.size || !Array.isArray(applied.preserve_terms)) return applied
  return { ...applied, preserve_terms: applied.preserve_terms.filter(term => !derived.has(String(term))) }
}

function isTyping(target: EventTarget | null) {
  const element = target as HTMLElement | null
  return Boolean(element && (element.closest('input, textarea, select, [contenteditable="true"]')))
}

export default function Workbench() {
  const toast = useToast()
  const { currentProject, currentProjectId, refreshStats, refreshProjects, engine } = useApp()
  const [params, setParams] = useSearchParams()
  const taskParam = params.get('task')

  const [text, setText] = useState(() => loadDraft() ?? SAMPLES[0].text)
  const [config, setConfigState] = useState<ProcessingConfig>(loadProcessingConfig)
  const [result, setResult] = useState<DetectResult | null>(null)
  const [view, setView] = useState<'compose' | 'analyze'>(taskParam ? 'analyze' : 'compose')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [layer, setLayer] = useState<Layer | null>(null)
  const [filter, setFilter] = useState<EntityFilter>('all')
  const [query, setQuery] = useState('')
  const [showTags, setShowTags] = useState(false)
  const [revealing, setRevealing] = useState(false)
  const [tab, setTab] = useState<'result' | 'final' | 'restore'>('result')

  const [finalText, setFinalText] = useState('')
  const [savedFinal, setSavedFinal] = useState('')
  const [revision, setRevision] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle', message: '' })
  const [autoChanged, setAutoChanged] = useState(false)
  const [highlight, setHighlight] = useState<{ start: number; end: number; nonce: number } | null>(null)

  const [addOpen, setAddOpen] = useState(false)
  // 在原文里拖选文字后，选区旁出现“补充为实体”，打开补充对话框时带上选中的文字
  const [addQuery, setAddQuery] = useState('')
  const [pick, setPick] = useState<{ text: string; top: number; left: number } | null>(null)
  const originalRef = useRef<HTMLDivElement>(null)
  const capturePick = useCallback(() => {
    const selection = window.getSelection()
    const node = originalRef.current
    if (!selection || selection.isCollapsed || !selection.rangeCount || !node) { setPick(null); return }
    const range = selection.getRangeAt(0)
    const value = selection.toString().trim()
    if (!node.contains(range.commonAncestorContainer) || !value || value.length > 80 || /[\r\n]/.test(value)) { setPick(null); return }
    const rect = range.getBoundingClientRect()
    setPick({ text: value, top: rect.bottom + 6, left: Math.max(8, Math.min(rect.left, window.innerWidth - 150)) })
  }, [])
  useEffect(() => {
    if (!pick) return
    const hide = () => setPick(null)
    const onSelectionChange = () => { if (window.getSelection()?.isCollapsed) setPick(null) }
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    document.addEventListener('selectionchange', onSelectionChange)
    return () => {
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('resize', hide)
      document.removeEventListener('selectionchange', onSelectionChange)
    }
  }, [pick])
  const [adjustSpan, setAdjustSpan] = useState<Span | null>(null)
  const [compareOpen, setCompareOpen] = useState(false)
  const [recheck, setRecheck] = useState<{ open: boolean; loading: boolean; result: RecheckResult | null; pendingExport: ExportFormat | null; forText: string | null }>({ open: false, loading: false, result: null, pendingExport: null, forText: null })
  const [auditsOpen, setAuditsOpen] = useState(false)
  const [audits, setAudits] = useState<AuditEntry[] | null>(null)

  const fileInput = useRef<HTMLInputElement>(null)
  const loadedTask = useRef<string | null>(null)
  const generation = useRef(0)
  const savingRef = useRef(false)

  const setConfig = useCallback((next: ProcessingConfig) => {
    setConfigState(next)
    saveProcessingConfig(next)
  }, [])

  // 侧栏切换项目时同步方案
  useEffect(() => {
    const onConfig = (event: Event) => setConfigState(normalizeConfig((event as CustomEvent).detail))
    window.addEventListener('privshield-config', onConfig)
    return () => window.removeEventListener('privshield-config', onConfig)
  }, [])

  useEffect(() => { saveDraft(text) }, [text])

  const spans = useMemo(() => result?.spans || [], [result])
  const spansById = useMemo(() => new Map(spans.map(span => [span.id, span])), [spans])
  const selected = selectedId ? spansById.get(selectedId) || null : null
  const sameText = useSameText(spans, selected)
  const visibleList = useMemo(() => filterSpans(spans, filter, layer, query), [spans, filter, layer, query])
  const focusIds = useMemo(() => layer ? new Set(spans.filter(span => spanLayers(span).includes(layer)).map(span => span.id)) : null, [spans, layer])
  const replacementFor = useMemo(() => new Map((result?.replacements || []).map(item => [item.span_id, item])), [result])
  const appliedStrategy = (result?.applied_config.strategy as Strategy | undefined) || config.strategy
  const appliedStrength = Number(result?.applied_config.privacy_strength || config.privacy_strength)
  const byPolicies = Boolean(result?.applied_config.use_policies)
  const riskLevel = (result?.applied_config.risk_level as 'standard' | 'strict' | undefined) || 'strict'
  const llmStatus = result?.trace.find(step => step.key === 'llm')?.status
  const automatic = result?.redacted_text || ''
  const dirty = Boolean(result) && finalText !== savedFinal
  const pendingCount = spans.filter(isPending).length
  const resultIsCurrent = Boolean(result) && result?.text === text
  const projectModified = Boolean(currentProject) && JSON.stringify(normalizeConfig(currentProject?.config)) !== JSON.stringify(config)

  const applySnapshot = useCallback((snapshot: DetectResult, options: { select?: string | null; keepDraft?: boolean } = {}) => {
    generation.current += 1
    setResult(snapshot)
    const serverFinal = snapshot.final_text ?? snapshot.redacted_text
    setSavedFinal(serverFinal)
    setRevision(snapshot.final_revision || 0)
    if (!options.keepDraft) setFinalText(serverFinal)
    if (options.select !== undefined) setSelectedId(options.select)
    if (snapshot.review_notice) toast(snapshot.review_notice)
  }, [toast])

  const firstSelection = (snapshot: DetectResult) => (snapshot.spans.find(isPending) || snapshot.spans.find(span => span.status !== 'rejected') || null)?.id || null

  // 上手指南里的“打开示例”：/workbench?sample=interview 换成对应示例（可撤销）
  const sampleParam = params.get('sample')
  useEffect(() => {
    if (!sampleParam) return
    const sample = SAMPLES.find(item => item.key === sampleParam) || SAMPLES[0]
    const previous = text
    if (sample.text !== previous) {
      setText(sample.text)
      toast(`已放入示例：${sample.label}。点“识别并脱敏”开始`, 'info', previous.trim() ? { label: '撤销', onClick: () => setText(previous) } : undefined)
    }
    setView('compose')
    const next = new URLSearchParams(params); next.delete('sample'); setParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sampleParam])

  // 从任务记录、批量结果或复核队列打开任务
  useEffect(() => {
    if (!taskParam || loadedTask.current === taskParam) return
    loadedTask.current = taskParam
    setLoading(true); setError('')
    api.getTask(taskParam).then(snapshot => {
      setText(snapshot.text)
      setConfig(normalizeConfig(requestedPlan(snapshot.applied_config)))
      applySnapshot(snapshot, { select: params.get('span') || firstSelection(snapshot) })
      setAutoChanged(false); setLayer(null); setFilter(params.get('span') ? 'all' : 'all'); setQuery(''); setTab('result')
      setView('analyze')
    }).catch(caught => {
      loadedTask.current = null
      setError(caught instanceof Error ? caught.message : '任务载入失败')
      setView('compose')
    }).finally(() => setLoading(false))
  }, [taskParam, applySnapshot, setConfig, params])

  async function runDetect() {
    if (!text.trim() || loading) return
    // 重新识别会生成新任务；上一个任务的最终稿先保存好
    if (dirty) await saveFinal()
    setLoading(true); setError(''); setView('analyze')
    try {
      const snapshot = await api.detect(text, config, currentProjectId || null)
      loadedTask.current = snapshot.task_id
      setParams({ task: snapshot.task_id }, { replace: false })
      applySnapshot(snapshot, { select: firstSelection(snapshot) })
      setLayer(null); setFilter('all'); setQuery(''); setTab('result'); setAutoChanged(false)
      setRecheck(current => ({ ...current, result: null, forText: null }))
      setRevealing(true)
      window.setTimeout(() => setRevealing(false), 1400)
      void refreshStats()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '识别失败')
      setView(result ? 'analyze' : 'compose')
    } finally { setLoading(false) }
  }

  async function importFile(file?: File) {
    if (!file) return
    setLoading(true); setError('')
    try {
      const extracted = await api.extract([file])
      if (!extracted.text.trim()) throw new Error(`${file.name} 中没有可识别的文字`)
      const previous = text
      setText(extracted.text)
      toast(`已从 ${file.name} 提取 ${extracted.records.length} 段文字`, 'success', { label: '撤销', onClick: () => setText(previous) })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '文件解析失败')
    } finally {
      setLoading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  function pickSample(sample: typeof SAMPLES[number]) {
    if (sample.text === text) return
    const previous = text
    setText(sample.text)
    toast(`已换成示例：${sample.label}`, 'info', { label: '撤销', onClick: () => setText(previous) })
  }

  async function review(payload: Record<string, unknown>, select?: string | null) {
    if (!result || busy) return false
    setBusy(true); setError('')
    const manual = finalText !== automatic
    try {
      const { snapshot } = await api.review({ task_id: result.task_id, ...payload })
      applySnapshot(snapshot, { select: select === undefined ? selectedId : select, keepDraft: manual })
      if (manual) setAutoChanged(true)
      setRecheck(current => ({ ...current, result: null, forText: null }))
      void refreshStats()
      return true
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : '操作没有保存', 'error')
      return false
    } finally { setBusy(false) }
  }

  function nextSelectable(afterId: string) {
    const list = filterSpans(spans, filter, layer, query)
    const index = list.findIndex(span => span.id === afterId)
    const rest = [...list.slice(index + 1), ...list.slice(0, Math.max(0, index))]
    return (rest.find(isPending) || list[index + 1] || list[index] || null)?.id || null
  }

  async function decide(status: 'accepted' | 'rejected', all: boolean) {
    if (!selected) return
    const ids = all ? sameText.map(span => span.id) : [selected.id]
    const advance = filter === 'pending' || isPending(selected) ? nextSelectable(selected.id) : selected.id
    const ok = ids.length > 1
      ? await review({ span_id: selected.id, operation: status === 'accepted' ? 'accept_many' : 'reject_many', span_ids: ids }, advance)
      : await review({ span_id: selected.id, operation: status === 'accepted' ? 'accept' : 'reject', before: selected.status, after: status }, advance)
    if (ok) toast(status === 'accepted' ? `已确认脱敏${ids.length > 1 ? ` ${ids.length} 处` : ''}` : `已恢复原文${ids.length > 1 ? ` ${ids.length} 处` : ''}`, 'success')
  }

  async function changeType(type: EntityType) {
    if (!selected || type === selected.entity_type) return
    await review({ span_id: selected.id, operation: 'change_type', before: selected.entity_type, after: type, strategy: selected.strategy })
  }

  async function setSpanStrategy(strategy: Strategy) {
    if (!selected || strategy === selected.strategy) return
    await review({ span_id: selected.id, operation: 'set_span_strategy', before: selected.strategy, after: strategy })
  }

  async function setReplacement(value: string) {
    if (!selected) return
    const before = typeof selected.metadata.custom_replacement === 'string' ? selected.metadata.custom_replacement : ''
    if (await review({ span_id: selected.id, operation: 'set_replacement', before, after: value })) toast(value ? '已使用自定义替换词' : '已恢复按方式生成', 'success')
  }

  async function changeStrategy(strategy: Strategy) {
    if (!result) return
    if (await review({ span_id: 'all', operation: 'set_strategy', before: appliedStrategy, after: strategy })) setConfig({ ...config, strategy, use_policies: false })
  }

  async function changeStrength(level: number) {
    if (!result) return
    if (await review({ span_id: 'all', operation: 'set_strength', before: String(appliedStrength), after: String(level) })) setConfig({ ...config, privacy_strength: level })
  }

  async function applyCompared(strategy: Strategy, level: number) {
    let ok = true
    if (strategy !== appliedStrategy || byPolicies) ok = await review({ span_id: 'all', operation: 'set_strategy', before: appliedStrategy, after: strategy })
    if (ok && level !== appliedStrength) ok = await review({ span_id: 'all', operation: 'set_strength', before: String(appliedStrength), after: String(level) })
    if (ok) {
      setConfig({ ...config, strategy, privacy_strength: level, use_policies: false })
      setCompareOpen(false)
      toast(`已改用${STRATEGY_SHORT[strategy]}，力度${STRENGTH_LABEL[level]}`, 'success')
    }
  }

  async function addEntities(ranges: Array<{ start: number; end: number }>, type: EntityType) {
    const characters = Array.from(text)
    const strategy = appliedStrategy
    const newSpans = ranges.map(range => ({
      id: `human_${uid().slice(0, 10)}`, start: range.start, end: range.end, text: characters.slice(range.start, range.end).join(''),
      entity_type: type, score: 1, sources: ['HUMAN'], status: 'accepted', conflict: false, strategy, metadata: { operation: 'manual_add' },
    }))
    const ok = await review({ span_id: newSpans[0].id, operation: 'add_many', spans: newSpans }, newSpans[0].id)
    if (ok) {
      setAddOpen(false); setFilter('all'); setLayer(null); setQuery('')
      toast(`已补充 ${newSpans.length} 处${ENTITY_LABEL[type]}`, 'success')
    }
    return ok
  }

  async function adjustRange(start: number, end: number) {
    if (!adjustSpan) return false
    const ok = await review({ span_id: adjustSpan.id, operation: 'adjust_boundary', before: `${adjustSpan.start}:${adjustSpan.end}`, after: `${start}:${end}` }, adjustSpan.id)
    if (ok) setAdjustSpan(null)
    return ok
  }

  const saveFinal = useCallback(async (note?: string) => {
    if (!result || savingRef.current || finalText === savedFinal) return true
    savingRef.current = true
    setSaving(true)
    const current = generation.current
    try {
      const saved = await api.saveFinalText(result.task_id, finalText, automatic, revision, note)
      if (current !== generation.current) return false
      setSavedFinal(saved.final_text)
      setRevision(saved.final_revision)
      setResult(previous => previous ? { ...previous, final_text: saved.final_text, final_revision: saved.final_revision, has_manual_edits: saved.has_manual_edits } : previous)
      setSaveState({ kind: 'success', message: '' })
      return true
    } catch (caught) {
      setSaveState({ kind: 'error', message: caught instanceof Error ? caught.message : '保存失败' })
      return false
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }, [result, finalText, savedFinal, automatic, revision])

  async function runRecheck(draft = finalText, open = true) {
    if (!result) return null
    setRecheck(current => ({ ...current, open: open || current.open, loading: true }))
    try {
      const data = await api.recheck(result.task_id, draft)
      setRecheck(current => ({ ...current, loading: false, result: data, forText: draft }))
      return data
    } catch (caught) {
      setRecheck(current => ({ ...current, loading: false }))
      toast(caught instanceof Error ? caught.message : '复检失败', 'error')
      return null
    }
  }

  async function performExport(format: ExportFormat, checked?: RecheckResult | null) {
    if (!result) return
    try {
      if (format === 'report') {
        const [{ items }, hash] = await Promise.all([api.taskAudits(result.task_id), sha256(finalText)])
        // 刚做完的复检结果直接传进来；状态更新要到下一次渲染才生效，不能从 recheck 里读
        const recheckResult = checked ?? (recheck.forText === finalText ? recheck.result : null)
        const html = buildTaskReport({ task: result, finalText, revision, audits: items, recheck: recheckResult, projectName: currentProject?.name, finalHash: hash })
        saveBlob(`${result.task_id}-report.html`, html, 'text/html;charset=utf-8')
      } else {
        const { blob, filename } = await api.exportTask(result.task_id, format)
        saveBlob(filename, blob)
      }
      toast(format === 'json' ? '审计记录已导出，文件含原文，请仅用于内部留档' : '已导出', 'success')
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : '导出失败', 'error')
    }
  }

  async function exportAs(format: ExportFormat) {
    if (!result) return
    if (dirty && !(await saveFinal())) { toast('最终稿保存失败，暂不导出', 'error'); return }
    let checked: RecheckResult | null = null
    if (format !== 'json') {
      checked = recheck.forText === finalText && recheck.result ? recheck.result : await runRecheck(finalText, false)
      if (checked && !checked.passed) {
        setRecheck(current => ({ ...current, open: true, pendingExport: format }))
        return
      }
    }
    await performExport(format, checked)
  }

  function locate(finding: RecheckFinding) {
    setRecheck(current => ({ ...current, open: false }))
    setTab('final')
    setHighlight({ start: finding.start, end: finding.end, nonce: Date.now() })
  }

  async function openAudits() {
    if (!result) return
    setAuditsOpen(true); setAudits(null)
    try { setAudits((await api.taskAudits(result.task_id)).items) } catch { setAudits([]) }
  }

  async function saveConfigToProject() {
    if (!currentProject) return
    try {
      await api.updateProject(currentProject.id, { config })
      await refreshProjects()
      toast(`已保存到项目「${currentProject.name}」`, 'success')
    } catch (caught) { toast(caught instanceof Error ? caught.message : '保存失败', 'error') }
  }

  // 键盘：A 确认，R 恢复，J/K 切换；Ctrl+Enter 开始识别
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (document.body.classList.contains('has-dialog')) return
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && view === 'compose') { event.preventDefault(); void runDetect(); return }
      if (view !== 'analyze' || isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
      const key = event.key.toLowerCase()
      if ((key === 'j' || key === 'k') && visibleList.length) {
        event.preventDefault()
        const index = visibleList.findIndex(span => span.id === selectedId)
        const next = visibleList[(index + (key === 'j' ? 1 : -1) + visibleList.length) % visibleList.length]
        setSelectedId(next.id)
      } else if (key === 'a' && selected && !busy) { event.preventDefault(); void decide('accepted', false) }
      else if (key === 'r' && selected && !busy) { event.preventDefault(); void decide('rejected', false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // 待确认的实体处理完后，列表回到全部，避免停在空列表
  useEffect(() => {
    if (filter === 'pending' && result && pendingCount === 0) setFilter('all')
  }, [filter, pendingCount, result])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  // 最终稿改了还没来得及自动保存就切到别的页面：先保存再离开，保存失败就留在这里
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    let cancelled = false
    void saveFinal().then(ok => {
      if (cancelled) return
      if (ok) blocker.proceed()
      else { blocker.reset(); toast('最终稿没有保存成功，先留在这里，请稍后再试', 'error') }
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blocker.state])

  const legend = useMemo(() => {
    const counts = new Map<EntityType, number>()
    for (const span of spans) if (span.status !== 'rejected') counts.set(span.entity_type, (counts.get(span.entity_type) || 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [spans])

  /* ---------------- 输入视图 ---------------- */
  if (view === 'compose') {
    const length = countCharacters(text)
    return <div className="page page-wide wb">
      <header className="page-head">
        <div>
          <h1 className="page-title">工作台</h1>
          <p className="page-desc">粘贴或导入一段文本，系统按规则、NER、大模型三层识别其中的隐私信息，按你的方案脱敏，复核后导出。</p>
        </div>
        {result && <div className="page-actions"><button type="button" className="btn" onClick={() => setView('analyze')}>{resultIsCurrent ? '回到处理结果' : '查看上次结果'}</button></div>}
      </header>
      {error && <div className="notice notice-bad wb-error"><ShieldAlert size={16}/>{error}</div>}
      <div className="compose-grid">
        <section className={`compose-source sheet${dragging ? ' is-dragging' : ''}`}
          onDragOver={(event: DragEvent) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)}
          onDrop={(event: DragEvent) => { event.preventDefault(); setDragging(false); void importFile(event.dataTransfer.files?.[0]) }}>
          <div className="compose-head">
            <div className="compose-samples">
              <span>示例</span>
              {SAMPLES.map(sample => <button type="button" key={sample.key} className={sample.text === text ? 'is-current' : ''} onClick={() => pickSample(sample)}>{sample.label}</button>)}
            </div>
            <input ref={fileInput} type="file" hidden accept=".txt,.md,.csv,.json,.docx,.pdf" onChange={event => void importFile(event.target.files?.[0])}/>
            <button type="button" className="btn btn-sm" onClick={() => fileInput.current?.click()} disabled={loading}><FileUp size={14}/>导入文件</button>
          </div>
          <textarea className="compose-area" value={text} maxLength={100000} onChange={event => setText(event.target.value)}
            placeholder="在这里粘贴需要处理的文本，或把 TXT、Word、PDF 文件拖进来" aria-label="原文" spellCheck={false}/>
          <div className="compose-foot">
            <span className="compose-meta num">{length.toLocaleString()} / 100,000 字{describeLanguage(text) && `，${describeLanguage(text)}`}</span>
            <span className="compose-formats">可导入 TXT、Word、PDF 等文件</span>
            <button type="button" className="btn btn-primary btn-lg" onClick={() => void runDetect()} disabled={loading || !text.trim()}>
              {loading ? <Spinner size={16}/> : <ShieldCheck size={17}/>}识别并脱敏<Kbd>Ctrl ↵</Kbd>
            </button>
          </div>
          {dragging && <div className="compose-drop">松开即可导入文件</div>}
        </section>

        <aside className="compose-plan">
          <div className="plan-head">
            <h2>处理方案</h2>
            <p>
              {currentProject ? <>来自项目 <Link to={`/projects/${currentProject.id}`}>{currentProject.name}</Link>{projectModified && <span className="plan-modified">已改动</span>}</> : <>临时方案，<Link to="/projects">可保存为项目</Link></>}
            </p>
            {projectModified && <button type="button" className="btn btn-sm" onClick={saveConfigToProject}>保存到项目</button>}
          </div>
          <ConfigPanel value={config} onChange={setConfig}/>
        </aside>
      </div>
    </div>
  }

  /* ---------------- 结果视图 ---------------- */
  const selectedIndex = selected ? visibleList.findIndex(span => span.id === selected.id) : -1
  const recheckFresh = recheck.forText === finalText ? recheck.result : null
  return <div className="wb wb-analyze">
    <header className="analyze-bar">
      <button type="button" className="btn btn-quiet" onClick={() => setView('compose')}><ArrowLeft size={16}/>修改原文</button>
      <div className="analyze-title">
        <h1>处理结果</h1>
        {result && <p className="num">任务 {shortId(result.task_id)}，{countCharacters(result.text).toLocaleString()} 字{describeLanguage(result.text) && `，${describeLanguage(result.text)}`}{pendingCount ? <>，<button type="button" className="link-btn" onClick={() => { setFilter('pending'); setLayer(null) }}>{pendingCount} 处待确认</button></> : '，无待确认'}</p>}
      </div>
      <div className="analyze-actions">
        <button type="button" className="btn" disabled={!result || loading} onClick={() => setCompareOpen(true)}><Columns3 size={15}/>三种方式对照</button>
        <button type="button" className={`btn${recheckFresh ? (recheckFresh.passed ? ' is-pass' : ' is-fail') : ''}`} disabled={!result || loading} onClick={() => void runRecheck(finalText, true)}>
          {recheckFresh && !recheckFresh.passed ? <ShieldAlert size={15}/> : <ShieldCheck size={15}/>}{recheckFresh ? (recheckFresh.passed ? '复检通过' : `${recheckFresh.high} 处残留`) : '隐私复检'}
        </button>
        <button type="button" className="icon-btn" title="操作记录" aria-label="操作记录" disabled={!result} onClick={openAudits}><HistoryIcon size={17}/></button>
        <MenuButton className="btn btn-primary" label="导出" icon={<Download size={15}/>} items={[
          { label: '最终稿 TXT', description: '纯文本，可直接交给大模型分析', icon: <FileText size={15}/>, onSelect: () => void exportAs('txt') },
          { label: '最终稿 Word', description: '.docx，按段落排版', icon: <FileText size={15}/>, onSelect: () => void exportAs('docx') },
          { label: '处理报告', description: '一页可打印的说明，不含原文', icon: <Printer size={15}/>, onSelect: () => void exportAs('report') },
          { label: '审计记录 JSON', description: '含原文和全部实体，仅供内部留档', icon: <FileJson size={15}/>, onSelect: () => void exportAs('json') },
        ]}/>
      </div>
    </header>

    {error && <div className="notice notice-bad wb-error"><ShieldAlert size={16}/>{error}</div>}

    {loading && !resultIsCurrent ? <div className="analyze-loading">
      <div className="sheet analyze-loading-sheet"><p className="analyze-loading-title">正在识别</p><p className="muted">规则层、NER 层依次处理，大模型核查视服务配置而定</p><RedactionSkeleton lines={8}/></div>
    </div> : result && <>
      <PipelineTrace trace={result.trace} spans={spans} activeLayer={layer} onLayer={value => { setLayer(value); if (value) setFilter('all') }}/>
      <div className="analyze-grid">
        <section className="doc-sheet sheet" aria-label="原文">
          <header className="doc-head">
            <h2>原文</h2>
            <div className="doc-legend">
              {legend.map(([type, count]) => <span key={type} className="legend-item" style={entityStyle(type)}><span className="dot"/>{ENTITY_LABEL[type]}<span className="num">{count}</span></span>)}
            </div>
            <label className="check doc-tags"><input type="checkbox" checked={showTags} onChange={event => setShowTags(event.target.checked)}/>显示类型</label>
          </header>
          <div className="doc-body" ref={originalRef} onMouseUp={capturePick} onKeyUp={capturePick}>
            <AnnotatedText text={result.text} spans={spans} selectedId={selectedId} linkedId={hoverId} focusIds={focusIds} showTags={showTags}
              showRejected={filter === 'rejected'} onSelect={span => setSelectedId(span.id)} onHover={setHoverId}/>
          </div>
          {pick && <button type="button" className="btn btn-sm selection-add" style={{ top: pick.top, left: pick.left }} disabled={busy}
            onMouseDown={event => event.preventDefault()}
            onClick={() => { setAddQuery(pick.text); setAddOpen(true); setPick(null); window.getSelection()?.removeAllRanges() }}>
            <Plus size={13}/>补充为实体
          </button>}
        </section>

        <section className="doc-sheet sheet" aria-label="脱敏结果">
          <header className="doc-head">
            <div className="doc-tabs" role="tablist">
              <button type="button" role="tab" aria-selected={tab === 'result'} className={tab === 'result' ? 'is-active' : ''} onClick={() => setTab('result')}>脱敏结果</button>
              <button type="button" role="tab" aria-selected={tab === 'final'} className={tab === 'final' ? 'is-active' : ''} onClick={() => setTab('final')}>
                最终稿<span className="num">v{revision}</span>{dirty && <i className="tab-dirty" aria-label="有未保存修改"/>}
              </button>
              <button type="button" role="tab" aria-selected={tab === 'restore'} className={tab === 'restore' ? 'is-active' : ''} onClick={() => setTab('restore')}
                title="把大模型的回答换回原文">还原回答</button>
            </div>
            {busy && <Spinner size={14}/>}
          </header>
          {tab === 'result' && <div className="doc-toolbar">
            <Segmented label="脱敏方式" size="sm" value={byPolicies ? ('' as Strategy) : appliedStrategy} disabled={busy} onChange={value => void changeStrategy(value)}
              options={(['mask', 'pseudonymize', 'generalize'] as Strategy[]).map(value => ({ value, label: STRATEGY_SHORT[value], title: byPolicies ? '当前按实体类型分别设置，选择后统一使用这种方式' : undefined }))}/>
            <span className="doc-toolbar-group">
              <span className="doc-toolbar-label">力度</span>
              <Segmented label="力度" size="sm" value={appliedStrength} disabled={busy || appliedStrategy === 'mask'} onChange={value => void changeStrength(value)}
                options={[1, 2, 3].map(level => ({ value: level, label: STRENGTH_LABEL[level], title: appliedStrategy === 'mask' ? '掩码不分力度' : undefined }))}/>
            </span>
            {byPolicies && <span className="doc-toolbar-note">按实体类型分别设置</span>}
          </div>}
          {tab === 'result' && <div className="doc-body">
            {result.has_manual_edits && <p className="doc-notice">最终稿已人工修改（v{revision}），这里显示的是自动结果。</p>}
            <RedactedText text={automatic} replacements={result.replacements} spansById={spansById} selectedId={selectedId} linkedId={hoverId}
              revealing={revealing} onSelect={id => setSelectedId(id)} onHover={setHoverId}/>
            {!result.replacements?.length && !spans.some(span => span.status !== 'rejected') && <p className="muted doc-empty">没有需要替换的内容。可以在右侧补充遗漏。</p>}
          </div>}
          {tab === 'final' && <div className="doc-body doc-body-editor">
            {autoChanged && finalText !== automatic && <div className="notice notice-info doc-auto-notice">
              <span>自动结果已随复核更新，最终稿保留了你的人工修改。</span>
              <button type="button" className="btn btn-sm notice-action" onClick={() => { setFinalText(automatic); setAutoChanged(false) }}>换成新的自动结果</button>
            </div>}
            <FinalTextEditor key={result.task_id} value={finalText} automaticText={automatic} savedText={savedFinal} revision={revision}
              saving={saving} saveState={saveState} highlight={highlight}
              onChange={value => { setFinalText(value); if (saveState.kind !== 'idle') setSaveState({ kind: 'idle', message: '' }) }} onSave={note => { void saveFinal(note) }}/>
          </div>}
          <RestorePanel key={result.task_id} taskId={result.task_id} hidden={tab !== 'restore'}/>
        </section>

        <aside className="inspector" aria-label="实体检查">
          <EntityList spans={spans} selectedId={selectedId} filter={filter} onFilter={value => { setFilter(value); if (value !== 'all') setLayer(null) }} layer={layer}
            query={query} onQuery={setQuery} onSelect={span => setSelectedId(span.id)} onAdd={() => { setAddQuery(''); setAddOpen(true) }} busy={busy}/>
          {selected ? <EntityDetail key={selected.id} span={selected} replacement={replacementFor.get(selected.id)} sameText={sameText}
            index={selectedIndex} total={visibleList.length} strength={appliedStrength} llmStatus={llmStatus} busy={busy}
            lang={contextLanguage(result.text, selected.start, selected.end)} threshold={engine.models?.confidence_threshold ?? 0.9}
            onPrev={() => { const index = visibleList.findIndex(span => span.id === selectedId); const next = visibleList[(index - 1 + visibleList.length) % visibleList.length]; if (next) setSelectedId(next.id) }}
            onNext={() => { const index = visibleList.findIndex(span => span.id === selectedId); const next = visibleList[(index + 1) % visibleList.length]; if (next) setSelectedId(next.id) }}
            onDecide={(status, all) => void decide(status, all)} onChangeType={type => void changeType(type)} onSetStrategy={strategy => void setSpanStrategy(strategy)}
            onSetReplacement={value => void setReplacement(value)} onAdjust={() => setAdjustSpan(selected)}/>
            : <div className="inspector-empty"><EmptyState title="选择一个实体" illustration="queue">点击原文或结果中的高亮词，在这里确认、恢复或调整。</EmptyState></div>}
        </aside>
      </div>
    </>}

    {result && <>
      <AddEntityDialog key={addOpen ? 'open' : 'closed'} open={addOpen} text={result.text} spans={spans} initialQuery={addQuery} busy={busy} onClose={() => setAddOpen(false)} onSubmit={addEntities}/>
      {adjustSpan && <AdjustRangeDialog key={adjustSpan.id} open text={result.text} span={adjustSpan} spans={spans} busy={busy} onClose={() => setAdjustSpan(null)} onSubmit={adjustRange}/>}
      <StrategyCompare open={compareOpen} text={result.text} spans={spans} current={byPolicies ? null : appliedStrategy} strength={appliedStrength} riskLevel={riskLevel} busy={busy}
        onClose={() => setCompareOpen(false)} onApply={(strategy, level) => void applyCompared(strategy, level)}/>
      <RecheckDialog open={recheck.open} loading={recheck.loading} result={recheck.result} exportLabel={recheck.pendingExport ? EXPORT_LABEL[recheck.pendingExport] : null}
        onClose={() => setRecheck(current => ({ ...current, open: false, pendingExport: null }))} onLocate={locate} onRecheck={() => void runRecheck(finalText, true)}
        onExportAnyway={() => { const format = recheck.pendingExport; setRecheck(current => ({ ...current, open: false, pendingExport: null })); if (format) void performExport(format) }}/>
      <Dialog open={auditsOpen} onClose={() => setAuditsOpen(false)} title="操作记录" description="这个任务上的每一次人工操作。记录中不保存敏感原文，只保存长度和摘要。">
        {!audits ? <div className="recheck-loading"><Spinner/></div> : !audits.length ? <p className="muted">还没有人工操作，当前是自动结果。</p> : <ol className="audit-timeline">
          {audits.map(item => <li key={item.id}><time className="num">{formatDateTime(item.created_at)}</time><strong>{operationLabel(item.operation)}</strong><span className="muted">{describeAudit(item)}</span></li>)}
        </ol>}
      </Dialog>
    </>}
  </div>
}

function describeAudit(item: AuditEntry) {
  const payload = item.payload as Record<string, unknown>
  if (item.operation === 'edit_text') return `保存为 v${payload.revision}，改动约 ${payload.changed_characters} 字`
  if (item.operation === 'change_type') return `${ENTITY_LABEL[payload.before as EntityType] || payload.before} 改为 ${ENTITY_LABEL[payload.after as EntityType] || payload.after}`
  if (item.operation === 'set_strategy' || item.operation === 'set_span_strategy') return `改为${STRATEGY_SHORT[payload.after as Strategy] || payload.after}`
  if (item.operation === 'set_strength') return `力度改为${STRENGTH_LABEL[Number(payload.after)] || payload.after}`
  if (item.operation === 'add_many') return `${(payload.spans as unknown[] | undefined)?.length || 0} 处`
  if (item.operation === 'accept_many' || item.operation === 'reject_many') return `${(payload.span_ids as unknown[] | undefined)?.length || 0} 处`
  if (item.operation === 'adjust_boundary') return `范围 ${payload.before} 改为 ${payload.after}`
  if (item.operation === 'restore') return `换回 ${payload.restored ?? 0} 处`
  return ''
}
