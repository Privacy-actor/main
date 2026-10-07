import { normalizeConfig, type ProcessingConfig } from './types'

// 工作台的“当前方案”和“当前项目”保存在浏览器本地，刷新页面后保持不变。
const CONFIG_KEY = 'privshield.processing-config'
const PROJECT_KEY = 'privshield.project-id'

function read(key: string) {
  try { return localStorage.getItem(key) } catch { return null }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch { /* 隐私模式等情况下忽略 */ }
}

export function loadProcessingConfig(): ProcessingConfig {
  try {
    return normalizeConfig(JSON.parse(read(CONFIG_KEY) || '{}'))
  } catch {
    return normalizeConfig({})
  }
}

export function saveProcessingConfig(config: ProcessingConfig) {
  write(CONFIG_KEY, JSON.stringify(config))
  window.dispatchEvent(new CustomEvent('privshield-config', { detail: config }))
}

export function loadProjectId() {
  return read(PROJECT_KEY) || ''
}

export function saveProjectId(projectId: string) {
  write(PROJECT_KEY, projectId || null)
  window.dispatchEvent(new CustomEvent('privshield-project', { detail: projectId }))
}

const DRAFT_KEY = 'privshield.workbench-draft'

export function loadDraft() {
  return read(DRAFT_KEY)
}

export function saveDraft(text: string) {
  write(DRAFT_KEY, text)
}
