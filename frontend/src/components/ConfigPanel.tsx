import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, Shuffle, Sparkles } from 'lucide-react'
import { api } from '../api'
import { describeEngines, useApp } from '../hooks/AppContext'
import { ENTITY_LABEL, STRATEGY_EXAMPLE, STRATEGY_HINT, STRATEGY_LABEL, STRENGTH_LABEL, entityStyle, strengthEffect } from '../lib/entities'
import { allEntityTypes, type EntityType, type InstructionPlan, type ProcessingConfig, type Replacement, type Span, type Strategy } from '../types'
import RedactedText from './RedactedText'
import { Segmented, Spinner, TagInput, Toggle } from './ui'
import './ConfigPanel.css'

const EXAMPLE = '张伟就读于北京大学，电话 13800138000。'
const EXAMPLE_SPANS: Span[] = [
  { id: 'ex-person', start: 0, end: 2, text: '张伟', entity_type: 'PERSON', score: 1, sources: ['RULE'], status: 'accepted', conflict: false, strategy: 'mask', metadata: {} },
  { id: 'ex-org', start: 5, end: 9, text: '北京大学', entity_type: 'ORG', score: 1, sources: ['RULE'], status: 'accepted', conflict: false, strategy: 'mask', metadata: {} },
  { id: 'ex-phone', start: 13, end: 24, text: '13800138000', entity_type: 'PHONE', score: 1, sources: ['RULE'], status: 'accepted', conflict: false, strategy: 'mask', metadata: {} },
]

/** 力度滑块旁的实时示例：用后端真实算法处理一句固定的示例文本。 */
export function StrategyPreview({ strategy, strength }: { strategy: Strategy; strength: number }) {
  const [result, setResult] = useState<{ text: string; replacements: Replacement[] } | null>(null)
  const [failed, setFailed] = useState(false)
  const [nonce, setNonce] = useState(0)
  const spans = useMemo(() => EXAMPLE_SPANS.map(span => ({ ...span, strategy })), [strategy])
  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      api.redact(EXAMPLE, spans, strategy, strength).then(data => {
        if (!cancelled) { setResult({ text: data.redacted_text, replacements: data.replacements }); setFailed(false) }
      }).catch(() => { if (!cancelled) setFailed(true) })
    }, 120)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [strategy, strength, spans, nonce])
  return <div className="strategy-preview">
    <div className="strategy-preview-row"><span className="strategy-preview-label">原句</span><p>{EXAMPLE}</p></div>
    <div className="strategy-preview-row">
      <span className="strategy-preview-label">效果</span>
      {failed ? <p className="muted">处理服务未连接，暂时无法预览</p> : result ? <RedactedText text={result.text} replacements={result.replacements} compact/> : <p className="muted">正在生成…</p>}
      {strategy === 'pseudonymize' && !failed && <button type="button" className="icon-btn" title="重新抽样一次" aria-label="重新抽样一次" onClick={() => setNonce(value => value + 1)}><Shuffle size={15}/></button>}
    </div>
  </div>
}

export function StrategyChoice({ value, onChange, disabled }: { value: Strategy; onChange: (strategy: Strategy) => void; disabled?: boolean }) {
  return <div className="strategy-choice" role="radiogroup" aria-label="脱敏方式">
    {(Object.keys(STRATEGY_LABEL) as Strategy[]).map(strategy => {
      const [from, to] = STRATEGY_EXAMPLE[strategy]
      return <button type="button" role="radio" aria-checked={value === strategy} key={strategy} disabled={disabled}
        className={`strategy-option${value === strategy ? ' is-active' : ''}`} onClick={() => onChange(strategy)}>
        <span className="strategy-option-head"><span className="radio-dot" aria-hidden="true"/><strong>{STRATEGY_LABEL[strategy]}</strong></span>
        <span className="strategy-option-example"><span>{from}</span><span className="arrow" aria-hidden="true">›</span>{strategy === 'mask' ? <span className="mini-mask">{to.slice(1, -1)}</span> : <span className="mini-swap">{to}</span>}</span>
        <span className="strategy-option-hint">{STRATEGY_HINT[strategy]}</span>
      </button>
    })}
  </div>
}

