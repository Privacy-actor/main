import { useEffect, useState } from 'react'
import { ArrowLeft, CalendarClock, Copy, FileText, ShieldAlert } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import PipelineTrace from '../components/PipelineTrace'
import type { EntityType, HistoryTaskDetail } from '../types'

const labels: Record<EntityType,string> = {
  PERSON:'姓名', ORG:'机构', LOCATION:'地点', ADDRESS:'详细地址',
  PHONE:'电话', EMAIL:'邮箱', ID_CARD:'身份证', BANK_CARD:'银行卡',
  PASSPORT:'护照', CUSTOM:'自定义',
}

export default function HistoryDetail(){
  const {taskId=''} = useParams()
  const [task,setTask] = useState<HistoryTaskDetail|null>(null)
  const [error,setError] = useState('')

  useEffect(()=>{
    api.historyDetail(taskId).then(setTask).catch(e=>setError(e instanceof Error?e.message:'历史任务加载失败'))
  },[taskId])

  if(error)return <div className="page"><Link className="back-link" to="/history"><ArrowLeft/>返回历史中心</Link><div className="error-banner"><ShieldAlert/>{error}</div></div>
  if(!task)return <div className="page loading-page" aria-live="polite">正在读取任务详情…</div>

  return <div className="page history-detail-page">
    <Link className="back-link" to="/history"><ArrowLeft/>返回历史中心</Link>
    <div className="detail-meta"><CalendarClock/><span><small>创建时间</small><strong>{new Date(task.created_at).toLocaleString()}</strong></span><span className={`risk-level ${task.risk}`}>{task.risk==='high'?'高风险':task.risk==='medium'?'中风险':'低风险'}</span></div>

    <div className="detail-kpis">
      <div><span>实体总数</span><strong>{task.summary.total}</strong></div>
      <div><span>待复核</span><strong>{task.summary.pending}</strong></div>
      <div><span>风险分</span><strong>{task.summary.risk_score}</strong></div>
      <div><span>运行模型</span><strong>{task.model.name.split('/').pop()}</strong></div>
    </div>

    <section className="panel detail-result-card">
      <div className="section-heading"><div><span>REDACTED OUTPUT</span><h2>脱敏结果</h2></div><button onClick={()=>navigator.clipboard.writeText(task.redacted_text)}><Copy/>复制结果</button></div>
      <p>{task.redacted_text}</p>
      <details><summary><ShieldAlert/>查看敏感原文</summary><div className="sensitive-source">{task.text}</div></details>
    </section>

    <section className="panel detail-entities">
      <div className="section-heading"><div><span>ENTITY SPANS</span><h2>实体明细</h2></div><small>{task.spans.length} 个 Span</small></div>
      <div className="detail-entity-table">
        <div className="detail-entity-row head"><span>实体</span><span>类型</span><span>状态</span><span>策略</span><span>来源 / 置信度</span></div>
        {task.spans.map(span=><div className="detail-entity-row" key={span.id}>
          <span><FileText/>{span.text}</span><span>{labels[span.entity_type]}</span>
          <span className={`review-status ${span.status}`}>{span.status==='pending'?'待复核':span.status==='rejected'?'已拒绝':'已接受'}</span>
          <span>{span.strategy==='mask'?'一致性掩码':span.strategy==='pseudonymize'?'确定性伪名':'层级泛化'}</span>
          <span>{span.sources.join(' + ')} · {span.score==null?'—':`${Math.round(span.score*100)}%`}</span>
        </div>)}
      </div>
    </section>

    <section className="panel trace-panel">
      <div className="section-heading"><div><span>PROCESS TRACE</span><h2>处理轨迹</h2></div></div>
      <PipelineTrace trace={task.trace}/>
    </section>
  </div>
}
