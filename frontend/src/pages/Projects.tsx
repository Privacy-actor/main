import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Check, Layers, Pencil, Plus, RefreshCw, ScanText, Trash2 } from 'lucide-react'
import { api } from '../api'
import ConfigPanel from '../components/ConfigPanel'
import RuleEditor, { emptyRule, type RuleDraft } from '../components/RuleEditor'
import { ConfirmDialog, Dialog, EmptyState, Segmented, Spinner, Toggle } from '../components/ui'
import { loadProcessingConfig, saveProcessingConfig } from '../configStore'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { ENTITY_LABEL, STRATEGY_LABEL, STRENGTH_LABEL, entityStyle } from '../lib/entities'
import { formatTime, shortId } from '../lib/format'
import { allEntityTypes, defaultProcessingConfig, normalizeConfig, type CustomRule, type HistoryItem, type ProcessingConfig, type Project } from '../types'
import './Projects.css'

export function describePlan(config: ProcessingConfig) {
  const parts = [config.use_policies ? '按类型分别设置方式' : `${STRATEGY_LABEL[config.strategy]}${config.strategy === 'mask' ? '' : `，力度${STRENGTH_LABEL[config.privacy_strength]}`}`]
  parts.push(config.enabled_entity_types.length === allEntityTypes.length ? '识别全部类型' : `识别 ${config.enabled_entity_types.length} 类实体`)
  parts.push(config.use_llm ? '大模型核查开启' : '不使用大模型')
  parts.push(config.deployment_mode === 'cloud' ? '云端部署' : '本地部署')
  return parts.join('；')
}

export default function Projects() {
  const { projectId } = useParams()
  return projectId ? <ProjectEditor key={projectId} projectId={projectId}/> : <ProjectList/>
}

function ProjectList() {
  const { projects, projectsLoaded, projectsError, currentProjectId, selectProject, refreshProjects } = useApp()
  const toast = useToast()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [creating, setCreating] = useState(params.get('new') === '1')
  const [deleting, setDeleting] = useState<Project | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [ruleCounts, setRuleCounts] = useState<Record<string, number>>({})

  useEffect(() => { if (params.get('new') === '1') setCreating(true) }, [params])

  useEffect(() => {
    let cancelled = false
    Promise.all(projects.map(project => api.rules(project.id).then(result => [project.id, result.items.filter(rule => rule.project_id === project.id).length] as const).catch(() => [project.id, 0] as const)))
      .then(entries => { if (!cancelled) setRuleCounts(Object.fromEntries(entries)) })
    return () => { cancelled = true }
  }, [projects])

  function closeCreate() {
    setCreating(false)
    if (params.get('new')) setParams({}, { replace: true })
  }

  async function remove() {
    if (!deleting) return
    setDeleteBusy(true)
    try {
      await api.deleteProject(deleting.id)
      if (currentProjectId === deleting.id) selectProject('', { applyConfig: false })
      await refreshProjects()
      toast(`已删除项目「${deleting.name}」`, 'success')
      setDeleting(null)
    } catch (caught) { toast(caught instanceof Error ? caught.message : '删除失败', 'error') }
    finally { setDeleteBusy(false) }
  }

  return <div className="page projects">
    <header className="page-head">
      <div>
        <h1 className="page-title">项目</h1>
        <p className="page-desc">把一类材料的处理方案和自定义规则保存为项目。在侧栏切换项目后，工作台和批量处理都会套用它。</p>
      </div>
      <div className="page-actions"><button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={15}/>新建项目</button></div>
    </header>

    {!projectsLoaded && !projectsError ? <div className="projects-loading"><Spinner/></div>
      : projectsError && !projects.length ? <div className="panel"><EmptyState title="暂时读不到项目" illustration="folder" action={<button type="button" className="btn btn-primary" onClick={() => void refreshProjects()}><RefreshCw size={15}/>重试</button>}>
          处理服务没有响应，项目都还在。确认后端窗口仍在运行后重试。
        </EmptyState></div>
      : !projects.length ? <div className="panel"><EmptyState title="还没有项目" illustration="folder" action={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={15}/>新建项目</button>}>
          例如“访谈资料”用差分隐私替换保留语义，“客服工单”用掩码并加上工单号规则。
        </EmptyState></div>
      : <ul className="project-list">
        {projects.map(project => {
          const config = normalizeConfig(project.config)
          const current = project.id === currentProjectId
          return <li key={project.id} className={`project-row${current ? ' is-current' : ''}`}>
            <div className="project-main">
              <div className="project-title">
                <Link to={`/projects/${project.id}`}>{project.name}</Link>
                {current && <span className="chip chip-cobalt"><Check size={12}/>当前项目</span>}
              </div>
              {project.description && <p className="project-desc">{project.description}</p>}
              <p className="project-plan">{describePlan(config)}；自定义规则 {ruleCounts[project.id] ?? 0} 条</p>
            </div>
            <div className="project-side">
              <span className="muted num">更新于 {formatTime(project.updated_at)}</span>
              <div className="project-actions">
                {!current && <button type="button" className="btn btn-sm" onClick={() => { selectProject(project.id); toast(`已切换到「${project.name}」，工作台会使用它的方案`, 'success') }}>设为当前</button>}
                <Link className="btn btn-sm btn-quiet" to={`/projects/${project.id}`}><Pencil size={14}/>编辑</Link>
                <button type="button" className="icon-btn" aria-label={`删除 ${project.name}`} title="删除" onClick={() => setDeleting(project)}><Trash2 size={15}/></button>
              </div>
            </div>
          </li>
        })}
      </ul>}

    {creating && <CreateProjectDialog onClose={closeCreate} onCreated={project => { closeCreate(); navigate(`/projects/${project.id}`) }}/>}
    <ConfirmDialog open={Boolean(deleting)} title="删除这个项目？" confirmLabel="删除项目" busy={deleteBusy} onClose={() => setDeleting(null)} onConfirm={() => void remove()}
      description={deleting ? `「${deleting.name}」的方案和 ${ruleCounts[deleting.id] ?? 0} 条项目规则会一起删除，已经处理过的任务保留。` : ''}/>
  </div>
}

function CreateProjectDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (project: Project) => void }) {
  const { refreshProjects, selectProject } = useApp()
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [base, setBase] = useState<'current' | 'default'>('current')
  const [makeCurrent, setMakeCurrent] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function create() {
    if (!name.trim()) { setError('请填写项目名称'); return }
    setBusy(true); setError('')
    try {
      const config = base === 'current' ? loadProcessingConfig() : { ...defaultProcessingConfig }
      const project = await api.createProject({ name: name.trim(), description: description.trim(), config })
      await refreshProjects()
      if (makeCurrent) {
        selectProject(project.id, { applyConfig: false })
        saveProcessingConfig(normalizeConfig(project.config))
      }
      toast(`已创建项目「${project.name}」`, 'success')
      onCreated(project)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '创建失败')
    } finally { setBusy(false) }
  }

  return <Dialog open onClose={onClose} title="新建项目" description="项目保存一套处理方案和专属规则，适合反复处理同一类材料。" footer={<>
    {error && <span className="rule-error" role="alert">{error}</span>}
    <button type="button" className="btn" onClick={onClose}>取消</button>
    <button type="button" className="btn btn-primary" onClick={() => void create()} disabled={busy}>{busy && <Spinner size={14}/>}创建项目</button>
  </>}>
    <form className="project-form" onSubmit={event => { event.preventDefault(); void create() }}>
      <label className="field"><span className="field-label">名称</span><input className="input" value={name} maxLength={80} onChange={event => { setName(event.target.value); setError('') }} placeholder="例如：访谈资料、客服工单" data-autofocus/></label>
      <label className="field"><span className="field-label">说明（可选）</span><input className="input" value={description} maxLength={500} onChange={event => setDescription(event.target.value)} placeholder="这类材料的来源、用途"/></label>
      <div className="field">
        <span className="field-label">初始方案</span>
        <Segmented label="初始方案" value={base} onChange={setBase} options={[{ value: 'current', label: '使用当前方案' }, { value: 'default', label: '使用默认方案' }]}/>
      </div>
      <Toggle checked={makeCurrent} onChange={setMakeCurrent} label="创建后设为当前项目"/>
      <button type="submit" hidden/>
    </form>
  </Dialog>
}

