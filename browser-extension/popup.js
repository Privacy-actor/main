const $ = id => document.getElementById(id)

const state = {
  task: null,        // 当前任务（识别结果）
  automatic: '',     // 自动生成的结果
  saved: '',         // 已保存到服务器的最终稿
  revision: 0,
  saveTimer: 0,
  project: null,     // 设置里选的项目：方式和力度默认取项目的，这里临时改动只影响这一次
  touched: {},       // 这次在弹窗里改过的项（strategy、strength、useLlm）
  resultPlan: '',    // 当前结果用的方案，方案改动后提示重新识别
  restored: null,    // 最近一次还原的结果
}

/* ---------- 方案：方式、力度、大模型 ---------- */

function setStrategy(value) {
  document.querySelectorAll('#strategy button').forEach(button => button.setAttribute('aria-checked', String(button.dataset.value === value)))
  const off = value === 'mask'
  $('strength').disabled = off
  $('strengthBox').classList.toggle('is-off', off)
  $('strengthBox').title = off ? '掩码不分力度' : ''
}

function setStrength(value) {
  $('strength').value = String(value)
  $('strength').style.setProperty('--fill', `${((value - 1) / 2) * 100}%`)
  $('strengthLabel').textContent = MOYIN_STRENGTH[value]
}

function currentPlan() {
  const strategy = document.querySelector('#strategy button[aria-checked="true"]')?.dataset.value || 'mask'
  return { strategy, strength: Number($('strength').value), useLlm: $('llm').checked }
}

/** 传给识别的临时改动：没选项目时就是当前开关；选了项目时只传这次改过的项，其余用项目方案。 */
function planOverrides() {
  const plan = currentPlan()
  if (!state.project) return plan
  return Object.fromEntries(Object.entries(plan).filter(([key]) => state.touched[key]))
}

/** 方案改动后，已经显示的结果不再对应当前开关：提示重新识别。 */
function markStale() {
  if (!state.task) return
  const stale = JSON.stringify(currentPlan()) !== state.resultPlan
  $('run').textContent = stale ? '按新的方式重新识别' : '识别并脱敏'
  $('summary').classList.toggle('is-stale', stale)
  if (stale) $('summary').textContent = '方式已改动，结果还是上一次的'
}

function changed(key, value) {
  state.touched[key] = true
  // 没选项目时，开关就是插件的默认方案，记住它；选了项目时只影响这一次
  if (!state.project) void moyinSaveSettings({ [key]: value })
  markStale()
}

document.querySelectorAll('#strategy button').forEach(button => button.addEventListener('click', () => {
  setStrategy(button.dataset.value)
  changed('strategy', button.dataset.value)
}))
$('strength').addEventListener('input', () => {
  setStrength(Number($('strength').value))
  changed('strength', Number($('strength').value))
})
$('llm').addEventListener('change', () => changed('useLlm', $('llm').checked))

/* ---------- 读取文本 ---------- */

async function activeText(mode) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  if (!tab?.id) return ''
  try {
    // 选中的文字可能在页面里的 iframe 中（不少对话框是这样嵌进来的），逐个框架找
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: mode !== 'page' },
      func: requested => requested === 'page' ? (document.body?.innerText || '') : (window.getSelection()?.toString() || ''),
      args: [mode],
    })
    const main = results.find(item => item.frameId === 0)?.result || ''
    return main || results.map(item => item.result).find(text => text && text.trim()) || ''
  } catch {
    return ''
  }
}

function updateSourceMeta() {
  const length = Array.from($('source').value).length
  $('sourceMeta').textContent = length ? `${length.toLocaleString()} 字${length > 100000 ? '，超过 10 万字，请分段处理' : ''}` : ''
}

$('source').addEventListener('input', updateSourceMeta)
$('readSelection').addEventListener('click', async () => {
  const text = await activeText('selection')
  if (text) { $('source').value = text; updateSourceMeta() }
  else showError('当前页面没有选中的文字')
})
$('readPage').addEventListener('click', async () => {
  const text = await activeText('page')
  if (text) { $('source').value = text.slice(0, 100000); updateSourceMeta() }
  else showError('读取不到这个页面的文字（浏览器内置页面不允许读取）')
})
$('settings').addEventListener('click', () => chrome.runtime.openOptionsPage())

