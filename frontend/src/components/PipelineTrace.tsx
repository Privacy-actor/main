import { Check, Cpu, GitMerge, ScanLine, Sparkles } from 'lucide-react'
import type { TraceStep } from '../types'
const icons: Record<string, any> = { rule: ScanLine, ner: Cpu, llm: Sparkles, merge: GitMerge }
export default function PipelineTrace({ trace }: { trace: TraceStep[] }) {
  return <div className="pipeline">{trace.map((step, index) => { const Icon = icons[step.key] || Check; return <div className={`pipeline-step ${step.status}`} key={step.key}>
    <div className="step-icon"><Icon size={16}/></div><div className="step-copy"><strong>{step.label}</strong><span>{step.detail}</span></div>
    <div className="step-stats"><b>{step.count}</b><small>{step.duration_ms} ms</small></div>{index < trace.length - 1 && <div className="step-line"/>}
  </div>})}</div>
}
