import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Filter, Inbox, Keyboard, Search, X } from 'lucide-react'
import { api } from '../api'

type ReviewItem = { id:string; task:string; text:string; entity:string; type:string; score:number; reason:string; source:string[]; status:string }
const demo: ReviewItem[] = [
  {id:'rv-01',task:'task_demo_01',text:'采访对象表示，王洋目前在明理书院参与研究。',entity:'王洋',type:'PERSON',score:.71,reason:'NER 与 LLM 置信度分歧',source:['NER','LLM'],status:'pending'},
  {id:'rv-02',task:'task_demo_02',text:'材料请寄往海淀区中关村大街59号科研楼。',entity:'海淀区中关村大街59号',type:'ADDRESS',score:.77,reason:'地址边界需要人工确认',source:['NER-LITE'],status:'pending'},
  {id:'rv-03',task:'task_demo_03',text:'Please contact Dr. Alice Morgan after the meeting.',entity:'Alice Morgan',type:'PERSON',score:.79,reason:'英文姓名低置信度候选',source:['NER'],status:'pending'},
]
export default function Review() {
  const [items,setItems]=useState<ReviewItem[]>([]); const [index,setIndex]=useState(0); const [filter,setFilter]=useState('全部待复核'); const [query,setQuery]=useState(''); const [loading,setLoading]=useState(true); const [error,setError]=useState('')
  const pending=useMemo(()=>items.filter(x=>x.status==='pending').filter(x=>{
    const q=query.trim().toLowerCase(); const searchHit=!q||[x.task,x.text,x.entity,x.type].some(v=>v.toLowerCase().includes(q))
    const filterHit=filter==='全部待复核'||(filter==='模型冲突'&&x.reason.includes('分歧'))||(filter==='低置信度'&&x.score<.78)||(filter==='边界异常'&&x.reason.includes('边界'))
    return searchHit&&filterHit
  }),[items,query,filter]); const current=pending[index] || null
  async function act(status:'accepted'|'rejected'){if(!current)return;setItems(v=>v.map(x=>x.id===current.id?{...x,status}:x));setIndex(0);await api.review({task_id:current.task,span_id:current.id,operation:status==='accepted'?'accept':'reject',before:'pending',after:status})}
  useEffect(()=>{api.reviewQueue().then(data=>setItems((data.items||[]).map((x:any)=>({id:x.span.id,task:x.task_id,text:x.context,entity:x.span.text,type:x.span.entity_type,score:x.span.score||0,reason:x.reason,source:x.span.sources,status:'pending'})))).catch(e=>{setError(e instanceof Error?e.message:'复核队列加载失败');setItems(demo)}).finally(()=>setLoading(false))},[])
  useEffect(()=>{setIndex(0)},[query,filter])
  useEffect(()=>{const fn=(e:KeyboardEvent)=>{const target=e.target as HTMLElement;if(target.matches('input, textarea, select')||target.isContentEditable)return;if(e.key.toLowerCase()==='a')act('accepted');if(e.key.toLowerCase()==='r')act('rejected')};window.addEventListener('keydown',fn);return()=>window.removeEventListener('keydown',fn)},[current])
  return <div className="page"><header className="page-header"><div><div className="eyebrow">HUMAN IN THE LOOP</div><h1>人工复核队列</h1><p>集中处理低置信度、模型冲突和边界异常，每次操作自动写入审计日志。</p></div><div className="review-counter"><strong>{pending.length}</strong><span>项等待处理</span></div></header>
    {error&&<div className="notice demo-notice"><AlertTriangle/><span><strong>演示数据 · 不代表真实复核任务</strong> 后端队列暂不可用：{error}</span></div>}
    <div className="review-toolbar"><label className="search-box"><Search size={16}/><span className="sr-only">搜索复核任务</span><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索任务、实体或文本"/></label><div className="filter-btn"><Filter size={15}/><span>{filter}</span></div><div className="queue-tabs" role="group" aria-label="复核原因筛选">{['全部待复核','模型冲突','低置信度','边界异常'].map(x=><button className={filter===x?'active':''} aria-pressed={filter===x} onClick={()=>setFilter(x)} key={x}>{x}</button>)}</div></div>
    <div className="review-layout"><section className="panel queue-list"><div className="queue-head"><span>待复核项</span><small>按风险优先级排序</small></div>{pending.length ? pending.map((item,i)=><button key={item.id} className={`queue-item ${current?.id===item.id?'active':''}`} onClick={()=>setIndex(i)}><div className="queue-item-top"><span className="queue-type">{item.type}</span><b>{Math.round(item.score*100)}%</b></div><strong>{item.entity}</strong><p>{item.text}</p><small><AlertTriangle size={12}/>{item.reason}</small></button>) : <div className="queue-empty"><Inbox size={32}/><strong>队列已清空</strong><p>所有疑难候选均已复核。</p></div>}</section>
      <section className="panel review-focus" aria-busy={loading}>{current ? <><div className="focus-head"><div><span>复核任务 · {current.task}</span><h2>判断该实体是否应当脱敏</h2></div><div className="pager"><button aria-label="上一个复核项" disabled={index===0} onClick={()=>setIndex(Math.max(0,index-1))}><ChevronLeft/></button><span>{index+1} / {pending.length}</span><button aria-label="下一个复核项" disabled={index===pending.length-1} onClick={()=>setIndex(Math.min(pending.length-1,index+1))}><ChevronRight/></button></div></div>
        <div className="context-card"><small>原始语境</small><p>{current.text.split(current.entity)[0]}<mark>{current.entity}<span>{current.type}</span></mark>{current.text.split(current.entity).slice(1).join(current.entity)}</p></div>
        <div className="review-evidence"><div><span>候选类型</span><strong>{current.type}</strong></div><div><span>置信度</span><strong>{Math.round(current.score*100)}%</strong></div><div><span>识别来源</span><strong>{current.source.join(' + ')}</strong></div><div><span>进入队列原因</span><strong>{current.reason}</strong></div></div>
        <div className="decision-note"><AlertTriangle size={17}/><div><strong>系统建议：人工确认</strong><p>候选存在不确定性。接受后将按当前策略脱敏，拒绝后保留原文。</p></div></div>
        <div className="decision-actions"><button className="btn reject big" onClick={()=>act('rejected')}><X/>拒绝并保留 <kbd>R</kbd></button><button className="btn accept big" onClick={()=>act('accepted')}><Check/>接受并脱敏 <kbd>A</kbd></button></div>
        <div className="shortcut-hint"><Keyboard size={14}/>支持键盘快捷复核，所有操作均记录操作者、时间与模型版本。</div>
      </> : <div className="queue-empty large"><Check size={42}/><strong>{loading?'正在读取队列':query||filter!=='全部待复核'?'没有匹配项':'复核完成'}</strong><p>{loading?'正在同步后端复核任务…':query||filter!=='全部待复核'?'请尝试调整搜索词或筛选条件。':'当前没有等待处理的候选实体。'}</p></div>}</section></div>
  </div>
}
