export type EntityType = 'PERSON' | 'ORG' | 'LOCATION' | 'ADDRESS' | 'PHONE' | 'EMAIL' | 'ID_CARD' | 'BANK_CARD'
export type Strategy = 'mask' | 'pseudonymize' | 'generalize'

export interface Span {
  id: string; start: number; end: number; text: string; entity_type: EntityType
  score: number | null; sources: string[]; status: 'accepted' | 'pending' | 'rejected'
  conflict: boolean; strategy: Strategy; metadata: Record<string, unknown>
}
export interface TraceStep { key: string; label: string; duration_ms: number; count: number; status: 'done' | 'skipped' | 'degraded'; detail: string }
export interface DetectResult {
  task_id: string; text: string; spans: Span[]; redacted_text: string; trace: TraceStep[]
  summary: { total: number; pending: number; risk_score: number; by_type: Record<string, number> }
  model: { name: string; enabled: boolean; mode: string; runtime: string }; created_at: string
}
