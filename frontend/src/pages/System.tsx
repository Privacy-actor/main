import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Copy, RefreshCw } from 'lucide-react'
import { api } from '../api'
import { BRAND } from '../brand'
import ModelEndpointForm from '../components/ModelEndpointForm'
import { Segmented, Spinner } from '../components/ui'
import { loadProcessingConfig, saveProcessingConfig } from '../configStore'
import { describeEngines, useApp } from '../hooks/AppContext'
import { useToast } from '../hooks/Toast'
import { copyText } from '../lib/download'
import { HARDWARE_GUIDE } from '../lib/modelProviders'
import { normalizeConfig, type DeploymentMode, type ModelSettingsView } from '../types'
import './System.css'

type State = 'on' | 'lite' | 'idle' | 'warn' | 'off'

const shortName = (name: string) => name.split(/[\\/]/).filter(Boolean).pop() || name

function StateDot({ state }: { state: State }) {
  return <i className={`engine-dot is-${state}`} aria-hidden="true"/>
}

function Command({ children }: { children: string }) {
  const toast = useToast()
  return <div className="command">
    <pre><code>{children}</code></pre>
    <button type="button" className="icon-btn" aria-label="复制命令" title="复制" onClick={async () => { if (await copyText(children)) toast('已复制', 'success') }}><Copy size={14}/></button>
  </div>
}

