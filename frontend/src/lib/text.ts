// 后端按 Unicode 码点计算位置（与 Python 一致），JavaScript 字符串按 UTF-16 计。
// 这里集中处理两者之间的换算，避免表情、扩展汉字之后的高亮错位。

export function codePoints(text: string): string[] {
  return Array.from(text)
}

export function sliceCodePoints(chars: string[], start: number, end: number) {
  return chars.slice(start, end).join('')
}

/** 码点位置 → UTF-16 位置（用于 textarea 选区）。 */
export function codePointToUnit(text: string, index: number) {
  let units = 0
  let points = 0
  for (const character of text) {
    if (points === index) return units
    units += character.length
    points += 1
  }
  return units
}

/** 原文中某段文本的全部出现位置（码点）。 */
export function findOccurrences(text: string, query: string): Array<{ start: number; end: number }> {
  if (!query) return []
  const boundaries = new Map<number, number>([[0, 0]])
  let units = 0
  let points = 0
  for (const character of Array.from(text)) {
    units += character.length
    boundaries.set(units, ++points)
  }
  const matches: Array<{ start: number; end: number }> = []
  let offset = text.indexOf(query)
  while (offset !== -1) {
    const start = boundaries.get(offset)
    const end = boundaries.get(offset + query.length)
    if (start !== undefined && end !== undefined) matches.push({ start, end })
    offset = text.indexOf(query, offset + 1)
  }
  return matches
}

/** 中文、英文字符占比，用于提示文本语种构成。 */
export function languageMix(text: string) {
  let cjk = 0
  let latin = 0
  for (const character of text) {
    if (/[一-鿿]/.test(character)) cjk += 1
    else if (/[A-Za-z]/.test(character)) latin += 1
  }
  const total = cjk + latin
  if (!total) return null
  return { zh: Math.round((cjk / total) * 100), en: Math.round((latin / total) * 100) }
}

export function describeLanguage(text: string) {
  const mix = languageMix(text)
  if (!mix) return ''
  if (mix.zh >= 90) return '中文'
  if (mix.en >= 90) return '英文'
  return `中英混合（中文 ${mix.zh}%）`
}

export function countCharacters(text: string) {
  return Array.from(text).length
}

/** 实体所在句子的语言（与后端 context_language 一致）：英文单词明显多于中文时视为英文语境。位置按码点。 */
export function contextLanguage(text: string, start: number, end: number, window = 48): 'zh' | 'en' {
  const characters = Array.from(text)
  const isBreak = (character: string) => /[\n。！？!?；;]/.test(character)
  let left = start
  while (left > Math.max(0, start - window) && !isBreak(characters[left - 1])) left -= 1
  let right = end
  while (right < Math.min(characters.length, end + window) && !isBreak(characters[right])) right += 1
  const around = `${characters.slice(left, start).join('')} ${characters.slice(end, right).join('')}`
  const latinWords = around.match(/[A-Za-z]+/g)?.length || 0
  const cjk = Array.from(around).filter(character => /[\u4e00-\u9fff]/.test(character)).length
  if (!latinWords && !cjk) {
    const own = characters.slice(start, end).join('')
    return /[A-Za-z]/.test(own) && !/[\u4e00-\u9fff]/.test(own) ? 'en' : 'zh'
  }
  return latinWords > cjk / 1.7 ? 'en' : 'zh'
}
