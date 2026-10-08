import type {
  AuditEntry, BatchJob, CustomRule, DetectResult, EntityType, FinalTextSaveResult, Health, HistoryItem, InstructionPlan,
  KnowledgeLookup, ModelEndpointUpdate, ModelSettingsView, ModelsInfo, ProcessingConfig, Project, RecheckResult, Replacement, RestoreResult, ReviewQueueItem, Span, Stats, Strategy,
} from './types'

const API = import.meta.env.VITE_API_BASE || '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function detailMessage(detail: unknown, status: number) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: unknown[] }
    return first?.msg ? `请求参数有误：${first.msg}` : `请求失败（${status}）`
  }
  if (status === 0) return '无法连接处理服务，请确认后端已启动'
  if (status === 413) return '上传的文件太大，请分批上传'
  if (status >= 500) return `处理服务出错（${status}），请稍后重试`
  return `请求失败（${status}）`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API}${path}`, init)
  } catch {
    throw new ApiError('无法连接处理服务，请确认后端已启动', 0)
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(detailMessage((data as { detail?: unknown }).detail, response.status), response.status)
  }
  return response.json()
}

async function download(path: string): Promise<{ blob: Blob; filename: string }> {
  let response: Response
  try {
    response = await fetch(`${API}${path}`)
  } catch {
    throw new ApiError('无法连接处理服务，请确认后端已启动', 0)
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(detailMessage((data as { detail?: unknown }).detail, response.status), response.status)
  }
  const disposition = response.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  return { blob: await response.blob(), filename: match?.[1] || 'download' }
}

const json = (body: unknown, method = 'POST'): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => request<Health>('/health'),
  models: () => request<ModelsInfo>('/models'),
  llmSettings: () => request<ModelSettingsView>('/llm/settings'),
  saveLlmSettings: (body: { local?: ModelEndpointUpdate; cloud?: ModelEndpointUpdate; reset?: Array<'local' | 'cloud'> }) =>
    request<ModelSettingsView>('/llm/settings', json(body, 'PUT')),
  llmModels: (body: { target: 'local' | 'cloud'; base_url: string; api_key?: string | null }) =>
    request<{ models: string[]; count: number }>('/llm/models', json(body)),
  testLlm: (body: { target: 'local' | 'cloud'; base_url: string; model: string; api_key?: string | null }) =>
    request<{ ok: boolean; message: string; latency_ms?: number; reply?: string }>('/llm/test', json(body)),
  stats: () => request<Stats>('/stats'),
  knowledgeLookup: (term: string, entityType: EntityType, allowRemote = true, lang: 'zh' | 'en' = 'zh') => request<KnowledgeLookup>('/knowledge/lookup', json({ term, entity_type: entityType, allow_remote: allowRemote, lang })),
  detect: (text: string, config: ProcessingConfig, projectId?: string | null, persist = true) => request<DetectResult>('/detect', json({ text, ...config, project_id: projectId || null, persist })),
  redact: (text: string, spans: Span[], strategy: Strategy | null, privacyStrength = 2, riskLevel: 'standard' | 'strict' = 'strict') =>
    request<{ redacted_text: string; replacements: Replacement[] }>('/redact', json({ text, spans, strategy, privacy_strength: privacyStrength, risk_level: riskLevel })),
  extract: (files: File[]) => {
    const body = new FormData()
    files.forEach(file => body.append('files', file, file.webkitRelativePath || file.name))
    return request<{ text: string; records: { file: string; row: number; text: string }[]; files: number }>('/extract', { method: 'POST', body })
  },
  parseInstruction: (instruction: string, useLlm = true, deploymentMode: 'local' | 'cloud' = 'local') => request<InstructionPlan>('/instructions/parse', json({ instruction, use_llm: useLlm, deployment_mode: deploymentMode })),
  review: (payload: Record<string, unknown>) => request<{ snapshot: DetectResult }>('/reviews', json(payload)),
  reviewQueue: () => request<{ items: ReviewQueueItem[]; total?: number }>('/reviews'),
  saveFinalText: (taskId: string, text: string, automaticText: string, expectedRevision: number, note?: string) =>
    request<FinalTextSaveResult>(`/tasks/${encodeURIComponent(taskId)}/final-text`, json({ text, automatic_text: automaticText, expected_revision: expectedRevision, note: note || null }, 'PUT')),
  getTask: (taskId: string) => request<DetectResult>(`/tasks/${encodeURIComponent(taskId)}`),
  taskAudits: (taskId: string) => request<{ items: AuditEntry[] }>(`/tasks/${encodeURIComponent(taskId)}/audits`),
  recheck: (taskId: string, text?: string) => request<RecheckResult>(`/tasks/${encodeURIComponent(taskId)}/recheck`, json(text === undefined ? {} : { text })),
  /** 把大模型的回答换回原文；taskIds 按优先顺序排列。 */
  restore: (text: string, taskIds: string[]) => request<RestoreResult>('/restore', json({ text, task_ids: taskIds })),
  exportTask: (taskId: string, format: 'txt' | 'docx' | 'json' | 'md') => download(`/tasks/${encodeURIComponent(taskId)}/export?format=${format}`),
  deleteTask: (taskId: string) => request<{ ok: boolean }>(`/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' }),
  purgeTasks: (days: number) => request<{ deleted: number }>(`/tasks?older_than_days=${days}`, { method: 'DELETE' }),
  /** 任务记录：服务端分页和查找（q 同时查原文、结果和任务号）。 */
  history: (options: { limit?: number; auditLimit?: number; offset?: number; q?: string; pending?: boolean; projectId?: string | null } = {}) => {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100), audit_limit: String(options.auditLimit ?? 200), offset: String(options.offset ?? 0) })
    if (options.q) query.set('q', options.q)
    if (options.pending) query.set('pending', 'true')
    if (options.projectId) query.set('project_id', options.projectId)
    return request<{ items: HistoryItem[]; total: number; audits: AuditEntry[] }>(`/history?${query}`)
  },
  policies: () => request<{ policies: Record<string, Strategy>; version: string }>('/policies'),
  savePolicies: (policies: Record<string, Strategy>) => request<{ policies: Record<string, Strategy> }>('/policies', json({ policies }, 'PUT')),
  projects: () => request<{ items: Project[] }>('/projects'),
  createProject: (payload: { name: string; description: string; config: ProcessingConfig }) => request<Project>('/projects', json(payload)),
  updateProject: (id: string, payload: Partial<Pick<Project, 'name' | 'description' | 'config'>>) => request<Project>(`/projects/${encodeURIComponent(id)}`, json(payload, 'PUT')),
  deleteProject: (id: string) => request<{ ok: boolean }>(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  rules: (projectId?: string | null) => request<{ items: CustomRule[] }>(`/rules${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  createRule: (payload: Omit<CustomRule, 'id' | 'created_at'>) => request<CustomRule>('/rules', json(payload)),
  updateRule: (id: string, payload: Partial<Omit<CustomRule, 'id' | 'created_at' | 'project_id'>>) => request<CustomRule>(`/rules/${encodeURIComponent(id)}`, json(payload, 'PUT')),
  deleteRule: (id: string) => request<{ ok: boolean }>(`/rules/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testRule: (payload: { kind: 'keyword' | 'regex'; pattern: string; text: string; case_sensitive: boolean }) =>
    request<{ valid?: boolean; error?: string | null; matches: { start: number; end: number; text: string }[]; count: number }>('/rules/test', json(payload)),
  batch: (files: File[], config: ProcessingConfig, projectId?: string | null) => {
    const body = new FormData()
    files.forEach(file => body.append('files', file, file.webkitRelativePath || file.name))
    body.append('config_json', JSON.stringify(config))
    if (projectId) body.append('project_id', projectId)
    return request<BatchJob>('/jobs', { method: 'POST', body })
  },
  jobs: (projectId?: string | null) => request<{ items: BatchJob[] }>(`/jobs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  job: (id: string) => request<BatchJob>(`/jobs/${encodeURIComponent(id)}`),
  downloadJob: (id: string) => download(`/jobs/${encodeURIComponent(id)}/download`),
  deleteJob: (id: string) => request<{ ok: boolean }>(`/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
