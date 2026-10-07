const $ = id => document.getElementById(id)

function setStrategy(value) {
  document.querySelectorAll('#strategy button').forEach(button => button.setAttribute('aria-checked', String(button.dataset.value === value)))
}

function setStrength(value) {
  $('strength').value = String(value)
  $('strength').style.setProperty('--fill', `${((value - 1) / 2) * 100}%`)
  $('strengthLabel').textContent = MOYIN_STRENGTH[value]
}

document.querySelectorAll('#strategy button').forEach(button => button.addEventListener('click', () => setStrategy(button.dataset.value)))
$('strength').addEventListener('input', () => setStrength(Number($('strength').value)))

function normalizeBase(value) {
  return value.trim().replace(/\/+$/, '')
}

/** 非本机地址需要额外授权，浏览器会弹出确认。 */
async function ensurePermission(urls) {
  const origins = [...new Set(urls.map(url => `${new URL(url).origin}/*`))]
    .filter(origin => !/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/\*$/.test(origin))
  if (!origins.length) return true
  return chrome.permissions.request({ origins })
}

/** 从 base 地址读取项目列表。读不到时保留之前选的项目，保存设置不会把它清掉。 */
async function loadProjects(base, selected, selectedName) {
  const select = $('project')
  const temporary = select.options[0]
  try {
    const response = await fetch(`${base}/projects`)
    if (!response.ok) throw new Error(String(response.status))
    const { items } = await response.json()
    select.replaceChildren(temporary, ...items.map(project => new Option(project.name, project.id)))
    const exists = items.some(project => project.id === selected)
    select.value = exists ? selected : ''
    $('projectState').textContent = selected && !exists ? '之前选的项目已被删除，保存后改用临时方案' : ''
  } catch {
    select.replaceChildren(temporary)
    if (selected) {
      select.append(new Option(selectedName || '之前选的项目', selected))
      select.value = selected
    }
    $('projectState').textContent = '暂时读不到项目列表，先检查服务地址；保存时保留之前的选择'
  }
}

/** 读取 base/health；地址漏写了 /api/v1 时自动补上再试。返回能用的地址和服务信息。 */
async function probeService(base) {
  const candidates = /\/api\/v1$/.test(base) ? [base] : [base, `${base}/api/v1`]
  for (const candidate of candidates) {
    try {
      const response = await fetch(`${candidate}/health`)
      const health = response.ok ? await response.json().catch(() => null) : null
      if (health && health.status) return { base: candidate, health }
    } catch { /* 换下一个地址试 */ }
  }
  return null
}

$('test').addEventListener('click', async () => {
  $('testState').textContent = '正在连接…'
  try {
    let apiBase = normalizeBase($('apiBase').value) || MOYIN_DEFAULTS.apiBase
    if (!(await ensurePermission([apiBase]))) throw new Error('没有获得访问该地址的授权')
    const found = await probeService(apiBase)
    if (!found) throw new Error('连接不上墨隐服务，请确认后端已经启动，地址形如 http://127.0.0.1:8000/api/v1')
    if (found.base !== apiBase) { apiBase = found.base; $('apiBase').value = apiBase }
    const health = found.health
    $('testState').textContent = `连接成功，服务版本 ${health.version || ''}。保存设置后生效`
    const select = $('project')
    await loadProjects(apiBase, select.value, select.value ? select.options[select.selectedIndex]?.textContent : '')
  } catch (error) {
    $('testState').textContent = `连接失败：${error.message}`
  }
})

$('save').addEventListener('click', async () => {
  const apiBase = normalizeBase($('apiBase').value) || MOYIN_DEFAULTS.apiBase
  const appBase = normalizeBase($('appBase').value) || MOYIN_DEFAULTS.appBase
  try {
    new URL(apiBase); new URL(appBase)
  } catch {
    $('saveState').textContent = '地址格式不正确，请以 http:// 或 https:// 开头'
    return
  }
  if (!(await ensurePermission([apiBase, appBase]))) {
    $('saveState').textContent = '没有获得访问该地址的授权，设置未保存'
    return
  }
  const select = $('project')
  await moyinSaveSettings({
    apiBase, appBase,
    strategy: document.querySelector('#strategy button[aria-checked="true"]')?.dataset.value || 'mask',
    strength: Number($('strength').value),
    useLlm: $('llm').checked,
    projectId: select.value,
    projectName: select.value ? select.options[select.selectedIndex].textContent : '',
  })
  $('saveState').textContent = '已保存'
  window.setTimeout(() => { $('saveState').textContent = '' }, 1800)
})

async function init() {
  const settings = await moyinSettings()
  $('apiBase').value = settings.apiBase
  $('appBase').value = settings.appBase
  setStrategy(settings.strategy)
  setStrength(Number(settings.strength) || 2)
  $('llm').checked = Boolean(settings.useLlm)
  await loadProjects(normalizeBase(settings.apiBase), settings.projectId, settings.projectName)
}

void init()
