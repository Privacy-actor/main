const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { loader } = require('./_vite.cjs')

let vite, buildTaskReport
before(async () => {
  vite = await loader('report')
  buildTaskReport = (await vite.load('/src/lib/report.ts')).buildTaskReport
})
after(async () => { if (vite) await vite.server.close() })

test('处理报告不含原文和实体明文，只含最终稿与统计', () => {
  const task = {
    task_id: 'task_1', text: '联系人李明，电话13800138000', created_at: '2026-10-02T03:00:00Z',
    spans: [
      { id: 'a', start: 3, end: 5, text: '李明', entity_type: 'PERSON', status: 'accepted', sources: ['NER-LITE'], conflict: false, strategy: 'mask', metadata: {}, score: 0.92 },
      { id: 'b', start: 8, end: 19, text: '13800138000', entity_type: 'PHONE', status: 'accepted', sources: ['RULE'], conflict: false, strategy: 'mask', metadata: {}, score: 0.99 },
    ],
    redacted_text: '联系人【PERSON-001】，电话【PHONE-001】', trace: [], summary: { total: 2, pending: 0, risk_score: 20, by_type: {} },
    model: { name: '', enabled: false, mode: '', runtime: '' }, final_text: '', final_revision: 0, has_manual_edits: false,
    applied_config: { strategy: 'mask', privacy_strength: 2, enabled_entity_types: ['PERSON', 'PHONE'] },
  }
  const html = buildTaskReport({ task, finalText: task.redacted_text, revision: 0, audits: [], recheck: { passed: true, high: 0, medium: 0, findings: [], checked_characters: 20, checked_at: '' }, finalHash: 'abc' })
  assert.ok(html.includes('【PERSON-001】'))
  assert.ok(!html.includes('李明'))
  assert.ok(!html.includes('13800138000'))
  assert.match(html, /本报告由墨隐生成/)
})
