const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { loader } = require('./_vite.cjs')

let vite, resultsCsv, toCsv
before(async () => {
  vite = await loader('batch-export')
  resultsCsv = (await vite.load('/src/pages/Batch.tsx')).resultsCsv
  toCsv = (await vite.load('/src/lib/download.ts')).toCsv
})
after(async () => { if (vite) await vite.server.close() })

const record = { file: 'folder/example.txt', row: 1, task_id: 'task-1', status: 'completed', entity_count: 1, pending_count: 0, final_revision: 2, redacted_text: '自动稿中仍有应删除的内容', has_manual_edits: true }
const job = rows => ({ id: 'job_1', payload: { results: rows, failures: [] } })

test('结果表导出保存过的最终稿，即使人工改成了空白也不回退到自动稿', () => {
  const csv = resultsCsv(job([{ ...record, final_text: '' }]))
  const lines = csv.replace(/^﻿/, '').split('\n')
  assert.equal(lines[0], '文件,段落,任务,状态,实体数,待确认,最终稿版本,最终稿')
  assert.equal(lines[1], '"folder/example.txt","1","task-1","已完成","1","0","2",""')
  assert.ok(!csv.includes(record.redacted_text))
})

test('引号、逗号和换行正确转义，旧记录没有最终稿时用自动稿', () => {
  const csv = resultsCsv(job([{ ...record, final_text: '第一行,"备注"\n第二行' }, { ...record, final_text: undefined, status: 'needs_review' }]))
  assert.ok(csv.includes('"第一行,""备注""\n第二行"'))
  assert.ok(csv.includes('"待复核"'))
  assert.ok(csv.endsWith('"自动稿中仍有应删除的内容"'))
})

test('CSV 带 BOM，Excel 打开中文不乱码', () => {
  assert.ok(toCsv(['a'], [['中文']]).startsWith('﻿'))
})
