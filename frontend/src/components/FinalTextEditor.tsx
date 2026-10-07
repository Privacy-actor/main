import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { CheckCircle2, Copy, Diff, Redo2, Replace, RotateCcw, Save, Undo2 } from 'lucide-react'
import { copyText } from '../lib/download'
import { codePointToUnit } from '../lib/text'
import { Kbd, Spinner } from './ui'
import './FinalTextEditor.css'

export type SaveState = { kind: 'idle' | 'success' | 'error'; message: string }

interface FinalTextEditorProps {
  value: string
  automaticText: string
  savedText: string
  revision: number
  saving: boolean
  saveState: SaveState
  highlight?: { start: number; end: number; nonce: number } | null
  onChange: (value: string) => void
  onSave: (note?: string) => void
}

export function changedCharacterCount(before: string, after: string) {
  let prefix = 0
  const limit = Math.min(before.length, after.length)
  while (prefix < limit && before[prefix] === after[prefix]) prefix += 1
  let suffix = 0
  const remaining = Math.min(before.length - prefix, after.length - prefix)
  while (suffix < remaining && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix += 1
  return Math.max(before.length - prefix - suffix, after.length - prefix - suffix)
}

/** 自动稿与人工稿的差异：找出共同前后缀，中间部分分别标为删除和新增。 */
export function diffParts(automaticText: string, value: string) {
  let prefix = 0
  const limit = Math.min(automaticText.length, value.length)
  while (prefix < limit && automaticText[prefix] === value[prefix]) prefix += 1
  let suffix = 0
  const remaining = Math.min(automaticText.length - prefix, value.length - prefix)
  while (suffix < remaining && automaticText[automaticText.length - 1 - suffix] === value[value.length - 1 - suffix]) suffix += 1
  return {
    prefix: value.slice(0, prefix),
    before: automaticText.slice(prefix, automaticText.length - suffix),
    after: value.slice(prefix, value.length - suffix),
    suffix: suffix ? value.slice(value.length - suffix) : '',
  }
}

/** 最终稿编辑器：可自由修改任意位置，自动保存并留下版本记录。 */
export default function FinalTextEditor({ value, automaticText, savedText, revision, saving, saveState, highlight, onChange, onSave }: FinalTextEditorProps) {
  const [history, setHistory] = useState([value])
  const [cursor, setCursor] = useState(0)
  const [note, setNote] = useState('')
  const [replaceOpen, setReplaceOpen] = useState(false)
  const [diffOpen, setDiffOpen] = useState(false)
  const [find, setFind] = useState('')
  const [replaceWith, setReplaceWith] = useState('')
  const [copied, setCopied] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)
  const dirty = value !== savedText
  const edited = value !== automaticText
  const changed = useMemo(() => changedCharacterCount(automaticText, value), [automaticText, value])
  const diff = useMemo(() => diffParts(automaticText, value), [automaticText, value])
  const matches = find ? value.split(find).length - 1 : 0

  useEffect(() => {
    if (!dirty || saving || saveState.kind === 'error') return
    const timer = window.setTimeout(() => onSave(note), 1600)
    return () => window.clearTimeout(timer)
    // 自动保存只跟随文本变化；保存失败后等待再次编辑或手动重试
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, dirty, saving, note, saveState.kind])

  useEffect(() => {
    if (!highlight || !area.current) return
    const element = area.current
    setDiffOpen(false)
    const from = codePointToUnit(value, highlight.start)
    const to = codePointToUnit(value, highlight.end)
    element.focus()
    element.setSelectionRange(from, to)
    // 让选区滚动到可见位置
    const before = value.slice(0, from)
    const lines = before.split('\n').length
    element.scrollTop = Math.max(0, (lines - 3) * 28)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.nonce])

  function commit(next: string) {
    if (next === value) return
    const nextHistory = [...history.slice(0, cursor + 1), next].slice(-100)
    setHistory(nextHistory)
    setCursor(nextHistory.length - 1)
    onChange(next)
  }
  function undo() { if (cursor > 0) { setCursor(cursor - 1); onChange(history[cursor - 1]) } }
  function redo() { if (cursor < history.length - 1) { setCursor(cursor + 1); onChange(history[cursor + 1]) } }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const command = event.ctrlKey || event.metaKey
    const key = event.key.toLowerCase()
    if (command && key === 's') { event.preventDefault(); if (dirty && !saving) onSave(note) }
    else if (command && key === 'z' && !event.shiftKey) { event.preventDefault(); undo() }
    else if ((command && key === 'y') || (command && event.shiftKey && key === 'z')) { event.preventDefault(); redo() }
    else if (command && key === 'h') { event.preventDefault(); setReplaceOpen(true) }
  }

  return <div className="final-editor">
    <div className="final-toolbar" role="toolbar" aria-label="最终稿工具">
      <button type="button" className="btn btn-quiet btn-sm" disabled={cursor === 0} onClick={undo} title="撤销 Ctrl+Z"><Undo2 size={14}/>撤销</button>
      <button type="button" className="btn btn-quiet btn-sm" disabled={cursor >= history.length - 1} onClick={redo} title="重做 Ctrl+Y"><Redo2 size={14}/>重做</button>
      <button type="button" className={`btn btn-quiet btn-sm${replaceOpen ? ' is-on' : ''}`} aria-pressed={replaceOpen} onClick={() => setReplaceOpen(open => !open)} title="查找替换 Ctrl+H"><Replace size={14}/>查找替换</button>
      <button type="button" className={`btn btn-quiet btn-sm${diffOpen ? ' is-on' : ''}`} aria-pressed={diffOpen} disabled={!edited} onClick={() => setDiffOpen(open => !open)} title="与自动生成的结果对比"><Diff size={14}/>对比</button>
      <button type="button" className="btn btn-quiet btn-sm" disabled={!edited} onClick={() => commit(automaticText)}><RotateCcw size={14}/>恢复自动稿</button>
      <span className="final-toolbar-spacer"/>
      <button type="button" className="btn btn-quiet btn-sm" onClick={async () => { if (await copyText(value)) { setCopied(true); window.setTimeout(() => setCopied(false), 1400) } }}><Copy size={14}/>{copied ? '已复制' : '复制'}</button>
    </div>
    {replaceOpen && <div className="final-replace">
      <input className="input input-sm" value={find} onChange={event => setFind(event.target.value)} placeholder="查找" aria-label="查找内容" autoFocus/>
      <input className="input input-sm" value={replaceWith} onChange={event => setReplaceWith(event.target.value)} placeholder="替换为（留空即删除）" aria-label="替换为"/>
      <button type="button" className="btn btn-sm" disabled={!matches} onClick={() => commit(value.split(find).join(replaceWith))}>全部替换{matches ? ` ${matches} 处` : ''}</button>
    </div>}
    {diffOpen && edited ? <div className="final-diff doc-text is-compact" aria-label="自动稿与最终稿的差异">
      <span>{diff.prefix}</span>{diff.before && <del>{diff.before}</del>}{diff.after && <ins>{diff.after}</ins>}<span>{diff.suffix}</span>
    </div> : <textarea ref={area} className="final-area" value={value} maxLength={100000} spellCheck={false} aria-label="最终稿"
      onChange={event => commit(event.target.value)} onKeyDown={onKeyDown}/>}
    <div className="final-foot">
      <span className="final-metrics num">{Array.from(value).length.toLocaleString()} 字{edited ? `，人工修改约 ${changed.toLocaleString()} 字` : '，与自动稿一致'}</span>
      <input className="input input-sm final-note" value={note} maxLength={500} onChange={event => setNote(event.target.value)} placeholder="修订说明（可选）" aria-label="修订说明"/>
      <span className={`final-state is-${saveState.kind}${dirty ? ' is-dirty' : ''}`} aria-live="polite">
        {saving ? <><Spinner size={13}/>保存中</> : saveState.kind === 'error' ? saveState.message : dirty ? '有未保存的修改' : <><CheckCircle2 size={13}/>已保存 v{revision}</>}
      </span>
      <button type="button" className="btn btn-sm" disabled={!dirty || saving} onClick={() => onSave(note)}><Save size={14}/>保存<Kbd>Ctrl S</Kbd></button>
    </div>
  </div>
}
