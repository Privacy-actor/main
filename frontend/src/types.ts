export type EntityType = 'PERSON' | 'ORG' | 'LOCATION' | 'ADDRESS' | 'PHONE' | 'EMAIL' | 'ID_CARD' | 'BANK_CARD' | 'PASSPORT' | 'CUSTOM'
export type Strategy = 'mask' | 'pseudonymize' | 'generalize'
export type Language = 'auto' | 'zh' | 'en' | 'mixed' | 'multilingual'

export interface CustomKeyword {
  value: string
  entity_type: EntityType
  case_sensitive: boolean
}

export interface CustomPattern {
  name: string
  pattern: string
  entity_type: EntityType
  case_sensitive: boolean
}

export interface ProcessingConfig {
  language: Language
  risk_level: 'standard' | 'strict'
  strategy: Strategy
  privacy_strength: number
  use_llm: boolean
  use_policies: boolean
  deployment_mode: 'local' | 'cloud'
  enabled_entity_types: EntityType[]
  custom_keywords: CustomKeyword[]
  custom_patterns: CustomPattern[]
  preserve_terms: string[]
  instruction: string | null
}

export const allEntityTypes: EntityType[] = ['PERSON', 'ORG', 'LOCATION', 'ADDRESS', 'PHONE', 'EMAIL', 'ID_CARD', 'BANK_CARD', 'PASSPORT', 'CUSTOM']

export const defaultProcessingConfig: ProcessingConfig = {
  language: 'auto',
  risk_level: 'strict',
  strategy: 'mask',
  privacy_strength: 2,
  use_llm: true,
  use_policies: false,
  deployment_mode: 'local',
  enabled_entity_types: [...allEntityTypes],
  custom_keywords: [],
  custom_patterns: [],
  preserve_terms: [],
  instruction: null,
}

export interface Project {
  id: string
  name: string
  description: string
  config: ProcessingConfig
  created_at: string
  updated_at: string
}

export interface CustomRule {
  id: string
  project_id: string | null
  created_at: string
  name: string
  kind: 'keyword' | 'regex'
  pattern: string
  entity_type: EntityType
  enabled: boolean
  case_sensitive: boolean
}

export interface Span {
  id: string; start: number; end: number; text: string; entity_type: EntityType
  score: number | null; sources: string[]; status: 'accepted' | 'pending' | 'rejected'
  conflict: boolean; strategy: Strategy; metadata: Record<string, unknown>
}
export interface TraceStep { key: string; label: string; duration_ms: number; count: number; status: 'done' | 'skipped' | 'degraded'; detail: string }
export interface KnowledgeLookup {
  term: string; entity_type: EntityType; levels: string[]; source: string
  status: string; provider: string; detail: string; remote_attempted: boolean
}
export interface DetectResult {
  task_id: string; text: string; spans: Span[]; redacted_text: string; trace: TraceStep[]
  summary: { total: number; pending: number; risk_score: number; by_type: Record<string, number> }
  model: { name: string; enabled: boolean; mode: string; runtime: string }; created_at: string
  final_text: string; final_revision: number; has_manual_edits: boolean
  applied_config: Record<string, unknown>; project_id?: string | null
}

export interface FinalTextSaveResult {
  final_text: string; final_revision: number; has_manual_edits: boolean
  saved_at: string | null; changed: boolean
  audit?: { changed_characters: number; before: string; after: string; before_hash?: string; after_hash?: string }
}

export interface BatchRecord {
  file: string
  row: number
  task_id: string
  redacted_text: string
  final_text: string
  final_revision: number
  has_manual_edits: boolean
  entity_count: number
  pending_count: number
  status: 'completed' | 'needs_review'
}

export interface BatchFailure { file: string; row: number; text_length: number; text_hash: string; error: string }
export interface BatchJob {
  id: string
  project_id: string | null
  created_at: string
  updated_at: string
  status: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed'
  total: number
  processed: number
  failed: number
  progress: number
  payload: { results: BatchRecord[]; failures: BatchFailure[]; files?: string[]; config?: ProcessingConfig }
}
