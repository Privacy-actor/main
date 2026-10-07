import { ChevronRight } from 'lucide-react'
import { spanLayers, type Layer } from '../lib/entities'
import { formatDuration } from '../lib/format'
import type { Span, TraceStep } from '../types'
import './PipelineTrace.css'

interface PipelineTraceProps {
  trace: TraceStep[]
  spans: Span[]
  activeLayer?: Layer | 'all' | null
  onLayer?: (layer: Layer | null) => void
}

type Station = { key: Layer | 'merge'; name: string; count: number; note: string; duration: number; status: TraceStep['status'] }

function find(trace: TraceStep[], keys: string[]) {
  return trace.find(step => keys.includes(step.key))
}

/** 三层识别轨迹：规则层 → NER 层 → 大模型核查 → 合并与脱敏。点击某层可只看这一层识别出的实体。 */
export default function PipelineTrace({ trace, spans, activeLayer, onLayer }: PipelineTraceProps) {
  const active = spans.filter(span => span.status !== 'rejected')
  const counts: Record<Layer, number> = { rule: 0, ner: 0, llm: 0, human: 0 }
  for (const span of active) for (const layer of spanLayers(span)) counts[layer] += 1

  const rule = find(trace, ['rule'])
  const ner = find(trace, ['ner_model', 'ner'])
  const llm = find(trace, ['llm'])
  const merge = find(trace, ['merge'])
  const knowledge = find(trace, ['knowledge'])
  const instruction = find(trace, ['instruction'])
  const propagated = Number(merge?.detail.match(/同名补全 (\d+)/)?.[1] || 0)
  const customRules = Number(rule?.detail.match(/(\d+) 条自定义规则/)?.[1] || 0)
  const llmAdded = active.filter(span => span.sources.includes('LLM') && span.metadata.llm_addition).length
  const llmReviewed = active.filter(span => span.sources.includes('LLM') && !span.metadata.llm_addition).length

  const llmNote = !llm ? '未运行'
    : llm.status === 'skipped' ? (llm.detail.includes('关闭') ? '本次未调用' : llm.detail.includes('改为云端') ? '本地模型未启用' : '模型未连接')
    : llm.status === 'degraded' ? '调用失败，已降级'
    : `复核 ${llmReviewed}，补充 ${llmAdded}`

  // 模型名可能是路径；模型没加载成功时由轻量识别器兜底
  const nerNote = ner?.key !== 'ner_model' ? '姓名、机构、地点（轻量识别器）'
    : ner.status === 'degraded' ? '模型未加载，使用轻量识别器'
    : ner.detail.split(/[；;]/)[0].split(/[\\/]/).pop() || 'NER 模型'

  const stations: Station[] = [
    { key: 'rule', name: '规则层', count: counts.rule, duration: rule?.duration_ms || 0, status: rule?.status || 'done', note: customRules ? `结构化标识与 ${customRules} 条自定义规则` : '手机号、邮箱、证件号等结构化标识' },
    { key: 'ner', name: 'NER 层', count: counts.ner, duration: ner?.duration_ms || 0, status: ner?.status || 'done', note: nerNote },
    { key: 'llm', name: '大模型核查', count: counts.llm, duration: llm?.duration_ms || 0, status: llm?.status || 'skipped', note: llmNote },
    { key: 'merge', name: '合并与脱敏', count: active.length, duration: merge?.duration_ms || 0, status: merge?.status || 'done', note: propagated ? `去重消歧，同名补全 ${propagated} 处` : '去重、消歧并替换' },
  ]

  const notes: string[] = []
  if (instruction && instruction.status !== 'skipped' && instruction.count) notes.push(`已按自然语言要求调整 ${instruction.count} 个词`)
  const ignoredClauses = Number(instruction?.detail.match(/(\d+) 句要求暂不支持/)?.[1] || 0)
  if (ignoredClauses) notes.push(`${ignoredClauses} 句要求暂不支持（如只保留后几位），这部分按默认方式整段处理`)
  if (llm?.status === 'skipped' && llm.detail.includes('改为云端')) notes.push('已设置云端模型：在“更多设置”里把部署模式改为云端，核查才会使用它')
  if (knowledge && knowledge.status !== 'skipped') notes.push(`知识图谱为 ${knowledge.count} 个实体生成层级${knowledge.status === 'degraded' ? '（远程不可用，使用本地层级）' : ''}`)
  if (counts.human) notes.push(`人工处理 ${counts.human} 处`)

  return <div className="pipeline" aria-label="识别轨迹">
    <ol className="pipeline-row">
      {stations.map((station, index) => {
        const layer = station.key === 'merge' ? null : station.key
        const selected = layer ? activeLayer === layer : !activeLayer || activeLayer === 'all'
        return <li key={station.key} className="pipeline-cell">
          <button
            type="button" className={`station is-${station.status}${selected ? ' is-active' : ''}`}
            aria-pressed={Boolean(layer) && selected} onClick={() => onLayer?.(layer && activeLayer !== layer ? layer : null)}
            title={layer ? (selected ? '显示全部实体' : `只看${station.name}识别的实体`) : '显示全部实体'}
          >
            <span className="station-name">{station.name}</span>
            <span className="station-figure"><b className="num">{station.status === 'skipped' && station.key === 'llm' ? '—' : station.count}</b><small>{station.key === 'merge' ? '处已处理' : '处'}</small></span>
            <span className="station-note">{station.note}</span>
            <span className="station-time num">{station.status === 'skipped' ? '' : formatDuration(station.duration)}</span>
          </button>
          {index < stations.length - 1 && <ChevronRight className="pipeline-arrow" size={16} aria-hidden="true"/>}
        </li>
      })}
    </ol>
    {notes.length > 0 && <p className="pipeline-notes">{notes.join('；')}</p>}
  </div>
}
