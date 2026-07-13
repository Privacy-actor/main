import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Check, Clipboard, Download, FileJson, FileUp, LoaderCircle, Network, Play, Plus, RotateCcw, ShieldAlert, Sparkles, WandSparkles, X } from 'lucide-react'
import { api } from '../api'
import { loadProcessingConfig, loadProjectId, saveProcessingConfig, saveProjectId } from '../configStore'
import AnnotatedText from '../components/AnnotatedText'
import FinalTextEditor from '../components/FinalTextEditor'
import PipelineTrace from '../components/PipelineTrace'
import { allEntityTypes, defaultProcessingConfig, type DetectResult, type EntityType, type KnowledgeLookup, type ProcessingConfig, type Project, type Span, type Strategy } from '../types'

const samples = {
  '中英混合访谈': '采访对象姓名：王洋，现就读于中国人民大学。联系电话是13800138000，邮箱 wang.yang@example.com，住在北京市海淀区中关村大街59号。His supervisor is Dr. Alice Morgan from Northbridge Institute.',
  '客户服务记录': '我叫李明，银行卡号为6222021001116247，护照号E12345678。请将材料寄到上海市浦东新区世纪大道88号，电话13912345678。',
  '英文邮件': 'Hi, I am Sarah Johnson. Please contact me at sarah.j@example.com or +1 415-555-0136. I currently work at Northbridge University in London.',
}

const labels: Record<EntityType, string> = { PERSON: '姓名', ORG: '机构', LOCATION: '地点', ADDRESS: '详细地址', PHONE: '电话', EMAIL: '邮箱', ID_CARD: '身份证', BANK_CARD: '银行卡', PASSPORT: '护照', CUSTOM: '自定义' }
const strategyLabels: Record<Strategy, string> = { mask: '一致性掩码', pseudonymize: '语义伪名替换', generalize: '知识层级泛化' }
type SaveState = { kind: 'idle' | 'success' | 'error'; message: string }

