chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: 'privshield-selection', title: '用 PrivShield 脱敏选中文本', contexts: ['selection'] })
})

chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== 'privshield-selection' || !info.selectionText) return
  await chrome.storage.local.set({ pendingSelection: info.selectionText })
  try { await chrome.action.openPopup() } catch { await chrome.action.setBadgeText({ text: '1' }) }
})