function showError(message) {
  $('error').textContent = message
}

/* ---------- 识别与结果 ---------- */

function renderPreview(text, replacements, spans) {
  const preview = $('preview')
  preview.replaceChildren()
  const characters = Array.from(text)
  const pendingIds = new Set((spans || []).filter(span => span.status === 'pending' || span.conflict).map(span => span.id))
  let cursor = 0
  for (const item of [...(replacements || [])].sort((a, b) => a.out_start - b.out_start)) {
    if (item.out_start < cursor) continue
    if (item.out_start > cursor) preview.append(characters.slice(cursor, item.out_start).join(''))
    const token = document.createElement('span')
    const isMask = item.strategy === 'mask' && /^【.+】$/.test(item.replacement)
    token.className = `${isMask ? 'tok-mask' : 'tok-swap'}${pendingIds.has(item.span_id) ? ' pending' : ''}`
    token.style.setProperty('--c', MOYIN_COLORS[item.entity_type] || MOYIN_COLORS.CUSTOM)
    token.textContent = isMask ? item.replacement.slice(1, -1) : item.replacement
    token.title = `${MOYIN_LABELS[item.entity_type] || item.entity_type}${pendingIds.has(item.span_id) ? '，建议复核' : ''}`
    preview.append(token)
    cursor = item.out_end
  }
  if (cursor < characters.length) preview.append(characters.slice(cursor).join(''))
}

function renderChips(spans) {
  const counts = new Map()
  for (const span of spans) if (span.status !== 'rejected') counts.set(span.entity_type, (counts.get(span.entity_type) || 0) + 1)
  $('chips').replaceChildren(...[...counts.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => {
    const chip = document.createElement('span')
    chip.className = 'chip'
    chip.style.setProperty('--c', MOYIN_COLORS[type] || MOYIN_COLORS.CUSTOM)
    chip.innerHTML = '<i></i>'
    chip.append(`${MOYIN_LABELS[type] || type} ${count}`)
    return chip
  }))
}

