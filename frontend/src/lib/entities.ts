import type { CSSProperties } from 'react'
import type { EntityType, Span, Strategy } from '../types'

export const ENTITY_LABEL: Record<EntityType, string> = {
  PERSON: '姓名', ORG: '机构', LOCATION: '地点', ADDRESS: '详细地址', PHONE: '电话',
  EMAIL: '邮箱', ID_CARD: '身份证号', BANK_CARD: '银行卡号', PASSPORT: '护照号', ROLE: '职务身份', CUSTOM: '自定义',
}

/** 每类实体用一种颜料色，界面上始终和文字标签一起出现。 */
export function entityStyle(type: EntityType): CSSProperties {
  return { ['--c' as string]: `var(--e-${type})` } as CSSProperties
}

/** 结构化标识符由规则层直接识别，其余为需要上下文判断的语义实体。 */
export const STRUCTURED_TYPES: EntityType[] = ['PHONE', 'EMAIL', 'ID_CARD', 'BANK_CARD', 'PASSPORT']
export const SEMANTIC_TYPES: EntityType[] = ['PERSON', 'ORG', 'LOCATION', 'ADDRESS', 'ROLE']

export const STRATEGY_LABEL: Record<Strategy, string> = {
  mask: '掩码',
  pseudonymize: '差分隐私替换',
  generalize: '知识图谱泛化',
}

export const STRATEGY_SHORT: Record<Strategy, string> = {
  mask: '掩码',
  pseudonymize: '差分隐私',
  generalize: '知识图谱',
}

export const STRATEGY_HINT: Record<Strategy, string> = {
  mask: '替换为【类型-编号】，同一实体全文编号一致',
  pseudonymize: '按指数机制从同类候选中随机抽取替换词',
  generalize: '沿知识图谱上溯，换成更抽象的上位概念',
}

export const STRATEGY_EXAMPLE: Record<Strategy, [string, string]> = {
  mask: ['张伟', '【PERSON-001】'],
  pseudonymize: ['张伟', '林清禾'],
  generalize: ['北京大学', '高等院校'],
}

export const STRENGTH_LABEL = ['', '轻', '标准', '强'] as const

/** 差分隐私替换的 ε 取值与后端 PSEUDONYM_EPSILON 一致：力度越高 ε 越小，替换越随机。 */
export const EPSILON: Record<number, number> = { 1: 4, 2: 1, 3: 0.25 }

export function strengthEffect(strategy: Strategy, strength: number) {
  if (strategy === 'mask') return '掩码不分力度，所有实体统一替换为编号'
  if (strategy === 'pseudonymize') {
    const randomness = ['', '随机性低，替换词与原词更接近', '随机性适中', '随机性高，替换词与原词差异更大'][strength]
    return `ε = ${EPSILON[strength]}，${randomness}`
  }
  return ['', '上溯一级，保留较多语境', '上溯两级', '上溯三级，只保留大类'][strength]
}

const SOURCE_LABEL: Record<string, string> = {
  RULE: '规则', CUSTOM_RULE: '自定义规则', 'NER-LITE': '轻量识别', IMPLICIT: '隐性识别', NER: 'NER 模型', LLM: '大模型', HUMAN: '人工',
}

export function sourceLabel(source: string) {
  return SOURCE_LABEL[source] || source
}

/** 识别轨迹里的层级：用于按层筛选实体。 */
export type Layer = 'rule' | 'ner' | 'llm' | 'human'

export function spanLayers(span: Span): Layer[] {
  const layers = new Set<Layer>()
  for (const source of span.sources) {
    if (source === 'RULE' || source === 'CUSTOM_RULE') layers.add('rule')
    else if (source === 'NER' || source === 'NER-LITE' || source === 'IMPLICIT') layers.add('ner')
    else if (source === 'LLM') layers.add('llm')
    else if (source === 'HUMAN') layers.add('human')
  }
  return [...layers]
}

export function statusLabel(span: Span) {
  if (span.status === 'rejected') return '已恢复原文'
  if (span.status === 'pending' || span.conflict) return '待确认'
  if (span.sources.includes('HUMAN')) return '已人工确认'
  return '自动采纳'
}

export function isPending(span: Span) {
  return span.status !== 'rejected' && (span.status === 'pending' || span.conflict)
}
