import { Check, X } from 'lucide-react'

type Props = {
  onAccept: () => void | Promise<void>
  onReject: () => void | Promise<void>
  busy?: boolean
  compact?: boolean
  showShortcuts?: boolean
}

export default function ReviewDecision({ onAccept, onReject, busy = false, compact = false, showShortcuts = false }: Props) {
  return <div className={`review-buttons shared-review-actions ${compact ? 'compact' : 'large'}`}>
    <button className={`btn reject ${compact ? '' : 'big'}`} disabled={busy} onClick={onReject}>
      <X size={16}/>{compact ? '拒绝' : '拒绝并保留'}{showShortcuts && <kbd>R</kbd>}
    </button>
    <button className={`btn accept ${compact ? '' : 'big'}`} disabled={busy} onClick={onAccept}>
      <Check size={16}/>{compact ? '接受' : '接受并脱敏'}{showShortcuts && <kbd>A</kbd>}
    </button>
  </div>
}
