const api = document.getElementById('api'), app = document.getElementById('app'), msg = document.getElementById('msg')
chrome.storage.sync.get({ apiBase: 'http://127.0.0.1:8000/api/v1', appBase: 'http://127.0.0.1:5173' }).then(v => { api.value = v.apiBase; app.value = v.appBase })
document.getElementById('save').onclick = async () => {
  const apiBase = api.value.replace(/\/$/, ''), appBase = app.value.replace(/\/$/, '')
  try {
    const origins = [...new Set([new URL(apiBase).origin + '/*', new URL(appBase).origin + '/*'])]
    const granted = await chrome.permissions.request({ origins })
    if (!granted) throw new Error('未授予服务器访问权限')
    await chrome.storage.sync.set({ apiBase, appBase })
    msg.textContent = ' 已保存'
  } catch (error) { msg.textContent = ' ' + (error.message || '地址无效') }
  setTimeout(() => msg.textContent = '', 1800)
}
