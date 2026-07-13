import { lazy, Suspense, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { Activity, Beaker, Clock3, Files, ScanSearch, ShieldCheck, Sparkles } from 'lucide-react'
import { api } from './api'
const Workbench=lazy(()=>import('./pages/Workbench'))
const Review=lazy(()=>import('./pages/Review'))
const Batch=lazy(()=>import('./pages/Batch'))
const Evaluation=lazy(()=>import('./pages/Evaluation'))
const History=lazy(()=>import('./pages/History'))

const nav = [
  { to: '/workbench', icon: ScanSearch, label: '隐私工作台' },
  { to: '/review', icon: ShieldCheck, label: '人工复核' },
  { to: '/batch', icon: Files, label: '批量处理' },
  { to: '/evaluation', icon: Beaker, label: '评估实验室' },
  { to: '/history', icon: Clock3, label: '历史与策略' },
]

export default function App() {
  const [online, setOnline] = useState<boolean | null>(null)
  const [mode, setMode] = useState('检测中')
  const [modelName,setModelName]=useState('读取模型配置中')
  useEffect(() => {
    api.health().then(v => { setOnline(v.status === 'ok'); setMode(v.mode === 'llm' ? '大模型在线模式' : '轻量降级模式') }).catch(() => setOnline(false))
    api.models().then(v=>{const active=typeof v.active==='string'?v.active:'';setModelName(active.split('/').pop()||'未配置大模型');if(typeof v.provider==='string')setMode(v.provider)}).catch(()=>setModelName('模型配置不可用'))
  }, [])
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">跳转到主要内容</a>
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><ShieldCheck size={22}/></div><div><strong>PrivShield</strong><span>隐私盾 · Research</span></div></div>
      <div className="project-chip"><Sparkles size={14}/><span>中英文本智能脱敏</span></div>
      <nav aria-label="主导航">{nav.map(item => <NavLink key={item.to} to={item.to} className={({isActive}) => isActive ? 'nav-item active' : 'nav-item'}><item.icon size={18}/><span>{item.label}</span></NavLink>)}</nav>
      <div className="sidebar-bottom">
        <div className="model-card"><div className="model-head"><span className={`status-dot ${online ? 'online' : online === false ? 'offline' : ''}`}/><span>{online ? '系统在线' : online === false ? '连接失败' : '连接中'}</span></div><strong title={modelName}>{modelName}</strong><small>{mode}</small></div>
        <div className="version"><Activity size={13}/> v0.1.0 · 审计已启用</div>
      </div>
    </aside>
    <main className="main-content" id="main-content">
      <Suspense fallback={<div className="page loading-page" aria-live="polite">正在加载页面…</div>}>
      <Routes>
        <Route path="/workbench" element={<Workbench/>}/><Route path="/review" element={<Review/>}/>
        <Route path="/batch" element={<Batch/>}/><Route path="/evaluation" element={<Evaluation/>}/>
        <Route path="/history" element={<History/>}/><Route path="*" element={<Navigate to="/workbench" replace/>}/>
      </Routes>
      </Suspense>
    </main>
  </div>
}