export default function System() {
  const { engine, refreshEngine, currentProject } = useApp()
  const toast = useToast()
  const [checking, setChecking] = useState(false)
  const [ruleCount, setRuleCount] = useState<number | null>(null)
  const [mode, setMode] = useState<DeploymentMode>(() => loadProcessingConfig().deployment_mode)
  const [modelSettings, setModelSettings] = useState<ModelSettingsView | null>(null)
  const [settingsError, setSettingsError] = useState('')
  const models = engine.models
  const health = engine.health
  const engines = describeEngines(models)
  const offline = engine.state === 'offline'

  // 从上手指南、工作台跳到某一节（/system#models）时滚动过去
  const location = useLocation()
  useEffect(() => {
    if (!location.hash) return
    const timer = window.setTimeout(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: 'start' }), 120)
    return () => window.clearTimeout(timer)
  }, [location.hash, modelSettings])

  const loadModelSettings = useCallback(async () => {
    try { setModelSettings(await api.llmSettings()); setSettingsError('') }
    catch (caught) { setSettingsError(caught instanceof Error ? `读不到大模型设置：${caught.message}` : '读不到大模型设置') }
  }, [])

  // 服务恢复连接后重新读取
  useEffect(() => { if (engine.state === 'online') void loadModelSettings() }, [engine.state, loadModelSettings])

  useEffect(() => {
    api.rules().then(data => setRuleCount(data.items.length)).catch(() => setRuleCount(null))
    const onConfig = (event: Event) => setMode(normalizeConfig((event as CustomEvent).detail).deployment_mode)
    window.addEventListener('privshield-config', onConfig)
    return () => window.removeEventListener('privshield-config', onConfig)
  }, [])

  async function recheck() {
    setChecking(true)
    await refreshEngine()
    setChecking(false)
  }

  function changeMode(next: DeploymentMode) {
    saveProcessingConfig({ ...loadProcessingConfig(), deployment_mode: next })
    setMode(next)
    toast(next === 'cloud' ? '当前方案改为云端部署：大模型核查使用云端接口' : '当前方案改为本地部署：所有处理留在本机', 'success')
  }

  const local = models?.endpoints?.local
  const cloud = models?.endpoints?.cloud
  const semantic = models?.semantic
  const knowledge = models?.knowledge_graph
  const epsilon = models?.pseudonym_epsilon || {}
  const eps = (level: number) => (epsilon as Record<string, number>)[String(level)] ?? { 1: 4, 2: 1, 3: 0.25 }[level]
  const threshold = Math.round((models?.confidence_threshold ?? 0.9) * 100)
  const nerStatus = models?.ner_status
  const nerDot: State = { ready: 'on', loading: 'idle', failed: 'warn', off: 'lite' }[engines.nerState] as State
  const nerDetail = {
    ready: `多语种命名实体识别模型，与内置的轻量识别器同时运行。置信度达到 ${threshold}% 直接采纳，低于 ${threshold}% 交给大模型核查。`,
    loading: '模型正在加载，通常需要十几秒到一分钟，加载完成前由内置的轻量识别器处理。稍后点“重新检测”刷新。',
    failed: `模型没有加载成功${nerStatus?.detail ? `（${nerStatus.detail}）` : ''}，识别照常进行，由内置的轻量识别器处理。按下方步骤检查后重启后端。`,
    off: `当前使用内置的轻量识别器（姓名、机构、地点、地址）。置信度低于 ${threshold}% 的实体交给大模型或人工确认。按下方步骤可以接入多语种 NER 模型。`,
  }[engines.nerState]

  const layers: Array<{ name: string; state: State; value: string; detail: string }> = [
    {
      name: '规则层', state: offline ? 'off' : 'on', value: engines.rules,
      detail: `手机号、邮箱、身份证（校验位）、银行卡（Luhn 校验）、护照等结构化标识。${ruleCount ? `另有 ${ruleCount} 条全局自定义规则。` : '可以在规则库补充单位内部的编号格式。'}`,
    },
    { name: 'NER 层', state: offline ? 'off' : nerDot, value: engines.ner, detail: nerDetail },
    {
      name: '大模型核查', state: offline ? 'off' : engines.llmReady ? 'on' : 'idle',
      value: [local?.enabled && `本地 ${shortName(local.model)}`, cloud?.enabled && `云端 ${shortName(cloud.model)}`].filter(Boolean).join('，') || '未启用',
      detail: engines.llmReady ? '复核低置信度实体、补充前两层遗漏的信息，并解析自然语言要求；调用失败时自动降级，不影响出结果。' : '未启用大模型时，识别由规则层和 NER 层完成，低置信度实体交给人工确认。在下方“大模型与部署方式”里选择本地或云端模型。',
    },
    {
      name: '隐性隐私', state: offline ? 'off' : 'on', value: '职务身份识别',
      detail: '“心内科主任”“财务处处长”这类职务与部门的组合不含姓名，但能推断出具体的人。系统单独标为“职务身份”，交给大模型或人工确认，可泛化为“某部门负责人”。',
    },
  ]

  return <div className="page system">
    <header className="page-head">
      <div>
        <h1 className="page-title">部署与插件</h1>
        <p className="page-desc">查看识别引擎的运行状态，选择本地或云端的大模型，以及安装浏览器插件，在网页和大模型对话框里直接脱敏。</p>
      </div>
      <div className="page-actions"><button type="button" className="btn" onClick={() => void recheck()} disabled={checking}>{checking ? <Spinner size={14}/> : <RefreshCw size={15}/>}重新检测</button></div>
    </header>

    {offline && <div className="notice notice-bad system-offline">处理服务未连接。在 Windows 上双击项目根目录的 <code>启动墨隐.cmd</code>（或在 backend 目录运行 <code>python -m uvicorn app.main:app --port 8000</code>），然后点“重新检测”。</div>}

    <section className="section system-first">
      <div className="section-head"><h2 className="section-title">识别引擎</h2>{health && <span className="section-note num">服务版本 {health.version}，数据库{health.database === 'online' ? '正常' : '异常'}</span>}</div>
      <div className="engine-table panel">
        {layers.map(layer => <div className="engine-line" key={layer.name}>
          <div className="engine-line-name"><StateDot state={layer.state}/><strong>{layer.name}</strong></div>
          <div className="engine-line-value">{engine.state === 'checking' ? '检测中' : offline ? '未连接' : layer.value}</div>
          <p className="engine-line-detail">{layer.detail}</p>
        </div>)}
        <div className="engine-line">
          <div className="engine-line-name"><StateDot state={offline ? 'off' : 'on'}/><strong>知识图谱</strong></div>
          <div className="engine-line-value">{knowledge ? `内置 ${knowledge.local_entries} 条实体与行政区划` : '—'}</div>
          <p className="engine-line-detail">{knowledge?.enabled ? '已启用远程 CN-Probase / CN-DBpedia 查询，失败时回退到内置知识库。' : '用于“知识图谱泛化”：沿上位概念上溯，例如“北京协和医院”到“北京某医院”“医疗机构”。英文句子给出英文概念。远程查询默认关闭，避免把实体词发到外部。'}</p>
        </div>
        <div className="engine-line">
          <div className="engine-line-name"><StateDot state={offline ? 'off' : semantic?.state === 'ready' ? 'on' : 'lite'}/><strong>差分隐私替换</strong></div>
          <div className="engine-line-value num">ε：轻 {eps(1)}，标准 {eps(2)}，强 {eps(3)}</div>
          <p className="engine-line-detail">按指数机制从同类候选中抽取替换词，大学换成大学、医院换成医院；ε 越小随机性越强。{semantic?.state === 'ready' ? '候选的语义相近程度由句向量模型评分。' : '句向量模型未加载时，按候选词的内置特征评分。'}</p>
        </div>
      </div>
    </section>

    {engines.nerState !== 'ready' && <section className="section" id="ner">
      <div className="section-head"><h2 className="section-title">接入 NER 模型</h2><span className="section-note">可选，CPU 即可运行</span></div>
      <div className="ner-setup panel panel-pad">
        <p>推荐使用多语种模型 Davlan/xlm-roberta-base-ner-hrl（XLM-RoBERTa，覆盖中文、英文等十种语言），约 1.1 GB。接入后人名、机构、地点由模型和轻量识别器共同识别，地址、号码等仍由规则层负责。</p>
        <ol className="plugin-steps">
          <li>在 Windows 上双击项目里的 <code>scripts\安装NER模型.cmd</code>。它会安装 PyTorch 和 Transformers，从魔搭社区下载模型（失败时改用 Hugging Face 镜像），试运行一次，再在 <code>backend\.env</code> 里开启 NER。</li>
          <li>关掉后端窗口，重新运行 <code>启动墨隐.cmd</code>。回到本页点“重新检测”，NER 层显示模型名即可使用。</li>
          <li>有 NVIDIA 显卡并装了 CUDA 版 PyTorch 时，可把 <code>PRIVSHIELD_NER_DEVICE</code> 改为 <code>0</code> 用显卡推理。</li>
        </ol>
        <p className="deploy-label">macOS、Linux 或服务器上，用后端虚拟环境里的 Python 在项目根目录运行</p>
        <Command>{'python scripts/setup_ner.py'}</Command>
      </div>
    </section>}

    <section className="section" id="models">
      <div className="section-head"><h2 className="section-title">大模型与部署方式</h2><span className="section-note">核查低置信度实体、补充遗漏、理解自然语言要求；不接大模型也能用</span></div>
      <div className="deploy-switch panel">
        <div>
          <strong>当前方案使用</strong>
          <p>{currentProject ? `项目「${currentProject.name}」` : '临时方案'}。本地部署时原文不离开本机；云端部署时，只有需要核查的句子发送到云端模型。</p>
        </div>
        <Segmented label="部署模式" value={mode} onChange={changeMode} options={[
          { value: 'local', label: '本地部署' },
          { value: 'cloud', label: '云端部署', disabled: !engines.cloudLlm && mode !== 'cloud', title: engines.cloudLlm ? undefined : '先在下方设置并启用云端模型' },
        ]}/>
      </div>
      {settingsError && <div className="notice notice-bad">{settingsError}</div>}
      <div className="deploy-grid">
        <article className={`deploy panel panel-pad${mode === 'local' ? ' is-current' : ''}`}>
          <header><h3>本地模型</h3><span className={`chip ${local?.enabled ? 'chip-ok' : ''}`}>{local?.enabled ? `已启用 ${shortName(local.model)}` : '未启用'}</span></header>
          <p>模型运行在本机或单位内网，文本全程不离开本地，适合涉密、医疗、政务等材料。电脑配置不够时可以先只用规则和 NER，或改用云端模型。</p>
          {modelSettings ? <ModelEndpointForm target="local" view={modelSettings.local} editable={modelSettings.editable} onSaved={setModelSettings}/> : !settingsError && <div className="model-loading"><Spinner/></div>}
          <details className="model-guide">
            <summary>按电脑配置选模型</summary>
            <dl>{HARDWARE_GUIDE.map(([machine, choice]) => <div key={machine}><dt>{machine}</dt><dd>{choice}</dd></div>)}</dl>
            <p>用 Ollama 下载模型后，点上方“读取模型列表”即可选用。模型更新很快，以 Ollama 模型库里的最新版本为准。</p>
            <Command>{'ollama pull qwen3:8b'}</Command>
          </details>
        </article>
        <article className={`deploy panel panel-pad${mode === 'cloud' ? ' is-current' : ''}`}>
          <header><h3>云端模型</h3><span className={`chip ${cloud?.enabled ? 'chip-ok' : ''}`}>{cloud?.enabled ? `已启用 ${shortName(cloud.model)}` : '未启用'}</span></header>
          <p>规则层和 NER 层仍在本地运行，只把需要核查的句子发给云端的 OpenAI 兼容接口，不需要本地显卡。适合配置不高的电脑和非敏感材料。</p>
          {modelSettings ? <ModelEndpointForm target="cloud" view={modelSettings.cloud} editable={modelSettings.editable} onSaved={setModelSettings}/> : !settingsError && <div className="model-loading"><Spinner/></div>}
        </article>
      </div>
      <details className="server-deploy panel panel-pad">
        <summary>部署到 GPU 服务器</summary>
        <p>服务器上用 Docker 一次启动前端、后端和 vLLM。启动后在上方“本地模型”里选 vLLM，地址填 <code>http://服务器IP:8001/v1</code>，密钥是 .env 里的 <code>PRIVSHIELD_LLM_API_KEY</code>。多人共用的服务器可以在 .env 里设 <code>PRIVSHIELD_MODEL_SETTINGS_EDITABLE=false</code>，只允许管理员改模型配置。</p>
        <Command>{'cp .env.example .env\ndocker compose --profile gpu up -d --build'}</Command>
      </details>
    </section>

    <section className="section" id="plugin">
      <div className="section-head"><h2 className="section-title">浏览器插件</h2><span className="section-note">在网页、邮件和大模型对话框里直接使用{BRAND.name}</span></div>
      <div className="plugin panel">
        <div className="plugin-copy">
          <ul className="plugin-uses">
            <li><strong>对话框里一键脱敏。</strong>在 ChatGPT、豆包、Kimi 等对话框里写好内容，选中后右键“用{BRAND.name}脱敏并替换”，或按 <kbd className="kbd">Alt</kbd> <kbd className="kbd">Shift</kbd> <kbd className="kbd">M</kbd>，文字原地换成脱敏结果，再发送。</li>
            <li><strong>网页文字随手处理。</strong>选中网页上的文字右键“用{BRAND.name}脱敏选中文本”，在弹窗里看结果、改字、复制或导出。</li>
            <li><strong>和工作台连通。</strong>插件里的任务会保存在任务记录中，需要逐条复核时一键在工作台打开；可以选择使用哪个项目的方案和规则。</li>
          </ul>
          <ol className="plugin-steps">
            <li>在 Chrome 或 Edge 地址栏打开 <code>chrome://extensions</code>（Edge 为 <code>edge://extensions</code>）。</li>
            <li>打开“开发者模式”，点“加载已解压的扩展程序”，选择项目里的 <code>browser-extension</code> 文件夹。</li>
            <li>保持处理服务运行。插件默认连接 <code>http://127.0.0.1:8000</code>，地址不同时在插件的“设置”里修改。</li>
          </ol>
        </div>
        <figure className="plugin-figure" aria-hidden="true">
          <div className="plugin-chat">
            <span className="plugin-chat-label">发送给大模型之前</span>
            <p>请帮我总结：客户<b>李明</b>（电话 <b>13800138000</b>）反馈……</p>
          </div>
          <div className="plugin-chat is-after">
            <span className="plugin-chat-label">按下 Alt Shift M 之后</span>
            <p>请帮我总结：客户<i>PERSON-001</i>（电话 <i>PHONE-001</i>）反馈……</p>
          </div>
        </figure>
      </div>
    </section>

    <section className="section">
      <div className="section-head"><h2 className="section-title">数据与隐私</h2></div>
      <ul className="privacy-list panel panel-pad">
        <li><strong>原文只保存在本地数据库。</strong>任务记录、复核和最终稿都保存在本机的 SQLite 文件中，可在<Link to="/history">任务记录</Link>里随时删除或按天数清理。</li>
        <li><strong>操作记录不含明文。</strong>人工确认、补充遗漏等操作只记录位置、长度和哈希，自定义替换词同样只记哈希。</li>
        <li><strong>导出前自动复检。</strong>导出最终稿或报告前，会再检查一遍是否还有可识别的号码、已确认的实体或职务身份，发现残留会先提醒。</li>
        <li><strong>远程服务默认关闭。</strong>远程知识图谱和云端大模型都需要在配置中显式开启，开启后才会向外发送数据。</li>
      </ul>
    </section>
  </div>
}
