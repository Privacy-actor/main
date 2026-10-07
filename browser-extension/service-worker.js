importScripts('shared.js')

const MENU_REPLACE = 'moyin-redact-replace'
const MENU_SELECTION = 'moyin-redact-selection'
// 每个标签页同一时间只处理一段，连按快捷键不会重复插入
const busyTabs = new Set()

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_REPLACE, title: '用墨隐脱敏并替换', contexts: ['editable'] })
    chrome.contextMenus.create({ id: MENU_SELECTION, title: '用墨隐脱敏选中文本', contexts: ['selection'] })
  })
})

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab?.id) return
  if (info.menuItemId === MENU_REPLACE) void redactInPage(tab.id, info.frameId ?? 0)
  else if (info.menuItemId === MENU_SELECTION && info.selectionText) void openPopupWith(info.selectionText)
})

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== 'redact-selection') return
  const target = tab?.id ? tab : (await chrome.tabs.query({ active: true, currentWindow: true }))[0]
  if (target?.id) void redactInPage(target.id)
})

/** 选中的是网页上的普通文字：交给弹窗处理。暂存在会话存储里，浏览器关闭即清除。 */
async function openPopupWith(text) {
  await chrome.storage.session.set({ pendingSelection: text })
  try { await chrome.action.openPopup() } catch { await chrome.action.setBadgeText({ text: '1' }) }
}

/** 浏览器内置页面（设置、扩展商店等）不允许插件读取，在插件图标上提示一下。 */
async function flagRestricted(tabId) {
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color: '#b3261e' })
    await chrome.action.setBadgeText({ tabId, text: '!' })
    await chrome.action.setTitle({ tabId, title: '墨隐：这个页面不允许插件读取内容，请在普通网页里使用' })
    setTimeout(() => {
      chrome.action.setBadgeText({ tabId, text: '' }).catch(() => {})
      chrome.action.setTitle({ tabId, title: '墨隐' }).catch(() => {})
    }, 4000)
  } catch { /* 标签页已关闭 */ }
}

async function inject(target) {
  await chrome.scripting.executeScript({ target, files: ['page-tools.js'] })
}

async function run(target, func, args = []) {
  const [{ result } = {}] = await chrome.scripting.executeScript({ target, func, args })
  return result
}

const pageToast = (target, message, tone = 'info', link = '') =>
  run(target, (text, kind, href) => window.__moyinTools?.toast(text, kind, href), [message, tone, link]).catch(() => {})

/** 快捷键没有告诉我们是哪个框架：找到获得焦点、且焦点不在子框架上的那个（对话框常放在 iframe 里）。 */
async function focusedFrame(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['page-tools.js'] })
    const results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: () => window.__moyinTools?.probe() })
    const usable = results.filter(item => item.result)
    const pick = usable.find(item => item.result.focused && !item.result.inChildFrame && (item.result.editable || item.result.hasSelection))
      || usable.find(item => item.result.focused && !item.result.inChildFrame)
      || usable.find(item => item.result.hasSelection)
    return pick ? pick.frameId : 0
  } catch {
    return 0
  }
}

async function redactInPage(tabId, frameId) {
  // 先占住这个标签页，再做任何等待：连按两次时第二次直接提示，不会生成两个任务
  if (busyTabs.has(tabId)) {
    await pageToast({ tabId, frameIds: [frameId ?? 0] }, '上一段还在处理，请稍候')
    return
  }
  busyTabs.add(tabId)
  try {
    await redactWhileBusy(tabId, frameId)
  } finally {
    busyTabs.delete(tabId)
  }
}

async function redactWhileBusy(tabId, frameId) {
  if (frameId === undefined) frameId = await focusedFrame(tabId)
  const target = { tabId, frameIds: [frameId] }
  try {
    await inject(target)
  } catch {
    await flagRestricted(tabId)
    return
  }
  const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  const grabbed = await run(target, key => window.__moyinTools.grab(key), [token]).catch(() => null)
  if (!grabbed?.text?.trim()) {
    await pageToast(target, '先选中要脱敏的文字，或把光标放在要处理的输入框里')
    return
  }
  if (!grabbed.editable) {
    await run(target, key => window.__moyinTools.cancel(key), [token]).catch(() => {})
    await openPopupWith(grabbed.text)
    return
  }
  try {
    await pageToast(target, '正在脱敏，请稍候…', 'busy')
    const task = await moyinDetect(grabbed.text)
    const output = task.final_text ?? task.redacted_text
    const count = (task.replacements || []).length
    const pending = task.summary?.pending || 0
    const notice = task.moyinNotice ? `${task.moyinNotice}。` : ''
    if (!count) {
      await run(target, key => window.__moyinTools.cancel(key), [token]).catch(() => {})
      await pageToast(target, `${notice}没有发现需要隐去的信息，原文未改动`)
      return
    }
    const outcome = await run(target, (key, text) => window.__moyinTools.apply(key, text), [token, output])
    const { appBase } = await moyinSettings()
    const link = pending ? `${appBase.replace(/\/$/, '')}/workbench?task=${encodeURIComponent(task.task_id)}` : ''
    if (outcome?.status === 'replaced') {
      await pageToast(target, `${notice}已隐去 ${count} 处隐私信息${pending ? `，其中 ${pending} 处建议复核` : ''}，按 Ctrl+Z 可撤销`, 'info', link)
      await chrome.storage.local.set({ lastTask: { id: task.task_id, at: Date.now(), count } })
    } else if (outcome?.status === 'copied') {
      await pageToast(target, '这个输入框不支持直接写入，脱敏结果已复制，请按 Ctrl+V 粘贴替换', 'info', link)
    } else if (outcome?.status === 'changed') {
      await pageToast(target, '处理期间输入框里的内容有改动，没有替换。请重新选中后再试', 'error')
    } else {
      await pageToast(target, '没能写入这个输入框，也无法复制。请打开插件弹窗处理这段文字', 'error')
    }
  } catch (error) {
    await run(target, key => window.__moyinTools?.cancel(key), [token]).catch(() => {})
    await pageToast(target, error.message || '脱敏失败', 'error')
  }
}
