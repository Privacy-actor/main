const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { loader } = require('./_vite.cjs')

let vite, AnnotatedText, RedactedText, diffParts, changedCharacterCount
before(async () => {
  vite = await loader('editor')
  AnnotatedText = (await vite.load('/src/components/AnnotatedText.tsx')).default
  RedactedText = (await vite.load('/src/components/RedactedText.tsx')).default
  const editor = await vite.load('/src/components/FinalTextEditor.tsx')
  diffParts = editor.diffParts
  changedCharacterCount = editor.changedCharacterCount
})
after(async () => { if (vite) await vite.server.close() })

for (const prefix of ['', '😀', '𠮷', '👩‍💻']) {
  test(`原文高亮按码点定位：前缀为 ${prefix || '普通文本'}`, () => {
    const text = prefix + '电话13800138000；尾部'
    const start = Array.from(prefix + '电话').length
    const span = { id: 'phone', start, end: start + 11, text: '13800138000', entity_type: 'PHONE', status: 'accepted', sources: ['RULE'], conflict: false, strategy: 'mask', metadata: {}, score: 1 }
    const html = renderToStaticMarkup(React.createElement(AnnotatedText, { text, spans: [span], onSelect() {} }))
    // 类型标签是 CSS 伪元素（data-label），不在文字里：拖选原文时不会选进标签
    assert.match(html, /role="button"[^>]*data-label="电话"[^>]*>13800138000<\/span>/)
    assert.ok(html.includes(prefix + '电话'))
    assert.match(html, /；尾部/)
  })
}

test('偏移与原文对不上的实体不渲染，避免高亮错位', () => {
  const span = { id: 'bad', start: 0, end: 2, text: '王五', entity_type: 'PERSON', status: 'accepted', sources: [], conflict: false, strategy: 'mask', metadata: {}, score: 1 }
  const html = renderToStaticMarkup(React.createElement(AnnotatedText, { text: '李明来了', spans: [span] }))
  assert.doesNotMatch(html, /role="button"/)
  assert.match(html, /李明来了/)
})

test('掩码结果渲染为墨条，括号保留在可复制的文本里', () => {
  const text = '联系人【PERSON-001】'
  const replacements = [{ span_id: 'p', start: 3, end: 5, out_start: 3, out_end: 15, replacement: '【PERSON-001】', strategy: 'mask', entity_type: 'PERSON' }]
  const html = renderToStaticMarkup(React.createElement(RedactedText, { text, replacements }))
  assert.match(html, /class="tok-mask"><span class="bracket">【<\/span>PERSON-001<span class="bracket">】<\/span>/)
})

test('最终稿差异只标出改动的部分', () => {
  assert.deepEqual(diffParts('原文', '新增原文'), { prefix: '', before: '', after: '新增', suffix: '原文' })
  assert.deepEqual(diffParts('张三来了', '某人来了'), { prefix: '', before: '张三', after: '某人', suffix: '来了' })
  assert.equal(changedCharacterCount('abc', 'abc'), 0)
  assert.equal(changedCharacterCount('abcdef', 'abXYef'), 2)
})
