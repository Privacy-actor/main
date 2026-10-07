/* 墨隐插件：注入到网页里的工具（在插件的隔离环境中运行，网页脚本访问不到）。
 * 后台先注入本文件，再调用 window.__moyinTools 的方法：读取选中的文字并记下位置、
 * 在原位置写回脱敏结果（写回前核对内容没被改动）、显示提示条。 */
(() => {
  if (window.__moyinTools) return

  const TOAST_STYLE = `
    .toast { position: fixed; left: 50%; bottom: 28px; z-index: 2147483647; transform: translateX(-50%);
      display: flex; align-items: center; gap: 12px; max-width: min(560px, calc(100vw - 32px)); padding: 10px 14px 10px 10px;
      border-radius: 10px; background: #1b2333; color: #fff; box-shadow: 0 18px 48px -12px rgba(27,35,51,.45);
      font: 13.5px/1.5 "PingFang SC","Microsoft YaHei UI","Noto Sans SC",sans-serif; animation: rise .2s ease-out; }
    .toast.is-error { background: #4b1d1a; }
    .seal { display: grid; place-items: center; flex: none; width: 24px; height: 24px; border-radius: 4px; background: #fff; color: #1b2333;
      font: 700 14px/1 "Songti SC","STSong","SimSun","Noto Serif SC",serif; }
    .is-busy .seal { animation: breathe 1.2s ease-in-out infinite; }
    span { flex: 1; }
    a { flex: none; color: #9fb0ff; text-decoration: none; font-weight: 500; }
    a:hover { text-decoration: underline; }
    @keyframes rise { from { opacity: 0; transform: translate(-50%, 8px); } }
    @keyframes breathe { 50% { opacity: .45; } }
    @media (prefers-reduced-motion: reduce) { .toast, .is-busy .seal { animation: none; } }`

  let toastTimer = 0

  /** 页面底部的提示条。tone：info、busy（处理中，不自动消失）、error。 */
  function toast(message, tone = 'info', link = '') {
    document.querySelectorAll('[data-moyin-toast]').forEach(node => node.remove())
    window.clearTimeout(toastTimer)
    const host = document.createElement('div')
    host.setAttribute('data-moyin-toast', '')
    const shadow = host.attachShadow({ mode: 'open' })
    shadow.innerHTML = `<style>${TOAST_STYLE}</style><div class="toast${tone === 'error' ? ' is-error' : tone === 'busy' ? ' is-busy' : ''}" role="status"><b class="seal">隐</b><span></span></div>`
    shadow.querySelector('span').textContent = message
    if (link) {
      const anchor = document.createElement('a')
      anchor.href = link
      anchor.target = '_blank'
      anchor.rel = 'noopener'
      anchor.textContent = '在工作台复核'
      shadow.querySelector('.toast').appendChild(anchor)
    }
    document.documentElement.appendChild(host)
    if (tone !== 'busy') toastTimer = window.setTimeout(() => host.remove(), 6500)
  }

  /** 真正获得焦点的元素：进入 Shadow DOM 里的输入框。 */
  function focusedElement() {
    let active = document.activeElement
    while (active && active.shadowRoot && active.shadowRoot.activeElement) active = active.shadowRoot.activeElement
    return active
  }

  function isTextField(element) {
    return Boolean(element) && (element.tagName === 'TEXTAREA' || (element.tagName === 'INPUT' && /^(text|search|email|url|tel|)$/i.test(element.type || '')))
  }

  function selectionFor(element) {
    const root = element && element.getRootNode ? element.getRootNode() : document
    return (root && root !== document && typeof root.getSelection === 'function' ? root.getSelection() : null) || window.getSelection()
  }

  function editableHost(node) {
    let element = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)
    while (element && element.parentElement && element.parentElement.isContentEditable) element = element.parentElement
    return element && element.isContentEditable ? element : null
  }

  let pending = null

  /** 读取要处理的文字：输入框里的选中部分，没有选中时取整个输入框；网页上选中的文字。记下位置备用。 */
  function grab(token) {
    const active = focusedElement()
    if (isTextField(active)) {
      let start = active.selectionStart ?? 0
      let end = active.selectionEnd ?? 0
      if (start === end) { start = 0; end = active.value.length; active.setSelectionRange(start, end) }
      const text = active.value.slice(start, end)
      pending = { token, kind: 'field', element: active, start, end, text }
      return { text, editable: true }
    }
    const selection = selectionFor(active)
    let text = selection ? selection.toString() : ''
    let range = selection && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null
    let host = range ? editableHost(range.commonAncestorContainer) : null
    if (!text && active && active.isContentEditable && selection) {
      host = editableHost(active)
      range = document.createRange()
      range.selectNodeContents(host)
      selection.removeAllRanges()
      selection.addRange(range)
      text = selection.toString()
      range = range.cloneRange()
    }
    pending = range ? { token, kind: 'range', range, host, root: active && active.getRootNode ? active.getRootNode() : document, text } : null
    return { text, editable: Boolean(host) }
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true } catch { /* 页面没有焦点时会失败，改用旧方法 */ }
    const area = document.createElement('textarea')
    area.value = text
    area.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0'
    document.documentElement.appendChild(area)
    area.select()
    let ok = false
    try { ok = document.execCommand('copy') } catch { ok = false }
    area.remove()
    return ok
  }

  /**
   * 在原来的位置写回脱敏结果。写回前核对：输入框还在、原来那段文字还在原位置。
   * 在等待结果期间用户改了内容或挪了光标，也只替换当初选中的那一段；内容已变就不动原文，返回 changed。
   */
  async function apply(token, redacted) {
    const target = pending
    if (!target || target.token !== token) return { status: 'stale' }
    pending = null
    if (target.kind === 'field') {
      const element = target.element
      if (!element.isConnected || element.value.slice(target.start, target.end) !== target.text) return { status: 'changed' }
      element.focus()
      element.setSelectionRange(target.start, target.end)
      let done = false
      try { done = document.execCommand('insertText', false, redacted) } catch { done = false }
      if (!done || element.value.slice(target.start, target.start + redacted.length) !== redacted) {
        element.setRangeText(redacted, target.start, target.end, 'end')
        element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertReplacementText', data: redacted }))
      }
      return { status: 'replaced' }
    }
    const host = target.host
    if (!host || !host.isConnected) return { status: 'changed' }
    host.focus({ preventScroll: true })
    const selection = selectionFor(host)
    selection.removeAllRanges()
    selection.addRange(target.range)
    if (selection.toString() !== target.text) return { status: 'changed' }
    // 段落式编辑器（<p>）里，选中文字的段落之间是两个换行；按原样插入会多出空段落，这里还原成一个
    const paragraphs = Boolean(target.range.cloneContents().querySelector && target.range.cloneContents().querySelector('p'))
    const value = paragraphs ? redacted.replace(/\n\n/g, '\n') : redacted
    let done = false
    try { done = document.execCommand('insertText', false, value) } catch { done = false }
    if (done) return { status: 'replaced' }
    return { status: (await copy(redacted)) ? 'copied' : 'failed' }
  }

  function cancel(token) {
    if (pending && pending.token === token) pending = null
  }

  /** 当前框架是否是用户正在操作的那个（快捷键没有告诉后台是哪个框架时用）。 */
  function probe() {
    const active = focusedElement()
    const selection = selectionFor(active)
    return {
      focused: document.hasFocus(),
      inChildFrame: Boolean(active && (active.tagName === 'IFRAME' || active.tagName === 'FRAME')),
      hasSelection: Boolean(selection && selection.toString().trim()),
      editable: isTextField(active) || Boolean(active && active.isContentEditable),
    }
  }

  window.__moyinTools = { grab, apply, cancel, toast, probe }
})()
