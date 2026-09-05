import type { StrategyMode } from '../types'

export default function StrategyModeSelector({ value, onChange }: { value: StrategyMode; onChange: (value: StrategyMode) => void }) {
  return <div className="strategy-mode-field">
    <span>策略模式</span>
    <div className="strategy-mode-toggle" role="group" aria-label="策略模式">
      <button type="button" className={value === 'uniform' ? 'active' : ''} aria-pressed={value === 'uniform'} onClick={() => onChange('uniform')}>统一策略</button>
      <button type="button" className={value === 'by_type' ? 'active' : ''} aria-pressed={value === 'by_type'} onClick={() => onChange('by_type')}>按实体类型</button>
    </div>
    <small>{value === 'uniform' ? '本次任务的所有实体使用同一种策略。' : '使用“历史与策略”中保存的各实体默认策略，并覆盖统一策略。'}</small>
  </div>
}
