import { useMemo, useState } from 'react'
import { Check, ChevronDown, Clipboard, Download, FileJson, FileUp, LoaderCircle, Play, Plus, RotateCcw, ShieldAlert, Sparkles, X } from 'lucide-react'
import { api } from '../api'
import AnnotatedText from '../components/AnnotatedText'
import PipelineTrace from '../components/PipelineTrace'
import type { DetectResult, EntityType, Span, Strategy } from '../types'

const samples = {
  '中英混合访谈': '采访对象姓名：王洋，现就读于中国人民大学。联系电话是13800138000，邮箱 wang.yang@example.com，住在北京市海淀区中关村大街59号。His supervisor is Dr. Alice Morgan from Northbridge Institute.',
  '客户服务记录': '我叫李明，银行卡号为6222021001116247。请将材料寄到上海市浦东新区世纪大道88号，电话13912345678。',
  '英文邮件': 'Hi, I am Sarah Johnson. Please contact me at sarah.j@example.com or +1 415-555-0136. I currently work at Northbridge University in London.',
}
const labels: Record<EntityType,string> = { PERSON:'姓名',ORG:'机构',LOCATION:'地点',ADDRESS:'详细地址',PHONE:'电话',EMAIL:'邮箱',ID_CARD:'身份证',BANK_CARD:'银行卡' }

