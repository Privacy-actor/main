import { useMemo, useState, type ReactNode } from 'react'
import { Copy, PencilLine } from 'lucide-react'
import { api } from '../api'
import { copyText } from '../lib/download'
import { ENTITY_LABEL, entityStyle } from '../lib/entities'
import { useToast } from '../hooks/Toast'
import { Kbd, Spinner } from './ui'
import type { RestoreNote, RestoreResult } from '../types'
import './RestorePanel.css'

interface RestorePanelProps {
  taskId: string
  hidden?: boolean
}

function describeNote(note: RestoreNote) {
  const options = (note.candidates || []).join('、')
  if (note.reason === 'ambiguous') return `“${note.text}”对应 ${note.candidates?.length || 0} 个原词（${options}），没有换回，请自己判断`
  if (note.reason === 'conflict') return `${note.text} 在几次脱敏里指的不是同一个，按最近一次换成了“${note.chosen}”（其他：${(note.candidates || []).filter(item => item !== note.chosen).join('、')}）`
  return `${note.text} 在这次任务里没有对应的原文，保持原样`
}

/**
 * 还原大模型回答：把回答里的【PERSON-001】、替换词换回这次任务的原文。
 * 只在本机处理，回答和还原结果都不保存；切换页签时保留在页面上，换任务时清空。
 */
export default function RestorePanel({ taskId, hidden }: RestorePanelProps) {
  const toast = useToast()
  const [answer, setAnswer] = useState('')
  const [result, setResult] = useState<RestoreResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function restore() {
    if (!answer.trim() || busy) return
    setBusy(true)
    try {
      setResult(await api.restore(answer, [taskId]))
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : '还原失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    if (!result) return
    const ok = await copyText(result.text)
    toast(ok ? '已复制还原结果' : '复制失败，请手动选择文字复制', ok ? 'success' : 'error')
  }

  const nodes = useMemo(() => {
    if (!result) return []
    const characters = Array.from(result.text)
    const parts: ReactNode[] = []
    let cursor = 0
    for (const item of [...result.items].sort((a, b) => a.start - b.start)) {
      if (item.start < cursor) continue
      if (item.start > cursor) parts.push(characters.slice(cursor, item.start).join(''))
      const label = `${ENTITY_LABEL[item.entity_type] || '实体'}：原为 ${item.replaced}${item.check ? '（按泛化词换回，请核对）' : ''}`
      parts.push(<mark key={`${item.start}-${item.end}`} className={`ent restore-ent${item.check ? ' is-check' : ''}`} style={entityStyle(item.entity_type)} title={label} aria-label={label}>
        {characters.slice(item.start, item.end).join('')}
      </mark>)
      cursor = item.end
    }
    if (cursor < characters.length) parts.push(characters.slice(cursor).join(''))
    return parts
  }, [result])

  const checks = result?.items.filter(item => item.check).length || 0

  return <div className="restore" hidden={hidden}>
    {!result ? <>
      <textarea className="restore-area" value={answer} spellCheck={false} aria-label="大模型的回答"
        placeholder={'把大模型的回答粘贴到这里。\n回答里的【PERSON-001】、差分隐私替换词会换回这次任务的原文；只在本机处理，不保存。'}
        onChange={event => setAnswer(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void restore() } }}/>
      <div className="restore-foot">
        <span className="restore-hint">泛化词只在唯一对应一个原词时换回；大模型改写过的称呼换不回</span>
        <button type="button" className="btn btn-primary btn-sm" disabled={!answer.trim() || busy} onClick={() => void restore()}>
          {busy ? <Spinner size={13}/> : null}还原<Kbd>Ctrl Enter</Kbd>
        </button>
      </div>
    </> : <>
      <div className="restore-summary" role="status">
        <p><strong>{result.restored ? `换回 ${result.restored} 处` : '回答里没有找到这次任务的编号或替换词'}</strong>
          {checks > 0 && <span>，其中 {checks} 处按泛化词换回（虚线标出），请核对</span>}</p>
        {result.unresolved.length > 0 && <ul>{result.unresolved.map(note => <li key={`${note.reason}-${note.text}`}>{describeNote(note)}</li>)}</ul>}
      </div>
      <div className="restore-body"><div className="doc-text">{nodes}</div></div>
      <div className="restore-foot">
        <button type="button" className="btn btn-sm" onClick={() => setResult(null)}><PencilLine size={14}/>修改回答</button>
        <button type="button" className="btn btn-sm btn-quiet" onClick={() => { setResult(null); setAnswer('') }}>清空</button>
        <button type="button" className="btn btn-primary btn-sm restore-copy" onClick={() => void copy()}><Copy size={14}/>复制还原结果</button>
      </div>
    </>}
  </div>
}
