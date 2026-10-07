import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Check, Pencil, Plus, Trash2, RefreshCw } from 'lucide-react'
import { api } from '../api'
import RuleEditor, { emptyRule, type RuleDraft } from '../components/RuleEditor'
import { ConfirmDialog, EmptyState, Segmented, Spinner, Toggle } from '../components/ui'
import { loadProcessingConfig, saveProcessingConfig } from '../configStore'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { ENTITY_LABEL, STRATEGY_HINT, STRATEGY_SHORT, entityStyle } from '../lib/entities'
import { RULE_TEMPLATES, type RuleTemplate } from '../lib/ruleTemplates'
import { allEntityTypes, type CustomRule, type EntityType, type Strategy } from '../types'
import './Rules.css'

type Tab = 'rules' | 'templates' | 'policies'

/** 与后端 recognizers.PATTERNS 对应的内置规则说明 */
const BUILTIN_RULES: Array<{ name: string; type: EntityType; description: string; check: string }> = [
  { name: '手机号', type: 'PHONE', description: '中国大陆 11 位手机号，支持 +86 前缀', check: '号段校验' },
  { name: '国际电话', type: 'PHONE', description: '区号加号码，支持 +1、+44、+61、+81、+82、+65 等格式', check: '—' },
  { name: '邮箱', type: 'EMAIL', description: '标准电子邮件地址', check: '—' },
  { name: '身份证号', type: 'ID_CARD', description: '18 位居民身份证号，末位可为 X', check: '校验码' },
  { name: '银行卡号', type: 'BANK_CARD', description: '12 到 19 位卡号，允许空格或短横线分隔', check: 'Luhn 校验' },
  { name: '护照号', type: 'PASSPORT', description: 'E、G、D、S、P 开头加 8 位数字，以及常见的外国护照格式', check: '—' },
]
const TABS: Array<[Tab, string]> = [['rules', '自定义规则'], ['templates', '规则模板'], ['policies', '按类型设置方式']]

