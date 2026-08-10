import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Check, Cloud, ExternalLink, FolderCog, Plus, Save, Search, Server, Sparkles, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { loadProcessingConfig, loadProjectId, saveProcessingConfig, saveProjectId } from '../configStore'
import { allEntityTypes, defaultProcessingConfig, type CustomRule, type EntityType, type KnowledgeLookup, type ProcessingConfig, type Project } from '../types'

const labels: Record<EntityType, string> = { PERSON: '姓名', ORG: '机构', LOCATION: '地点', ADDRESS: '详细地址', PHONE: '电话', EMAIL: '邮箱', ID_CARD: '身份证', BANK_CARD: '银行卡', PASSPORT: '护照', CUSTOM: '自定义敏感项' }

export default function Projects() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState(loadProjectId())
  const [name, setName] = useState('默认隐私处理项目')
  const [description, setDescription] = useState('用于保存实体范围、脱敏强度和自定义规则。')
  const [config, setConfig] = useState<ProcessingConfig>(loadProcessingConfig())
  const [rules, setRules] = useState<CustomRule[]>([])
  const [ruleDraft, setRuleDraft] = useState({ name: '', kind: 'keyword' as 'keyword' | 'regex', pattern: '', entity_type: 'CUSTOM' as EntityType, case_sensitive: false })
  const [ruleTestText, setRuleTestText] = useState('Employee ACCT-12345678 / ACCT-87654321')
  const [parseResult, setParseResult] = useState<Record<string, unknown> | null>(null)
  const [knowledgeTerm, setKnowledgeTerm] = useState('中国人民大学')
  const [knowledgeType, setKnowledgeType] = useState<EntityType>('ORG')
  const [knowledgeResult, setKnowledgeResult] = useState<KnowledgeLookup | null>(null)
  const [knowledgeStatus, setKnowledgeStatus] = useState<Record<string, unknown> | null>(null)
  const [knowledgeBusy, setKnowledgeBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)

  async function refresh(preferred?: string) {
    const data = await api.projects()
    setProjects(data.items)
    const nextId = preferred ?? projectId
    const selected = data.items.find(item => item.id === nextId)
    if (selected) {
      setProjectId(selected.id); setName(selected.name); setDescription(selected.description); setConfig({ ...defaultProcessingConfig, ...selected.config }); saveProjectId(selected.id); saveProcessingConfig({ ...defaultProcessingConfig, ...selected.config })
      setRules((await api.rules(selected.id)).items)
    } else {
      setRules([])
    }
  }

  async function loadProjects() {
    const data = await api.projects()
    setProjects(data.items)
  }

  useEffect(() => { loadProjects().catch(error => setMessage(error.message)); api.knowledgeStatus().then(setKnowledgeStatus).catch(() => undefined) }, [])

  async function selectProject(id: string) {
    const selected = projects.find(item => item.id === id)
    setProjectId(id); saveProjectId(id); setEditing(true)
    if (!selected) { setRules([]); return }
    const nextConfig = { ...defaultProcessingConfig, ...selected.config }
    setName(selected.name); setDescription(selected.description); setConfig(nextConfig); saveProcessingConfig(nextConfig)
    setRules((await api.rules(id)).items)
  }

  function updateConfig(patch: Partial<ProcessingConfig>) {
    const next = { ...config, ...patch }
    setConfig(next); saveProcessingConfig(next)
  }

  async function createProject() {
    setBusy(true); setMessage('')
    try {
      const created = await api.createProject({ name: name.trim() || '新项目', description, config })
      await refresh(created.id); setMessage('项目已创建并设为当前项目。')
    } catch (error) { setMessage(error instanceof Error ? error.message : '创建失败') } finally { setBusy(false) }
  }

  async function saveProject() {
    if (!projectId) return createProject()
    setBusy(true); setMessage('')
    try {
      await api.updateProject(projectId, { name, description, config }); saveProcessingConfig(config); await refresh(projectId); setMessage('项目配置已保存，工作台和批处理将使用该配置。')
    } catch (error) { setMessage(error instanceof Error ? error.message : '保存失败') } finally { setBusy(false) }
  }

  async function removeProject() {
    if (!projectId || !window.confirm('删除项目及其自定义规则？历史处理任务不会被删除。')) return
    await api.deleteProject(projectId); saveProjectId(''); setProjectId(''); setEditing(false); setConfig({ ...defaultProcessingConfig }); saveProcessingConfig({ ...defaultProcessingConfig }); await loadProjects(); setMessage('项目已删除。')
  }

  async function addRule() {
    if (!ruleDraft.name.trim() || !ruleDraft.pattern.trim()) { setMessage('请填写规则名称和匹配内容。'); return }
    const created = await api.createRule({ ...ruleDraft, project_id: projectId || null, enabled: true })
    setRules(items => [...items, created]); setRuleDraft({ name: '', kind: 'keyword', pattern: '', entity_type: 'CUSTOM', case_sensitive: false }); setMessage('自定义规则已保存。')
  }

  async function toggleRule(rule: CustomRule) {
    const updated = await api.updateRule(rule.id, { enabled: !rule.enabled })
    setRules(items => items.map(item => item.id === rule.id ? updated : item))
  }

  async function removeRule(id: string) {
    await api.deleteRule(id); setRules(items => items.filter(item => item.id !== id))
  }

  async function previewInstruction() {
    if (!config.instruction?.trim()) return
    setBusy(true)
    try { setParseResult(await api.parseInstruction(config.instruction, config.use_llm)) } catch (error) { setMessage(error instanceof Error ? error.message : '解析失败') } finally { setBusy(false) }
  }

  async function lookupKnowledge() {
    if (!knowledgeTerm.trim()) return
    setKnowledgeBusy(true); setMessage('')
    try { setKnowledgeResult(await api.knowledgeLookup(knowledgeTerm.trim(), knowledgeType)) } catch (error) { setMessage(error instanceof Error ? error.message : '知识图谱查询失败') } finally { setKnowledgeBusy(false) }
  }

  const strengthLabel = ['低：保留更多结构', '中：平衡保护与可读性', '高：最大限度隐藏'][config.privacy_strength - 1]
  const enabledCount = useMemo(() => config.enabled_entity_types.length, [config.enabled_entity_types])
  const rulePreview = useMemo(() => {
    if (!ruleDraft.pattern) return { valid: true, matches: [] as Array<{ text: string; start: number; end: number }>, message: '输入规则后即时检查语法与命中位置。' }
    try {
      const source = ruleDraft.kind === 'keyword' ? ruleDraft.pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : ruleDraft.pattern
      const matcher = new RegExp(source, ruleDraft.case_sensitive ? 'gu' : 'giu')
      const matches = Array.from(ruleTestText.matchAll(matcher)).slice(0, 20).map(match => ({ text: match[0], start: match.index, end: match.index + match[0].length }))
      return { valid: true, matches, message: matches.length ? `\u547d\u4e2d ${matches.length} \u5904\uff08\u6700\u591a\u663e\u793a 20 \u5904\uff09` : '\u8bed\u6cd5\u6709\u6548\uff0c\u4f46\u6d4b\u8bd5\u6587\u672c\u4e2d\u6ca1\u6709\u547d\u4e2d\u3002' }
    } catch (error) {
      return { valid: false, matches: [] as Array<{ text: string; start: number; end: number }>, message: error instanceof Error ? error.message : '规则语法无效。' }
    }
  }, [ruleDraft.pattern, ruleDraft.kind, ruleDraft.case_sensitive, ruleTestText])
  const strategyPreview = useMemo(() => {
    if (config.strategy === 'mask') return '【PERSON-001】就读于【ORG-001】，联系电话为【PHONE-001】。'
    if (config.strategy === 'pseudonymize') {
      const epsilons = [0.25, 1, 4]
      return `\u6307\u6570\u673a\u5236\u4ece\u540c\u7c7b\u5019\u9009\u8bcd\u4e2d\u968f\u673a\u91c7\u6837\uff08\u03b5=${epsilons[config.privacy_strength - 1]}\uff09\uff1b\u4f8b\u5982\u5c06\u59d3\u540d\u66ff\u6362\u4e3a\u201c\u6797\u6e05\u201d\u3001\u673a\u6784\u66ff\u6362\u4e3a\u201c\u534e\u4e1c\u67d0\u9ad8\u6821\u201d\uff0c\u6bcf\u6b21\u4efb\u52a1\u91cd\u65b0\u91c7\u6837\u3002`
    }
    const organizations = (knowledgeResult?.levels?.length ?? 0) >= 3 ? knowledgeResult!.levels : ['北京高校', '高等院校', '教育机构']
    return `某位受访者就读于${organizations[config.privacy_strength - 1]}，联系电话泛化为联系方式。`
  }, [config.strategy, config.privacy_strength, knowledgeResult])

  return <div className="page project-page">
    {message && <div className="notice-banner">{message}</div>}

    {!editing ? <section className="panel project-list-panel">
      <div className="section-heading"><div><span>PROJECTS</span><h2>所有项目</h2></div></div>
      <div className="project-list">
        {projects.map(project => <div className={project.id === projectId ? 'project-list-item active' : 'project-list-item'} key={project.id}>
          <button className="project-list-main" onClick={() => navigate(`/batch?project=${project.id}`)}><FolderCog size={16}/><div><strong>{project.name}</strong><small>{project.description || '暂无说明'} · {new Date(project.updated_at).toLocaleString()}</small></div></button>
          <button className="btn ghost" onClick={() => selectProject(project.id)}><ExternalLink size={13}/>编辑</button>
          <button className="icon-danger" onClick={async () => { if (!window.confirm(`删除项目"${project.name}"及其自定义规则？`)) return; await api.deleteProject(project.id); if (projectId === project.id) { saveProjectId(''); setProjectId(''); setConfig({...defaultProcessingConfig}); saveProcessingConfig({...defaultProcessingConfig}); } await refresh(''); }} title="删除项目"><Trash2 size={14}/></button>
        </div>)}
        <button className="project-list-item new-project" onClick={async () => { const created = await api.createProject({ name: '新项目', description: '', config: defaultProcessingConfig }); setProjects(prev => [...prev, created]); await selectProject(created.id); }}>
          <Plus size={16}/><span>新建项目</span>
        </button>
      </div>
    </section> : <>
      <a className="back-link" onClick={() => { setEditing(false); setProjectId(''); }} style={{ marginBottom: '16px', display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}><ArrowLeft size={15}/>返回项目列表</a>

    <div className="project-layout">
      <section className="panel project-editor"><div className="section-heading"><div><span>PROJECT CONFIG</span><h2>项目脱敏设置</h2></div></div>
        <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr 1fr' }}><label><span>项目名称</span><input value={name} onChange={event => setName(event.target.value)} maxLength={80}/></label><label><span>语言范围</span><select value={config.language} onChange={event => updateConfig({ language: event.target.value as ProcessingConfig['language'] })}><option value="auto">自动判断</option><option value="zh">中文</option><option value="en">英文</option><option value="mixed">中英混合</option><option value="multilingual">多语种</option></select></label><label><span>默认脱敏策略</span><select value={config.strategy} onChange={event => updateConfig({ strategy: event.target.value as ProcessingConfig['strategy'] })}><option value="mask">一致性掩码</option><option value="pseudonymize">语义伪名替换</option><option value="generalize">知识层级泛化</option></select></label><label><span>风险模式</span><select value={config.risk_level} onChange={event => updateConfig({ risk_level: event.target.value as ProcessingConfig['risk_level'] })}><option value="standard">标准</option><option value="strict">严格</option></select></label></div>
        <label style={{ display: 'block', marginTop: '11px' }}><span>项目说明</span><textarea value={description} onChange={event => setDescription(event.target.value)} rows={2}/></label>
        <div className="deployment-choice"><button className={config.deployment_mode === 'local' ? 'active' : ''} onClick={() => updateConfig({ deployment_mode: 'local' })}><Server/><span><b>本地 / 私有服务器</b><small>原文不离开受控环境</small></span></button><button className={config.deployment_mode === 'cloud' ? 'active' : ''} onClick={() => updateConfig({ deployment_mode: 'cloud' })}><Cloud/><span><b>云端兼容接口</b><small>由管理员配置模型服务</small></span></button></div>
        <div style={{ marginTop: '14px' }}><span style={{ display: 'block', fontSize: '10px', color: '#7d8899', marginBottom: '6px' }}>实体类型</span><div className="entity-check-grid">{allEntityTypes.map(type => <label key={type} className={config.enabled_entity_types.includes(type) ? 'checked' : ''}><input type="checkbox" checked={config.enabled_entity_types.includes(type)} onChange={() => { const exists=config.enabled_entity_types.includes(type); const next=exists?config.enabled_entity_types.filter(item=>item!==type):[...config.enabled_entity_types,type]; if(next.length)updateConfig({enabled_entity_types:next}) }}/><i className={`legend-dot entity-${type}`}/><span>{labels[type]}</span></label>)}</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '13px', marginTop: '14px' }}>
          <div className="strength-control" style={{ marginTop: 0 }}><div><span>保护强度</span><b>{strengthLabel}</b></div><input type="range" min="1" max="3" value={config.privacy_strength} onChange={event => updateConfig({ privacy_strength: Number(event.target.value) })}/><div className="range-labels"><span>低</span><span>中</span><span>高</span></div></div>
          <div className="strategy-live-preview"><span>实时效果示例</span><p>{strategyPreview}</p><small>示例会随脱敏策略与保护强度即时变化；正式处理前仍建议用真实代表样本预检。</small></div>
        </div>
        <div className="toggle-row"><label><input type="checkbox" checked={config.use_llm} onChange={event => updateConfig({ use_llm: event.target.checked })}/><span>启用 14B 语义核验与补漏</span></label><label><input type="checkbox" checked={config.use_policies} onChange={event => updateConfig({ use_policies: event.target.checked })}/><span>叠加全局实体策略</span></label></div><small className="scope-summary">已启用 {enabledCount} / {allEntityTypes.length} 类实体</small>
      </section>
    </div>

    <section className="panel knowledge-explorer"><div className="section-heading"><div><span>KNOWLEDGE GRAPH</span><h2>知识图谱层级查询</h2></div></div>
      <div className="knowledge-query"><input value={knowledgeTerm} onChange={event => setKnowledgeTerm(event.target.value)} placeholder="输入机构、地点或人物实体"/><select value={knowledgeType} onChange={event => setKnowledgeType(event.target.value as EntityType)}>{allEntityTypes.map(type => <option value={type} key={type}>{labels[type]}</option>)}</select><button className="btn primary" onClick={lookupKnowledge} disabled={knowledgeBusy || !knowledgeTerm.trim()}><Search size={16}/>{knowledgeBusy ? '查询中' : '查询层级'}</button></div>
      <div className="knowledge-status"><span className={knowledgeStatus?.enabled ? 'online' : 'fallback'}>{knowledgeStatus?.enabled ? '远程图谱已配置' : '本地回退模式'}</span><p>{String(knowledgeStatus?.detail || '远程服务不可用时自动使用内置层级与规则推断，不阻断脱敏。')}</p></div>
      {knowledgeResult ? <div className="knowledge-result"><div className="knowledge-result-head"><strong>{knowledgeResult.term}</strong><span>{knowledgeResult.provider} · {knowledgeResult.source}</span></div><ol>{knowledgeResult.levels.slice(0, 3).map((level, index) => <li className={config.privacy_strength === index + 1 ? 'active' : ''} key={`${level}-${index}`}><span>级别 {index + 1}</span><strong>{level}</strong>{config.privacy_strength === index + 1 && <small>当前强度采用</small>}</li>)}</ol><p>{knowledgeResult.detail}</p></div> : <div className="empty-inline">输入实体并查询，可验证低、中、高三档泛化层级；查询结果会同步用于上方实时效果示例。</div>}
    </section>

    <section className="panel instruction-panel"><div className="section-heading"><div><span>NATURAL LANGUAGE REQUIREMENT</span><h2>自然语言策略输入</h2></div></div><div className="instruction-layout"><label><span>需求描述</span><textarea value={config.instruction || ''} onChange={event => updateConfig({ instruction: event.target.value || null })} placeholder="例如：保留所有北京地名，但隐藏上海相关地名；姓名使用伪名，其他信息严格脱敏。"/><div className="instruction-actions"><button className="btn ghost" onClick={previewInstruction} disabled={busy || !config.instruction?.trim()}><Sparkles size={16}/>解析预览</button><span>运行时会与上方菜单配置合并，显式需求优先。</span></div></label><div className="parse-preview"><strong>解析结果预览</strong>{parseResult ? <pre>{JSON.stringify(parseResult, null, 2)}</pre> : <p>输入需求后点击“解析预览”，可检查保留词、强制脱敏词、实体范围和策略。</p>}</div></div></section>

    <section className="panel rules-panel"><div className="section-heading"><div><span>CUSTOM RULE CRUD</span><h2>自定义关键词与正则</h2></div></div><div className="rule-builder"><input placeholder="规则名称" value={ruleDraft.name} onChange={event => setRuleDraft({...ruleDraft,name:event.target.value})}/><select value={ruleDraft.kind} onChange={event => setRuleDraft({...ruleDraft,kind:event.target.value as 'keyword'|'regex'})}><option value="keyword">关键词</option><option value="regex">正则表达式</option></select><input className="rule-pattern" placeholder={ruleDraft.kind === 'regex' ? '例如：ACCT-\\d{8}' : '例如：内部项目代号'} value={ruleDraft.pattern} onChange={event => setRuleDraft({...ruleDraft,pattern:event.target.value})}/><select value={ruleDraft.entity_type} onChange={event => setRuleDraft({...ruleDraft,entity_type:event.target.value as EntityType})}>{allEntityTypes.map(type=><option key={type} value={type}>{labels[type]}</option>)}</select><label className="case-check"><input type="checkbox" checked={ruleDraft.case_sensitive} onChange={event=>setRuleDraft({...ruleDraft,case_sensitive:event.target.checked})}/>区分大小写</label><button className="btn primary" onClick={addRule}><Plus/>添加</button></div>
      <div className="regex-tester"><label><span>规则测试文本</span><textarea value={ruleTestText} onChange={event => setRuleTestText(event.target.value)} rows={3}/></label><div className={rulePreview.valid ? 'regex-test-result valid' : 'regex-test-result invalid'}><strong>{rulePreview.valid ? '规则可用' : '语法错误'}</strong><span>{rulePreview.message}</span>{rulePreview.matches.length > 0 && <div className="regex-match-list">{rulePreview.matches.map((match, index) => <code key={`${match.start}-${match.end}-${index}`}>{match.text}{' \u00b7 '}{match.start}:{match.end}</code>)}</div>}<small>前端即时预览用于调试；保存时后端会再用 Python 正则进行权威校验。</small></div></div>
      {rules.length ? <div className="rule-list">{rules.map(rule => <div className={rule.enabled ? 'rule-item' : 'rule-item disabled'} key={rule.id}><button className="rule-switch" onClick={() => toggleRule(rule)}><span/><small>{rule.enabled?'启用':'停用'}</small></button><div><strong>{rule.name}</strong><code>{rule.pattern}</code></div><span>{rule.kind === 'regex' ? '正则' : '关键词'} · {labels[rule.entity_type]}</span><button className="icon-danger" onClick={() => removeRule(rule.id)}><Trash2/></button></div>)}</div> : <div className="empty-inline">尚未添加持久化规则。工作台内的临时配置仍可单次使用。</div>}
    </section>
      <div className="header-actions project-actions" style={{ marginTop: '24px' }}><button className="btn primary" onClick={saveProject} disabled={busy}><Save size={16}/>保存当前项目</button></div>
    </>}
  </div>
}