function showTask(task) {
  state.task = task
  state.resultPlan = JSON.stringify(currentPlan())
  $('run').textContent = '识别并脱敏'
  $('summary').classList.remove('is-stale')
  state.automatic = task.redacted_text
  state.saved = task.final_text ?? task.redacted_text
  state.revision = task.final_revision || 0
  $('final').value = state.saved
  $('final').hidden = true
  $('preview').hidden = false
  $('edit').textContent = '修改'
  renderPreview(state.automatic, task.replacements, task.spans)
  renderChips(task.spans)
  const pending = task.summary?.pending || 0
  const total = (task.spans || []).filter(span => span.status !== 'rejected').length
  $('summary').textContent = total ? `${total} 处实体${pending ? `，${pending} 处建议复核` : ''}` : '没有发现隐私信息'
  $('saveState').textContent = ''
  if (task.moyinNotice) { showError(task.moyinNotice); state.project = null; $('project').textContent = '临时方案' }
  $('restored').hidden = true
  $('result').hidden = false
  $('result').scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

$('run').addEventListener('click', async () => {
  const text = $('source').value
  if (!text.trim()) { showError('请先输入或读取需要处理的文字'); return }
  if (state.task && $('final').value !== state.saved) await saveFinal()
  showError('')
  $('run').disabled = true
  $('run').textContent = '正在识别…'
  try {
    showTask(await moyinDetect(text, planOverrides()))
  } catch (error) {
    showError(error.message || '处理失败')
    $('run').textContent = '识别并脱敏'
  } finally {
    $('run').disabled = false
  }
})

/* ---------- 还原大模型回答 ---------- */

function restoreNote(note) {
  const options = (note.candidates || []).join('、')
  if (note.reason === 'ambiguous') return `“${note.text}”对应 ${(note.candidates || []).length} 个原词（${options}），没有换回，请自己判断`
  if (note.reason === 'conflict') return `${note.text} 在几次脱敏里指的不是同一个，按最近一次换成了“${note.chosen}”`
  return `${note.text} 在最近的脱敏记录里没有对应，保持原样`
}

function renderRestored(result) {
  const preview = $('restoredPreview')
  preview.replaceChildren()
  const characters = Array.from(result.text)
  let cursor = 0
  for (const item of [...result.items].sort((a, b) => a.start - b.start)) {
    if (item.start < cursor) continue
    if (item.start > cursor) preview.append(characters.slice(cursor, item.start).join(''))
    const mark = document.createElement('mark')
    mark.className = `tok-swap${item.check ? ' pending' : ''}`
    mark.style.setProperty('--c', MOYIN_COLORS[item.entity_type] || MOYIN_COLORS.CUSTOM)
    mark.textContent = characters.slice(item.start, item.end).join('')
    mark.title = `${MOYIN_LABELS[item.entity_type] || '实体'}：原为 ${item.replaced}${item.check ? '（按泛化词换回，请核对）' : ''}`
    preview.append(mark)
    cursor = item.end
  }
  if (cursor < characters.length) preview.append(characters.slice(cursor).join(''))
}

async function runRestore() {
  const text = $('source').value
  if (!text.trim()) { showError('请先粘贴或读取大模型的回答'); return }
  showError('')
  $('restore').disabled = true
  $('restore').textContent = '正在还原…'
  try {
    const result = await moyinRestore(text)
    state.restored = result
    renderRestored(result)
    const checks = result.items.filter(item => item.check).length
    $('restoredSummary').textContent = result.restored ? `换回 ${result.restored} 处${checks ? `，${checks} 处按泛化词换回，请核对` : ''}` : '没有找到可以换回的编号或替换词'
    $('restoredNotes').replaceChildren(...result.unresolved.map(note => {
      const item = document.createElement('li')
      item.textContent = restoreNote(note)
      return item
    }))
    $('restoredNotes').hidden = !result.unresolved.length
    $('result').hidden = true
    $('restored').hidden = false
    $('restored').scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } catch (error) {
    showError(error.message || '还原失败')
  } finally {
    $('restore').disabled = false
    $('restore').textContent = '还原回答'
  }
}

$('restore').addEventListener('click', () => void runRestore())

$('copyRestored').addEventListener('click', async () => {
  if (!state.restored) return
  try {
    await navigator.clipboard.writeText(state.restored.text)
    $('copyRestored').textContent = '已复制'
  } catch {
    $('copyRestored').textContent = '复制失败，请手动选择'
  }
  window.setTimeout(() => { $('copyRestored').textContent = '复制还原结果' }, 1400)
})

/* ---------- 修改与保存最终稿 ---------- */

async function saveFinal() {
  if (!state.task) return true
  const value = $('final').value
  if (value === state.saved) return true
  window.clearTimeout(state.saveTimer)
  $('saveState').textContent = '正在保存修改…'
  try {
    const saved = await moyinFetch(`/tasks/${encodeURIComponent(state.task.task_id)}/final-text`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: value, automatic_text: state.automatic, expected_revision: state.revision, note: '浏览器插件修改' }),
    })
    state.saved = saved.final_text
    state.revision = saved.final_revision
    $('saveState').textContent = `修改已保存（第 ${state.revision} 版）`
    return true
  } catch (error) {
    $('saveState').textContent = `没有保存：${error.message}`
    return false
  }
}

$('edit').addEventListener('click', () => {
  const editing = $('final').hidden
  $('final').hidden = !editing
  $('preview').hidden = editing
  $('edit').textContent = editing ? '完成修改' : '修改'
  if (editing) $('final').focus()
  else void saveFinal().then(() => renderPreviewFromFinal())
})

function renderPreviewFromFinal() {
  // 人工修改后不再有替换映射，预览直接显示最终稿
  if ($('final').value === state.automatic && state.task) renderPreview(state.automatic, state.task.replacements, state.task.spans)
  else $('preview').textContent = $('final').value
}

