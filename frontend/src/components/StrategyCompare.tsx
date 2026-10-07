import { useCallback, useEffect, useState } from 'react'
import { Check, Shuffle } from 'lucide-react'
import { api } from '../api'
import { EPSILON, STRATEGY_HINT, STRATEGY_LABEL, STRENGTH_LABEL } from '../lib/entities'
import type { Replacement, Span, Strategy } from '../types'
import RedactedText from './RedactedText'
import { Dialog, Segmented, Spinner } from './ui'
import './StrategyCompare.css'

interface StrategyCompareProps {
  open: boolean
  text: string
  spans: Span[]
  current: Strategy | null
  strength: number
  riskLevel: 'standard' | 'strict'
  busy?: boolean
  onClose: () => void
  onApply: (strategy: Strategy, strength: number) => void
}

type Result = { text: string; replacements: Replacement[] } | { error: string }

const ORDER: Strategy[] = ['mask', 'pseudonymize', 'generalize']

function parameter(strategy: Strategy, strength: number) {
  if (strategy === 'mask') return '全文统一编号'
  if (strategy === 'pseudonymize') return `指数机制，ε = ${EPSILON[strength]}`
  return `沿知识图谱上溯 ${strength} 级`
}

/** 同一段文本，三种脱敏方式并排对照。 */
export default function StrategyCompare({ open, text, spans, current, strength: initialStrength, riskLevel, busy, onClose, onApply }: StrategyCompareProps) {
  const [strength, setStrength] = useState(initialStrength)
  const [results, setResults] = useState<Partial<Record<Strategy, Result>>>({})

  useEffect(() => { if (open) setStrength(initialStrength) }, [open, initialStrength])

  const run = useCallback(async (strategy: Strategy, level: number) => {
    const active = spans.filter(span => span.status !== 'rejected')
    try {
      const data = await api.redact(text, active, strategy, level, riskLevel)
      setResults(current => ({ ...current, [strategy]: { text: data.redacted_text, replacements: data.replacements } }))
    } catch (caught) {
      setResults(current => ({ ...current, [strategy]: { error: caught instanceof Error ? caught.message : '生成失败' } }))
    }
  }, [spans, text, riskLevel])

  useEffect(() => {
    if (!open) return
    setResults({})
    ORDER.forEach(strategy => { void run(strategy, strength) })
  }, [open, strength, run])

  const spansById = new Map(spans.map(span => [span.id, span]))

  return <Dialog open={open} onClose={onClose} size="xl" title="三种脱敏方式对照" description="同一份识别结果，分别用掩码、差分隐私替换和知识图谱泛化处理。选定后会应用到当前任务。">
    <div className="compare-toolbar">
      <span>力度</span>
      <Segmented label="对照力度" value={strength} onChange={setStrength} options={[1, 2, 3].map(level => ({ value: level, label: STRENGTH_LABEL[level] }))}/>
    </div>
    <div className="compare-grid">
      {ORDER.map(strategy => {
        const result = results[strategy]
        return <section key={strategy} className={`compare-col${current === strategy ? ' is-current' : ''}`}>
          <header className="compare-head">
            <div>
              <h3>{STRATEGY_LABEL[strategy]}</h3>
              <p>{parameter(strategy, strength)}</p>
            </div>
            {strategy === 'pseudonymize' && <button type="button" className="icon-btn" title="重新抽样" aria-label="重新抽样" onClick={() => void run(strategy, strength)}><Shuffle size={15}/></button>}
          </header>
          <p className="compare-hint">{STRATEGY_HINT[strategy]}</p>
          <div className="compare-body">
            {!result ? <div className="compare-loading"><Spinner/></div>
              : 'error' in result ? <p className="range-error">{result.error}</p>
              : <RedactedText text={result.text} replacements={result.replacements} spansById={spansById} compact/>}
          </div>
          <footer className="compare-foot">
            {current === strategy && strength === initialStrength
              ? <span className="chip chip-ok"><Check size={13}/>当前使用</span>
              : <button type="button" className="btn btn-sm" disabled={busy} onClick={() => onApply(strategy, strength)}>采用这种方式</button>}
          </footer>
        </section>
      })}
    </div>
  </Dialog>
}
