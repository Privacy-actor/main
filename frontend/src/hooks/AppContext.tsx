import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import { loadProjectId, saveProcessingConfig, saveProjectId } from '../configStore'
import { normalizeConfig, type Health, type ModelsInfo, type Project, type Stats } from '../types'

type EngineState = 'checking' | 'online' | 'offline'

interface AppContextValue {
  engine: { state: EngineState; health: Health | null; models: ModelsInfo | null }
  refreshEngine: () => Promise<void>
  projects: Project[]
  projectsLoaded: boolean
  /** 最近一次读取项目列表失败（多半是处理服务没连上）。此时列表保持上一次的结果。 */
  projectsError: boolean
  refreshProjects: () => Promise<Project[]>
  currentProjectId: string
  currentProject: Project | null
  selectProject: (id: string, options?: { applyConfig?: boolean }) => void
  stats: Stats | null
  refreshStats: () => Promise<void>
}

const AppContext = createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [engine, setEngine] = useState<AppContextValue['engine']>({ state: 'checking', health: null, models: null })
  const [projects, setProjects] = useState<Project[]>([])
  const [projectsLoaded, setProjectsLoaded] = useState(false)
  const [projectsError, setProjectsError] = useState(false)
  const [currentProjectId, setCurrentProjectId] = useState(loadProjectId())
  const [stats, setStats] = useState<Stats | null>(null)
  const engineRequest = useRef(0)
  const projectsRef = useRef<Project[]>([])
  const lastEngineState = useRef<EngineState>('checking')

  const refreshEngine = useCallback(async () => {
    const id = ++engineRequest.current
    const [health, models] = await Promise.allSettled([api.health(), api.models()])
    if (id !== engineRequest.current) return
    setEngine({
      state: health.status === 'fulfilled' ? 'online' : 'offline',
      health: health.status === 'fulfilled' ? health.value : null,
      models: models.status === 'fulfilled' ? models.value : null,
    })
  }, [])

  const refreshProjects = useCallback(async () => {
    try {
      const { items } = await api.projects()
      projectsRef.current = items
      setProjects(items)
      setProjectsLoaded(true)
      setProjectsError(false)
      return items
    } catch {
      // 服务没连上时保留已有的列表和当前项目，不能当成“项目被删除了”
      setProjectsError(true)
      return projectsRef.current
    }
  }, [])

  const refreshStats = useCallback(async () => {
    try { setStats(await api.stats()) } catch { /* 状态栏数字取不到时保持上一次结果 */ }
  }, [])

  const selectProject = useCallback((id: string, options: { applyConfig?: boolean } = {}) => {
    setCurrentProjectId(id)
    saveProjectId(id)
    if (options.applyConfig !== false) {
      const project = projects.find(item => item.id === id)
      if (project) saveProcessingConfig(normalizeConfig(project.config))
    }
  }, [projects])

  useEffect(() => {
    void refreshEngine()
    void refreshProjects()
    void refreshStats()
    const timer = window.setInterval(() => { void refreshEngine() }, 30_000)
    const onProject = (event: Event) => setCurrentProjectId(String((event as CustomEvent).detail || ''))
    window.addEventListener('privshield-project', onProject)
    return () => { window.clearInterval(timer); window.removeEventListener('privshield-project', onProject) }
  }, [refreshEngine, refreshProjects, refreshStats])

  // 处理服务从断开恢复后，重新读取项目和统计
  useEffect(() => {
    if (engine.state === 'online' && lastEngineState.current === 'offline') {
      void refreshProjects()
      void refreshStats()
    }
    if (engine.state !== 'checking') lastEngineState.current = engine.state
  }, [engine.state, refreshProjects, refreshStats])

  // 当前项目被删除后回到临时方案（只在成功读到项目列表时判断）
  useEffect(() => {
    if (projectsLoaded && !projectsError && currentProjectId && !projects.some(project => project.id === currentProjectId)) {
      setCurrentProjectId('')
      saveProjectId('')
    }
  }, [projectsLoaded, projectsError, projects, currentProjectId])

  const value = useMemo<AppContextValue>(() => ({
    engine, refreshEngine, projects, projectsLoaded, projectsError, refreshProjects, currentProjectId,
    currentProject: projects.find(project => project.id === currentProjectId) || null,
    selectProject, stats, refreshStats,
  }), [engine, refreshEngine, projects, projectsLoaded, projectsError, refreshProjects, currentProjectId, selectProject, stats, refreshStats])

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp() {
  const value = useContext(AppContext)
  if (!value) throw new Error('useApp must be used inside AppProvider')
  return value
}

/** 由服务状态推导出三层引擎在界面上的说法。 */
export function describeEngines(models: ModelsInfo | null) {
  const local = models?.endpoints?.local
  const cloud = models?.endpoints?.cloud
  // 模型名可能是 Windows 路径，按两种分隔符取最后一段
  const llmShort = (name: string) => name.split(/[\\/]/).filter(Boolean).pop() || name
  const nerState: 'off' | 'loading' | 'ready' | 'failed' = !models?.ner_enabled ? 'off'
    : models.ner_status?.state === 'failed' ? 'failed'
    : models.ner_status?.state === 'idle' ? 'loading' : 'ready'
  const nerName = llmShort(models?.ner_status?.model || models?.ner || '')
  return {
    rules: models?.rules ? `内置 ${models.rules.builtin_patterns} 类规则` : '内置规则',
    ner: nerState === 'ready' ? nerName : nerState === 'loading' ? `${nerName}，加载中` : nerState === 'failed' ? '模型未加载，使用轻量识别器' : '轻量识别器',
    nerModel: nerState === 'ready',
    nerState,
    llm: local?.enabled ? `本地 ${llmShort(local.model)}` : cloud?.enabled ? `云端 ${llmShort(cloud.model)}` : '未连接',
    llmReady: Boolean(local?.enabled || cloud?.enabled),
    localLlm: Boolean(local?.enabled),
    cloudLlm: Boolean(cloud?.enabled),
    localName: local?.enabled ? llmShort(local.model) : '',
    cloudName: cloud?.enabled ? llmShort(cloud.model) : '',
  }
}
