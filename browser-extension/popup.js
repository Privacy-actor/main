const $ = id => document.getElementById(id)
let currentTask = ''
let automaticText = ''
let savedText = ''
let currentRevision = 0
let saveTimer = 0

async function settings() {
  const stored = await chrome.storage.sync.get({ apiBase: 'http://127.0.0.1:8000/api/v1', appBase: 'http://127.0.0.1:5173' })
  return stored
}

async function activeText(mode = 'selection') {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  if (!tab?.id) return ''
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: requested => requested === 'page' ? document.body?.innerText || '' : window.getSelection()?.toString() || '', args: [mode] })
  return result || ''
}

async function loadSelection() {
  const pending = await chrome.storage.local.get('pendingSelection')
  const text = pending.pendingSelection || await activeText('selection')
  if (text) $('source').value = text
  await chrome.storage.local.remove('pendingSelection')
  await chrome.action.setBadgeText({ text: '' })
}

async function checkHealth() {
  const { apiBase } = await settings()
  try {
    const response = await fetch(`${apiBase}/health`)
    if (!response.ok) throw new Error()
    const health = await response.json()
    $('status').textContent = health.mode === 'llm' ? '本地服务在线 · 14B 已启用' : '本地服务在线 · 轻量模式'
    $('status').className = 'status ok'
  } catch {
    $('status').textContent = '未连接本地服务，请先启动 PrivShield 后端'
    $('status').className = 'status warn'
  }
}

$('selection').onclick = async () => { $('source').value = await activeText('selection') }
$('page').onclick = async () => { $('source').value = (await activeText('page')).slice(0, 100000) }
$('settings').onclick = () => chrome.runtime.openOptionsPage()
async function saveFinalText(silent = false) {
  const value = $('result').value
  if (!currentTask || value === savedText) return true
  window.clearTimeout(saveTimer)
  $('saveFinal').disabled = true
  $('saveStatus').textContent = '正在保存人工修订…'
  try {
    const { apiBase } = await settings()
    const response = await fetch(`${apiBase}/tasks/${encodeURIComponent(currentTask)}/final-text`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: value, automatic_text: automaticText, expected_revision: currentRevision, note: '浏览器插件人工修订' }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
    savedText = payload.final_text
    currentRevision = payload.final_revision
    $('saveStatus').textContent = `人工最终稿已保存 · v${currentRevision}`
    return true
  } catch (error) {
    $('saveStatus').textContent = '保存失败，请重试或进入完整工作台'
    if (!silent) $('error').textContent = error.message || '最终稿保存失败'
    return false
  } finally { $('saveFinal').disabled = false }
}

$('result').addEventListener('input', () => {
  $('saveStatus').textContent = '存在未保存的人工修改'
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => saveFinalText(true), 1000)
})
$('saveFinal').onclick = () => saveFinalText(false)

$('run').onclick = async () => {
  if (currentTask && $('result').value !== savedText && !window.confirm('当前人工修订尚未保存，重新检测会丢失修改。是否继续？')) return
  const text = $('source').value.trim()
  if (!text) { $('error').textContent = '请先输入或读取文本。'; return }
  $('run').disabled = true; $('run').textContent = '处理中…'; $('error').textContent = ''
  try {
    const { apiBase } = await settings()
    const response = await fetch(`${apiBase}/detect`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, strategy: $('strategy').value, privacy_strength: Number($('strength').value), use_llm: $('llm').checked, language: 'auto', risk_level: 'strict' }) })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
    currentTask = payload.task_id
    automaticText = payload.redacted_text
    savedText = payload.final_text || payload.redacted_text
    currentRevision = payload.final_revision || 0
    $('result').value = savedText
    $('saveStatus').textContent = `自动结果已保存 · v${currentRevision}，可继续人工修订`
    $('summary').textContent = `${payload.summary.total} 个实体 · ${payload.summary.pending} 个待复核`
    $('resultCard').hidden = false
  } catch (error) { $('error').textContent = error.message || '处理失败' }
  finally { $('run').disabled = false; $('run').textContent = '识别并脱敏' }
}
$('copy').onclick = async () => { await navigator.clipboard.writeText($('result').value); $('copy').textContent = '已复制'; setTimeout(() => $('copy').textContent = '复制结果', 1200) }
$('download').onclick = () => { const url = URL.createObjectURL(new Blob([$('result').value], { type: 'text/plain;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = 'privshield-browser-result.txt'; a.click(); URL.revokeObjectURL(url) }
$('full').onclick = async () => { if (!currentTask) return; const saved = await saveFinalText(false); if (!saved) return; const { appBase } = await settings(); chrome.tabs.create({ url: `${appBase}/workbench?task=${encodeURIComponent(currentTask)}` }) }

loadSelection(); checkHealth()