$('final').addEventListener('input', () => {
  $('saveState').textContent = '有未保存的修改'
  window.clearTimeout(state.saveTimer)
  state.saveTimer = window.setTimeout(() => { void saveFinal() }, 900)
})

$('copy').addEventListener('click', async () => {
  const value = $('final').value
  try {
    await navigator.clipboard.writeText(value)
    $('copy').textContent = '已复制'
  } catch {
    $('copy').textContent = '复制失败，请手动选择'
  }
  window.setTimeout(() => { $('copy').textContent = '复制结果' }, 1400)
})

$('download').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([$('final').value], { type: 'text/plain;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `墨隐脱敏结果-${new Date().toISOString().slice(0, 10)}.txt`
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
})

$('open').addEventListener('click', async () => {
  if (!state.task) return
  if (!(await saveFinal())) return
  const { appBase } = await moyinSettings()
  chrome.tabs.create({ url: `${appBase.replace(/\/$/, '')}/workbench?task=${encodeURIComponent(state.task.task_id)}` })
})

/* ---------- 启动 ---------- */

async function checkHealth() {
  const status = $('status')
  try {
    const [health, models] = await Promise.all([moyinFetch('/health'), moyinFetch('/models').catch(() => null)])
    const llm = models?.endpoints?.local?.enabled || models?.endpoints?.cloud?.enabled
    status.className = 'status ok'
    status.lastElementChild.textContent = health.status === 'ok' ? (llm ? '服务已连接，大模型核查可用' : '服务已连接，规则与 NER 识别') : '服务连接异常'
    if (!llm) $('llm').title = '服务端未连接大模型，开启后仍只使用规则与 NER'
  } catch {
    status.className = 'status bad'
    status.lastElementChild.textContent = '未连接服务，请先启动墨隐后端'
  }
}

async function loadProjectPlan(settings) {
  if (!settings.projectId) { $('project').textContent = '临时方案'; return }
  $('project').textContent = `项目：${settings.projectName || '读取中'}`
  try {
    const { project, missing } = await moyinProject(settings)
    if (missing) {
      await moyinSaveSettings({ projectId: '', projectName: '' })
      $('project').textContent = '临时方案'
      showError(`插件设置里选的项目${settings.projectName ? `「${settings.projectName}」` : ''}已被删除，改用临时方案`)
      return
    }
    state.project = project
    const config = project.config || {}
    // 方式和力度默认用项目的方案；这里改动只影响这一次（没碰过的项继续跟随项目）
    if (!state.touched.strategy) setStrategy(config.strategy || 'mask')
    if (!state.touched.strength) setStrength(Number(config.privacy_strength) || 2)
    if (!state.touched.useLlm && config.use_llm !== undefined) $('llm').checked = Boolean(config.use_llm)
    $('project').textContent = `项目：${project.name}`
    $('project').title = config.use_policies ? '按项目方案：不同类型的实体用不同方式；在这里换方式后，本次统一用所选方式' : '按项目方案处理；在这里临时改动只影响这一次'
  } catch {
    $('project').textContent = `项目：${settings.projectName || '已选择'}`
  }
}

async function init() {
  const settings = await moyinSettings()
  setStrategy(settings.strategy)
  setStrength(Number(settings.strength) || 2)
  $('llm').checked = Boolean(settings.useLlm)
  const session = await chrome.storage.session.get(['pendingSelection', 'pendingMode']).catch(() => ({}))
  const legacy = await chrome.storage.local.get('pendingSelection')
  const restoring = session.pendingMode === 'restore'
  // 右键菜单给的选中文字会把换行压成空格；还原时先从页面上读一次选区，保留回答的分段
  const text = (restoring && await activeText('selection')) || session.pendingSelection || legacy.pendingSelection || await activeText('selection')
  if (text) { $('source').value = text; updateSourceMeta() }
  await chrome.storage.session.remove(['pendingSelection', 'pendingMode']).catch(() => {})
  await chrome.storage.local.remove('pendingSelection')
  if (restoring && text) void runRestore()
  await chrome.action.setBadgeText({ text: '' })
  void checkHealth()
  await loadProjectPlan(settings)
}

void init()
