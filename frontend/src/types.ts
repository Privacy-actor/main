export type EntityType = 'PERSON' | 'ORG' | 'LOCATION' | 'ADDRESS' | 'PHONE' | 'EMAIL' | 'ID_CARD' | 'BANK_CARD' | 'PASSPORT' | 'ROLE' | 'CUSTOM'
export type Strategy = 'mask' | 'pseudonymize' | 'generalize'
export type StrategyMode = 'uniform' | 'by_type'
export type Language = 'auto' | 'zh' | 'en' | 'mixed' | 'multilingual'
export type DeploymentMode = 'local' | 'cloud'

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
  deployment_mode: DeploymentMode
  enabled_entity_types: EntityType[]
  custom_keywords: CustomKeyword[]
  custom_patterns: CustomPattern[]
  preserve_terms: string[]
  instruction: string | null
  /** 2：识别范围包含职务身份之后保存的方案。旧方案没有这个字段。 */
  scope_version: number
}

export const allEntityTypes: EntityType[] = ['PERSON', 'ORG', 'LOCATION', 'ADDRESS', 'PHONE', 'EMAIL', 'ID_CARD', 'BANK_CARD', 'PASSPORT', 'ROLE', 'CUSTOM']

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
  scope_version: 2,
}

/** 职务身份加入之前的十类实体。旧方案勾选了这十类，表示“全部类型”，读取时补上职务身份。 */
const LEGACY_ENTITY_TYPES: EntityType[] = ['PERSON', 'ORG', 'LOCATION', 'ADDRESS', 'PHONE', 'EMAIL', 'ID_CARD', 'BANK_CARD', 'PASSPORT', 'CUSTOM']

