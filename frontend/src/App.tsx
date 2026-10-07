import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { createBrowserRouter, Link, NavLink, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { BookOpen, Braces, Check, ChevronsUpDown, FolderKanban, History, Layers, ListChecks, Plus, ScanText, Server } from 'lucide-react'
import { AppProvider, describeEngines, useApp } from './hooks/AppContext'
import { ToastProvider } from './hooks/Toast'
import { LogoMark } from './components/Logo'
import InkBackdrop from './components/InkBackdrop'
import { BRAND } from './brand'
import { Spinner } from './components/ui'
import './design-system.css'
import './styles.css'

const Workbench = lazy(() => import('./pages/Workbench'))
const Batch = lazy(() => import('./pages/Batch'))
const Review = lazy(() => import('./pages/Review'))
const Projects = lazy(() => import('./pages/Projects'))
const Rules = lazy(() => import('./pages/Rules'))
const History_ = lazy(() => import('./pages/History'))
const System = lazy(() => import('./pages/System'))
const Guide = lazy(() => import('./pages/Guide'))

const navigation = [
  [
    { to: '/workbench', icon: ScanText, label: '工作台' },
    { to: '/batch', icon: Layers, label: '批量处理' },
    { to: '/review', icon: ListChecks, label: '人工复核', badge: 'pending' as const },
  ],
  [
    { to: '/projects', icon: FolderKanban, label: '项目' },
    { to: '/rules', icon: Braces, label: '规则库' },
  ],
  [
    { to: '/history', icon: History, label: '任务记录' },
    { to: '/system', icon: Server, label: '部署与插件' },
  ],
  [
    { to: '/guide', icon: BookOpen, label: '上手指南' },
  ],
]

const GUIDE_SEEN_KEY = 'privshield.guideSeen'

/** 第一次打开时先进上手指南，之后直接进工作台。 */
function Home() {
  let seen = true
  try { seen = Boolean(localStorage.getItem(GUIDE_SEEN_KEY)) } catch { /* 读不了本地存储时直接进工作台 */ }
  return <Navigate to={seen ? '/workbench' : '/guide'} replace/>
}

function ProjectSwitcher() {
  const { projects, currentProject, currentProjectId, projectsError, selectProject } = useApp()
  const [open, setOpen] = useState(false)
  // 记着的项目还没读到（服务没连上或正在读取）时不显示“临时方案”，免得以为项目丢了
  const label = currentProject?.name || (currentProjectId ? (projectsError ? '暂时读不到项目' : '读取中…') : '临时方案')
  const root = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])
  return <div className="project-switch" ref={root}>
    <button type="button" className="project-switch-btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span className="project-switch-copy"><small>当前项目</small><strong>{label}</strong></span>
      <ChevronsUpDown size={15}/>
    </button>
    {open && <div className="project-switch-pop" role="listbox" aria-label="切换项目">
      <button type="button" role="option" aria-selected={!currentProject} onClick={() => { selectProject('', { applyConfig: false }); setOpen(false) }}>
        <span><strong>临时方案</strong><small>不保存到任何项目</small></span>{!currentProject && <Check size={15}/>}
      </button>
      {projects.map(project => <button type="button" role="option" aria-selected={currentProject?.id === project.id} key={project.id} onClick={() => { selectProject(project.id); setOpen(false) }}>
        <span><strong>{project.name}</strong><small>{project.description || '未填写说明'}</small></span>{currentProject?.id === project.id && <Check size={15}/>}
      </button>)}
      <div className="project-switch-foot">
        <button type="button" onClick={() => { setOpen(false); navigate('/projects?new=1') }}><Plus size={14}/>新建项目</button>
        <button type="button" onClick={() => { setOpen(false); navigate('/projects') }}>管理项目</button>
      </div>
    </div>}
  </div>
}

function EngineStatus() {
  const { engine } = useApp()
  const engines = describeEngines(engine.models)
  const offline = engine.state === 'offline'
  const rows = [
    { name: '规则层', value: engines.rules, state: offline ? 'off' : 'on' },
    { name: 'NER 层', value: engines.ner, state: offline ? 'off' : engines.nerModel ? 'on' : engines.nerState === 'failed' ? 'warn' : engines.nerState === 'loading' ? 'idle' : 'lite' },
    { name: '大模型核查', value: engines.llm, state: offline ? 'off' : engines.llmReady ? 'on' : 'idle' },
  ]
  return <Link to="/system" className="engine-status" title="查看部署与引擎状态">
    {offline ? <div className="engine-offline">处理服务未连接</div> : rows.map(row => <div className="engine-row" key={row.name}>
      <i className={`engine-dot is-${row.state}`} aria-hidden="true"/>
      <span className="engine-name">{row.name}</span>
      <span className="engine-value">{engine.state === 'checking' ? '检测中' : row.value}</span>
    </div>)}
  </Link>
}

function Shell() {
  const { stats } = useApp()
  const location = useLocation()
  useEffect(() => { document.getElementById('main')?.focus({ preventScroll: true }) }, [location.pathname])
  return <div className="shell">
    <a className="skip-link" href="#main">跳到主要内容</a>
    <aside className="sidebar">
      <Link to="/workbench" className="brand" aria-label={`${BRAND.name} 工作台`}>
        <LogoMark/>
        <span className="brand-copy"><strong>{BRAND.name}</strong><small>{BRAND.tagline}</small></span>
      </Link>
      <ProjectSwitcher/>
      <nav className="nav" aria-label="主导航">
        {navigation.map((group, index) => <div className="nav-group" key={index}>
          {group.map(item => <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}>
            <item.icon size={18} strokeWidth={1.7} aria-hidden="true"/>
            <span className="nav-label">{item.label}</span>
            {item.badge === 'pending' && stats && stats.pending > 0 && <span className="nav-badge" aria-label={`${stats.pending} 项待复核`}>{stats.pending > 99 ? '99+' : stats.pending}</span>}
          </NavLink>)}
        </div>)}
      </nav>
      <EngineStatus/>
    </aside>
    <InkBackdrop/>
    <main className="main" id="main" tabIndex={-1}>
      <Suspense fallback={<div className="page-loading"><Spinner size={20}/></div>}>
        <Outlet/>
      </Suspense>
    </main>
  </div>
}

function LegacyTaskRedirect() {
  const { taskId = '' } = useParams()
  return <Navigate to={`/workbench?task=${encodeURIComponent(taskId)}`} replace/>
}

function Root() {
  return <ToastProvider><AppProvider><Shell/></AppProvider></ToastProvider>
}

/** 数据路由：页面有未保存的修改时可以用 useBlocker 拦下离开（项目编辑、工作台最终稿）。 */
export const router = createBrowserRouter([{
  element: <Root/>,
  children: [
    { path: '/', element: <Home/> },
    { path: '/guide', element: <Guide/> },
    { path: '/workbench', element: <Workbench/> },
    { path: '/batch', element: <Batch/> },
    { path: '/review', element: <Review/> },
    { path: '/projects', element: <Projects/> },
    { path: '/projects/:projectId', element: <Projects/> },
    { path: '/rules', element: <Rules/> },
    { path: '/history', element: <History_/> },
    { path: '/history/:taskId', element: <LegacyTaskRedirect/> },
    { path: '/system', element: <System/> },
    { path: '*', element: <Navigate to="/workbench" replace/> },
  ],
}])
