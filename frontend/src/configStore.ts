import { defaultProcessingConfig, type ProcessingConfig } from './types'

const CONFIG_KEY = 'privshield.processing-config'
const PROJECT_KEY = 'privshield.project-id'

export function loadProcessingConfig(): ProcessingConfig {
  try {
    const parsed = JSON.parse(localStorage.getItem(CONFIG_KEY) || '{}')
    return { ...defaultProcessingConfig, ...parsed }
  } catch {
    return { ...defaultProcessingConfig }
  }
}

export function saveProcessingConfig(config: ProcessingConfig) {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config))
  window.dispatchEvent(new CustomEvent('privshield-config', { detail: config }))
}

export function loadProjectId() {
  return localStorage.getItem(PROJECT_KEY) || ''
}

export function saveProjectId(projectId: string) {
  if (projectId) localStorage.setItem(PROJECT_KEY, projectId)
  else localStorage.removeItem(PROJECT_KEY)
  window.dispatchEvent(new CustomEvent('privshield-project', { detail: projectId }))
}