export default function Rules() {
  const [params, setParams] = useSearchParams()
  const tab: Tab = (['rules', 'templates', 'policies'] as Tab[]).includes(params.get('tab') as Tab) ? params.get('tab') as Tab : 'rules'
  const { projects, projectsLoaded, projectsError } = useApp()
  const toast = useToast()
  const [rules, setRules] = useState<CustomRule[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [scope, setScope] = useState<string>('all')
  const [editor, setEditor] = useState<{ key: number; draft: RuleDraft; sample?: string } | null>(null)
  const [deleting, setDeleting] = useState<CustomRule | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [global, ...perProject] = await Promise.all([api.rules(), ...projects.map(project => api.rules(project.id))])
      const map = new Map<string, CustomRule>()
      for (const rule of [...global.items, ...perProject.flatMap(result => result.items)]) map.set(rule.id, rule)
      setRules([...map.values()].sort((a, b) => a.created_at.localeCompare(b.created_at)))
      setLoadError('')
    } catch (caught) {
      setLoadError(caught instanceof Error ? caught.message : '规则加载失败')
      setRules(current => current || [])
    }
  }, [projects])

  // 项目列表读不到（服务没连上）时也要尝试读取，失败了显示原因，而不是一直转圈
  useEffect(() => { if (projectsLoaded || projectsError) void load() }, [projectsLoaded, projectsError, load])

  const projectName = (id: string | null) => id ? projects.find(project => project.id === id)?.name || '已删除的项目' : '全部项目'
  const visible = useMemo(() => (rules || []).filter(rule => scope === 'all' || (scope === 'global' ? !rule.project_id : rule.project_id === scope)), [rules, scope])

  function openEditor(draft: RuleDraft, sample?: string) {
    setEditor({ key: Date.now(), draft, sample })
  }

  async function toggle(rule: CustomRule, enabled: boolean) {
    setRules(current => current?.map(item => item.id === rule.id ? { ...item, enabled } : item) || current)
    try { await api.updateRule(rule.id, { enabled }) }
    catch (caught) {
      setRules(current => current?.map(item => item.id === rule.id ? { ...item, enabled: !enabled } : item) || current)
      toast(caught instanceof Error ? caught.message : '没有保存', 'error')
    }
  }

  async function remove() {
    if (!deleting) return
    setDeleteBusy(true)
    try {
      await api.deleteRule(deleting.id)
      setRules(current => current?.filter(item => item.id !== deleting.id) || current)
      toast('规则已删除', 'success')
      setDeleting(null)
    } catch (caught) { toast(caught instanceof Error ? caught.message : '删除失败', 'error') }
    finally { setDeleteBusy(false) }
  }

  function addFromTemplate(template: RuleTemplate) {
    openEditor({ ...emptyRule(), name: template.name, kind: template.kind, pattern: template.pattern, entity_type: template.entity_type }, template.sample)
  }

  return <div className="page rules">
    <header className="page-head">
      <div>
        <h1 className="page-title">规则库</h1>
        <p className="page-desc">内置规则识别手机号、邮箱、证件号、银行卡等常见标识。单位内部的编号、项目代号等，可以在这里补充成自定义规则。</p>
      </div>
      {tab !== 'policies' && <div className="page-actions"><button type="button" className="btn btn-primary" onClick={() => openEditor(emptyRule())}><Plus size={15}/>新建规则</button></div>}
    </header>

    <div className="tabs" role="tablist" aria-label="规则库">
      {TABS.map(([key, label]) => <button type="button" role="tab" key={key} aria-selected={tab === key} className={tab === key ? 'is-active' : ''} onClick={() => setParams(key === 'rules' ? {} : { tab: key })}>
        {label}{key === 'rules' && rules ? <span className="num">{rules.length}</span> : null}
      </button>)}
    </div>

    {tab === 'rules' && <>
      {rules && rules.length > 0 && projects.length > 0 && <div className="rules-filter">
        <span>适用范围</span>
        <select className="select input-sm" value={scope} onChange={event => setScope(event.target.value)} aria-label="按适用范围筛选">
          <option value="all">全部规则</option>
          <option value="global">对全部项目生效</option>
          {projects.map(project => <option key={project.id} value={project.id}>项目：{project.name}</option>)}
        </select>
      </div>}
      {!rules ? <div className="table-wrap"><div className="rules-loading"><Spinner/></div></div>
        : loadError && !rules.length ? <div className="panel"><EmptyState title="暂时读不到规则" illustration="document" action={<button type="button" className="btn btn-primary" onClick={() => void load()}><RefreshCw size={15}/>重试</button>}>{loadError}。确认后端窗口仍在运行后重试。</EmptyState></div>
        : !rules.length ? <div className="panel"><EmptyState title="还没有自定义规则" illustration="document" action={<>
            <button type="button" className="btn btn-primary" onClick={() => openEditor(emptyRule())}><Plus size={15}/>新建规则</button>
            <button type="button" className="btn" onClick={() => setParams({ tab: 'templates' })}>从模板添加</button>
          </>}>比如项目代号“星舟计划”、学号、工号。添加后，工作台和批量处理都会自动使用。</EmptyState></div>
        : <div className="table-wrap">
          <table className="table rules-table">
            <thead><tr><th>名称</th><th>匹配内容</th><th>处理为</th><th>适用范围</th><th>启用</th><th aria-label="操作"/></tr></thead>
            <tbody>
              {visible.map(rule => <tr key={rule.id} className={rule.enabled ? '' : 'is-off'}>
                <td className="rules-name">{rule.name}</td>
                <td><span className="rules-kind">{rule.kind === 'keyword' ? '固定词' : '正则'}</span><code className="rules-pattern" title={rule.pattern}>{rule.pattern}</code>{rule.case_sensitive && <span className="rules-case">区分大小写</span>}</td>
                <td><span className="legend-item" style={entityStyle(rule.entity_type)}><span className="dot"/>{ENTITY_LABEL[rule.entity_type]}</span></td>
                <td className="rules-scope">{projectName(rule.project_id)}</td>
                <td><Toggle checked={rule.enabled} onChange={checked => void toggle(rule, checked)} label={<span className="sr-only">启用 {rule.name}</span>}/></td>
                <td className="rules-actions">
                  <button type="button" className="icon-btn" aria-label={`编辑 ${rule.name}`} title="编辑" onClick={() => openEditor({ id: rule.id, name: rule.name, kind: rule.kind, pattern: rule.pattern, entity_type: rule.entity_type, case_sensitive: rule.case_sensitive, enabled: rule.enabled, project_id: rule.project_id })}><Pencil size={15}/></button>
                  <button type="button" className="icon-btn" aria-label={`删除 ${rule.name}`} title="删除" onClick={() => setDeleting(rule)}><Trash2 size={15}/></button>
                </td>
              </tr>)}
              {!visible.length && <tr><td colSpan={6} className="rules-none">这个范围下没有规则</td></tr>}
            </tbody>
          </table>
        </div>}
      <section className="section builtin">
        <div className="section-head"><h2 className="section-title">内置规则</h2><span className="section-note">始终启用，与自定义规则一起在规则层运行</span></div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>处理为</th><th>识别内容</th><th>校验</th></tr></thead>
            <tbody>
              {BUILTIN_RULES.map(rule => <tr key={rule.name}>
                <td><span className="legend-item" style={entityStyle(rule.type)}><span className="dot"/>{rule.name}</span></td>
                <td className="builtin-desc">{rule.description}</td>
                <td className="builtin-check">{rule.check}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </section>
    </>}

    {tab === 'templates' && <ul className="templates">
      {RULE_TEMPLATES.map(template => {
        const added = rules?.some(rule => rule.pattern === template.pattern)
        return <li key={template.key} className="template">
          <div className="template-main">
            <div className="template-title"><h3>{template.name}</h3><span className="legend-item" style={entityStyle(template.entity_type)}><span className="dot"/>{ENTITY_LABEL[template.entity_type]}</span></div>
            <p>{template.description}</p>
            <p className="template-sample">示例：{template.sample}</p>
          </div>
          {added ? <span className="chip chip-ok"><Check size={13}/>已添加</span> : <button type="button" className="btn btn-sm" onClick={() => addFromTemplate(template)}><Plus size={14}/>添加</button>}
        </li>
      })}
    </ul>}

    {tab === 'policies' && <PolicyTable/>}

    {editor && <RuleEditor key={editor.key} open initial={editor.draft} sample={editor.sample} projects={projects} onClose={() => setEditor(null)}
      onSaved={rule => { setEditor(null); toast(editor.draft.id ? '规则已更新' : `已添加规则「${rule.name}」`, 'success'); void load(); if (tab === 'templates') setParams({}) }}/>}
    <ConfirmDialog open={Boolean(deleting)} title="删除这条规则？" confirmLabel="删除" busy={deleteBusy} onClose={() => setDeleting(null)} onConfirm={() => void remove()}
      description={deleting ? `「${deleting.name}」删除后，之后的识别不再使用它。已经处理过的任务不受影响。` : ''}/>
  </div>
}

function PolicyTable() {
  const toast = useToast()
  const [policies, setPolicies] = useState<Record<string, Strategy> | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [enabled, setEnabled] = useState(() => loadProcessingConfig().use_policies)

  useEffect(() => {
    api.policies().then(data => setPolicies(data.policies)).catch(() => setPolicies({}))
    const onConfig = () => setEnabled(loadProcessingConfig().use_policies)
    window.addEventListener('privshield-config', onConfig)
    return () => window.removeEventListener('privshield-config', onConfig)
  }, [])

  async function change(type: string, strategy: Strategy) {
    setSaving(type)
    try {
      const data = await api.savePolicies({ [type]: strategy })
      setPolicies(data.policies)
    } catch (caught) { toast(caught instanceof Error ? caught.message : '没有保存', 'error') }
    finally { setSaving(null) }
  }

  function setUse(value: boolean) {
    saveProcessingConfig({ ...loadProcessingConfig(), use_policies: value })
    setEnabled(value)
    toast(value ? '当前方案已改为按类型使用不同方式' : '当前方案已改回统一的脱敏方式', 'success')
  }

  return <div className="policies">
    <div className="policies-switch panel panel-pad">
      <Toggle checked={enabled} onChange={setUse} label="在当前方案中按类型使用不同方式" description={<>开启后，工作台和批量处理按下表为每类实体选择方式；关闭则统一使用方案里选的方式。也可以在<Link to="/workbench">工作台</Link>的“更多设置”里切换。</>}/>
    </div>
    {!policies ? <div className="rules-loading"><Spinner/></div> : <div className="table-wrap">
      <table className="table policies-table">
        <thead><tr><th>实体类型</th><th>脱敏方式</th><th>效果</th></tr></thead>
        <tbody>
          {allEntityTypes.map(type => {
            const value = (policies[type] || 'mask') as Strategy
            return <tr key={type}>
              <td><span className="legend-item policies-type" style={entityStyle(type)}><span className="dot"/>{ENTITY_LABEL[type]}</span></td>
              <td><Segmented label={`${ENTITY_LABEL[type]}的脱敏方式`} size="sm" value={value} disabled={saving === type} onChange={strategy => void change(type, strategy)}
                options={(['mask', 'pseudonymize', 'generalize'] as Strategy[]).map(strategy => ({ value: strategy, label: STRATEGY_SHORT[strategy] }))}/></td>
              <td className="policies-hint">{STRATEGY_HINT[value]}</td>
            </tr>
          })}
        </tbody>
      </table>
    </div>}
  </div>
}