/** 力度滑块：三档，掩码不分力度时禁用。 */
export function StrengthControl({ strategy, value, onChange, disabled }: { strategy: Strategy; value: number; onChange: (value: number) => void; disabled?: boolean }) {
  const off = disabled || strategy === 'mask'
  const percent = ((value - 1) / 2) * 100
  return <div className={`strength${off ? ' is-off' : ''}`}>
    <div className="strength-slider" style={{ ['--fill' as string]: `${percent}%` }}>
      <input type="range" min={1} max={3} step={1} value={value} disabled={off} aria-label="脱敏力度"
        aria-valuetext={STRENGTH_LABEL[value]} onChange={event => onChange(Number(event.target.value))}/>
      <div className="strength-stops" aria-hidden="true">
        {[1, 2, 3].map(level => <button type="button" tabIndex={-1} key={level} disabled={off} className={level === value ? 'is-active' : ''} onClick={() => onChange(level)}>{STRENGTH_LABEL[level]}</button>)}
      </div>
    </div>
    <p className="strength-effect">{strengthEffect(strategy, value)}</p>
  </div>
}

export function EntityScope({ value, onChange }: { value: EntityType[]; onChange: (value: EntityType[]) => void }) {
  const all = value.length === allEntityTypes.length
  return <div className="scope">
    <div className="scope-chips">
      {allEntityTypes.map(type => {
        const on = value.includes(type)
        return <button type="button" key={type} className={`type-chip ${on ? 'is-on' : 'is-off'}`} style={entityStyle(type)} aria-pressed={on}
          onClick={() => { const next = on ? value.filter(item => item !== type) : [...value, type]; if (next.length) onChange(next) }}>
          <span className="dot"/>{ENTITY_LABEL[type]}
        </button>
      })}
    </div>
    <button type="button" className="link-btn scope-all" onClick={() => onChange(all ? ['PERSON', 'PHONE', 'EMAIL', 'ID_CARD'] : [...allEntityTypes])}>{all ? '只保留常用 4 类' : '全部选中'}</button>
  </div>
}

export function InstructionInput({ value, onChange, useLlm, deploymentMode = 'local' }: { value: string | null; onChange: (value: string | null) => void; useLlm: boolean; deploymentMode?: 'local' | 'cloud' }) {
  const [plan, setPlan] = useState<InstructionPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const lastParsed = useRef('')
  async function parse() {
    const instruction = value?.trim()
    if (!instruction) return
    setBusy(true); setError('')
    try { setPlan(await api.parseInstruction(instruction, useLlm, deploymentMode)); lastParsed.current = instruction }
    catch (caught) { setError(caught instanceof Error ? caught.message : '解析失败') }
    finally { setBusy(false) }
  }
  const stale = plan && lastParsed.current !== (value?.trim() || '')
  const chips: Array<{ kind: string; label: string }> = []
  if (plan && !stale) {
    plan.preserve_terms?.forEach(term => chips.push({ kind: 'keep', label: `保留 ${term}` }))
    plan.force_terms?.forEach(term => chips.push({ kind: 'hide', label: `隐去 ${term}` }))
    if (plan.enabled_entity_types?.length) chips.push({ kind: 'scope', label: `只处理 ${plan.enabled_entity_types.map(type => ENTITY_LABEL[type]).join('、')}` })
    if (plan.disabled_entity_types?.length) chips.push({ kind: 'keep', label: `不处理 ${plan.disabled_entity_types.map(type => ENTITY_LABEL[type]).join('、')}` })
    if (plan.force_types?.length) chips.push({ kind: 'hide', label: `一定处理 ${plan.force_types.map(type => ENTITY_LABEL[type]).join('、')}` })
    if (plan.strategy) chips.push({ kind: 'strategy', label: `方式改为${STRATEGY_LABEL[plan.strategy]}` })
    for (const [type, strategy] of Object.entries(plan.type_strategies || {})) {
      if (strategy) chips.push({ kind: 'strategy', label: `${ENTITY_LABEL[type as EntityType]}改用${STRATEGY_LABEL[strategy]}` })
    }
    if (plan.privacy_strength) chips.push({ kind: 'strategy', label: `力度改为${STRENGTH_LABEL[plan.privacy_strength]}` })
  }
  const ignored = plan && !stale ? plan.ignored_clauses || [] : []
  return <div className="instruction">
    <textarea className="textarea" rows={2} value={value || ''} placeholder="例如：保留北京的地名，上海的地名隐去；姓名用差分隐私替换"
      onChange={event => onChange(event.target.value || null)} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void parse() } }}/>
    <div className="instruction-foot">
      <button type="button" className="btn btn-sm" onClick={parse} disabled={busy || !value?.trim()}>{busy ? <Spinner size={13}/> : <Sparkles size={13}/>}看看会怎么理解</button>
      {error && <span className="instruction-error">{error}</span>}
    </div>
    {plan && !stale && <div className="instruction-plan" aria-live="polite">
      {chips.length ? chips.map(chip => <span className={`plan-chip is-${chip.kind}`} key={chip.label}>{chip.label}</span>)
        : !ignored.length && <span className="muted">没有识别出明确的要求，换个说法试试</span>}
      {ignored.map(clause => <span className="plan-chip is-ignored" key={`ignored-${clause}`} title="系统按整个实体处理，不支持只隐去或只保留其中一部分">暂不支持：{clause}</span>)}
      {ignored.length > 0 && <small className="muted">这些要求不会改变设置，相关内容照常整段脱敏</small>}
      {plan.parser && <small className="muted">{plan.parser.startsWith('llm') ? '大模型解析' : '本地规则解析'}，开始处理时生效</small>}
    </div>}
  </div>
}

