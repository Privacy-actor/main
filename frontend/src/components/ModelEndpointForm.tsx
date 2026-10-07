import { useEffect, useMemo, useState } from 'react'
import { Check, ListRestart, PlugZap } from 'lucide-react'
import { api } from '../api'
import { useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { CLOUD_PROVIDERS, LOCAL_PROVIDERS, providerFor } from '../lib/modelProviders'
import type { ModelEndpointView, ModelSettingsView } from '../types'
import { Spinner, Toggle } from './ui'

type Target = 'local' | 'cloud'
type Result = { tone: 'ok' | 'bad' | 'info'; text: string } | null

function sameHost(a: string, b: string) {
  try { return new URL(a).host === new URL(b).host } catch { return false }
}

/**
 * 一侧（本地或云端）的大模型设置：选服务、地址、模型、密钥，读取模型列表、测试连接、保存后立即生效。
 */
export default function ModelEndpointForm({ target, view, editable, onSaved }: {
  target: Target; view: ModelEndpointView; editable: boolean; onSaved: (next: ModelSettingsView) => void
}) {
  const toast = useToast()
  const { refreshEngine } = useApp()
  const providers = target === 'local' ? LOCAL_PROVIDERS : CLOUD_PROVIDERS
  // 从没设置过、也没启用时，本地默认选 Ollama，云端默认选第一家
  const initial = useMemo(() => {
    const fresh = view.source === 'env' && !view.enabled
    const provider = fresh ? providers[0] : providerFor(providers, view.provider, view.base_url)
    return { provider: provider.key, baseUrl: fresh ? provider.baseUrl : view.base_url || provider.baseUrl, model: fresh ? '' : view.model, enabled: view.enabled }
  }, [view, providers])

  const [providerKey, setProviderKey] = useState(initial.provider)
  const [baseUrl, setBaseUrl] = useState(initial.baseUrl)
  const [model, setModel] = useState(initial.model)
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(initial.enabled)
  const [models, setModels] = useState<string[]>([])
  const [busy, setBusy] = useState<'' | 'list' | 'test' | 'save'>('')
  const [result, setResult] = useState<Result>(null)

  useEffect(() => {
    setProviderKey(initial.provider); setBaseUrl(initial.baseUrl); setModel(initial.model); setEnabled(initial.enabled); setApiKey('')
  }, [initial])

  const provider = providers.find(item => item.key === providerKey) || providers[providers.length - 1]
  // 换了服务地址时，已保存的密钥不会带过去，需要重新填写
  const keySaved = view.api_key_set && sameHost(baseUrl, view.base_url)
  const dirty = providerKey !== initial.provider || baseUrl !== initial.baseUrl || model !== initial.model || enabled !== initial.enabled || Boolean(apiKey)
  const suggestions = models.length ? models : provider.examples
  const disabled = !editable || Boolean(busy)

  function chooseProvider(key: string) {
    const next = providers.find(item => item.key === key)
    if (!next) return
    setProviderKey(key)
    if (next.baseUrl) setBaseUrl(next.baseUrl)
    setModels([]); setResult(null)
    if (!next.examples.includes(model)) setModel('')
  }

  const keyPayload = () => apiKey.trim() || null

  async function listModels() {
    if (!baseUrl.trim()) { setResult({ tone: 'bad', text: '先填写服务地址' }); return }
    setBusy('list'); setResult(null)
    try {
      const { models: names } = await api.llmModels({ target, base_url: baseUrl.trim(), api_key: keyPayload() })
      setModels(names)
      if (!model && names.length === 1) setModel(names[0])
      setResult({ tone: 'info', text: `读到 ${names.length} 个模型，点一下选用` })
    } catch (caught) {
      setResult({ tone: 'bad', text: caught instanceof Error ? caught.message : '读取失败' })
    } finally { setBusy('') }
  }

  async function test() {
    if (!baseUrl.trim() || !model.trim()) { setResult({ tone: 'bad', text: '先填写服务地址和模型名称' }); return }
    if (provider.needsKey && target === 'cloud' && !apiKey.trim() && !keySaved) { setResult({ tone: 'bad', text: '先填写密钥' }); return }
    setBusy('test'); setResult(null)
    try {
      const outcome = await api.testLlm({ target, base_url: baseUrl.trim(), model: model.trim(), api_key: keyPayload() })
      setResult({ tone: outcome.ok ? 'ok' : 'bad', text: outcome.message })
    } catch (caught) {
      setResult({ tone: 'bad', text: caught instanceof Error ? caught.message : '测试失败' })
    } finally { setBusy('') }
  }

  async function save() {
    if (enabled && (!baseUrl.trim() || !model.trim())) { setResult({ tone: 'bad', text: '启用前请填写服务地址和模型名称' }); return }
    if (enabled && target === 'cloud' && provider.needsKey && !apiKey.trim() && !keySaved) { setResult({ tone: 'bad', text: '启用云端模型需要填写密钥' }); return }
    setBusy('save'); setResult(null)
    try {
      const next = await api.saveLlmSettings({ [target]: { enabled, provider: providerKey, base_url: baseUrl.trim(), model: model.trim(), api_key: keyPayload() } })
      onSaved(next)
      setApiKey('')
      void refreshEngine()
      toast(enabled ? `已保存，${target === 'local' ? '本地' : '云端'}模型立即生效` : '已保存', 'success')
    } catch (caught) {
      setResult({ tone: 'bad', text: caught instanceof Error ? caught.message : '保存失败' })
    } finally { setBusy('') }
  }

  const listId = `models-${target}`
  return <div className="model-form">
    <div className="model-grid">
      <label className="field"><span className="field-label">{target === 'local' ? '模型服务' : '服务商'}</span>
        <select className="select" value={providerKey} disabled={disabled} onChange={event => chooseProvider(event.target.value)}>
          {providers.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select>
      </label>
      <label className="field"><span className="field-label">服务地址</span>
        <input className="input" value={baseUrl} disabled={disabled} spellCheck={false} placeholder="https://…/v1" onChange={event => { setBaseUrl(event.target.value); setModels([]) }}/>
      </label>
    </div>
    {provider.needsKey && <label className="field"><span className="field-label">密钥{target === 'local' ? '（服务设置了才需要）' : ''}</span>
      <input className="input" type="password" autoComplete="off" spellCheck={false} value={apiKey} disabled={disabled}
        placeholder={keySaved ? `已保存，尾号 ${view.api_key_hint || '已隐藏'}；不更换就留空` : target === 'cloud' ? '在服务商控制台创建的 API Key' : '没有设置就留空'}
        onChange={event => setApiKey(event.target.value)}/>
    </label>}
    <div className="field">
      <span className="field-label">模型</span>
      <div className="model-pick">
        <input className="input" list={listId} value={model} disabled={disabled} spellCheck={false}
          placeholder={provider.examples[0] ? `例如 ${provider.examples[0]}` : '填写模型名称'} onChange={event => setModel(event.target.value)}/>
        <datalist id={listId}>{suggestions.map(name => <option key={name} value={name}/>)}</datalist>
        <button type="button" className="btn" disabled={disabled} onClick={() => void listModels()}>{busy === 'list' ? <Spinner size={14}/> : <ListRestart size={15}/>}读取模型列表</button>
      </div>
      {suggestions.length > 0 && <div className="model-chips" role="group" aria-label={models.length ? '服务上的模型' : '常用模型'}>
        {!models.length && <span className="model-chips-label">例如</span>}
        {suggestions.slice(0, 12).map(name => <button type="button" key={name} className={`model-chip${name === model ? ' is-on' : ''}`} disabled={disabled} onClick={() => setModel(name)}>{name === model && <Check size={12}/>}{name}</button>)}
        {models.length > 12 && <span className="model-chips-label">另有 {models.length - 12} 个，可在输入框里搜索</span>}
      </div>}
      {provider.note && <p className="field-hint">{provider.note}</p>}
    </div>
    <Toggle checked={enabled} disabled={disabled} onChange={setEnabled}
      label={target === 'local' ? '用这个本地模型做大模型核查' : '允许使用云端模型'}
      description={target === 'local' ? '关闭时只用规则和 NER，低置信度实体交给人工确认' : '处理方案的部署方式选“云端”时生效'}/>
    <div className="model-actions">
      <button type="button" className="btn" disabled={disabled} onClick={() => void test()}>{busy === 'test' ? <Spinner size={14}/> : <PlugZap size={15}/>}测试连接</button>
      <button type="button" className="btn btn-primary" disabled={disabled || !dirty} onClick={() => void save()}>{busy === 'save' && <Spinner size={14}/>}保存</button>
      {result && <p className={`model-result is-${result.tone}`} role="status">{result.text}</p>}
    </div>
    {!editable && <p className="field-hint">服务器管理员关闭了在网页里修改模型设置，当前配置来自服务器的 .env。</p>}
  </div>
}
