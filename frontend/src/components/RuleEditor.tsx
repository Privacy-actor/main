import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { ENTITY_LABEL, entityStyle } from '../lib/entities'
import { allEntityTypes, type CustomRule, type EntityType, type Project } from '../types'
import { Dialog, Segmented, Spinner } from './ui'
import './RuleEditor.css'

export interface RuleDraft {
  id?: string
  name: string
  kind: 'keyword' | 'regex'
  pattern: string
  entity_type: EntityType
  case_sensitive: boolean
  enabled: boolean
  project_id: string | null
}

export const emptyRule = (projectId: string | null = null): RuleDraft => ({ name: '', kind: 'keyword', pattern: '', entity_type: 'CUSTOM', case_sensitive: false, enabled: true, project_id: projectId })

interface RuleEditorProps {
  open: boolean
  initial: RuleDraft
  sample?: string
  projects: Project[]
  /** 固定在某个项目里新建时隐藏适用范围选择 */
  lockScope?: boolean
  onClose: () => void
  onSaved: (rule: CustomRule) => void
}

type Match = { start: number; end: number; text: string }

function Highlighted({ text, matches }: { text: string; matches: Match[] }) {
  const characters = Array.from(text)
  const parts = []
  let cursor = 0
  for (const match of matches) {
    if (match.start < cursor) continue
    if (match.start > cursor) parts.push(<span key={`t${cursor}`}>{characters.slice(cursor, match.start).join('')}</span>)
    parts.push(<mark key={`m${match.start}`}>{characters.slice(match.start, match.end).join('')}</mark>)
    cursor = match.end
  }
  if (cursor < characters.length) parts.push(<span key="tail">{characters.slice(cursor).join('')}</span>)
  return <p className="rule-preview">{parts}</p>
}

/** 新建或编辑一条自定义规则，右侧用后端的正则引擎实时试匹配。 */
export default function RuleEditor({ open, initial, sample, projects, lockScope, onClose, onSaved }: RuleEditorProps) {
  const [draft, setDraft] = useState<RuleDraft>(initial)
  const [testText, setTestText] = useState(sample || '')
  const [matches, setMatches] = useState<Match[] | null>(null)
  const [testError, setTestError] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = <K extends keyof RuleDraft>(key: K, value: RuleDraft[K]) => { setDraft(current => ({ ...current, [key]: value })); setError('') }

  // 调用方每次打开时用新的 key 重新挂载，这里只在挂载时读取 initial 与 sample
  useEffect(() => {
    if (!open || !draft.pattern.trim() || !testText.trim()) { setMatches(null); setTestError(''); return }
    let cancelled = false
    setTesting(true)
    const timer = window.setTimeout(async () => {
      try {
        const data = await api.testRule({ kind: draft.kind, pattern: draft.pattern, text: testText, case_sensitive: draft.case_sensitive })
        if (!cancelled) {
          if (data.valid === false) { setMatches(null); setTestError(data.error || '正则表达式无效') }
          else { setMatches(data.matches); setTestError('') }
        }
      } catch (caught) {
        if (!cancelled) { setMatches(null); setTestError(caught instanceof Error ? caught.message : '无法测试') }
      } finally { if (!cancelled) setTesting(false) }
    }, 260)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [open, draft.kind, draft.pattern, draft.case_sensitive, testText])

  const invalid = useMemo(() => {
    if (!draft.name.trim()) return '请填写规则名称'
    if (!draft.pattern.trim()) return draft.kind === 'keyword' ? '请填写要隐去的词' : '请填写正则表达式'
    if (draft.kind === 'regex' && testError) return testError
    return ''
  }, [draft, testError])

  async function save() {
    if (invalid) { setError(invalid); return }
    setSaving(true); setError('')
    try {
      const payload = { name: draft.name.trim(), kind: draft.kind, pattern: draft.pattern, entity_type: draft.entity_type, case_sensitive: draft.case_sensitive, enabled: draft.enabled }
      const saved = draft.id ? await api.updateRule(draft.id, payload) : await api.createRule({ ...payload, project_id: draft.project_id })
      onSaved(saved)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存失败')
    } finally { setSaving(false) }
  }

  return <Dialog open={open} onClose={onClose} size="lg" title={draft.id ? '编辑规则' : '新建规则'} description="规则会在规则层与内置规则一起运行，命中的内容按所选类型脱敏。"
    footer={<>
      {error && <span className="rule-error" role="alert">{error}</span>}
      <button type="button" className="btn" onClick={onClose}>取消</button>
      <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving}>{saving && <Spinner size={14}/>}{draft.id ? '保存修改' : '添加规则'}</button>
    </>}>
    <div className="rule-editor">
      <div className="rule-form">
        <label className="field">
          <span className="field-label">名称</span>
          <input className="input" value={draft.name} maxLength={80} onChange={event => set('name', event.target.value)} placeholder="例如：项目代号、学号" data-autofocus/>
        </label>
        <div className="field">
          <span className="field-label">匹配方式</span>
          <Segmented label="匹配方式" value={draft.kind} onChange={kind => set('kind', kind)} options={[{ value: 'keyword', label: '固定词' }, { value: 'regex', label: '正则表达式' }]}/>
        </div>
        <label className="field">
          <span className="field-label">{draft.kind === 'keyword' ? '要隐去的词' : '正则表达式'}</span>
          <input className={`input${draft.kind === 'regex' ? ' is-code' : ''}`} value={draft.pattern} maxLength={500} spellCheck={false} onChange={event => set('pattern', event.target.value)}
            placeholder={draft.kind === 'keyword' ? '例如：星舟计划' : String.raw`例如：ACCT-\d{8}`}/>
          {draft.kind === 'regex' && <span className="field-hint">使用 Python 正则语法，与后端识别时完全一致。</span>}
        </label>
        <div className="field">
          <span className="field-label">命中后按什么类型处理</span>
          <div className="rule-types">
            {allEntityTypes.map(type => <button type="button" key={type} className={`type-chip ${draft.entity_type === type ? 'is-on' : ''}`} style={entityStyle(type)} aria-pressed={draft.entity_type === type} onClick={() => set('entity_type', type)}>
              <span className="dot"/>{ENTITY_LABEL[type]}
            </button>)}
          </div>
        </div>
        <div className="rule-row">
          <label className="check"><input type="checkbox" checked={draft.case_sensitive} onChange={event => set('case_sensitive', event.target.checked)}/>区分大小写</label>
          {!lockScope && !draft.id && <label className="rule-scope">
            <span>适用于</span>
            <select className="select input-sm" value={draft.project_id || ''} onChange={event => set('project_id', event.target.value || null)} aria-label="适用范围">
              <option value="">全部项目</option>
              {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>}
        </div>
      </div>

      <div className="rule-test">
        <div className="rule-test-head">
          <span className="field-label">试一试</span>
          <span className="rule-test-state" aria-live="polite">{testing ? <Spinner size={13}/> : testError ? <span className="rule-error">{testError}</span> : matches ? `命中 ${matches.length} 处` : ''}</span>
        </div>
        <textarea className="textarea" rows={4} value={testText} onChange={event => setTestText(event.target.value)} placeholder="粘贴一段示例文字，看看规则会命中哪些内容" aria-label="测试文本"/>
        {testText.trim() && draft.pattern.trim() && matches && <Highlighted text={testText} matches={matches}/>}
        {(!testText.trim() || !draft.pattern.trim()) && <p className="rule-test-empty">填写规则和示例文字后，这里会标出命中的内容。</p>}
      </div>
    </div>
  </Dialog>
}
