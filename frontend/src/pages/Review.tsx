import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Filter, Inbox, Keyboard, LoaderCircle, Search, X } from 'lucide-react'
import { api } from '../api'

type ReviewItem={id:string;task:string;text:string;entity:string;type:string;score:number;reason:string;source:string[];status:'pending'|'accepted'|'rejected'}
const demo: ReviewItem[] = [
  {id:'rv-01',task:'task_demo_01',text:'采访对象表示，王洋目前在明理书院参与研究。',entity:'王洋',type:'PERSON',score:.71,reason:'NER 与 LLM 置信度分歧',source:['NER','LLM'],status:'pending'},
  {id:'rv-02',task:'task_demo_02',text:'材料请寄往海淀区中关村大街59号科研楼。',entity:'海淀区中关村大街59号',type:'ADDRESS',score:.77,reason:'地址边界需要人工确认',source:['NER-LITE'],status:'pending'},
  {id:'rv-03',task:'task_demo_03',text:'Please contact Dr. Alice Morgan after the meeting.',entity:'Alice Morgan',type:'PERSON',score:.79,reason:'英文姓名低置信度候选',source:['NER'],status:'pending'},
]
export default function Review(){
  const [items,setItems]=useState<ReviewItem[]>([])
  const [index,setIndex]=useState(0)
  const [query,setQuery]=useState('')
  const [filter,setFilter]=useState('全部待复核')
  const [loading,setLoading]=useState(true)
  const [actionBusy,setActionBusy]=useState(false)
  const [error,setError]=useState('')
  const [actionError,setActionError]=useState('')
  const [isDemo,setIsDemo]=useState(false)
  const pending=useMemo(()=>items.filter(x=>x.status==='pending'&&(!query||x.entity.includes(query)||x.text.includes(query)||x.task.includes(query))&&(filter==='全部待复核'||(filter==='模型冲突'&&x.reason.includes('分歧'))||(filter==='低置信度'&&x.score<.8)||(filter==='边界异常'&&x.reason.includes('边界')))),[items,query,filter])
  const current=pending[Math.min(index,Math.max(0,pending.length-1))]
  async function act(status:'accepted'|'rejected'){
    if(!current||isDemo||actionBusy)return
    setActionBusy(true);setActionError('')
    try{
      await api.review({task_id:current.task,span_id:current.id,operation:status==='accepted'?'accept':'reject',before:'pending',after:status})
      setItems(value=>value.map(item=>item.id===current.id?{...item,status}:item));setIndex(0)
    }catch(caught){setActionError(caught instanceof Error?caught.message:'复核提交失败，列表未发生变更。')}
    finally{setActionBusy(false)}
  }
  useEffect(()=>{api.reviewQueue().then(data=>{setItems((data.items||[]).map((x:any)=>({id:x.span.id,task:x.task_id,text:x.context,entity:x.span.text,type:x.span.entity_type,score:x.span.score||0,reason:x.reason,source:x.span.sources,status:'pending'})));setIsDemo(false)}).catch(caught=>{setError(caught instanceof Error?caught.message:'复核队列加载失败');setItems(demo);setIsDemo(true)}).finally(()=>setLoading(false))},[])
  useEffect(()=>{setIndex(0)},[query,filter])
  useEffect(()=>{const fn=(event:KeyboardEvent)=>{const target=event.target as HTMLElement;if(target.matches('input, textarea, select')||target.isContentEditable||isDemo||actionBusy)return;if(event.key.toLowerCase()==='a')void act('accepted');if(event.key.toLowerCase()==='r')void act('rejected')};window.addEventListener('keydown',fn);return()=>window.removeEventListener('keydown',fn)},[current,isDemo,actionBusy])
  return <div className="page"><header className="page-header"><div><div className="eyebrow">HUMAN IN THE LOOP</div><h1>人工复核队列</h1><p>集中处理低置信度、模型冲突和边界异常，后端确认成功后才更新队列。</p></div><div className="review-counter"><strong>{pending.length}</strong><span>项等待处理</span></div></header>
    {error&&<div className="notice demo-notice"><AlertTriangle/><span><strong>只读演示数据 · 操作已禁用</strong> 后端队列暂不可用：{error}</span></div>}
    {actionError&&<div className="error-banner"><AlertTriangle size={17}/>{actionError}</div>}
    <div className="review-toolbar"><label className="search-box"><Search size={16}/><span className="sr-only">搜索复核任务</span><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索任务、实体或文本"/></label><div className="filter-btn"><Filter size={15}/><span>{filter}</span></div><div className="queue-tabs" role="group" aria-label="复核原因筛选">{['全部待复核','模型冲突','低置信度','边界异常'].map(value=><button className={filter===value?'active':''} aria-pressed={filter===value} onClick={()=>setFilter(value)} key={value}>{value}</button>)}</div></div>
    <div className="review-layout"><section className="panel queue-list"><div className="queue-head"><span>待复核项</span><small>按风险优先级排序</small></div>{pending.length ? pending.map((item,itemIndex)=><button key={item.id} className={`queue-item ${current?.id===item.id?'active':''}`} onClick={()=>setIndex(itemIndex)}><div className="queue-item-top"><span className="queue-type">{item.type}</span><b>{Math.round(item.score*100)}%</b></div><strong>{item.entity}</strong><p>{item.text}</p><small><AlertTriangle size={12}/>{item.reason}</small></button>) : <div className="queue-empty"><Inbox size={32}/><strong>队列已清空</strong><p>所有疑难候选均已复核。</p></div>}</section>
      <section className="panel review-focus" aria-busy={loading||actionBusy}>{current ? <><div className="focus-head"><div><span>复核任务 · {current.task}</span><h2>判断该实体是否应当脱敏</h2></div><div className="pager"><button aria-label="上一个复核项" disabled={index===0||actionBusy} onClick={()=>setIndex(Math.max(0,index-1))}><ChevronLeft/></button><span>{index+1} / {pending.length}</span><button aria-label="下一个复核项" disabled={index===pending.length-1||actionBusy} onClick={()=>setIndex(Math.min(pending.length-1,index+1))}><ChevronRight/></button></div></div>
        <div className="context-card"><small>原始语境</small><p>{current.text.split(current.entity)[0]}<mark>{current.entity}<span>{current.type}</span></mark>{current.text.split(current.entity).slice(1).join(current.entity)}</p></div>
        <div className="review-evidence"><div><span>候选类型</span><strong>{current.type}</strong></div><div><span>置信度</span><strong>{Math.round(current.score*100)}%</strong></div><div><span>识别来源</span><strong>{current.source.join(' + ')}</strong></div><div><span>进入队列原因</span><strong>{current.reason}</strong></div></div>
        <div className="decision-note"><AlertTriangle size={17}/><div><strong>{isDemo?'只读预览':'系统建议：人工确认'}</strong><p>{isDemo?'当前为演示条目，恢复后端连接后才能提交。':'接受后将按当前策略脱敏，拒绝后保留原文；失败时不会提前改变队列。'}</p></div></div>
        <div className="decision-actions"><button className="btn reject big" disabled={isDemo||actionBusy} onClick={()=>void act('rejected')}>{actionBusy?<LoaderCircle className="spin"/>:<X/>}拒绝并保留 <kbd>R</kbd></button><button className="btn accept big" disabled={isDemo||actionBusy} onClick={()=>void act('accepted')}>{actionBusy?<LoaderCircle className="spin"/>:<Check/>}接受并脱敏 <kbd>A</kbd></button></div>
        <div className="shortcut-hint"><Keyboard size={14}/>支持键盘快捷复核；仅在后端写入审计日志成功后更新界面。</div>
      </> : <div className="queue-empty large"><Check size={42}/><strong>{loading?'正在读取队列':query||filter!=='全部待复核'?'没有匹配项':'复核完成'}</strong><p>{loading?'正在同步后端复核任务…':query||filter!=='全部待复核'?'请尝试调整搜索词或筛选条件。':'当前没有等待处理的候选实体。'}</p></div>}</section></div>
  </div>
}
