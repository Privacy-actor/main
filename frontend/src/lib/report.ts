import { BRAND } from '../brand'
import { ENTITY_LABEL, EPSILON, STRATEGY_LABEL, STRENGTH_LABEL } from './entities'
import { formatDateTime } from './format'
import type { AuditEntry, DetectResult, EntityType, RecheckResult, Strategy } from '../types'

const escapeHtml = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character] as string))

const OPERATION_LABEL: Record<string, string> = {
  accept: '确认脱敏', reject: '恢复原文', change_type: '修改类型', add: '补充遗漏', add_many: '补充遗漏（多处）',
  adjust_boundary: '调整范围', set_strategy: '切换脱敏方式', set_span_strategy: '单个实体换方式', set_strength: '调整力度',
  set_replacement: '自定义替换词', accept_many: '批量确认', reject_many: '批量恢复', edit_text: '编辑最终稿',
  restore: '还原大模型回答',
}

export function operationLabel(operation: string) {
  return OPERATION_LABEL[operation] || operation
}

interface ReportInput {
  task: DetectResult
  finalText: string
  revision: number
  audits: AuditEntry[]
  recheck: RecheckResult | null
  projectName?: string
  finalHash: string
}

/**
 * 自然语言要求在报告里只写它起了什么作用，不写原句：原句里常有要隐去或保留的具体词（“隐去星舟”），
 * 写进报告就等于把要隐去的内容又露出来了。
 */
export function describeRequirement(config: Record<string, unknown>) {
  if (!String(config.instruction || '').trim()) return '无'
  const plan = (config.instruction_plan || {}) as Record<string, unknown>
  const count = (key: string) => Array.isArray(plan[key]) ? (plan[key] as unknown[]).length : 0
  const effects: string[] = []
  if (count('preserve_terms')) effects.push(`保留 ${count('preserve_terms')} 个词`)
  if (count('force_terms')) effects.push(`额外隐去 ${count('force_terms')} 个词`)
  if (count('enabled_entity_types') || count('disabled_entity_types') || count('force_types')) effects.push('调整了识别范围')
  if (plan.strategy || (plan.type_strategies && Object.keys(plan.type_strategies as object).length)) effects.push('指定了脱敏方式')
  if (plan.privacy_strength) effects.push('指定了力度')
  if (count('ignored_clauses')) effects.push(`${count('ignored_clauses')} 句暂不支持，按默认方式处理`)
  return `已设置${effects.length ? `：${effects.join('，')}` : ''}（原句可能含有要隐去的词，不写入报告）`
}

