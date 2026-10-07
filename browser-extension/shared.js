/* 墨隐插件：弹窗、设置页和后台共用的配置与接口。后台通过 importScripts 引入。 */

const MOYIN_DEFAULTS = {
  apiBase: 'http://127.0.0.1:8000/api/v1',
  appBase: 'http://127.0.0.1:5173',
  strategy: 'mask',
  strength: 2,
  useLlm: true,
  projectId: '',
  projectName: '',
}

const MOYIN_LABELS = {
  PERSON: '姓名', ORG: '机构', LOCATION: '地点', ADDRESS: '详细地址', PHONE: '电话', EMAIL: '邮箱',
  ID_CARD: '身份证号', BANK_CARD: '银行卡号', PASSPORT: '护照号', ROLE: '职务身份', CUSTOM: '自定义',
}

const MOYIN_COLORS = {
  PERSON: '#b23a5e', ORG: '#10727a', LOCATION: '#3a7f45', ADDRESS: '#2c6fa0', PHONE: '#b0592a', EMAIL: '#7351a3',
  ID_CARD: '#bf3a2b', BANK_CARD: '#8f6d00', PASSPORT: '#5a7224', ROLE: '#94524a', CUSTOM: '#5f6778',
}

const MOYIN_STRENGTH = ['', '轻', '标准', '强']

async function moyinSettings() {
  const stored = await chrome.storage.sync.get(MOYIN_DEFAULTS)
  return { ...MOYIN_DEFAULTS, ...stored }
}

async function moyinSaveSettings(patch) {
  await chrome.storage.sync.set(patch)
}

/**
 * 调用墨隐服务。timeout 毫秒后放弃（默认 15 秒；识别可能要等大模型，调用方会给更长时间）。
 * 出错时抛出能直接给用户看的说明。
 */
async function moyinFetch(path, init = {}, timeout = 15000) {
  const { apiBase } = await moyinSettings()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  let response
  try {
    response = await fetch(`${apiBase.replace(/\/$/, '')}${path}`, { ...init, signal: controller.signal })
  } catch (error) {
    if (error && error.name === 'AbortError') throw new Error(`墨隐服务 ${Math.round(timeout / 1000)} 秒内没有响应，请稍后重试；文字较长或开启了大模型核查时可以分段处理`)
    throw new Error('连接不上墨隐服务，请确认后端已经启动，或在插件设置里检查服务地址')
  } finally {
    clearTimeout(timer)
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data && (typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail[0]?.msg : '')
    if (response.status === 404 && !detail) throw new Error('服务地址不对：请在插件设置里填写以 /api/v1 结尾的地址，例如 http://127.0.0.1:8000/api/v1')
    if (response.status === 404 && /Not Found/i.test(detail || '')) throw new Error('服务地址不对：请在插件设置里填写以 /api/v1 结尾的地址，例如 http://127.0.0.1:8000/api/v1')
    throw new Error(detail || `服务返回错误（${response.status}）`)
  }
  if (data === null) throw new Error('服务返回的不是墨隐的数据，请在插件设置里检查服务地址（应以 /api/v1 结尾）')
  return data
}

/** 设置里选的项目。项目已被删除时 missing 为 true。 */
async function moyinProject(settings) {
  if (!settings.projectId) return { project: null, missing: false }
  const { items } = await moyinFetch('/projects')
  const project = items.find(item => item.id === settings.projectId) || null
  return { project, missing: !project }
}

/**
 * 识别并脱敏。选了项目时使用项目的整套方案（方式、力度、识别范围、保留词、规则、按类型的方式）；
 * overrides 是弹窗里临时改的方式和力度，只影响这一次。
 */
async function moyinDetect(text, overrides = {}) {
  if (Array.from(text).length > 100000) throw new Error('选中的文字超过 10 万字，请分段处理')
  const settings = await moyinSettings()
  const { project, missing } = await moyinProject(settings)
  if (missing) await moyinSaveSettings({ projectId: '', projectName: '' })
  const base = project?.config || {}
  const chosen = Boolean(overrides.strategy)
  const body = {
    ...base,
    text,
    strategy: overrides.strategy || (project ? base.strategy : settings.strategy) || 'mask',
    privacy_strength: Number(overrides.strength || (project ? base.privacy_strength : settings.strength) || 2),
    use_llm: overrides.useLlm ?? (project && base.use_llm !== undefined ? base.use_llm : settings.useLlm),
    use_policies: chosen ? false : Boolean(project && base.use_policies),
    project_id: project ? project.id : null,
  }
  delete body.persist
  const task = await moyinFetch('/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, 120000)
  if (missing) task.moyinNotice = `插件设置里选的项目${settings.projectName ? `「${settings.projectName}」` : ''}已被删除，这次按临时方案处理`
  return task
}