interface ConfigPanelProps {
  value: ProcessingConfig
  onChange: (next: ProcessingConfig) => void
  variant?: 'workbench' | 'batch' | 'project'
  disabled?: boolean
}

/** 处理方案：脱敏方式、力度、识别范围、自然语言要求和更多设置。工作台、批量处理和项目共用。 */
export default function ConfigPanel({ value, onChange, variant = 'workbench', disabled }: ConfigPanelProps) {
  const { engine } = useApp()
  const engines = describeEngines(engine.models)
  const [moreOpen, setMoreOpen] = useState(variant === 'project')
  const set = <K extends keyof ProcessingConfig>(key: K, next: ProcessingConfig[K]) => onChange({ ...value, [key]: next })
  const keywords = value.custom_keywords.map(item => item.value)
  // 核查用哪个模型由部署模式决定：本地模式只用本地模型，云端模式才把待核查的句子发给云端模型
  const cloudMode = value.deployment_mode === 'cloud'
  const modeModel = cloudMode ? engines.cloudName : engines.localName
  const otherModel = cloudMode ? engines.localName : engines.cloudName
  const llmDescription = modeModel ? `复核低置信度实体并补充遗漏，当前使用${cloudMode ? '云端' : '本地'}模型 ${modeModel}`
    : otherModel ? <>{cloudMode ? '云端' : '本地'}模型还没有启用，本次只使用规则与 NER。已设置{cloudMode ? '本地' : '云端'}模型 {otherModel}，
      <button type="button" className="link-btn" onClick={event => { event.preventDefault(); set('deployment_mode', cloudMode ? 'local' : 'cloud') }}>{cloudMode ? '改用本地' : '改用云端'}</button>
      {cloudMode ? '' : '后会把待核查的句子发到云端。'}</>
    : <>还没有启用大模型，开启后本次仍只使用规则与 NER。<Link to="/system#models">去选择本地或云端模型</Link></>

  return <fieldset className="config" disabled={disabled}>
    <section className="config-block">
      <h3 className="config-title">脱敏方式</h3>
      <StrategyChoice value={value.strategy} onChange={strategy => set('strategy', strategy)}/>
    </section>

    <section className="config-block">
      <h3 className="config-title">力度</h3>
      <StrengthControl strategy={value.strategy} value={value.privacy_strength} onChange={level => set('privacy_strength', level)}/>
      <StrategyPreview strategy={value.strategy} strength={value.privacy_strength}/>
    </section>

    <section className="config-block">
      <h3 className="config-title">识别范围 <span className="config-count num">{value.enabled_entity_types.length}/{allEntityTypes.length}</span></h3>
      <EntityScope value={value.enabled_entity_types} onChange={types => set('enabled_entity_types', types)}/>
    </section>

    <section className="config-block">
      <h3 className="config-title">用一句话提要求</h3>
      <InstructionInput value={value.instruction} useLlm={value.use_llm} deploymentMode={value.deployment_mode} onChange={instruction => set('instruction', instruction)}/>
    </section>

    <section className="config-block config-more">
      <button type="button" className="config-more-toggle" aria-expanded={moreOpen} onClick={() => setMoreOpen(open => !open)}>
        <span>更多设置</span><ChevronDown size={15} className={moreOpen ? 'is-open' : ''}/>
      </button>
      {moreOpen && <div className="config-more-body">
        <Toggle checked={value.use_llm} onChange={checked => set('use_llm', checked)} label="大模型核查与补漏"
          description={llmDescription}/>
        <div className="config-row">
          <span className="config-row-label">部署模式</span>
          <Segmented label="部署模式" size="sm" value={value.deployment_mode} onChange={mode => set('deployment_mode', mode)} options={[
            { value: 'local', label: '本地', title: '模型与数据都在本机或内网服务器' },
            { value: 'cloud', label: '云端', title: engines.cloudLlm ? '大模型核查改用云端模型' : '还没有启用云端模型，先在“部署与插件”里设置', disabled: !engines.cloudLlm && value.deployment_mode !== 'cloud' },
          ]}/>
        </div>
        <div className="config-row">
          <span className="config-row-label">待确认的实体</span>
          <Segmented label="待确认的实体" size="sm" value={value.risk_level} onChange={level => set('risk_level', level)} options={[
            { value: 'strict', label: '先脱敏', title: '低置信度的实体也先替换，复核时可恢复' },
            { value: 'standard', label: '先保留', title: '低置信度的实体保持原文，确认后再替换' },
          ]}/>
        </div>
        <div className="config-row">
          <span className="config-row-label">文本语言</span>
          <select className="select input-sm" value={value.language} onChange={event => set('language', event.target.value as ProcessingConfig['language'])} aria-label="文本语言">
            <option value="auto">自动判断</option><option value="zh">中文</option><option value="en">英文</option><option value="mixed">中英混合</option><option value="multilingual">多语种</option>
          </select>
        </div>
        {variant !== 'project' ? <>
          <div className="config-stack">
            <span className="config-row-label">本次额外隐去</span>
            <TagInput label="本次额外隐去的词" values={keywords} placeholder="项目代号、内部编号…回车添加"
              onChange={values => set('custom_keywords', values.map(item => value.custom_keywords.find(existing => existing.value === item) || { value: item, entity_type: 'CUSTOM', case_sensitive: false }))}/>
          </div>
          <div className="config-stack">
            <span className="config-row-label">本次保留不动</span>
            <TagInput label="本次保留的词" values={value.preserve_terms} placeholder="公开机构名、城市名…回车添加" onChange={values => set('preserve_terms', values)}/>
          </div>
        </> : <div className="config-stack">
          <span className="config-row-label">始终保留不动</span>
          <TagInput label="项目中始终保留的词" values={value.preserve_terms} placeholder="公开机构名、城市名…回车添加" onChange={values => set('preserve_terms', values)}/>
          <span className="field-hint">需要额外隐去的词，请在右侧的项目规则里添加。</span>
        </div>}
        <Toggle checked={value.use_policies} onChange={checked => set('use_policies', checked)} label="按实体类型使用不同方式"
          description={<>例如姓名用替换、电话用掩码，<Link to="/rules?tab=policies">在规则库设置</Link></>}/>
      </div>}
    </section>
  </fieldset>
}
