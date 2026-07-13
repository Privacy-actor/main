import type { BatchJob, CustomRule, DetectResult, EntityType, FinalTextSaveResult, KnowledgeLookup, ProcessingConfig, Project, Span, Strategy } from './types'

const API = import.meta.env.VITE_API_BASE || '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || `请求失败 (${response.status})`)
  }
  return response.json()
}

const json = (body: unknown, method = 'POST'): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => request<{ status: string; mode: string; deployment?: string }>('/health'),
  models: () => request<Record<string, unknown>>('/models'),
  knowledgeStatus: () => request<Record<string, unknown>>('/knowledge/status'),
  knowledgeLookup: (term: string, entityType: EntityType, allowRemote = true) => request<KnowledgeLookup>('/knowledge/lookup', json({ term, entity_type: entityType, allow_remote: allowRemote })),
  detect: (text: string, config: ProcessingConfig, projectId?: string | null) => request<DetectResult>('/detect', json({ text, ...config, project_id: projectId || null })),
  redact: (text: string, spans: Span[], strategy: Strategy, privacyStrength = 2, riskLevel: 'standard' | 'strict' = 'strict') => request<{ redacted_text: string }>('/redact', json({ text, spans, strategy, privacy_strength: privacyStrength, risk_level: riskLevel })),
  extract: (files: File[]) => { const body = new FormData(); files.forEach(file => body.append('files', file)); return request<{ text: string; records: { file: string; row: number; text: string }[]; files: number }>('/extract', { method: 'POST', body }) },
  parseInstruction: (instruction: string, useLlm = true) => request<Record<string, unknown>>('/instructions/parse', json({ instruction, use_llm: useLlm })),
  review: (payload: Record<string, unknown>) => request<{ snapshot: DetectResult }>('/reviews', json(payload)),
  saveFinalText: (taskId: string, text: string, automaticText: string, expectedRevision: number, note?: string) => request<FinalTextSaveResult>(`/tasks/${taskId}/final-text`, json({ text, automatic_text: automaticText, expected_revision: expectedRevision, note: note || null }, 'PUT')),
  getTask: (taskId: string) => request<DetectResult>(`/tasks/${taskId}`),
  deleteTask: (taskId: string) => request<{ ok: boolean }>(`/tasks/${taskId}`, { method: 'DELETE' }),
  purgeTasks: (days: number) => request<{ deleted: number }>(`/tasks?older_than_days=${days}`, { method: 'DELETE' }),
  reviewQueue: () => request<any>('/reviews'),
  history: () => request<{ items: Record<string, unknown>[]; audits: Record<string, unknown>[] }>('/history'),
  evaluations: () => request<any>('/evaluations'),
  policies: () => request<any>('/policies'),
  savePolicies: (policies: Record<string, Strategy>) => request<any>('/policies', json({ policies }, 'PUT')),
  projects: () => request<{ items: Project[] }>('/projects'),
  createProject: (payload: { name: string; description: string; config: ProcessingConfig }) => request<Project>('/projects', json(payload)),
  updateProject: (id: string, payload: Partial<Pick<Project, 'name' | 'description' | 'config'>>) => request<Project>(`/projects/${id}`, json(payload, 'PUT')),
  deleteProject: (id: string) => request<{ ok: boolean }>(`/projects/${id}`, { method: 'DELETE' }),
  rules: (projectId?: string | null) => request<{ items: CustomRule[] }>(`/rules${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  createRule: (payload: Omit<CustomRule, 'id' | 'created_at'>) => request<CustomRule>('/rules', json(payload)),
  updateRule: (id: string, payload: Partial<Omit<CustomRule, 'id' | 'created_at' | 'project_id'>>) => request<CustomRule>(`/rules/${id}`, json(payload, 'PUT')),
  deleteRule: (id: string) => request<{ ok: boolean }>(`/rules/${id}`, { method: 'DELETE' }),
  batch: (files: File[], config: ProcessingConfig, projectId?: string | null) => {
    const body = new FormData()
    files.forEach(file => body.append('files', file, file.webkitRelativePath || file.name))
    body.append('config_json', JSON.stringify(config))
    if (projectId) body.append('project_id', projectId)
    return request<BatchJob>('/jobs', { method: 'POST', body })
  },
  jobs: () => request<{ items: BatchJob[] }>('/jobs'),
  job: (id: string) => request<BatchJob>(`/jobs/${id}`),
  downloadJob: async (id: string) => {
    const response = await fetch(`${API}/jobs/${id}/download`)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail || `HTTP ${response.status}`)
    }
    return response.blob()
  },
  deleteJob: (id: string) => request<{ ok: boolean }>(`/jobs/${id}`, { method: 'DELETE' }),
}
