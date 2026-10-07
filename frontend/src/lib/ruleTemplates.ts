import type { EntityType } from '../types'

export interface RuleTemplate {
  key: string
  name: string
  description: string
  kind: 'keyword' | 'regex'
  pattern: string
  entity_type: EntityType
  sample: string
}

/** 内置规则之外常见的标识符。正则按后端 Python re 语法编写，默认不区分大小写。 */
export const RULE_TEMPLATES: RuleTemplate[] = [
  {
    key: 'landline', name: '固定电话', entity_type: 'PHONE', kind: 'regex',
    description: '区号加号码，如 010-62751234。内置规则只覆盖手机号和国际格式。',
    pattern: String.raw`(?<![\d-])0\d{2,3}-\d{7,8}(?![\d-])`,
    sample: '办公室电话 010-62751234，传真 0571-87654321 转 8。',
  },
  {
    key: 'student-id', name: '学号', entity_type: 'CUSTOM', kind: 'regex',
    description: '以入学年份开头的 10 位学号，如 2021123456。',
    pattern: String.raw`(?<!\d)20\d{8}(?!\d)`,
    sample: '学号 2021123456 的同学已提交材料，订单 202112345678 不算。',
  },
  {
    key: 'employee-id', name: '工号', entity_type: 'CUSTOM', kind: 'regex',
    description: '字母前缀加数字的员工编号，如 EMP004521。可按单位的编号规则修改前缀。',
    pattern: String.raw`(?<![A-Za-z0-9])(?:EMP|EID|NO)[-_]?\d{4,8}(?![A-Za-z0-9])`,
    sample: '员工工号 EMP004521，另一位是 NO-20341。',
  },
  {
    key: 'plate', name: '车牌号', entity_type: 'CUSTOM', kind: 'regex',
    description: '省份简称加字母和号码，含新能源车牌，如 京A·12345。',
    pattern: String.raw`[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-HJ-NP-Z]·?[A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]`,
    sample: '车牌京A·12345 的车辆停在门口，另一辆是沪B8F9D2。',
  },
  {
    key: 'uscc', name: '统一社会信用代码', entity_type: 'CUSTOM', kind: 'regex',
    description: '18 位企业和机构代码，如 91310115MA1K4ABC2X。形如身份证号（第 7 到 14 位是出生日期）的号码不算。',
    pattern: String.raw`(?<![0-9A-Z])(?!\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9X](?![0-9A-Z]))[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Z])`,
    sample: '统一社会信用代码 91310115MA1K4ABC2X，法人身份证 110105199003071239 不算。',
  },
  {
    key: 'ipv4', name: 'IPv4 地址', entity_type: 'CUSTOM', kind: 'regex',
    description: '日志、工单中的 IP 地址，不会误伤 1.2.3 这样的版本号。',
    pattern: String.raw`(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])`,
    sample: '登录来源 192.168.31.20，网关 10.0.0.1，版本 1.2.3 不算。',
  },
  {
    key: 'wechat', name: '微信号', entity_type: 'CUSTOM', kind: 'regex',
    description: '“微信：”“微信号”之后的账号。',
    pattern: String.raw`(?:(?<=微信号[:：])|(?<=微信[:：])|(?<=微信号 )|(?<=微信 ))[a-zA-Z][-_a-zA-Z0-9]{5,19}`,
    sample: '加我微信：lin_rn2024 或者微信号 star-boat88。',
  },
  {
    key: 'qq', name: 'QQ 号', entity_type: 'CUSTOM', kind: 'regex',
    description: '“QQ：”“QQ号”之后的 5 到 11 位号码。',
    pattern: String.raw`(?:(?<=QQ[:：])|(?<=QQ号[:：])|(?<=QQ )|(?<=QQ号 ))[1-9]\d{4,10}`,
    sample: 'QQ：123456789，备用 QQ号 98765。',
  },
  {
    key: 'hk-macau-permit', name: '港澳通行证', entity_type: 'PASSPORT', kind: 'regex',
    description: 'C 开头的 9 位证件号，含新旧两种格式。',
    pattern: String.raw`(?<![A-Za-z0-9])C[0-9A-Z]\d{7}(?!\d)`,
    sample: '港澳通行证号 C12345678，新版 CA1234567。',
  },
  {
    key: 'contract', name: '合同编号', entity_type: 'CUSTOM', kind: 'regex',
    description: 'HT 开头的合同编号，如 HT-2024-0318。可按单位的编号规则修改。',
    pattern: String.raw`(?<![A-Za-z0-9])HT-?\d{4}-?\d{3,6}(?!\d)`,
    sample: '合同编号 HT-2024-0318 已归档，HT20230456 也是。',
  },
  {
    key: 'medical-record', name: '病历号', entity_type: 'CUSTOM', kind: 'regex',
    description: '“病历号：”“住院号：”“病案号：”之后的编号。',
    pattern: String.raw`(?:(?<=病历号[:：])|(?<=住院号[:：])|(?<=病案号[:：]))[A-Za-z0-9]{6,12}`,
    sample: '病历号：MR20240318，住院号：Z0098765。',
  },
]