export default function Workbench() {
  const [text, setText] = useState(samples['中英混合访谈'])
  const [strategy, setStrategy] = useState<Strategy>('mask')
  const [result, setResult] = useState<DetectResult | null>(null)
  const [spans, setSpans] = useState<Span[]>([])
  const [selected, setSelected] = useState<Span | null>(null)
  const [redacted, setRedacted] = useState('')
  const [loading, setLoading] = useState(false); const [error, setError] = useState('')
  async function run() { setLoading(true); setError(''); try { const data = await api.detect(text, strategy); setResult(data); setSpans(data.spans); setRedacted(data.redacted_text); setSelected(data.spans[0] || null) } catch(e) { setError(e instanceof Error ? e.message : '检测失败') } finally { setLoading(false) } }
  async function refreshRedaction(nextSpans=spans, nextStrategy=strategy) { if (!result) return; const data = await api.redact(text, nextSpans, nextStrategy); setRedacted(data.redacted_text) }
  async function updateStatus(status: Span['status']) { if (!selected || !result) return; const updated = spans.map(s => s.id === selected.id ? {...s,status} : s); setSpans(updated); setSelected({...selected,status}); await refreshRedaction(updated); await api.review({task_id:result.task_id,span_id:selected.id,operation:status === 'rejected'?'reject':'accept',before:selected.status,after:status}) }
  async function changeType(type: EntityType) { if (!selected || !result) return; const next={...selected,entity_type:type,status:'accepted' as const}; const updated=spans.map(s=>s.id===selected.id?next:s); setSpans(updated); setSelected(next); await refreshRedaction(updated); await api.review({task_id:result.task_id,span_id:selected.id,operation:'change_type',before:selected.entity_type,after:type}) }
  function importFile(file?:File){if(!file)return;file.text().then(raw=>{try{if(file.name.endsWith('.json')){const parsed=JSON.parse(raw);setText(typeof parsed==='string'?parsed:parsed.text||parsed.content||JSON.stringify(parsed,null,2))}else if(file.name.endsWith('.csv')){const lines=raw.split(/\r?\n/).filter(Boolean);setText(lines.slice(1).map(x=>x.split(',')[0].replace(/^"|"$/g,'')).join('\n'))}else setText(raw);setResult(null);setSpans([])}catch{setError('文件解析失败，请检查 UTF-8 编码与格式')}})}
  async function addEntity(){if(!result)return;const value=window.prompt('请输入原文中需要新增为隐私实体的精确文本');if(!value)return;const start=text.indexOf(value);if(start<0){setError('新增失败：该文本不在原文中');return}const raw=window.prompt('实体类型：PERSON / ORG / LOCATION / ADDRESS / PHONE / EMAIL / ID_CARD / BANK_CARD','PERSON') as EntityType|null;if(!raw||!labels[raw])return;const next:Span={id:`human_${crypto.randomUUID().slice(0,8)}`,start,end:start+value.length,text:value,entity_type:raw,score:1,sources:['HUMAN'],status:'accepted',conflict:false,strategy,metadata:{operation:'manual_add'}};const updated=[...spans,next].sort((a,b)=>a.start-b.start);setSpans(updated);setSelected(next);await refreshRedaction(updated);await api.review({task_id:result.task_id,span_id:next.id,operation:'add',before:null,after:raw,span:next})}
  async function adjustBoundary(){if(!selected||!result)return;const value=window.prompt('输入调整后的精确实体文本',selected.text);if(!value)return;const searchFrom=Math.max(0,selected.start-20);const start=text.indexOf(value,searchFrom);if(start<0){setError('边界调整失败：输入内容不在原文附近');return}const next={...selected,start,end:start+value.length,text:value,sources:[...new Set([...selected.sources,'HUMAN'])],status:'accepted' as const};const updated=spans.map(s=>s.id===selected.id?next:s).sort((a,b)=>a.start-b.start);setSpans(updated);setSelected(next);await refreshRedaction(updated);await api.review({task_id:result.task_id,span_id:selected.id,operation:'adjust_boundary',before:`${selected.start}:${selected.end}`,after:`${next.start}:${next.end}`})}
  const counts = useMemo(() => spans.filter(s=>s.status!=='rejected').reduce<Record<string,number>>((a,s)=>(a[s.entity_type]=(a[s.entity_type]||0)+1,a),{}),[spans])
  return <div className="page workbench-page">
    <header className="page-header"><div><div className="eyebrow">PRIVACY OPERATIONS</div><h1>隐私处理工作台</h1><p>让每一个识别结果都可见、可控、可追溯。</p></div><div className="header-actions"><button className="btn ghost" onClick={()=>{setResult(null);setSpans([]);setRedacted('')}}><RotateCcw size={16}/>重置</button><button className="btn primary" onClick={run} disabled={loading||!text.trim()}>{loading?<LoaderCircle className="spin" size={17}/>:<Play size={17}/>}开始检测</button></div></header>
    {error && <div className="error-banner"><ShieldAlert size={17}/>{error}</div>}
    <div className="workbench-grid">
      <section className="panel input-panel"><div className="panel-title"><div><span className="step-number">01</span><strong>输入与策略</strong></div><label className="file-import"><FileUp size={13}/>导入<input type="file" hidden accept=".txt,.csv,.json" onChange={e=>importFile(e.target.files?.[0])}/></label><span className="char-count">{text.length.toLocaleString()} / 100,000</span></div>
        <div className="sample-row"><span>内置样例</span>{Object.keys(samples).map(name=><button key={name} onClick={()=>setText(samples[name as keyof typeof samples])}>{name}</button>)}</div>
        <textarea value={text} maxLength={100000} aria-label="待检测文本" onChange={e=>setText(e.target.value)} placeholder="粘贴需要检测的中文、英文或混合文本…"/>
        <div className="settings-row"><label><span>脱敏策略</span><select value={strategy} onChange={async e=>{const v=e.target.value as Strategy;setStrategy(v); await refreshRedaction(spans,v)}}><option value="mask">一致性掩码</option><option value="pseudonymize">确定性伪名</option><option value="generalize">层级泛化</option></select><ChevronDown size={14}/></label><label title="当前后端固定使用严格保护"><span>隐私级别（服务端固定）</span><select value="strict" disabled><option value="strict">严格保护</option></select><ChevronDown size={14}/></label></div>
      </section>
      <section className="panel analysis-panel"><div className="panel-title"><div><span className="step-number">02</span><strong>实体识别</strong></div>{result&&<div className="analysis-tools"><button onClick={addEntity}><Plus size={12}/>新增实体</button><span className="risk-pill"><ShieldAlert size={14}/>风险 {result.summary.risk_score}</span></div>}</div>
        {!result ? <div className="empty-state"><div className="empty-orbit"><Sparkles size={25}/></div><strong>等待开始检测</strong><p>运行后将在原文中高亮隐私实体，点击任意实体可查看识别依据。</p></div> : <><div className="legend">{Object.entries(counts).map(([type,count])=><span key={type}><i className={`legend-dot entity-${type}`}/>{labels[type as EntityType]} {count}</span>)}</div><AnnotatedText text={text} spans={spans} selected={selected?.id} onSelect={setSelected}/></>}
      </section>
      <aside className="panel inspector-panel"><div className="panel-title"><div><span className="step-number">03</span><strong>实体复核</strong></div></div>
        {!selected ? <div className="inspector-empty">选择高亮实体查看详情</div> : <div className="entity-detail"><div className="detail-hero"><span className={`type-icon entity-${selected.entity_type}`}>{labels[selected.entity_type][0]}</span><div><small>{labels[selected.entity_type]}</small><strong>{selected.text}</strong></div><span className={`review-status ${selected.status}`}>{selected.status==='pending'?'待复核':selected.status==='rejected'?'已拒绝':'已接受'}</span></div>
          <div className="confidence"><span>综合置信度</span><b>{Math.round((selected.score||0)*100)}%</b><div><i style={{width:`${(selected.score||0)*100}%`}}/></div></div>
          <dl><div><dt>字符区间</dt><dd>{selected.start} — {selected.end}</dd></div><div><dt>识别来源</dt><dd>{selected.sources.map(s=><span className="source-tag" key={s}>{s}</span>)}</dd></div><div><dt>冲突状态</dt><dd>{selected.conflict?'存在重叠冲突':'偏移校验通过'}</dd></div></dl>
          <label className="type-select"><span>调整实体类型</span><select value={selected.entity_type} onChange={e=>changeType(e.target.value as EntityType)}>{Object.entries(labels).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label><button className="boundary-button" onClick={adjustBoundary}>调整字符边界</button>
          <div className="review-buttons"><button className="btn reject" onClick={()=>updateStatus('rejected')}><X size={16}/>拒绝</button><button className="btn accept" onClick={()=>updateStatus('accepted')}><Check size={16}/>接受</button></div>
        </div>}
      </aside>
    </div>
    {result && <><section className="panel trace-panel"><div className="section-heading"><div><span>PROCESS TRACE</span><h2>三层处理轨迹</h2></div><small>任务 {result.task_id}</small></div><PipelineTrace trace={result.trace}/></section>
      <section className="comparison"><div className="comparison-card original"><div className="comparison-head"><span>原始文本</span><button onClick={()=>navigator.clipboard.writeText(text)}><Clipboard size={15}/>复制</button></div><p>{text}</p></div><div className="comparison-arrow">→</div><div className="comparison-card safe"><div className="comparison-head"><span><ShieldAlert size={15}/>脱敏结果</span><div><button onClick={()=>navigator.clipboard.writeText(redacted)}><Clipboard size={15}/>复制</button><button onClick={()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([redacted],{type:'text/plain'}));a.download='redacted.txt';a.click()}}><Download size={15}/>导出</button></div></div><p>{redacted}</p></div></section>
      <div className="export-bar"><div><FileJson size={18}/><span><strong>可审计结果已生成</strong><small>包含 Span、来源、置信度、模型与人工操作记录</small></span></div><button className="btn ghost" onClick={()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({...result,spans,redacted_text:redacted},null,2)],{type:'application/json'}));a.download='audit-result.json';a.click()}}><Download size={16}/>导出审计 JSON</button></div></>}
  </div>
}
