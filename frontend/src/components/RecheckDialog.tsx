import { CircleCheck, Crosshair, ShieldAlert } from 'lucide-react'
import { ENTITY_LABEL, entityStyle } from '../lib/entities'
import type { RecheckFinding, RecheckResult } from '../types'
import { Dialog, Spinner } from './ui'
import './RecheckDialog.css'

interface RecheckDialogProps {
  open: boolean
  loading: boolean
  result: RecheckResult | null
  exportLabel?: string | null
  onClose: () => void
  onLocate: (finding: RecheckFinding) => void
  onRecheck: () => void
  onExportAnyway?: () => void
}

/** 导出前隐私复检的结果。 */
export default function RecheckDialog({ open, loading, result, exportLabel, onClose, onLocate, onRecheck, onExportAnyway }: RecheckDialogProps) {
  const footer = <>
    <button type="button" className="btn" onClick={onRecheck} disabled={loading}>{loading && <Spinner size={14}/>}重新复检</button>
    {exportLabel && onExportAnyway && result && !result.passed && <button type="button" className="btn btn-danger" onClick={onExportAnyway}>仍然导出{exportLabel}</button>}
    <button type="button" className="btn btn-primary" onClick={onClose}>{result && !result.passed ? '回去修改' : '完成'}</button>
  </>
  return <Dialog open={open} onClose={onClose} size="md" title="导出前隐私复检" description="用识别层的规则和已确认的实体，再检查一遍最终稿里是否还留有可识别的信息。" footer={footer}>
    {loading && !result ? <div className="recheck-loading"><Spinner size={20}/><span>正在检查最终稿…</span></div>
      : !result ? null
      : result.passed && !result.findings.length ? <div className="recheck-pass"><CircleCheck size={28}/><div><strong>没有发现残留的敏感信息</strong><p>共检查 <span className="num">{result.checked_characters.toLocaleString()}</span> 个字符。</p></div></div>
      : <div className="recheck">
        <div className={`recheck-summary${result.passed ? ' is-soft' : ''}`}>
          {result.passed ? <CircleCheck size={20}/> : <ShieldAlert size={20}/>}
          <span>{result.high ? `${result.high} 处需要处理` : '没有必须处理的残留'}{result.medium ? `，${result.medium} 处建议人工确认` : ''}</span>
        </div>
        <ul className="recheck-list">
          {result.findings.map(finding => <li key={`${finding.start}-${finding.end}`} className={`recheck-item is-${finding.severity}`}>
            <span className="recheck-sev">{finding.severity === 'high' ? '需处理' : '建议确认'}</span>
            <span className="recheck-main">
              <span className="recheck-text" style={entityStyle(finding.entity_type)}>{finding.text}</span>
              <small>{ENTITY_LABEL[finding.entity_type] || finding.entity_type}，{finding.reason}</small>
            </span>
            <button type="button" className="btn btn-sm" onClick={() => onLocate(finding)}><Crosshair size={13}/>定位</button>
          </li>)}
        </ul>
      </div>}
  </Dialog>
}
