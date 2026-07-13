import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Clipboard, Download, FilePenLine, Redo2, Replace, RotateCcw, Save, Undo2 } from 'lucide-react'

type SaveState = { kind: 'idle' | 'success' | 'error'; message: string }

interface FinalTextEditorProps {
  value: string
  automaticText: string
  savedText: string
  revision: number
  saving: boolean
  saveState: SaveState
  onChange: (value: string) => void
  onSave: (note?: string) => void
}

function changedCharacterCount(before: string, after: string) {
  let prefix = 0
  const limit = Math.min(before.length, after.length)
  while (prefix < limit && before[prefix] === after[prefix]) prefix += 1
  let suffix = 0
  const remaining = Math.min(before.length - prefix, after.length - prefix)
  while (suffix < remaining && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix += 1
  return Math.max(before.length - prefix - suffix, after.length - prefix - suffix)
}

export default function FinalTextEditor({ value, automaticText, savedText, revision, saving, saveState, onChange, onSave }: FinalTextEditorProps) {
  const [history, setHistory] = useState([value])
  const [cursor, setCursor] = useState(0)
  const [copied, setCopied] = useState(false)
  const [note, setNote] = useState('')
  const [replaceFrom, setReplaceFrom] = useState('')
  const [replaceTo, setReplaceTo] = useState('')
  const dirty = value !== savedText
  const manuallyEdited = value !== automaticText
  const changedCharacters = useMemo(() => changedCharacterCount(automaticText, value), [automaticText, value])
  const diff = useMemo(() => {
    let prefix = 0
    const limit = Math.min(automaticText.length, value.length)
    while (prefix < limit && automaticText[prefix] === value[prefix]) prefix += 1
    let suffix = 0
    const remaining = Math.min(automaticText.length - prefix, value.length - prefix)
    while (suffix < remaining && automaticText[automaticText.length - 1 - suffix] === value[value.length - 1 - suffix]) suffix += 1
    return {
      prefix: value.slice(0, prefix),
      before: automaticText.slice(prefix, automaticText.length - suffix || automaticText.length),
      after: value.slice(prefix, value.length - suffix || value.length),
      suffix: suffix ? value.slice(value.length - suffix) : '',
    }
  }, [automaticText, value])

  useEffect(() => {
    if (!dirty || saving) return
    const timer = window.setTimeout(() => onSave(note), 1600)
    return () => window.clearTimeout(timer)
    // Auto-save is intentionally keyed to text changes; a failed save waits for another edit or manual retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, dirty, saving, note])

  function commit(next: string) {
    if (next === value) return
    const nextHistory = [...history.slice(0, cursor + 1), next].slice(-80)
    setHistory(nextHistory)
    setCursor(nextHistory.length - 1)
    onChange(next)
  }

  function undo() {
    if (cursor === 0) return
    const next = cursor - 1
    setCursor(next)
    onChange(history[next])
  }

  function redo() {
    if (cursor >= history.length - 1) return
    const next = cursor + 1
    setCursor(next)
    onChange(history[next])
  }

  async function copy() {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  function download() {
    const url = URL.createObjectURL(new Blob([value], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'privshield-final.txt'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  function replaceAll() {
    if (!replaceFrom) return
    commit(value.split(replaceFrom).join(replaceTo))
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    const command = event.ctrlKey || event.metaKey
    if (command && event.key.toLowerCase() === 's') {
      event.preventDefault()
      if (dirty && !saving) onSave(note)
    } else if (command && event.key.toLowerCase() === 'z' && !event.shiftKey) {
      event.preventDefault()
      undo()
    } else if ((command && event.key.toLowerCase() === 'y') || (command && event.shiftKey && event.key.toLowerCase() === 'z')) {
      event.preventDefault()
      redo()
    }
  }

  return <section className="panel final-editor-panel">
    <div className="editor-heading">
      <div className="editor-title"><span className="editor-icon"><FilePenLine /></span><div><small>FINAL TEXT / HUMAN REVISION</small><h2>最终文本编辑器</h2><p>可自由恢复误判内容、补充遗漏隐私，或直接修改任意替换词。</p></div></div>
      <div className="editor-badges">
        <span className={manuallyEdited ? 'manual' : 'automatic'}>{manuallyEdited ? '人工修订稿' : '自动结果'}</span>
        <span className={dirty ? 'dirty' : 'saved'}>{dirty ? '存在未保存修改' : `已保存 · v${revision}`}</span>
      </div>
    </div>
    <div className="editor-toolbar" role="toolbar" aria-label="文本编辑工具栏">
      <div className="editor-tool-group">
        <button type="button" disabled={cursor === 0} onClick={undo} title="撤销 Ctrl+Z"><Undo2 />撤销</button>
        <button type="button" disabled={cursor >= history.length - 1} onClick={redo} title="重做 Ctrl+Y"><Redo2 />重做</button>
        <button type="button" disabled={!manuallyEdited} onClick={() => commit(automaticText)} title="恢复自动脱敏结果"><RotateCcw />恢复自动稿</button>
      </div>
      <div className="editor-tool-group right">
        <button type="button" onClick={copy}><Clipboard />{copied ? '已复制' : '复制'}</button>
        <button type="button" onClick={download}><Download />导出 TXT</button>
      </div>
    </div>
    <div className="editor-replace"><Replace/><label><span>查找</span><input value={replaceFrom} onChange={event => setReplaceFrom(event.target.value)} placeholder="要替换的文本"/></label><label><span>替换为</span><input value={replaceTo} onChange={event => setReplaceTo(event.target.value)} placeholder="自定义替换词（可留空删除）"/></label><button type="button" disabled={!replaceFrom || !value.includes(replaceFrom)} onClick={replaceAll}>全部替换</button></div>
    <label className="editor-canvas">
      <span className="sr-only">人工修订后的最终文本</span>
      <textarea
        value={value}
        maxLength={100000}
        spellCheck
        onChange={event => commit(event.target.value)}
        onKeyDown={handleKeyDown}
        aria-describedby="editor-help"
      />
    </label>
    {manuallyEdited && <details className="editor-diff"><summary>查看自动稿与人工稿差异高亮</summary><div><span>{diff.prefix}</span>{diff.before && <del>{diff.before}</del>}{diff.after && <ins>{diff.after}</ins>}<span>{diff.suffix}</span></div></details>}
    <div className="editor-footer">
      <div className="editor-metrics">
        <span><b>{value.length.toLocaleString()}</b> 字符</span>
        <span><b>{value.split(/\r?\n/).length}</b> 行</span>
        <span><b>{changedCharacters.toLocaleString()}</b> 字符位于人工修改区</span>
      </div>
      <label className="revision-note"><span>修订说明</span><input value={note} maxLength={500} onChange={event => setNote(event.target.value)} placeholder="可选，例如：恢复机构名称并补充遗漏编号" /></label>
      <button className="btn primary editor-save" type="button" disabled={!dirty || saving} onClick={() => onSave(note)}>
        {saving ? <span className="editor-saving" /> : dirty ? <Save /> : <CheckCircle2 />}{saving ? '保存中' : dirty ? '保存最终稿' : '已保存'}
      </button>
    </div>
    <div id="editor-help" className={`editor-status ${saveState.kind}`} aria-live="polite">
      <span>{saveState.message || '快捷键：Ctrl+S 保存，Ctrl+Z 撤销，Ctrl+Y 重做。保存后会写入任务快照和审计记录。'}</span>
      <code>revision {revision}</code>
    </div>
  </section>
}