/** 只保留处理配置字段，避免把任务快照里的其他字段混进请求。 */
export function normalizeConfig(value: Partial<ProcessingConfig> | Record<string, unknown> | null | undefined): ProcessingConfig {
  const source = (value || {}) as Partial<ProcessingConfig>
  const pick = <K extends keyof ProcessingConfig>(key: K): ProcessingConfig[K] => (source[key] ?? defaultProcessingConfig[key]) as ProcessingConfig[K]
  const enabled = Array.isArray(source.enabled_entity_types) && source.enabled_entity_types.length ? source.enabled_entity_types.filter(type => allEntityTypes.includes(type)) : [...allEntityTypes]
  const legacy = !(Number(source.scope_version) >= 2)
  if (legacy && !enabled.includes('ROLE') && LEGACY_ENTITY_TYPES.every(type => enabled.includes(type))) enabled.push('ROLE')
  return {
    language: pick('language'),
    risk_level: pick('risk_level'),
    strategy: pick('strategy'),
    privacy_strength: Math.min(3, Math.max(1, Number(pick('privacy_strength')) || 2)),
    use_llm: Boolean(pick('use_llm')),
    use_policies: Boolean(pick('use_policies')),
    deployment_mode: pick('deployment_mode') === 'cloud' ? 'cloud' : 'local',
    enabled_entity_types: enabled.length ? enabled : [...allEntityTypes],
    custom_keywords: Array.isArray(source.custom_keywords) ? source.custom_keywords : [],
    custom_patterns: Array.isArray(source.custom_patterns) ? source.custom_patterns : [],
    preserve_terms: Array.isArray(source.preserve_terms) ? source.preserve_terms : [],
    instruction: typeof source.instruction === 'string' && source.instruction.trim() ? source.instruction : null,
    scope_version: 2,
  }
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

export interface Replacement {
  span_id: string; start: number; end: number; out_start: number; out_end: number
  replacement: string; strategy: Strategy; entity_type: EntityType
}

export interface TraceStep { key: string; label: string; duration_ms: number; count: number; status: 'done' | 'skipped' | 'degraded'; detail: string }

export interface KnowledgeLookup {
  term: string; entity_type: EntityType; levels: string[]; source: string
  status: string; provider: string; detail: string; remote_attempted: boolean
}

export interface InstructionPlan {
  enabled_entity_types?: EntityType[]
  /** 明确不处理的类型（“邮箱不要脱敏”） */
  disabled_entity_types?: EntityType[]
  /** 一定处理的类型 */
  force_types?: EntityType[]
  preserve_terms?: string[]
  force_terms?: string[]
  strategy?: Strategy | null
  /** 只针对某类实体的方式（“姓名用差分隐私替换”） */
  type_strategies?: Partial<Record<EntityType, Strategy>>
  privacy_strength?: number | null
  /** 没有对应设置、按默认方式整段处理的分句（“电话号码只保留后四位”） */
  ignored_clauses?: string[]
  parser?: string
}

export interface DetectResult {
  task_id: string; text: string; spans: Span[]; redacted_text: string; trace: TraceStep[]
  summary: { total: number; pending: number; risk_score: number; by_type: Record<string, number> }
  model: { name: string; enabled: boolean; mode: string; runtime: string; location?: string }; created_at: string
  final_text: string; final_revision: number; has_manual_edits: boolean
  applied_config: Record<string, unknown> & { instruction_plan?: InstructionPlan | null }
  project_id?: string | null
  replacements?: Replacement[]
  persisted?: boolean
  review_notice?: string
}

export interface FinalTextSaveResult {
  final_text: string; final_revision: number; has_manual_edits: boolean
  saved_at: string | null; changed: boolean
  audit?: { changed_characters: number; before: string; after: string; before_hash?: string; after_hash?: string }
}

export interface RecheckFinding { start: number; end: number; text: string; entity_type: EntityType; severity: 'high' | 'medium'; reason: string }
export interface RecheckResult { passed: boolean; high: number; medium: number; findings: RecheckFinding[]; checked_characters: number; checked_at: string }

export interface AuditEntry { id: number; task_id: string; created_at: string; operation: string; payload: Record<string, unknown> }

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
  by_type?: Record<string, number>
  /** deleted：这一段的任务已在任务记录里删除 */
  status: 'completed' | 'needs_review' | 'deleted'
  file_index?: number
  kind?: 'text' | 'table'
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

export interface HistoryItem {
  id: string; created_at: string; preview: string; entity_count: number; risk: 'low' | 'medium' | 'high'
  pending?: number; project_id?: string | null; strategy?: Strategy | null; use_policies?: boolean
  has_manual_edits?: boolean; final_revision?: number; text_length?: number
}

export interface Stats { tasks: number; entities: number; pending: number; edited_tasks: number; jobs: number; audits: number; by_type: Record<string, number> }

export interface Health { status: string; mode: string; version?: string; database?: string; deployment?: string; cloud_llm?: boolean }

export interface ModelsInfo {
  active: string; enabled: boolean; provider: string; ner: string; ner_enabled?: boolean; ner_threshold?: number
  /** NER 模型的加载状态：disabled 未启用，idle 加载中，ready 可用，failed 加载失败（已退回轻量识别器） */
  ner_status?: { state: 'disabled' | 'idle' | 'ready' | 'failed'; model: string; detail: string }
  confidence_threshold?: number
  semantic?: { state: string; detail: string; model: string }
  knowledge_graph?: { enabled: boolean; state: string; provider: string; local_entries: number; detail: string }
  rules?: { builtin_patterns: number; entity_types: number }
  knowledge_entries?: number
  pseudonym_epsilon?: Record<string, number>
  endpoints?: { local: { enabled: boolean; model: string; host: string }; cloud: { enabled: boolean; model: string; host: string } }
}

export interface ModelEndpointView {
  enabled: boolean; provider: string; base_url: string; model: string
  api_key_set: boolean; api_key_hint: string
  /** env：沿用 .env 的配置；user：在网页里设置过 */
  source: 'env' | 'user'
}

export interface ModelSettingsView { local: ModelEndpointView; cloud: ModelEndpointView; editable: boolean }

export interface ModelEndpointUpdate { enabled: boolean; provider: string; base_url: string; model: string; api_key?: string | null }

export interface ReviewQueueItem {
  task_id: string; context: string; context_offset: number; text_length?: number
  created_at?: string; project_id?: string | null; span: Span; reason: string
}
