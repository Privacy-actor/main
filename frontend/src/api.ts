import type { DetectResult, Span, Strategy } from './types'

const API = import.meta.env.VITE_API_BASE || '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || `请求失败 (${response.status})`)
  }
  return response.json()
}

export const api = {
  health: () => request<{ status: string; mode: string }>('/health'),
  models: () => request<Record<string, unknown>>('/models'),
  detect: (text: string, strategy: Strategy, useLlm = true) => request<DetectResult>('/detect', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, strategy, use_llm: useLlm, language: 'auto', risk_level: 'strict' }),
  }),
  redact: (text: string, spans: Span[], strategy: Strategy) => request<{ redacted_text: string }>('/redact', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, spans, strategy }),
  }),
  review: (payload: Record<string, unknown>) => request('/reviews', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  reviewQueue: () => request<any>('/reviews'),
  history: () => request<{ items: Record<string, unknown>[]; audits: Record<string, unknown>[] }>('/history'),
  evaluations: () => request<any>('/evaluations'),
  policies: () => request<any>('/policies'),
  savePolicies: (policies: Record<string, Strategy>) => request<any>('/policies', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ policies }) }),
  batch: (file: File, strategy: Strategy) => { const body = new FormData(); body.append('file', file); return request<any>(`/jobs?strategy=${strategy}`, { method: 'POST', body }) },
}