function ProjectEditor({ projectId }: { projectId: string }) {
  const { projects, projectsLoaded, projectsError, refreshProjects, currentProjectId, selectProject } = useApp()
  const toast = useToast()
  const navigate = useNavigate()
  const project = projects.find(item => item.id === projectId) || null
  const [name, setName] = useState(project?.name || '')
  const [description, setDescription] = useState(project?.description || '')
  const [config, setConfig] = useState<ProcessingConfig>(() => normalizeConfig(project?.config))
  const [initialized, setInitialized] = useState(Boolean(project))
  const [saving, setSaving] = useState(false)
  const [rules, setRules] = useState<CustomRule[] | null>(null)
  const [globalRules, setGlobalRules] = useState(0)
  const [editor, setEditor] = useState<{ key: number; draft: RuleDraft } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deletingRule, setDeletingRule] = useState<CustomRule | null>(null)
  const [tasks, setTasks] = useState<HistoryItem[] | null>(null)
  const [taskTotal, setTaskTotal] = useState(0)

  useEffect(() => {
    api.history({ limit: 6, auditLimit: 1, projectId }).then(data => { setTasks(data.items); setTaskTotal(data.total ?? data.items.length) }).catch(() => setTasks([]))
  }, [projectId])

  useEffect(() => {
    if (initialized || !project) return
    setName(project.name); setDescription(project.description); setConfig(normalizeConfig(project.config)); setInitialized(true)
  }, [initialized, project])

  const loadRules = useCallback(async () => {
    try {
      const { items } = await api.rules(projectId)
      setRules(items.filter(rule => rule.project_id === projectId))
      setGlobalRules(items.filter(rule => !rule.project_id).length)
    } catch { setRules([]) }
  }, [projectId])
  useEffect(() => { void loadRules() }, [loadRules])

  const saved = useMemo(() => project ? { name: project.name, description: project.description, config: JSON.stringify(normalizeConfig(project.config)) } : null, [project])
  const dirty = Boolean(saved) && (name !== saved?.name || description !== saved?.description || JSON.stringify(config) !== saved?.config)
  const current = currentProjectId === projectId

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  // 有未保存的修改时，点侧栏、返回链接或浏览器后退都先问一句
  const leaving = useRef(false)
  const blocker = useBlocker(({ currentLocation, nextLocation }) => !leaving.current && dirty && currentLocation.pathname !== nextLocation.pathname)

  async function save() {
    if (!project || !name.trim()) { toast('请填写项目名称', 'error'); return false }
    setSaving(true)
    try {
      await api.updateProject(project.id, { name: name.trim(), description: description.trim(), config })
      await refreshProjects()
      setName(name.trim()); setDescription(description.trim())
      if (current) saveProcessingConfig(config)
      toast(current ? '已保存，工作台已改用新的方案' : '已保存', 'success')
      return true
    } catch (caught) { toast(caught instanceof Error ? caught.message : '保存失败', 'error'); return false }
    finally { setSaving(false) }
  }

  async function saveAndLeave() {
    if (await save()) blocker.proceed?.()
    else blocker.reset?.()
  }

  /** 切换为当前项目并带着它的方案去工作台或批量处理；有未保存的修改时先提醒 */
  function openWith(path: string) {
    if (!project) return
    if (dirty) { toast('先保存或放弃当前修改', 'error'); return }
    if (!current) selectProject(project.id)
    navigate(path)
  }

  function discard() {
    if (!project) return
    setName(project.name); setDescription(project.description); setConfig(normalizeConfig(project.config))
  }

  async function removeProject() {
    if (!project) return
    setDeleteBusy(true)
    try {
      await api.deleteProject(project.id)
      if (current) selectProject('', { applyConfig: false })
      await refreshProjects()
      toast(`已删除项目「${project.name}」`, 'success')
      leaving.current = true
      navigate('/projects')
    } catch (caught) { toast(caught instanceof Error ? caught.message : '删除失败', 'error'); setDeleteBusy(false) }
  }

  async function toggleRule(rule: CustomRule, enabled: boolean) {
    setRules(list => list?.map(item => item.id === rule.id ? { ...item, enabled } : item) || list)
    try { await api.updateRule(rule.id, { enabled }) }
    catch (caught) { toast(caught instanceof Error ? caught.message : '没有保存', 'error'); void loadRules() }
  }

  async function removeRule() {
    if (!deletingRule) return
    try {
      await api.deleteRule(deletingRule.id)
      setRules(list => list?.filter(item => item.id !== deletingRule.id) || list)
      toast('规则已删除', 'success')
    } catch (caught) { toast(caught instanceof Error ? caught.message : '删除失败', 'error') }
    finally { setDeletingRule(null) }
  }

  if (!projectsLoaded) return <div className="page">{projectsError
    ? <div className="panel"><EmptyState title="暂时读不到项目" illustration="folder" action={<button type="button" className="btn btn-primary" onClick={() => void refreshProjects()}><RefreshCw size={15}/>重试</button>}>处理服务没有响应。确认后端窗口仍在运行后重试。</EmptyState></div>
    : <div className="projects-loading"><Spinner/></div>}</div>
  if (!project) return <div className="page"><div className="panel"><EmptyState title="没有找到这个项目" illustration="folder" action={<Link className="btn" to="/projects">返回项目列表</Link>}>它可能已经被删除。</EmptyState></div></div>

  return <div className="page project-editor">
    <Link to="/projects" className="back-link"><ArrowLeft size={15}/>全部项目</Link>
    <header className="page-head">
      <div>
        <h1 className="page-title">{project.name}</h1>
        <p className="page-desc">{current ? '这是当前项目，工作台和批量处理正在使用它的方案和规则。' : '切换为当前项目后，工作台和批量处理会使用这里的方案和规则。'}</p>
      </div>
      <div className="page-actions">
        {current ? <span className="chip chip-cobalt"><Check size={12}/>当前项目</span> : <button type="button" className="btn" onClick={() => { selectProject(project.id); toast(`已切换到「${project.name}」`, 'success') }}>设为当前项目</button>}
        <button type="button" className="btn" onClick={() => openWith('/workbench')}><ScanText size={15}/>在工作台使用</button>
        <button type="button" className="btn" onClick={() => openWith('/batch')}><Layers size={15}/>批量处理</button>
        <button type="button" className="btn btn-danger" onClick={() => setDeleting(true)}><Trash2 size={15}/>删除</button>
      </div>
    </header>

    <div className="project-grid">
      <div className="project-settings">
        <section className="project-section">
          <h2 className="section-title">基本信息</h2>
          <div className="project-fields">
            <label className="field"><span className="field-label">名称</span><input className="input" value={name} maxLength={80} onChange={event => setName(event.target.value)}/></label>
            <label className="field"><span className="field-label">说明</span><input className="input" value={description} maxLength={500} onChange={event => setDescription(event.target.value)} placeholder="这类材料的来源、用途"/></label>
          </div>
        </section>
        <section className="project-section">
          <h2 className="section-title">处理方案</h2>
          <ConfigPanel value={config} onChange={setConfig} variant="project"/>
        </section>
      </div>

      <aside className="project-rules panel">
        <header className="panel-head">
          <div><h2 className="panel-title">项目规则</h2><p className="project-rules-note">只在这个项目中生效。另有 {globalRules} 条全局规则对所有项目生效，<Link to="/rules">在规则库查看</Link>。</p></div>
          <button type="button" className="btn btn-sm" onClick={() => setEditor({ key: Date.now(), draft: emptyRule(project.id) })}><Plus size={14}/>添加</button>
        </header>
        {!rules ? <div className="projects-loading"><Spinner/></div>
          : !rules.length ? <p className="project-rules-empty">还没有项目规则。可以把这类材料特有的代号、编号加进来，比如“星舟计划”或工单号格式。</p>
          : <ul className="project-rule-list">
            {rules.map(rule => <li key={rule.id} className={rule.enabled ? '' : 'is-off'}>
              <div className="project-rule-main">
                <strong>{rule.name}</strong>
                <span><span className="legend-item" style={entityStyle(rule.entity_type)}><span className="dot"/>{ENTITY_LABEL[rule.entity_type]}</span><code>{rule.pattern}</code></span>
              </div>
              <Toggle checked={rule.enabled} onChange={checked => void toggleRule(rule, checked)} label={<span className="sr-only">启用 {rule.name}</span>}/>
              <button type="button" className="icon-btn" aria-label={`编辑 ${rule.name}`} onClick={() => setEditor({ key: Date.now(), draft: { id: rule.id, name: rule.name, kind: rule.kind, pattern: rule.pattern, entity_type: rule.entity_type, case_sensitive: rule.case_sensitive, enabled: rule.enabled, project_id: rule.project_id } })}><Pencil size={14}/></button>
              <button type="button" className="icon-btn" aria-label={`删除 ${rule.name}`} onClick={() => setDeletingRule(rule)}><Trash2 size={14}/></button>
            </li>)}
          </ul>}
        <div className="project-tasks">
          <header className="panel-head"><h2 className="panel-title">用这个项目处理的任务</h2>{tasks && tasks.length > 0 && <span className="muted num">{taskTotal} 个</span>}</header>
          {!tasks ? <div className="projects-loading"><Spinner/></div>
            : !tasks.length ? <p className="project-rules-empty">还没有任务。设为当前项目后，在工作台或批量处理中识别的文本会出现在这里。</p>
            : <ul className="project-task-list">
              {tasks.slice(0, 6).map(item => <li key={item.id}>
                <Link to={`/workbench?task=${encodeURIComponent(item.id)}`}>{item.preview || '（空白）'}</Link>
                <span className="muted num">{formatTime(item.created_at)}，任务 {shortId(item.id)}，{item.entity_count} 处实体{item.pending ? `，${item.pending} 处待确认` : ''}</span>
              </li>)}
            </ul>}
          {taskTotal > 6 && <Link className="project-tasks-more" to="/history">在任务记录中查看全部</Link>}
        </div>
      </aside>
    </div>

    {dirty && <div className="save-bar" role="region" aria-label="未保存的修改">
      <span>有未保存的修改</span>
      <button type="button" className="btn btn-quiet" onClick={discard} disabled={saving}>放弃修改</button>
      <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving}>{saving && <Spinner size={14}/>}保存项目</button>
    </div>}

    {editor && <RuleEditor key={editor.key} open lockScope initial={editor.draft} projects={projects} onClose={() => setEditor(null)}
      onSaved={rule => { setEditor(null); toast(editor.draft.id ? '规则已更新' : `已添加规则「${rule.name}」`, 'success'); void loadRules() }}/>}
    <ConfirmDialog open={deleting} title="删除这个项目？" confirmLabel="删除项目" busy={deleteBusy} onClose={() => setDeleting(false)} onConfirm={() => void removeProject()}
      description={`「${project.name}」的方案和 ${rules?.length || 0} 条项目规则会一起删除，已经处理过的任务保留。`}/>
    <Dialog open={blocker.state === 'blocked'} onClose={() => blocker.reset?.()} title="离开前保存修改？" size="sm"
      description={`「${project.name}」的方案有修改还没保存。`} footer={<>
        <button type="button" className="btn btn-quiet" onClick={() => blocker.reset?.()}>继续编辑</button>
        <button type="button" className="btn" onClick={() => { discard(); blocker.proceed?.() }}>不保存</button>
        <button type="button" className="btn btn-primary" disabled={saving} onClick={() => void saveAndLeave()}>{saving && <Spinner size={14}/>}保存并离开</button>
      </>}>
      <p className="muted">不保存的话，这次的修改会丢掉。</p>
    </Dialog>
    <ConfirmDialog open={Boolean(deletingRule)} title="删除这条规则？" confirmLabel="删除" onClose={() => setDeletingRule(null)} onConfirm={() => void removeRule()}
      description={deletingRule ? `「${deletingRule.name}」删除后，这个项目之后的识别不再使用它。` : ''}/>
  </div>
}
