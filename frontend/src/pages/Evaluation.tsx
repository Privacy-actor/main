import { useEffect, useState } from 'react'
import { AlertTriangle, Database, Info, ShieldCheck } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'

type SystemMetric={name:string;precision:number;recall:number;f1:number;latency?:number}
type CategoryMetric={name:string;recall:number}
type EvaluationData={notice?:string;systems:SystemMetric[];categories:CategoryMetric[];run_id?:string;dataset?:string;created_at?:string}
const colors=['#7b8cff','#42d5b0','#ffb85c','#8f6ff8']

export default function Evaluation(){
 const [data,setData]=useState<EvaluationData|null>(null);const [error,setError]=useState('')
 useEffect(()=>{api.evaluations().then(setData).catch(e=>setError(e instanceof Error?e.message:'评估结果加载失败'))},[])
 if(error)return <div className="page"><div className="error-banner"><AlertTriangle/>无法读取评估结果：{error}</div></div>
 if(!data)return <div className="page loading-page" aria-live="polite">正在读取实验结果…</div>
 const systems=data.systems||[];const best=systems.length?systems.reduce((a,b)=>b.f1>a.f1?b:a):null
 const isDemo=!data.run_id||data.notice?.includes('演示')
 return <div className={`page evaluation-page ${isDemo?'is-demo':''}`}>
  <header className="page-header"><div><div className="eyebrow">RESEARCH EVALUATION</div><h1>评估实验室</h1><p>只展示由评估接口返回的实验结果；正式指标需运行冻结测试集后生成。</p></div><div className="dataset-badge"><Database/><span><small>数据集</small><strong>{data.dataset||'尚未绑定冻结测试集'}</strong></span></div></header>
  <div className={`evaluation-status-banner ${isDemo?'demo':''}`} role="status"><Info/><div><span>{isDemo?'DEMO DATA · 待接入真实实验':'VERIFIED RUN · 已接入实验结果'}</span><strong>{isDemo?'当前指标仅用于界面演示，不可用于结项结论。':'当前页面展示已接入的实验运行结果。'}</strong><p>{data.notice||'暂无实验说明'}{isDemo?' 完成冻结测试集后，本区域会自动替换为真实运行信息。':''}</p></div></div>
  {best?<>
   <div className="metric-cards verified"><div className="metric-card hero"><div><span>接口返回的最高 F1</span><strong>{(best.f1*100).toFixed(1)}<small>%</small></strong><p>{best.name}</p></div><ShieldCheck/></div><div className="metric-card"><span>召回率</span><strong>{(best.recall*100).toFixed(1)}%</strong><small>{best.name}</small></div><div className="metric-card"><span>精确率</span><strong>{(best.precision*100).toFixed(1)}%</strong><small>{best.name}</small></div><div className="metric-card"><span>单条延迟</span><strong>{best.latency!=null?`${best.latency} ms`:'—'}</strong><small>{best.latency!=null?'接口记录值':'本次未记录'}</small></div></div>
   <div className="evaluation-grid"><section className="panel chart-panel wide"><div className="section-heading"><div><span>MODEL COMPARISON</span><h2>流水线检测性能</h2></div><div className="chart-legend">Precision / Recall / F1</div></div><ResponsiveContainer width="100%" height={300}><BarChart data={systems} barGap={3}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e7ebf1"/><XAxis dataKey="name" axisLine={false} tickLine={false}/><YAxis domain={[0,1]} tickFormatter={v=>`${v*100}%`} axisLine={false} tickLine={false}/><Tooltip formatter={(v:any)=>`${(Number(v)*100).toFixed(1)}%`}/><Legend/><Bar dataKey="precision" name="精确率" fill="#7b8cff" radius={[5,5,0,0]}/><Bar dataKey="recall" name="召回率" fill="#42d5b0" radius={[5,5,0,0]}/><Bar dataKey="f1" name="F1" fill="#ffb85c" radius={[5,5,0,0]}/></BarChart></ResponsiveContainer></section>
   <section className="panel chart-panel"><div className="section-heading"><div><span>BY CATEGORY</span><h2>各类实体召回率</h2></div></div>{data.categories?.length?<ResponsiveContainer width="100%" height={300}><BarChart data={data.categories} layout="vertical"><CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e7ebf1"/><XAxis type="number" domain={[0,1]} hide/><YAxis dataKey="name" type="category" axisLine={false} tickLine={false} width={58}/><Tooltip formatter={(v:any)=>`${(Number(v)*100).toFixed(1)}%`}/><Bar dataKey="recall" radius={[0,6,6,0]}>{data.categories.map((_,i)=><Cell fill={colors[i%colors.length]} key={i}/>)}</Bar></BarChart></ResponsiveContainer>:<div className="data-empty">本次评估未输出分类指标</div>}</section></div>
  </>:<section className="panel data-empty large"><Database/><strong>暂无可展示的评估结果</strong><p>运行 benchmark 并写入评估结果后，此处会自动生成图表。</p></section>}
 </div>
}
