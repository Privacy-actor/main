const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { loader } = require('./_vite.cjs')

let vite, text, validateEntityRange
before(async () => {
  vite = await loader('range-editor')
  text = await vite.load('/src/lib/text.ts')
  validateEntityRange = (await vite.load('/src/components/EntityRangeEditor.tsx')).validateEntityRange
})
after(async () => { if (vite) await vite.server.close() })

test('补充遗漏时按码点返回全部出现位置', () => {
  assert.deepEqual(text.findOccurrences('😀李明与𠮷李明', '李明'), [{ start: 1, end: 3 }, { start: 5, end: 7 }])
  assert.deepEqual(text.findOccurrences('前👩‍💻后👩‍💻', '👩‍💻'), [{ start: 1, end: 4 }, { start: 5, end: 8 }])
  assert.deepEqual(text.findOccurrences('aaaa', 'aa'), [{ start: 0, end: 2 }, { start: 1, end: 3 }, { start: 2, end: 4 }])
  assert.deepEqual(text.findOccurrences('AB', 'ab'), [])
  assert.deepEqual(text.findOccurrences('AB', ''), [])
  assert.deepEqual(text.findOccurrences('😀', '\ud83d'), [])
})

test('码点位置换算成编辑器选区位置', () => {
  assert.equal(text.codePointToUnit('😀李明', 1), 2)
  assert.equal(text.codePointToUnit('😀李明', 3), 4)
  assert.equal(text.codePointToUnit('abc', 10), 3)
})

test('调整范围的校验', () => {
  assert.equal(validateEntityRange('0', '2', 5), '')
  assert.match(validateEntityRange('3', '2', 5), /结束位置/)
  assert.match(validateEntityRange('0', '9', 5), /0 到 5/)
  assert.match(validateEntityRange('', '2', 5), /完整/)
  assert.match(validateEntityRange('0', '1', 0), /原文为空/)
})

test('知识图谱泛化按实体所在句子的语言选择中文或英文概念', () => {
  const sample = '导师在星河大学任教。\nShe presented it in Shanghai with Dr. Alice Morgan.'
  const chars = Array.from(sample)
  const at = word => { const start = text.findOccurrences(sample, word)[0].start; return [start, start + Array.from(word).length] }
  assert.equal(text.contextLanguage(sample, ...at('星河大学')), 'zh')
  assert.equal(text.contextLanguage(sample, ...at('Shanghai')), 'en')
  assert.equal(text.contextLanguage(sample, ...at('Alice Morgan')), 'en')
  assert.ok(chars.length > 0)
})