export default function Workbench() {
  const fileInput = useRef<HTMLInputElement>(null)
  const loadedTask = useRef('')
  const [searchParams, setSearchParams] = useSearchParams()
  const [text, setText] = useState(samples['中英混合访谈'])
  const [config, setConfig] = useState<ProcessingConfig>(loadProcessingConfig())
  const [projectId, setProjectIdState] = useState(loadProjectId())
  const [projects, setProjects] = useState<Project[]>([])
  const [result, setResult] = useState<DetectResult | null>(null)
  const [spans, setSpans] = useState<Span[]>([])
  const [selected, setSelected] = useState<Span | null>(null)
  const [replacementDraft, setReplacementDraft] = useState('')
  const [knowledge, setKnowledge] = useState<KnowledgeLookup | null>(null)
  const [knowledgeLoading, setKnowledgeLoading] = useState(false)
  const [redacted, setRedacted] = useState('')
  const [finalText, setFinalText] = useState('')
  const [savedFinalText, setSavedFinalText] = useState('')
  const [finalRevision, setFinalRevision] = useState(0)
  const [editorGeneration, setEditorGeneration] = useState(0)
  const [savingFinal, setSavingFinal] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle', message: '' })
  const [loading, setLoading] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [instructionPlan, setInstructionPlan] = useState<Record<string, unknown> | null>(null)
  const [quickKeywords, setQuickKeywords] = useState(config.custom_keywords.map(item => item.value).join('，'))
  const [preserveTerms, setPreserveTerms] = useState(config.preserve_terms.join('，'))
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => { api.projects().then(data => setProjects(data.items)).catch(() => undefined) }, [])
  useEffect(() => {
    let cancelled = false
    if (!selected) { setReplacementDraft(''); setKnowledge(null); setKnowledgeLoading(false); return }
    setReplacementDraft(typeof selected.metadata.custom_replacement === 'string' ? selected.metadata.custom_replacement : '')
    const embedded = selected.metadata.knowledge_levels
    if (Array.isArray(embedded) && embedded.length >= 3) {
      setKnowledge({ term: selected.text, entity_type: selected.entity_type, levels: embedded.slice(0, 3).map(String), source: String(selected.metadata.knowledge_source || 'task'), status: String(selected.metadata.knowledge_status || 'ready'), provider: String(selected.metadata.knowledge_provider || 'task'), detail: String(selected.metadata.knowledge_detail || '任务处理阶段已生成层级'), remote_attempted: false })
    } else setKnowledge(null)
    setKnowledgeLoading(true)
    api.knowledgeLookup(selected.text, selected.entity_type).then(data => { if (!cancelled) setKnowledge(data) }).catch(() => undefined).finally(() => { if (!cancelled) setKnowledgeLoading(false) })
    return () => { cancelled = true }
  }, [selected?.id, selected?.text, selected?.entity_type])
  useEffect(() => {
    const taskId = searchParams.get('task')
    if (!taskId || loadedTask.current === taskId) return
    loadedTask.current = taskId
    setLoading(true); setError(''); setNotice('')
    api.getTask(taskId).then(snapshot => {
      setText(snapshot.text)
      applySnapshot(snapshot)
      if (snapshot.project_id) { setProjectIdState(snapshot.project_id); saveProjectId(snapshot.project_id) }
      setNotice('已载入批处理或历史任务，可继续逐条复核并编辑最终稿。')
    }).catch(caught => { loadedTask.current = ''; setError(caught instanceof Error ? caught.message : '任务载入失败') }).finally(() => setLoading(false))
  }, [searchParams])

  const hasUnsavedFinalText = Boolean(result) && finalText !== savedFinalText
  const hasManualFinalText = Boolean(result) && finalText !== redacted
  const counts = useMemo(() => spans.filter(span => span.status !== 'rejected').reduce<Record<string, number>>((summary, span) => {
    summary[span.entity_type] = (summary[span.entity_type] || 0) + 1
    return summary
  }, {}), [spans])

  function persistConfig(next: ProcessingConfig) {
    setConfig(next)
    saveProcessingConfig(next)
  }

  function clearAnalysis() {
    setResult(null); setSpans([]); setSelected(null); setRedacted(''); setFinalText(''); setSavedFinalText(''); setFinalRevision(0)
    setSaveState({ kind: 'idle', message: '' }); setEditorGeneration(value => value + 1)
  }

  function confirmDiscard(message: string) {
    return !hasUnsavedFinalText || window.confirm(message)
  }

  function replaceSource(next: string, message = '') {
    if (!confirmDiscard('最终文本存在未保存修改。更换原文会丢失这些修改，是否继续？')) return
    setText(next)
    if (result) clearAnalysis()
    setNotice(message)
  }

  function effectiveConfig(): ProcessingConfig {
    const split = (value: string) => value.split(/[，,\n]/).map(item => item.trim()).filter(Boolean)
    return {
      ...config,
      custom_keywords: split(quickKeywords).map(value => ({ value, entity_type: 'CUSTOM', case_sensitive: false })),
      preserve_terms: split(preserveTerms),
    }
  }

  function applySnapshot(snapshot: DetectResult, selectedId?: string) {
    const nextFinal = snapshot.final_text || snapshot.redacted_text
    setResult(snapshot); setSpans(snapshot.spans); setRedacted(snapshot.redacted_text); setFinalText(nextFinal); setSavedFinalText(nextFinal)
    setFinalRevision(snapshot.final_revision || 0); setSelected(snapshot.spans.find(span => span.id === selectedId) || snapshot.spans[0] || null)
    setEditorGeneration(value => value + 1)
  }

  async function run() {
    if (!text.trim()) return
    if (!confirmDiscard('重新检测会替换当前未保存的最终稿，是否继续？')) return
    setLoading(true); setError(''); setNotice('')
    setSearchParams({}, { replace: true }); loadedTask.current = ''
    try {
      const nextConfig = effectiveConfig(); persistConfig(nextConfig)
      const data = await api.detect(text, nextConfig, projectId || null)
      applySnapshot(data)
      setSaveState({ kind: 'success', message: '自动脱敏结果已保存为任务初始版本。' })
    } catch (caught) { setError(caught instanceof Error ? caught.message : '检测失败') } finally { setLoading(false) }
  }

  async function importFile(file?: File) {
    if (!file) return
    setLoading(true); setError('')
    try {
      const extracted = await api.extract([file])
      replaceSource(extracted.text, `已从 ${file.name} 提取 ${extracted.records.length} 段文本，请确认后运行检测。`)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '文件解析失败') } finally { setLoading(false); if (fileInput.current) fileInput.current.value = '' }
  }

  async function reviewAndSync(payload: Record<string, unknown>, selectedId?: string) {
    if (!result) return false
    if (hasManualFinalText && !window.confirm('继续调整实体或策略会重新生成自动稿，并替换当前人工修订稿。是否继续？')) return false
    try {
      const response = await api.review(payload)
      let snapshot = response.snapshot
      if ((snapshot.final_text || snapshot.redacted_text) !== snapshot.redacted_text) {
        const reset = await api.saveFinalText(snapshot.task_id, snapshot.redacted_text, snapshot.redacted_text, snapshot.final_revision || 0, '实体或策略调整后恢复最新自动结果')
        snapshot = { ...snapshot, ...reset }
      }
      applySnapshot(snapshot, selectedId)
      setSaveState({ kind: 'success', message: '调整已写入任务快照和审计日志。' })
      return true
    } catch (caught) { setError(caught instanceof Error ? caught.message : '复核保存失败'); return false }
  }

  async function updateStatus(status: Span['status']) {
    if (!selected || !result) return
    await reviewAndSync({ task_id: result.task_id, span_id: selected.id, operation: status === 'rejected' ? 'reject' : 'accept', before: selected.status, after: status }, selected.id)
  }

  async function changeType(type: EntityType) {
    if (!selected || !result || type === selected.entity_type) return
    await reviewAndSync({ task_id: result.task_id, span_id: selected.id, operation: 'change_type', before: selected.entity_type, after: type }, selected.id)
  }

  async function changeSpanStrategy(strategy: Strategy) {
    if (!selected || !result || strategy === selected.strategy) return
    await reviewAndSync({ task_id: result.task_id, span_id: selected.id, operation: 'set_span_strategy', before: selected.strategy, after: strategy }, selected.id)
  }

  async function setCustomReplacement(value = replacementDraft) {
    if (!selected || !result) return
    const next = value.trim()
    const before = typeof selected.metadata.custom_replacement === 'string' ? selected.metadata.custom_replacement : ''
    if (next === before) return
    const changed = await reviewAndSync({ task_id: result.task_id, span_id: selected.id, operation: 'set_replacement', before, after: next }, selected.id)
    if (changed) setReplacementDraft(next)
  }

  async function changeStrategy(strategy: Strategy) {
    if (!result) { persistConfig({ ...config, strategy }); return }
    const changed = await reviewAndSync({ task_id: result.task_id, span_id: 'all', operation: 'set_strategy', before: config.strategy, after: strategy }, selected?.id)
    if (changed) persistConfig({ ...config, strategy })
  }

  async function changeStrength(privacyStrength: number) {
    if (!result) { persistConfig({ ...config, privacy_strength: privacyStrength }); return }
    const changed = await reviewAndSync({ task_id: result.task_id, span_id: 'all', operation: 'set_strength', before: String(config.privacy_strength), after: String(privacyStrength) }, selected?.id)
    if (changed) persistConfig({ ...config, privacy_strength: privacyStrength })
  }

  async function addEntity() {
    if (!result) return
    const raw = window.prompt('请输入字符区间 start:end，例如 10:15')
    if (!raw) return
    const [start, end] = raw.split(':').map(Number)
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > text.length) { setError('字符区间无效。'); return }
    const type = window.prompt(`实体类型：${allEntityTypes.join(' / ')}`, 'CUSTOM') as EntityType | null
    if (!type || !allEntityTypes.includes(type)) return
    const span: Span = { id: `human_${crypto.randomUUID().slice(0, 8)}`, start, end, text: text.slice(start, end), entity_type: type, score: 1, sources: ['HUMAN'], status: 'accepted', conflict: false, strategy: config.strategy, metadata: { operation: 'manual_add' } }
    await reviewAndSync({ task_id: result.task_id, span_id: span.id, operation: 'add', before: null, after: type, span }, span.id)
  }

  async function adjustBoundary() {
    if (!selected || !result) return
    const raw = window.prompt('输入调整后的 start:end', `${selected.start}:${selected.end}`)
    if (!raw) return
    await reviewAndSync({ task_id: result.task_id, span_id: selected.id, operation: 'adjust_boundary', before: `${selected.start}:${selected.end}`, after: raw }, selected.id)
  }

  async function parseInstruction() {
    if (!config.instruction?.trim()) return
    setParsing(true); setError('')
    try { setInstructionPlan(await api.parseInstruction(config.instruction, config.use_llm)) } catch (caught) { setError(caught instanceof Error ? caught.message : '需求解析失败') } finally { setParsing(false) }
  }

  async function selectProject(id: string) {
    if (!confirmDiscard('切换项目会替换当前配置，是否继续？')) return
    setProjectIdState(id); saveProjectId(id)
    const project = projects.find(item => item.id === id)
    if (project) {
      const next = { ...defaultProcessingConfig, ...project.config }
      persistConfig(next); setQuickKeywords(next.custom_keywords.map(item => item.value).join('，')); setPreserveTerms(next.preserve_terms.join('，'))
    }
    if (result) clearAnalysis()
  }

  async function saveFinalText(note?: string) {
    if (!result || finalText === savedFinalText) return
    setSavingFinal(true); setError('')
    try {
      const saved = await api.saveFinalText(result.task_id, finalText, redacted, finalRevision, note)
      setSavedFinalText(saved.final_text); setFinalRevision(saved.final_revision)
      setResult(current => current ? { ...current, final_text: saved.final_text, final_revision: saved.final_revision, has_manual_edits: saved.has_manual_edits } : current)
      setSaveState({ kind: 'success', message: saved.changed ? `最终稿已保存；本次人工修改区约 ${saved.audit?.changed_characters || 0} 个字符。` : '内容未变化，无需新增版本。' })
    } catch (caught) { const message = caught instanceof Error ? caught.message : '最终稿保存失败'; setSaveState({ kind: 'error', message }); setError(message) } finally { setSavingFinal(false) }
  }

  function exportAuditJson() {
    if (!result) return
    const payload = { ...result, spans, redacted_text: redacted, final_text: finalText, final_revision: finalRevision, has_manual_edits: finalText !== redacted, has_unsaved_changes: finalText !== savedFinalText, applied_config: effectiveConfig() }
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${result.task_id}-audit.json`; anchor.click(); URL.revokeObjectURL(url)
  }

  return <div className="page workbench-page">
    <header className="page-header"><div><div className="eyebrow">PRIVACY OPERATIONS</div><h1>隐私处理工作台</h1><p>菜单配置与自然语言需求双入口，检测、复核、编辑、审计和导出形成完整闭环。</p></div><div className="header-actions"><button className="btn ghost" onClick={() => { if (confirmDiscard('确定重置当前工作台？')) clearAnalysis() }}><RotateCcw size={16}/>重置结果</button><button className="btn primary" onClick={run} disabled={loading || !text.trim()}>{loading ? <LoaderCircle className="spin" size={17}/> : <Play size={17}/>}开始检测</button></div></header>
    {error && <div className="error-banner"><ShieldAlert size={17}/>{error}</div>}{notice && <div className="notice-banner">{notice}</div>}

    <section className="panel compact-config"><div className="compact-config-grid"><label><span>当前项目</span><select value={projectId} onChange={event => selectProject(event.target.value)}><option value="">临时配置</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label><span>脱敏策略</span><select value={config.strategy} onChange={event => changeStrategy(event.target.value as Strategy)}>{Object.entries(strategyLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label><label><span>保护强度</span><select value={config.privacy_strength} onChange={event => changeStrength(Number(event.target.value))}><option value="1">低 · 保留结构</option><option value="2">中 · 平衡</option><option value="3">高 · 强保护</option></select></label><label className="toggle-inline"><input type="checkbox" checked={config.use_llm} onChange={event => persistConfig({ ...config, use_llm: event.target.checked })}/><span>启用 14B 核验</span></label></div>
      <div className="entity-toggle-row">{allEntityTypes.map(type => <label className={config.enabled_entity_types.includes(type) ? 'active' : ''} key={type}><input type="checkbox" checked={config.enabled_entity_types.includes(type)} onChange={() => { const exists = config.enabled_entity_types.includes(type); const next = exists ? config.enabled_entity_types.filter(item => item !== type) : [...config.enabled_entity_types, type]; if (next.length) persistConfig({ ...config, enabled_entity_types: next }) }}/><span>{labels[type]}</span></label>)}</div>
      <div className="quick-rules"><label><span>临时敏感关键词</span><input value={quickKeywords} onChange={event => setQuickKeywords(event.target.value)} placeholder="逗号分隔，例如：天枢计划，内部编号"/></label><label><span>本次保留词</span><input value={preserveTerms} onChange={event => setPreserveTerms(event.target.value)} placeholder="例如：北京，公开机构名"/></label></div>
      <div className="instruction-row"><textarea value={config.instruction || ''} onChange={event => persistConfig({ ...config, instruction: event.target.value || null })} placeholder="自然语言需求，例如：保留北京地名，但隐藏上海相关地点；姓名使用伪名。"/><button className="btn ghost" onClick={parseInstruction} disabled={parsing || !config.instruction?.trim()}><WandSparkles size={16}/>{parsing ? '解析中' : '解析预览'}</button>{instructionPlan && <code title={JSON.stringify(instructionPlan, null, 2)}>已解析：{String(instructionPlan.parser || '规则解析器')}</code>}</div>
    </section>

    <div className="workbench-grid">
      <section className="panel input-panel"><div className="panel-title"><div><span className="step-number">01</span><strong>输入与文件导入</strong></div><span className="char-count">{text.length.toLocaleString()} / 100,000</span></div><div className="sample-row"><span>内置样例</span>{Object.keys(samples).map(name => <button key={name} onClick={() => replaceSource(samples[name as keyof typeof samples])}>{name}</button>)}</div><textarea value={text} maxLength={100000} onChange={event => replaceSource(event.target.value, result ? '原文已修改，旧 Span 已失效，请重新检测。' : '')} placeholder="粘贴需要检测的中文、英文或混合文本…"/><div className="input-footer"><small>支持 TXT / MD / CSV / JSON / DOCX / PDF 文本提取</small><div><input ref={fileInput} type="file" accept=".txt,.md,.csv,.json,.docx,.pdf" hidden onChange={event => importFile(event.target.files?.[0])}/><button className="btn ghost" onClick={() => fileInput.current?.click()}><FileUp size={16}/>导入文件</button></div></div></section>
      <section className="panel analysis-panel"><div className="panel-title"><div><span className="step-number">02</span><strong>实体识别</strong></div>{result && <div className="analysis-tools"><button onClick={addEntity}><Plus size={12}/>新增实体</button><span className="risk-pill"><ShieldAlert size={14}/>风险 {result.summary.risk_score}</span></div>}</div>{!result ? <div className="empty-state"><div className="empty-orbit"><Sparkles size={25}/></div><strong>等待开始检测</strong><p>运行后将在原文中高亮隐私实体，点击任意实体可查看识别依据。</p></div> : <><div className="legend">{Object.entries(counts).map(([type, count]) => <span key={type}><i className={`legend-dot entity-${type}`}/>{labels[type as EntityType]} {count}</span>)}</div><AnnotatedText text={text} spans={spans} selected={selected?.id} onSelect={setSelected}/></> }</section>
      <aside className="panel inspector-panel"><div className="panel-title"><div><span className="step-number">03</span><strong>实体复核</strong></div></div>{!selected ? <div className="inspector-empty">选择高亮实体查看详情</div> : <div className="entity-detail">
        <div className="detail-hero"><span className={`type-icon entity-${selected.entity_type}`}>{labels[selected.entity_type][0]}</span><div><small>{labels[selected.entity_type]}</small><strong>{selected.text}</strong></div><span className={`review-status ${selected.status}`}>{selected.status === 'pending' ? '待复核' : selected.status === 'rejected' ? '已拒绝' : '已接受'}</span></div>
        <div className="confidence"><span>综合置信度</span><b>{Math.round((selected.score || 0) * 100)}%</b><div><i style={{ width: `${(selected.score || 0) * 100}%` }}/></div></div>
        <dl><div><dt>字符区间</dt><dd>{selected.start} — {selected.end}</dd></div><div><dt>识别来源</dt><dd>{selected.sources.map(source => <span className="source-tag" key={source}>{source}</span>)}</dd></div></dl>
        <label className="type-select"><span>调整实体类型</span><select value={selected.entity_type} onChange={event => changeType(event.target.value as EntityType)}>{allEntityTypes.map(type => <option value={type} key={type}>{labels[type]}</option>)}</select></label>
        <label className="type-select"><span>该实体替换方式</span><select value={selected.strategy} onChange={event => changeSpanStrategy(event.target.value as Strategy)}>{Object.entries(strategyLabels).map(([key, value]) => <option value={key} key={key}>{value}</option>)}</select></label>
        <div className="custom-replacement"><span>自定义替换词</span><div><input value={replacementDraft} maxLength={500} onChange={event => setReplacementDraft(event.target.value)} placeholder="留空则按所选策略自动生成"/><button onClick={() => setCustomReplacement()}>应用</button></div>{typeof selected.metadata.custom_replacement === 'string' && <button className="replacement-reset" onClick={() => setCustomReplacement('')}>恢复策略生成</button>}</div>
        <div className="knowledge-card"><div><Network size={16}/><strong>知识图谱分级</strong><span>{knowledgeLoading ? '查询中' : knowledge?.provider || '本地'}</span></div>{knowledge ? <><ol>{knowledge.levels.slice(0, 3).map((level, index) => <li className={config.privacy_strength === index + 1 ? 'active' : ''} key={`${level}-${index}`}><span>{['低', '中', '高'][index]}</span><strong>{level}</strong>{config.privacy_strength === index + 1 && <small>当前采用</small>}</li>)}</ol><p>{knowledge.detail}</p></> : <p>{knowledgeLoading ? '正在读取实体层级…' : '当前实体使用类型通用层级。'}</p>}</div>
        <button className="boundary-button" onClick={adjustBoundary}>调整字符边界</button><div className="review-buttons"><button className="btn reject" onClick={() => updateStatus('rejected')}><X size={16}/>拒绝并保留</button><button className="btn accept" onClick={() => updateStatus('accepted')}><Check size={16}/>接受并脱敏</button></div>
      </div>}</aside>
    </div>

    {result && <><section className="panel trace-panel"><div className="section-heading"><div><span>PROCESS TRACE</span><h2>完整处理轨迹</h2></div><small>任务 {result.task_id}</small></div><PipelineTrace trace={result.trace}/></section><section className="comparison"><div className="comparison-card original"><div className="comparison-head"><span>原始文本</span><button onClick={() => navigator.clipboard.writeText(text)}><Clipboard size={15}/>复制</button></div><p>{text}</p></div><div className="comparison-arrow">→</div><div className="comparison-card safe"><div className="comparison-head"><span><ShieldAlert size={15}/>自动脱敏结果</span><button onClick={() => navigator.clipboard.writeText(redacted)}><Clipboard size={15}/>复制</button></div><p>{redacted}</p></div></section><FinalTextEditor key={`${result.task_id}-${editorGeneration}`} value={finalText} automaticText={redacted} savedText={savedFinalText} revision={finalRevision} saving={savingFinal} saveState={saveState} onChange={value => { setFinalText(value); setSaveState({ kind: 'idle', message: '' }) }} onSave={saveFinalText}/><div className="export-bar"><div><FileJson size={18}/><span><strong>可审计最终结果已生成</strong><small>JSON 同时保留自动结果、人工最终稿、Span、模型信息、项目配置与版本号</small></span></div><div className="export-actions"><span className={hasUnsavedFinalText ? 'export-warning' : 'export-ready'}>{hasUnsavedFinalText ? '最终稿尚未保存' : `服务器版本 v${finalRevision}`}</span><button className="btn ghost" onClick={exportAuditJson}><Download size={16}/>导出审计 JSON</button></div></div></>}
  </div>
}