/** 生成一页可打印的处理报告。报告只包含脱敏后的内容，不含原文和实体明文。 */
export function buildTaskReport({ task, finalText, revision, audits, recheck, projectName, finalHash }: ReportInput) {
  const config = task.applied_config as Record<string, unknown>
  const strategy = (config.strategy as Strategy) || 'mask'
  const strength = Number(config.privacy_strength || 2)
  const enabled = (config.enabled_entity_types as EntityType[] | undefined) || []
  const active = task.spans.filter(span => span.status !== 'rejected')
  const byType = new Map<EntityType, number>()
  for (const span of active) byType.set(span.entity_type, (byType.get(span.entity_type) || 0) + 1)
  const operations = new Map<string, number>()
  for (const audit of audits) operations.set(audit.operation, (operations.get(audit.operation) || 0) + 1)
  const parameter = strategy === 'pseudonymize' ? `ε = ${EPSILON[strength]}` : strategy === 'generalize' ? `上溯 ${strength} 级` : '统一编号'
  const llm = task.trace.find(step => step.key === 'llm')
  const requirement = describeRequirement(config)

  const rows = (pairs: Array<[string, string]>) => pairs.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')

  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>隐私脱敏处理报告 ${escapeHtml(task.task_id)}</title>
<style>
  :root { --ink:#1b2333; --ink2:#485267; --ink3:#7c8598; --rule:#dce1e8; --cobalt:#2b47c9; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #f1f3f5; color: var(--ink); font: 14px/1.6 "PingFang SC","Microsoft YaHei",sans-serif; }
  .report { max-width: 820px; margin: 32px auto; padding: 48px 56px; background: #fff; box-shadow: 0 10px 30px -18px rgba(27,35,51,.3); }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; padding-bottom: 20px; border-bottom: 2px solid var(--ink); }
  h1 { margin: 0; font: 700 26px/1.3 "Songti SC","STSong","SimSun",serif; letter-spacing: .04em; }
  header p { margin: 6px 0 0; color: var(--ink3); font-size: 13px; }
  button { padding: 7px 14px; border: 1px solid var(--rule); border-radius: 6px; background: #fff; font: inherit; cursor: pointer; }
  h2 { margin: 28px 0 10px; font-size: 15px; }
  dl { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0; margin: 0; border: 1px solid var(--rule); border-radius: 8px; }
  dl div { padding: 10px 14px; border-bottom: 1px solid var(--rule); }
  dl div:nth-child(odd) { border-right: 1px solid var(--rule); }
  dt { color: var(--ink3); font-size: 12px; }
  dd { margin: 2px 0 0; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 8px 10px; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: top; }
  th { color: var(--ink3); font-weight: 500; }
  td.n { text-align: right; font-variant-numeric: tabular-nums; }
  pre { margin: 0; padding: 16px 18px; border-radius: 8px; background: #f7f8fa; white-space: pre-wrap; word-break: break-word; font: 13.5px/1.8 "PingFang SC","Microsoft YaHei",sans-serif; }
  .hash { margin-top: 8px; color: var(--ink3); font: 12px/1.5 Consolas,monospace; word-break: break-all; }
  .pass { color: #2b7a57; } .fail { color: #b3261e; }
  footer { margin-top: 32px; padding-top: 14px; border-top: 1px solid var(--rule); color: var(--ink3); font-size: 12px; }
  @media print { body { background: #fff; } .report { margin: 0; padding: 0; box-shadow: none; max-width: none; } button { display: none; } }
</style></head>
<body><main class="report">
<header><div><h1>隐私脱敏处理报告</h1><p>任务 ${escapeHtml(task.task_id)}，报告生成于 ${escapeHtml(formatDateTime(new Date()))}</p></div><button onclick="window.print()">打印或存为 PDF</button></header>

<h2>处理概况</h2>
<dl>${rows([
  ['所属项目', projectName || '临时方案'],
  ['处理时间', formatDateTime(task.created_at)],
  ['原文长度', `${Array.from(task.text).length} 字`],
  ['识别实体', `${active.length} 处（待确认 ${active.filter(span => span.status === 'pending' || span.conflict).length} 处，已恢复 ${task.spans.length - active.length} 处）`],
  ['大模型核查', llm?.status === 'done' ? '已参与' : llm?.status === 'degraded' ? '调用失败，已降级为规则与 NER' : '未参与'],
  ['最终稿版本', `v${revision}${task.has_manual_edits ? '，含人工修订' : ''}`],
])}</dl>

<h2>处理方案</h2>
<dl>${rows([
  ['脱敏方式', config.use_policies ? '按实体类型分别设置' : STRATEGY_LABEL[strategy]],
  ['力度', `${STRENGTH_LABEL[strength]}（${parameter}）`],
  ['识别范围', enabled.length ? enabled.map(type => ENTITY_LABEL[type]).join('、') : '全部类型'],
  ['待确认实体', config.risk_level === 'standard' ? '先保留原文，确认后替换' : '先脱敏，复核时可恢复'],
  ['自然语言要求', requirement],
  ['部署模式', config.deployment_mode === 'cloud' ? '云端' : '本地'],
])}</dl>

<h2>识别轨迹</h2>
<table><thead><tr><th>环节</th><th>状态</th><th class="n">数量</th><th class="n">耗时</th><th>说明</th></tr></thead><tbody>
${task.trace.map(step => `<tr><td>${escapeHtml(step.label)}</td><td>${step.status === 'done' ? '完成' : step.status === 'skipped' ? '未运行' : '已降级'}</td><td class="n">${step.count}</td><td class="n">${step.duration_ms} ms</td><td>${escapeHtml(step.detail)}</td></tr>`).join('')}
</tbody></table>

<h2>实体统计</h2>
<table><thead><tr><th>类型</th><th class="n">数量</th></tr></thead><tbody>
${[...byType.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => `<tr><td>${escapeHtml(ENTITY_LABEL[type])}</td><td class="n">${count}</td></tr>`).join('') || '<tr><td colspan="2">没有识别出实体</td></tr>'}
</tbody></table>

<h2>人工复核记录</h2>
${operations.size ? `<table><thead><tr><th>操作</th><th class="n">次数</th></tr></thead><tbody>${[...operations.entries()].map(([operation, count]) => `<tr><td>${escapeHtml(operationLabel(operation))}</td><td class="n">${count}</td></tr>`).join('')}</tbody></table>` : '<p>本任务没有人工操作，使用的是自动结果。</p>'}

<h2>导出前复检</h2>
<p class="${recheck && !recheck.passed ? 'fail' : 'pass'}">${recheck ? (recheck.passed ? `通过：未发现必须处理的残留${recheck.medium ? `，另有 ${recheck.medium} 处建议人工确认` : ''}。` : `未通过：发现 ${recheck.high} 处疑似残留。`) : '未执行。'}</p>

<h2>最终稿</h2>
<pre>${escapeHtml(finalText)}</pre>
<p class="hash">SHA-256 ${escapeHtml(finalHash || '浏览器不支持计算')}</p>

<footer>本报告由${BRAND.name}生成，只包含脱敏后的最终稿和处理统计，不包含原文和敏感实体明文。</footer>
</main></body></html>`
}
