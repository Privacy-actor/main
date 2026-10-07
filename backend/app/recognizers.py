import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

import regex as user_regex

from .config import settings
from .knowledge_base import CITY_PROVINCE, MUNICIPALITIES, PROVINCE_REGION
from .schemas import EntityType, Span, Strategy, TraceStep


@dataclass(frozen=True)
class PatternSpec:
    entity_type: EntityType
    regex: re.Pattern[str]
    score: float
    validator: object | None = None
    # 需要结合上下文排除的格式（目前只有护照号：与订单号、设备编号等格式相同）
    context: str | None = None


def _luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 12 <= len(digits) <= 19:
        return False
    total, parity = 0, len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _cn_id(value: str) -> bool:
    value = value.upper()
    if not re.fullmatch(r"\d{17}[0-9X]", value):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    checks = "10X98765432"
    return checks[sum(int(value[i]) * weights[i] for i in range(17)) % 11] == value[-1]


PATTERNS = [
    PatternSpec(EntityType.EMAIL, re.compile(r"(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", re.I), 0.99),
    PatternSpec(EntityType.PHONE, re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), 0.99),
    PatternSpec(EntityType.PHONE, re.compile(r"(?<!\d)(?:\+?(?:1|44|61|81|82|65)[-. ]?)?\(?\d{2,4}\)?[-. ]\d{3,4}[-. ]\d{4}(?!\d)"), 0.94),
    # 国内座机：区号（010、02X、0XXX）+ 7-8 位本地号，可带括号、空格、分机号（“0571-87654321 转 8001”）
    PatternSpec(EntityType.PHONE, re.compile(
        r"(?<![\d-])(?:\(0(?:10|2\d|[3-9]\d{2})\)\s?|0(?:10|2\d|[3-9]\d{2})[- ]?)[2-9]\d{2,3}[- ]?\d{4}"
        r"(?:\s*(?:转|分机|ext\.?)\s*\d{1,6})?(?!\d)", re.I), 0.95),
    PatternSpec(EntityType.ID_CARD, re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"), 0.995, _cn_id),
    PatternSpec(EntityType.PASSPORT, re.compile(r"(?<![A-Z0-9])(?:[EGDSP]\d{8}|[A-Z]{1,2}\d{6,9})(?![A-Z0-9])", re.I), 0.94, None, "passport"),
    PatternSpec(EntityType.PASSPORT, re.compile(r"(?<=passport)(?:[EGDSP]\d{8}|[A-Z]{1,2}\d{6,9})(?![A-Z0-9])", re.I), 0.94),
    # 银行卡号不以 0 开头（以 0 开头的多是座机）；有 Luhn 校验，或紧跟“银行卡”“卡号”等说法
    PatternSpec(EntityType.BANK_CARD, re.compile(r"(?<!\d)[1-9](?:[ -]?\d){11,18}(?!\d)"), 0.97, _luhn),
]
# 只在产品流水线和导出前复检里使用的规则：紧跟“身份证”“银行卡号”等说法的号码，校验位写错也照样隐去。
# 第三层（finetune/）的上游不含这两条：校验位错误的号码按设计由大模型核查层补回（primitives.py 第 [8] 项）
CUE_PATTERNS = [
    PatternSpec(EntityType.ID_CARD, re.compile(r"(?<!\d)\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)"), 0.95, None, "id_cue"),
    PatternSpec(EntityType.BANK_CARD, re.compile(r"(?<!\d)[1-9](?:[ -]?\d){14,18}(?!\d)"), 0.93, None, "bank_cue"),
]

_ORG_SUFFIX = (
    r"(?:股份有限公司|有限责任公司|有限公司|公司|研究院|研究所|研究中心|设计院|规划院|科学院|实验室|工作室|合作社"
    r"|(?:咨询|服务|数据|研发|培训|检测|医疗|体检|技术|创新|物流|客服|运营|科技|文化|会展|孵化|政务|疾控)中心"
    r"|(?:公安|税务|教育|卫生|财政|统计|气象|交通|民政|商务|海关|市场监督管理|生态环境|自然资源|住建)局|派出所"
    r"|大学|学院|中学|小学|学校|幼儿园|医院|银行(?![卡账帐])|集团|委员会|法院|检察院|事务所|基金会|协会|商会|联合会|研究会|俱乐部|出版社|报社|杂志社|电视台)"
)
CN_ORG_CONTEXT_PATTERN = re.compile(rf"(?:就读于|任职于|来自|工作于|毕业于|供职于|隶属于|在)\s*([\u4e00-\u9fff·&]{{2,30}}?{_ORG_SUFFIX})")
# 不带上下文的机构名：取最短的“名称+后缀”，再去掉开头混进来的语境词（见 _trim_org_prefix）
CN_ORG_PATTERN = re.compile(rf"([\u4e00-\u9fff·&]{{2,30}}?{_ORG_SUFFIX})")
_EN_ORG_SUFFIX = (
    r"University|College|Hospital|Clinic|Institute|Academy|Company|Corporation|Corp\.?|Ltd\.?|Limited|Inc\.?|LLC|Bank|Foundation|School|Group"
    r"|Holdings|Partners|Association|Laboratory|Laboratories|Labs|Center|Centre|Cooperative|Council|Society|Agency|Trust|Ventures|Capital"
    r"|Consulting|Technologies|Systems|Solutions|Industries|Studios?|Network|Alliance|Federation|Authority|Ministry|Museum|Library"
)
EN_ORG_PATTERN = re.compile(rf"(?<![A-Za-z])((?:[A-Z][A-Za-z&.-]*[ \t]+){{1,5}}(?:{_EN_ORG_SUFFIX}))(?![A-Za-z])")
# 两个首字母大写的词，第二个是行业词或第一个是问候语时，不当作人名（Falcon Engineering、Hey Arthur）
_EN_NOT_NAME_SECOND = {
    "engineering", "learning", "digital", "materials", "telecom", "foods", "food", "finance", "financial", "insurance", "software", "energy",
    "environmental", "infrastructure", "imaging", "diagnostics", "data", "media", "logistics", "labs", "tech", "technology", "technologies",
    "systems", "solutions", "capital", "consulting", "analytics", "pharma", "biotech", "health", "healthcare", "medical", "security",
    "automation", "studio", "studios", "ventures", "network", "networks", "robotics", "motors", "trading", "industries", "manufacturing",
    "construction", "design", "communications", "electronics", "holdings", "partners", "tower", "terrace", "street", "road", "avenue",
    "lane", "park", "square", "plaza", "building", "center", "centre", "impact", "items", "assessment", "report", "review", "office",
}
_EN_CALENDAR_WORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december", "today", "tomorrow", "yesterday", "morning", "afternoon", "evening",
}
_EN_NOT_NAME_SECOND |= _EN_CALENDAR_WORDS | {
    "team", "teams", "department", "dept", "division", "unit", "committee", "board", "group", "results", "result", "meeting", "meetings",
    "summary", "update", "updates", "plan", "plans", "project", "projects", "program", "programme", "day", "week", "month", "year", "quarter",
    "season", "festival", "holiday", "conference", "workshop", "session", "policy", "agreement", "contract", "form", "letter", "notice",
    "street", "airport", "station", "river", "lake", "mountain", "island", "city", "county", "state", "province", "district", "delhi",
    # 部门、职务、街道（Customer Service、Human Resources、Facilities Coordinator、Oakwood Drive）
    "service", "services", "support", "resources", "relations", "affairs", "operations", "success", "care", "desk", "management",
    "administration", "accounting", "compliance", "procurement", "purchasing", "research", "development", "product", "products",
    "quality", "assurance", "control", "planning", "strategy", "coordinator", "manager", "director", "officer", "assistant", "specialist",
    "analyst", "engineer", "consultant", "representative", "administrator", "supervisor", "intern", "associate", "president",
    "executive", "secretary", "clerk", "technician", "transcript", "minutes", "parkway", "boulevard", "drive", "way", "court",
    "highway", "crescent", "alley", "view", "heights", "gardens", "campus", "village", "estate",
}
_EN_NOT_NAME_FIRST = {"hey", "hi", "hello", "dear", "thanks", "thank", "mentor", "please", "contact", "call", "email", "ask", "tell",
                      "meet", "visit", "welcome", "sincerely", "regards", "best", "cheers", "good", "privacy", "action", "the",
                      "last", "next", "this", "that", "these", "those", "every", "each", "our", "your", "their", "his", "her", "its", "my",
                      "new", "old", "sales", "marketing", "finance", "quarterly", "annual", "monthly", "weekly", "daily", "final", "first",
                      "second", "third", "project", "global", "general", "senior", "junior", "chief", "head", "north", "south", "east", "west",
                      "united", "national", "international", "happy", "merry", "see", "from", "with", "and", "for", "about", "after", "before",
                      # 句首的虚词、称呼和表格栏目（Did Julia、Alright Zachary、Per Exhibit、Respondent Nina、When Lucy）
                      "did", "does", "do", "alright", "okay", "ok", "per", "interview", "respondent", "respondents", "when", "where", "while",
                      "if", "so", "but", "yes", "also", "then", "maybe", "perhaps", "sorry", "oh", "well", "now", "here", "there", "since",
                      "because", "although", "though", "unless", "until", "during", "via", "re", "subject", "attn", "attention", "note",
                      "customer", "customers", "human", "public", "technical", "facilities", "facility", "information", "legal", "client",
                      "clients", "vendor", "staff", "employee", "member", "applicant", "patient", "student", "officer", "agent", "exhibit",
                      "section", "appendix", "item", "case", "ticket", "order", "invoice", "application", "request", "form", "minutes",
                      } | _EN_CALENDAR_WORDS

# 地址：行政区划 + 道路/小区 + 门牌，起点必须在非汉字或介词之后，避免把“上周在”之类的语境吞进来
# 以省级行政区或已知城市开头时，前面是动词也可以（“常驻上海市…”“地址写江苏省…”）
_KNOWN_REGION_START = "|".join(sorted(
    [f"{name}省" for name in PROVINCE_REGION if name not in MUNICIPALITIES and name not in {"香港", "澳门", "内蒙古", "广西", "西藏", "宁夏", "新疆", "台湾"}]
    + ["台湾省", "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区"]
    + [f"{name}市" for name in MUNICIPALITIES] + [f"{name}市" for name in CITY_PROVINCE],
    key=len, reverse=True,
))
_ADDRESS_START = rf"(?:(?<![一-鿿])|(?<=[在于到从住往至自是处为])|(?=(?:{_KNOWN_REGION_START})))"
# 行政区划名里不会出现的介词、动词和标签字，防止“寄往上海市”“住址北京市”“具体武汉市”被整体当作市名
_ADDR_CJK = r"(?:(?![在于到从住往至自是处为寄送发去来回位的和与及址具])[一-鿿])"
_ADDRESS_PROVINCE = rf"{_ADDR_CJK}{{2,4}}(?:省|自治区)"
_ADDRESS_CITY = rf"{_ADDR_CJK}{{2,4}}市"
_ADDRESS_DISTRICT = rf"{_ADDR_CJK}{{1,5}}(?:区|县|旗)"
_ADDRESS_TOWN = rf"{_ADDR_CJK}{{2,6}}(?:街道|镇|乡)"
_ADDRESS_REGION = (
    rf"(?:{_ADDRESS_PROVINCE}(?:{_ADDRESS_CITY})?(?:{_ADDRESS_DISTRICT})?|{_ADDRESS_CITY}(?:{_ADDRESS_DISTRICT})?|{_ADDRESS_DISTRICT})"
    rf"(?:{_ADDRESS_TOWN})?"
)
_SPACE = r"[ 　]?"
_ADDRESS_PLACE = r"[^\s，。；：、,.;:!?！？()（）“”\"'的和与及在]{1,12}(?:大道|大街|路|街|巷|弄|胡同|小区|社区|花园|大厦|园区|(?<=[一二三四五六七八九十])里)"
# 路段（人民南路四段、一环路南一段、天府大道北段）
_ADDRESS_SEGMENT = r"(?:[东南西北中]?[一二三四五六七八九十0-9]{0,3}段)"
_ADDRESS_NUMBER = rf"[甲乙丙丁]?[0-9一二三四五六七八九十百零]{{1,5}}{_SPACE}(?:号院|号)(?:附[0-9一二三四五六七八九十]{{1,4}}号)?"
# 门牌之后的楼宇、楼栋、楼层、房间：“SOHO现代城A座1205室”“日月广场双子座D栋701室”“写字楼35层3508”，可带单个空格
_ADDR_TAIL_CHAR = r"(?:[^\s，。；：、,.;:!?！？()（）“”\"'的和与及在]|[ 　](?=[0-9A-Za-z一-鿿]))"
_ADDRESS_UNIT = rf"(?:{_ADDR_TAIL_CHAR}{{0,14}}?(?:号楼|栋|幢|座|单元|层|楼|室)(?:[0-9]{{2,5}}(?![0-9号]))?)"
ADDRESS_PATTERN = re.compile(
    rf"{_ADDRESS_START}{_ADDRESS_REGION}(?:{_SPACE}{_ADDRESS_PLACE}{_ADDRESS_SEGMENT}?(?:{_SPACE}{_ADDRESS_NUMBER})?|{_SPACE}{_ADDRESS_NUMBER})"
    rf"{_ADDRESS_UNIT}{{0,4}}"
)
# 没有省市区、但有道路加门牌（“唐家湾镇创新路18号B栋3层”“文体路88号”），或小区、大厦加楼栋楼层（“锦绣花园6栋”“颐景大厦19层”）
# 没有行政区划时，道路名或小区名里不能有省市区县、介词和标签字，否则会把“住址”“位于”和前面的省市一起吞进来
_STREET_CHAR = r"(?:(?![在于到从住往至自是处为寄送发去来回位的和与及址具省市区县])[^\s，。；：、,.;:!?！？()（）“”\"'])"
_STREET_PLACE = rf"{_STREET_CHAR}{{1,10}}(?:大道|大街|路|街|巷|弄|胡同)"
_ADDRESS_ESTATE = rf"{_STREET_CHAR}{{1,10}}(?:小区|花园|社区|大厦|园区|公寓|家园|新村)"
ADDRESS_STREET_PATTERN = re.compile(
    rf"(?:(?<![一-鿿])|(?<=[在于到从住往至自是处为址的]))(?:{_ADDRESS_TOWN})?"
    rf"(?:{_STREET_PLACE}{_ADDRESS_SEGMENT}?{_SPACE}{_ADDRESS_NUMBER}{_ADDRESS_UNIT}{{0,4}}"
    rf"|{_ADDRESS_ESTATE}(?:{_SPACE}[0-9A-Za-z一二三四五六七八九十零]{{1,5}}{_SPACE}(?:号楼|栋|幢|座|单元|层|楼|室)(?:[0-9]{{2,5}}(?![0-9号]))?){{1,4}})"
)
# 英文街道地址：门牌 + 街名 + Street/Road/Avenue…，可带 Apt/Suite、城市、州与邮编（742 Evergreen Terrace, Springfield, IL 62704）
_EN_STREET_SUFFIX = (r"(?:Street|Road|Avenue|Boulevard|Lane|Drive|Terrace|Way|Parkway|Court|Place|Square|Highway|Crescent|Close|Circle|Row|Alley|Plaza"
                     r"|(?:St|Rd|Ave|Blvd|Ln|Dr|Pkwy|Ct|Pl|Sq|Hwy|Cir)\.?)")
EN_ADDRESS_PATTERN = re.compile(
    r"(?<![\w-])\d{1,6}[A-Z]?(?:-\d{1,4})?[ ]+"
    rf"(?:(?:(?:[A-Z][a-z]+|\d{{1,3}}(?:st|nd|rd|th))[ ]+){{1,4}}{_EN_STREET_SUFFIX}|Broadway)"
    r"(?:[ ]+(?:North|South|East|West|N|S|E|W|NE|NW|SE|SW)\b)?(?![A-Za-z])"
    r"(?:,?[ ]+(?:Apt|Apartment|Suite|Ste|Unit|Floor|Fl|Room|Rm|Building|Bldg|Warehouse)\.?[ ]*#?[A-Za-z0-9-]{1,6}\b)*"
    r"(?:,[ ]*[A-Z][a-z]+(?:[ ]+[A-Z][a-z]+){0,2}(?![A-Za-z])){0,2}"
    r"(?:,?[ ]+(?:[A-Z]{2}[ ]+\d{5}(?:-\d{4})?|[A-Z]{1,2}\d[A-Z\d]?[ ]+\d[A-Z]{2})\b)?"
    r"(?:,[ ]*(?:UK|USA|US|China)\b)?"
)
LOCATION_PATTERN = re.compile(
    r"(?:北京市|上海市|天津市|重庆市|浙江省|江苏省|广东省|四川省|湖北省|山东省|海淀区|朝阳区|浦东新区"
    r"|杭州|北京|上海|广州|深圳|南京|武汉|成都|重庆|西安|天津|苏州|长沙|郑州|青岛|厦门|香港|澳门"
    # 拉丁地名必须是完整的词：comparison 里的 paris 不算
    r"|(?<![A-Za-z])(?:London|Beijing|Shanghai|Shenzhen|Guangzhou|Hangzhou|Nanjing|Wuhan|Chengdu|New York|New Delhi|Mumbai|Tokyo|Osaka"
    r"|Singapore|Hong Kong|Macau|Taipei|Paris|Berlin|Munich|Madrid|Rome|Milan|Amsterdam|Zurich|Geneva|Vienna|Moscow|Dubai|Bangkok"
    r"|Sydney|Melbourne|Seoul|Boston|Seattle|San Francisco|Los Angeles|Toronto|Vancouver|Montreal|Chicago)(?![A-Za-z]))",
    re.I,
)
# 中文地名前面的字和地名首字组成常用词时不是地名（完成都、隆重庆祝、东西安全）
_LOCATION_FALSE_START = {
    "成都": set("完赞组形促达造变构合养落收长制生换当做看写改分建练学老现速集酿编译折"),
    "重庆": set("隆"),
    "西安": set("东"),
}

CN_SURNAMES = (
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤常温康施文牛樊葛邢安齐易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆农司卓古吉缪简车项连芦麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻苑迟邝"
)
_CN_COMPOUND_SURNAMES = "欧阳|司马|诸葛|上官|东方|慕容|令狐|皇甫|尉迟|公孙|夏侯|长孙|宇文|司徒|端木|独孤|南宫|呼延"
_CN_NAME = rf"(?:(?:{_CN_COMPOUND_SURNAMES})[\u4e00-\u9fff]{{1,2}}|[{CN_SURNAMES}][\u4e00-\u9fff]{{1,2}})"
_CN_PERSON_CUES = "我叫|名叫|叫做|姓名|联系人|采访对象|受访者|负责人|经办人|申请人|收件人|寄件人|委托人|当事人|患者|学生|员工|同事|客户|导师是|老师是|签名|署名"
_CN_TITLES = "教授|老师|博士|医生|大夫|先生|女士|小姐|同学|经理|主任|院长|校长|律师|总监|主管|处长|科长|局长|书记|工程师|研究员|护士|警官|师傅"
# 线索词之后的姓名：以常见姓氏开头，长度 2-4 字
CN_PERSON_PATTERN = re.compile(rf"(?:{_CN_PERSON_CUES})(?:姓名)?\s*[：:是为|｜]?\s*({_CN_NAME})")
# 亲属、对接与签字关系后的姓名（其母王秀兰、妻子李秀英、对接人倪嘉清、家属 | 刘小红）。没有冒号等分隔时，
# 以兼作常用字的姓氏开头的不算（“会员单位”“爱人和孩子”）
_CN_RELATION_CUES = (
    "其母|其父|其子|其女|其妻|其夫|母亲|父亲|儿子|女儿|妻子|丈夫|爱人|配偶|家属|亲属|监护人|对接人|合伙人|陪同人|参会人|主治医师|主治医生"
    "|责任护士|法定代表人|法人代表|收款人|付款人|持卡人|开户人|户主|房东|租户|业主|证人|借款人|担保人|审批人|审核人|填表人|报告人|发起人"
    "|接收人|代表|会员|同学|室友|朋友|邻居|搭档"
)
CN_PERSON_RELATION_PATTERN = re.compile(rf"(?:{_CN_RELATION_CUES})\s*([：:是为|｜]?)\s*({_CN_NAME})")
# 用顿号列出的几个姓名（参会人有王伟、陈静、李强；同行的还有刘伟、张敏和李静）：每一项都必须像姓名，否则整串不算
_CN_LIST_ITEM = rf"(?:(?:{_CN_COMPOUND_SURNAMES})[\u4e00-\u9fff]{{1,2}}?|[{CN_SURNAMES}][\u4e00-\u9fff]{{1,2}}?)"
CN_PERSON_LIST_PATTERN = re.compile(
    r"(?:(?<=^)|(?<=[，。；：:、\s（(“\"！？!?,;/有是和与及请由让给]))"
    rf"({_CN_LIST_ITEM}(?:、{_CN_LIST_ITEM})+(?:(?:以及|和|与|及){_CN_LIST_ITEM})?)"
    r"(?=[^\u4e00-\u9fff]|$|等|都|也|一起|共同|分别|几|两|三|同学|老师|参加|出席|负责|在|是|的|已|将|会|还|又|和)"
)
# 线索词之后、不以常见姓氏开头的姓名，置信度较低，交给人工确认
CN_PERSON_LOOSE_PATTERN = re.compile(rf"(?:我叫|姓名|联系人|采访对象(?:姓名)?)[：:]?\s*([\u4e00-\u9fff·]{{2,4}})(?![\u4e00-\u9fff])")
# 姓名后紧跟称谓：陈晓峰教授、王芳女士。用零宽匹配逐字尝试，取紧挨称谓的姓名
CN_PERSON_TITLE_PATTERN = re.compile(rf"(?=({_CN_NAME})(?:{_CN_TITLES}))")
# 这些姓氏也常作普通字（于、高、项目、车间…），两字或紧跟在其他汉字之后时不认作姓名
_AMBIGUOUS_SURNAMES = set("于和向方安成时明文关白高常全应师车路古单金华项任")
# 以姓氏字开头的常用词：线索词或称谓后面跟的是这些词时不是姓名（“患者高血压”“员工应当遵守”“学生时代”）
_NOT_NAME_WORDS = frozenset("""
于是 于今 于此 和谐 和睦 和蔼 和好 向前 向后 向上 向下 向来 向往 向着 方面 方法 方便 方向 方案 方式 方针 方才
安全 安排 安装 安静 安心 安置 安检 成功 成本 成绩 成果 成为 成立 成熟 成员 成长 成就 成交 时代 时间 时候 时期
时刻 时常 时尚 时光 时机 明天 明年 明白 明确 明显 明细 明日 文件 文化 文章 文字 文本 文档 文明 文学 关于 关系
关注 关键 关心 关闭 关联 白天 白色 白班 高度 高血 高兴 高级 高效 高速 高中 高考 高温 高烧 高热 高龄 高层 高管
高校 高新 高压 常常 常用 常见 常规 常年 常住 全部 全面 全体 全国 全程 全额 全年 全天 应当 应该 应用 应急 应对
应聘 应届 师生 师资 师范 车间 车辆 车站 车主 车位 路上 路线 路口 古代 单位 单独 单据 单身 金额 金融 华东 华北
华南 华中 华西 项目 任务 任何 任职 任期 周末 周年 周一 周二 周三 周四 周五 周六 周日 周边 周期 马上 黄金 夏天
夏季 温度 康复 石头 万一 万元 毛病 宁可 包括 包含 齐全 余额 程序 程度 董事 钱包 曾经 曾于 许多 许可 史上 陆续
谢谢 谢绝 严格 严重 严肃 钟头 甘心 祝贺 祝福 符合 舒适 舒服 纪律 纪念 童年 凌晨 毕业 毕竟 季度 季节 游戏 游客
辛苦 管理 管道 房间 房子 房屋 房产 解决 解释 尤其 阳光 农业 农村 司机 司法 简单 简历 连续 连接 景点 党员 费用
冷静 卫生 卫星 宗教 边境 商品 商场 商业 商务 沙发 荣誉 查询 查看 麻烦 迟到 江湖 林业 黎明 施工 章程 蓝色 雷达
武器 孔子 牛奶 龙头 夏令 秦朝 唐朝 宋朝 冯氏 汤圆 杨柳 罗列 苏醒 叶子 陈列 陈述 张开 张贴 王国 王者 李子 刘海
包装 包裹 白板 白纸 明明 金价 房价 房租 车费 余下 余地 万事 万物 史料 管控 管家 苗头 季风 段落 雷同 杜绝 韩剧 习惯
申请 申报 申诉 申明 何时 何地 何人 何况 何必 何种 何处 何为 郑重 韩国 范围 范例 范本 付款 付费 付出 顾客 顾问 岳父 岳母
欧洲 欧元 尚未 尚且 尚可 颜色 仲裁 熊猫 石油 汪洋 周围 黄昏 马路 胡说 胡乱 莫非 莫名 左边 左侧 庞大 庄重 庄严 盛大 吉祥
卓越 谷歌 骆驼 蒙古 柴油 席位 桂花 隋朝 刁难 甄别 巩固 池塘 宋体 唐突 肖像 田野 钟表 陶瓷 贺卡 贺信 焦点 梅花 涂改 曲线
米饭 柏油 宫殿 仇恨 桑拿 郎中 丛林
""".split())


def _clean_cn_name(value: str) -> str | None:
    """线索词后面的候选姓名：遇到虚词截断（“时代的”→“时代”），是常用词（“高血压”“应当遵”）就不是姓名。"""
    for index, char in enumerate(value[1:], start=1):
        if char in _NAME_STOP_CHARS:
            value = value[:index]
            break
    if len(value) < 2 or value[:2] in _NOT_NAME_WORDS or value in _NOT_NAME_WORDS:
        return None
    # 姓氏加称谓（李经理、王老师、张总、刘哥）按规格不算姓名：本身不识别具体的人
    for offset in (1, 2):
        rest = value[offset:]
        if rest and (rest[0] in _HONORIFIC_CHARS or any(rest.startswith(title) for title in _TITLE_WORDS)):
            return None
    return value


_TITLE_WORDS = tuple(_CN_TITLES.split("|"))
_HONORIFIC_CHARS = set("总哥姐叔姨嫂")
# 名字部分不会出现的虚词
_NAME_STOP_CHARS = set("于和在的是与及了被把向对从到给跟让由为们个这那些就都也还又再很最更已曾将会能要间")
EN_PERSON_CONTEXT_PATTERN = re.compile(
    r"(?:(?i:\b(?:I am|I'm|my name is|name:|dear|hi|hello|sincerely|regards|best|thanks|cheers),?)\s+|\b(?:Mr|Ms|Mrs|Miss|Dr|Prof)\.?\s+)"
    r"([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})\b"
)
# 名和姓之间只允许空格：署名下一行的部门名（John Smith\nCustomer Service）不算进姓名
EN_PERSON_PATTERN = re.compile(r"(?<![A-Za-z])(?:Mr\.?|Ms\.?|Dr\.?)?[ \t]*([A-Z][a-z]{2,}[ \t]+[A-Z][a-z]{2,})(?![A-Za-z])")

_ORG_SUFFIX_RE = re.compile(rf"{_ORG_SUFFIX}$")
_ORG_LEAD_BREAK = re.compile(r"[在于是到从和与及的向对由为给跟将把被们我你他她它]")
# 机构名前常见的语境词（“那个杭州智联基金会”“根据太原医疗信息有限公司”）
_ORG_LEAD_PHRASE = re.compile(r"^(?:这个|那个|这家|那家|这|那|什么|根据|按照|关于|通过|经过|甲方|乙方|并且?|但是?|而|接|去|来|代表|联合|协调|申请|投递|抄送了?|吐槽说|说|据)")
_BARE_COMPANY_NOISE = re.compile(r"[在于是到从和与及的向对由为给跟将把被们我你他她它已经前回去来寄发送]")
# 机构名的名称部分只有指代、时间、数量词时是泛指（这所学校、去年学校、我们公司），不是具体机构
_ORG_GENERIC_NAME = re.compile(
    r"^(?:这|那|哪|每|各|该|本|此|其|某|一|两|几|多|我|你|他|她|咱|我们|你们|他们|她们|咱们|大家|去年|今年|明年|前年|当年|附近|当地|本地|外地|周边"
    r"|所有|全部|其他|其它|任何|同一|整个|原来|现在|以前|之前|新|老|大|小)[所家个座间位些们的]*$"
)
_ORG_REGION_RE = re.compile("|".join(sorted(set(CITY_PROVINCE) | set(PROVINCE_REGION), key=len, reverse=True)))

# 职务身份（隐性隐私）：部门或科室 + 职务。机构级单位（医院、学院、公司）由机构识别处理，这里只看机构内部的部门。
_ROLE_TITLE_RE = re.compile(
    "副主任医师|主任医师|副主任|主任|副处长|处长|副科长|科长|副部长|部长|总经理|副经理|经理|总监|主管|组长|"
    "班主任|副书记|书记|副所长|所长|站长|队长|护士长|负责人"
)
_ROLE_UNITS = sorted(["科", "室", "系", "处", "部", "部门", "班", "组", "办", "队", "站", "中心", "车间", "教研室", "研究室", "实验室", "办公室"], key=len, reverse=True)
_ROLE_NAME_STOP = set("的了在是和与及或向对从到给跟让由为们个这那找问请位名任当做见我你他她它其该据经听称叫令使若如而但且并又也还都就才只把被将：:，,。；;、 ")
# 部门名前面常见的时间、语气词，不属于部门名（“后来车间主任”“待会儿技术部负责人”）
_ROLE_NAME_LEAD = re.compile(
    r"^(?:后来|然后|之后|以后|随后|接着|待会儿?|一会儿|刚才|刚刚|现在|目前|当时|最近|今天|昨天|明天|前天|后天|那天|这天|当年|如今|平时|已经|曾经|正在|马上|立刻|还是|就是|也是|都是|原来|本来"
    r"|联系|咨询|请教|询问|告诉|通知|转告|交给|转给|汇报给?|报告给?|抄送给?|拜访|采访|感谢|麻烦|拜托|委托|安排|邀请|提醒)+"
)
EN_ROLE_PATTERN = re.compile(
    r"\b(?:(?i:head|chair|director|dean|manager|chief|lead|principal)\s+of\s+(?:the\s+)?(?:[A-Z][a-z]+\s+){1,4}"
    r"(?:Department|Office|Division|Unit|Lab|Laboratory|Team|School|Faculty|Ward)"
    r"|(?:[A-Z][a-z]+\s+){1,3}(?:Department|Ward|Office|Division)\s+(?i:head|chair|director|manager|lead))\b"
)


_TITLE_BEFORE_NAME_RE = re.compile("主任|经理|老师|医生|大夫|教授|处长|科长|部长|院长|校长|总监|律师|护士长|同学|师傅")
# 姓名后面常接的词：“李明反馈”“王芳表示”里，第三个字属于后面的词，不是名字
_AFTER_NAME_WORDS = (
    "反馈", "反映", "表示", "认为", "指出", "介绍", "来电", "来信", "投诉", "提出", "要求", "希望", "同意", "确认", "签字",
    "负责", "参加", "出席", "回复", "说明", "告诉", "联系", "发来", "收到", "办理", "申请", "咨询", "询问", "报告", "汇报",
    "通知", "主持", "讲话", "发言", "提到", "提醒", "建议", "称", "说", "讲", "问", "答",
    "对接", "沟通", "协调", "见面", "会面", "联络", "合作", "共同", "讨论", "商量", "核实", "提交", "提供", "递交", "签署", "签订",
    "担任", "发现", "抱怨", "登记", "填写", "出具", "陪同", "前往", "赶到", "来到", "离开", "出发", "入职", "离职", "就读", "就诊",
    "住院", "出院", "代表", "同学", "本人", "立刻", "马上", "尽快", "务必", "必须", "一直", "刚刚", "随后", "亲自",
    "查房", "另有", "主刀", "接诊", "会诊", "值班", "签发", "审批", "批准", "带队", "牵头", "主讲",
    # 联系方式类说法：“赵敏电话135…”里的“电”属于后面的词
    "电话", "手机", "邮箱", "微信", "地址", "住址", "身份证", "电邮", "座机", "传真", "工号", "学号", "护照",
)


# 没有线索词时，以常见姓氏开头、前面是句首或标点、后面紧跟“表示”“下周”“的电话”“，138…”这类说法的 2-3 字，
# 多半是姓名。置信度低于阈值，交给大模型或人工确认（严格模式下照常脱敏）
_NAME_FOLLOWERS = "|".join(sorted(
    set(_AFTER_NAME_WORDS) | {"下周", "下个月", "明天", "今天", "昨天", "刚才", "已经", "正在", "一起", "也", "都", "住在", "来自", "毕业于", "任职于",
                              "电话", "手机", "邮箱", "微信", "身份证"},
    key=len, reverse=True,
))
_CONTACT_AFTER_DE = (r"的(?:办公|家庭|工作|私人|个人|联系|紧急联系|住宅|移动|手机)?"
                     r"(?:电话|手机号?|邮箱|地址|住址|身份证号?|微信号?|账号|工号|护照号?|银行卡号?|签字|签名)")
CN_PERSON_BEFORE_PREDICATE_PATTERN = re.compile(
    r"(?:(?<=^)|(?<=[，。；：、\s（(“\"！？!?,;:/和与及跟请由让叫找问给向对同陪派催等是被把将托])"
    r"|(?<=联系)|(?<=通知)|(?<=告诉)|(?<=委托)|(?<=要求)|(?<=邀请)|(?<=提醒)|(?<=安排)|(?<=拜托)|(?<=麻烦)|(?<=感谢)|(?<=还有)|(?<=以及))"
    rf"({_CN_NAME})"
    rf"(?=(?:{_NAME_FOLLOWERS})|{_CONTACT_AFTER_DE}|[，、,]\s*(?:电话|手机|座机|\+?86)?\s*(?:1[3-9]\d|0\d{{2,3}}-)|[/／])"
)
# “参与”“涉及”里的“与”“及”不是连词，后面的字不按“和……”处理
_NOT_CONJUNCTION = frozenset({"参与", "给与", "赋与", "涉及", "普及", "危及", "波及", "提及", "顾及", "触及", "论及", "遍及", "殃及"})
# “联系人”后面跟的是这些说法时不是姓名（联系人电话、联系人信息有误、联系人待定）
_LOOSE_NOT_NAME = re.compile(
    r"^(?:电话|手机|邮箱|信息|方式|地址|住址|微信|姓名|名字|身份|证件|号码|待定|失联|不详|未知|暂无|空缺|为空|一栏|一项|单位|部门"
    r"|也|都|还|又|就|要|但|是|在|已|未|没|不|无|请|需|可|和|与|及|等|们|的|写|填|找)"
)


def _name_length(text: str, start: int, value: str) -> int:
    """候选的第三、四个字和后面的字组成常见说法时（“李明反馈”“赵敏电话”“王芳电话138…”），只取前面的两三个字。"""
    for cut in range(2, len(value)):
        if any(text.startswith(word, start + cut) for word in _AFTER_NAME_WORDS):
            return cut
    return len(value)


def _names_after_titles(text: str, strategy: Strategy) -> list[Span]:
    """“心内科主任王芳”“请王老师”之外的另一种写法：称谓在前、姓名在后。取紧跟称谓的 2-3 个字中最长的合法姓名。"""
    spans: list[Span] = []
    for match in _TITLE_BEFORE_NAME_RE.finditer(text):
        start = match.end()
        for length in (3, 2):
            value = text[start:start + length]
            if len(value) < length or not all("\u4e00" <= char <= "\u9fff" for char in value):
                continue
            if value[0] not in CN_SURNAMES or any(char in _NAME_STOP_CHARS for char in value[1:]):
                continue
            if value[0] in _AMBIGUOUS_SURNAMES or _clean_cn_name(value) != value:
                continue
            if _name_length(text, start, value) != length:
                continue
            spans.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=start + length, text=value, entity_type=EntityType.PERSON,
                              score=0.9, sources=["NER-LITE"], strategy=strategy,
                              status="pending" if 0.9 < settings.confidence_threshold else "accepted"))
            break
    return spans


def _role_spans(text: str, strategy: Strategy) -> list[Span]:
    """“心内科主任”“三年级二班班主任”：职务前紧挨着部门，再向前取 1-8 个字作为部门名。"""
    spans: list[Span] = []
    for match in _ROLE_TITLE_RE.finditer(text):
        anchor = match.start()
        if anchor > 0 and text[anchor - 1] == "的":
            anchor -= 1
        unit = next((item for item in _ROLE_UNITS if text[max(0, anchor - len(item)):anchor] == item), None)
        if not unit:
            continue
        name_end = anchor - len(unit)
        start = name_end
        while start > 0 and name_end - start < 8:
            char = text[start - 1]
            if char in _ROLE_NAME_STOP or not ("一" <= char <= "鿿" or char.isdigit()):
                break
            start -= 1
        lead = _ROLE_NAME_LEAD.match(text[start:name_end])
        if lead:
            start += lead.end()
        if name_end - start < 1 and unit != "办公室":
            continue
        value = text[start:match.end()]
        spans.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=match.end(), text=value, entity_type=EntityType.ROLE,
                          score=0.8, sources=["IMPLICIT"], strategy=strategy,
                          status="pending" if 0.8 < settings.confidence_threshold else "accepted",
                          metadata={"implicit_identifier": True}))
    for match in EN_ROLE_PATTERN.finditer(text):
        span = _span(match, EntityType.ROLE, "IMPLICIT", 0.8, strategy)
        span.metadata["implicit_identifier"] = True
        spans.append(span)
    return spans


def _trim_org_prefix(value: str) -> int:
    """返回机构名真正的起点：反复去掉开头的“目前在”“是”“和”等语境，但不切进机构名本身（协和医院、同济大学）。

    中文机构名常以城市或省份开头（武汉清源设计院、北京协和医院），名称里出现已知地名时，从地名处开始。
    """
    suffix = _ORG_SUFFIX_RE.search(value)
    minimum = (len(suffix.group(0)) if suffix else 2) + 2
    offset = 0
    while True:
        rest = value[offset:]
        cut = next((match.end() for match in _ORG_LEAD_BREAK.finditer(rest) if len(rest) - match.end() >= minimum), None)
        if cut is None:
            lead = _ORG_LEAD_PHRASE.match(rest)
            if lead and len(rest) - lead.end() >= minimum:
                cut = lead.end()
        if cut is None:
            break
        offset += cut
    rest = value[offset:]
    region = _ORG_REGION_RE.search(rest, 1)
    if region and len(rest) - region.start() >= minimum:
        offset += region.start()
    return offset


def _span(match: re.Match[str], entity_type: EntityType, source: str, score: float, strategy: Strategy, group: int = 0) -> Span:
    start, end = match.span(group)
    return Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=match.group(group), entity_type=entity_type,
                score=score, sources=[source], strategy=strategy, status="pending" if score < settings.confidence_threshold else "accepted")


_PASSPORT_CUE = re.compile(r"passport|护照|旅行证件|证件|证照|travel document", re.I)
# 编号类说法后面的字母加数字不是护照号：“病历号BL123456”“住院号ZY20231234”“订单号AB654321”
_NOT_PASSPORT_BEFORE = re.compile(
    r"(?:\b(?:tag|reference|ref\.?|code|order|file|ticket|invoice|serial|batch|record|case|no\.?|id)"
    r"|[一-鿿]{0,4}(?:编号|号码|号|编码|代码|代号|序列号|单号)|工单|订单|批次|项目|合同|发票|型号|版本)"
    r"\s*(?:是|为|:|：)?\s*$", re.I)
_NOT_PASSPORT_AFTER = re.compile(r"^\s*(?:批次|这个|的项目|设备|序列号|代码|编号)")
_BANK_CUE = re.compile(r"(?:银行卡|卡号|账号|帐号|账户|帐户|储蓄卡|信用卡|借记卡|开户|收款|card|account|acct|iban)[^\d\n]{0,8}$", re.I)
_ID_CUE = re.compile(r"(?:身份证|身份信息|身份号|证件号|公民身份|居民身份|id card|id number|id no|identity|identification)[^\d\n]{0,8}$", re.I)


def _passes_context(spec: PatternSpec, text: str, start: int, end: int) -> bool:
    if spec.context == "bank_cue":
        return bool(_BANK_CUE.search(text[max(0, start - 20):start]))
    if spec.context == "id_cue":
        return bool(_ID_CUE.search(text[max(0, start - 20):start]))
    if spec.context != "passport":
        return True
    if _PASSPORT_CUE.search(text[max(0, start - 30):start]):
        return True
    return not (_NOT_PASSPORT_BEFORE.search(text[max(0, start - 16):start]) or _NOT_PASSPORT_AFTER.match(text[end:end + 8]))


_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")


def _normalized_view(text: str) -> tuple[str, list[int] | None]:
    """把全角字母数字（１３８、＋８６、＠）换成半角、去掉零宽字符，返回 (规整后的文本, 每个字符在原文中的位置)。
    文本不需要规整时第二项为 None。用于结构化规则：“１３８００１３８０００”“138\u200b0013\u200b8000”也能识别。"""
    if not any("\uff01" <= char <= "\uff5e" or char in _ZERO_WIDTH for char in text):
        return text, None
    characters: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char in _ZERO_WIDTH:
            continue
        characters.append(chr(ord(char) - 0xFEE0) if "\uff01" <= char <= "\uff5e" else char)
        positions.append(index)
    return "".join(characters), positions


def detect_rule_spans(text: str, strategy: Strategy, custom_rules: list[dict] | None = None,
                      enabled_types: set[EntityType] | None = None, include_cues: bool = False) -> tuple[list[Span], TraceStep]:
    started = time.perf_counter()
    found: list[Span] = []
    view, positions = _normalized_view(text)
    for spec in PATTERNS + (CUE_PATTERNS if include_cues else []):
        if enabled_types is not None and spec.entity_type not in enabled_types:
            continue
        for match in spec.regex.finditer(view):
            raw = match.group(0)
            if spec.validator and not spec.validator(raw):
                continue
            if not _passes_context(spec, view, match.start(), match.end()):
                continue
            if positions is None:
                found.append(_span(match, spec.entity_type, "RULE", spec.score, strategy))
                continue
            start, end = positions[match.start()], positions[match.end() - 1] + 1
            found.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=text[start:end], entity_type=spec.entity_type,
                              score=spec.score, sources=["RULE"], strategy=strategy,
                              status="pending" if spec.score < settings.confidence_threshold else "accepted"))
    # 18 位身份证号碰巧也能通过 Luhn 校验：同一位置已经认作身份证号时，不再当作银行卡号
    id_ranges = {(span.start, span.end) for span in found if span.entity_type == EntityType.ID_CARD}
    if id_ranges:
        found = [span for span in found if not (span.entity_type == EntityType.BANK_CARD and (span.start, span.end) in id_ranges)]
    timed_out: list[str] = []
    budget_end = time.perf_counter() + USER_RULES_BUDGET_SECONDS
    for rule in custom_rules or []:
        if not rule.get("enabled", True):
            continue
        try:
            entity_type = EntityType(rule["entity_type"])
        except (KeyError, ValueError):
            continue
        if enabled_types is not None and entity_type not in enabled_types and not rule.get("force"):
            continue
        remaining = budget_end - time.perf_counter()
        if remaining <= 0:
            timed_out.append(str(rule.get("name") or "自定义规则"))
            continue
        try:
            compiled = compile_user_pattern(str(rule["pattern"]), rule.get("kind") == "keyword", bool(rule.get("case_sensitive")))
        except (KeyError, ValueError, user_regex.error):
            continue
        matches, complete = find_user_matches(compiled, text, min(USER_RULE_TIMEOUT_SECONDS, remaining))
        if not complete:
            timed_out.append(str(rule.get("name") or "自定义规则"))
        for start, end in matches:
            item = Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=text[start:end], entity_type=entity_type,
                        score=1.0, sources=["CUSTOM_RULE"], strategy=strategy, status="accepted")
            item.metadata.update(rule_id=rule.get("id"), rule_name=rule.get("name"))
            if rule.get("force"):
                item.metadata["force_term"] = True
            found.append(item)
    elapsed = max(4, round((time.perf_counter() - started) * 1000))
    detail = f"内置多国标识符、校验码、Luhn 与 {len(custom_rules or [])} 条自定义规则"
    if timed_out:
        logging.getLogger("moyin.rules").warning("custom rules timed out: %s", ", ".join(timed_out))
        detail += f"；规则「{'、'.join(dict.fromkeys(timed_out))}」匹配超时，只处理了部分文本，请简化这条正则"
    return found, TraceStep(key="rule", label="结构化与自定义规则", duration_ms=elapsed, count=len(found), detail=detail,
                            status="degraded" if timed_out else "done")


# 用户自己写的正则用第三方 regex 模块执行：支持匹配超时，并在匹配时释放 GIL，
# 写得不好的正则（如 (a|aa)+$）最多占用这么久，不会卡住整个服务
USER_RULE_TIMEOUT_SECONDS = 1.0
USER_RULES_BUDGET_SECONDS = 3.0
_USER_PATTERN_CACHE: "OrderedDict[tuple[str, bool, bool], user_regex.Pattern]" = OrderedDict()


def compile_user_pattern(pattern: str, keyword: bool = False, case_sensitive: bool = False):
    """编译用户规则；keyword 为固定词。无效的正则抛出 user_regex.error。"""
    key = (pattern, keyword, case_sensitive)
    cached = _USER_PATTERN_CACHE.get(key)
    if cached is not None:
        _USER_PATTERN_CACHE.move_to_end(key)
        return cached
    expression = user_regex.escape(pattern) if keyword else pattern
    compiled = user_regex.compile(expression, 0 if case_sensitive else user_regex.IGNORECASE)
    _USER_PATTERN_CACHE[key] = compiled
    if len(_USER_PATTERN_CACHE) > 512:
        _USER_PATTERN_CACHE.popitem(last=False)
    return compiled


def find_user_matches(compiled, text: str, timeout: float = USER_RULE_TIMEOUT_SECONDS) -> tuple[list[tuple[int, int]], bool]:
    """返回 (匹配位置, 是否在时限内完成)。超时时保留已经找到的匹配。"""
    found: list[tuple[int, int]] = []
    try:
        for match in compiled.finditer(text, timeout=timeout, concurrent=True):
            if match.end() > match.start():
                found.append(match.span())
    except TimeoutError:
        return found, False
    return found, True


def detect_lite_ner_spans(
    text: str,
    strategy: Strategy,
    enabled_types: set[EntityType] | None = None,
    language: str = "auto",
) -> tuple[list[Span], TraceStep]:
    started = time.perf_counter()
    found: list[Span] = []
    scope = "all" if language in {"auto", "mixed", "multilingual"} else language
    specs = [
        # 置信度 ≥ 0.90 直接采纳，低于 0.90 送大模型核查或人工确认（立项书 2.1）
        (CN_ORG_CONTEXT_PATTERN, EntityType.ORG, 0.92, 1, "zh"),
        (CN_ORG_PATTERN, EntityType.ORG, 0.88, 1, "zh"),
        (EN_ORG_PATTERN, EntityType.ORG, 0.90, 1, "en"),
        (ADDRESS_PATTERN, EntityType.ADDRESS, 0.92, 0, "zh"),
        (ADDRESS_STREET_PATTERN, EntityType.ADDRESS, 0.86, 0, "zh"),
        (EN_ADDRESS_PATTERN, EntityType.ADDRESS, 0.92, 0, "en"),
        (LOCATION_PATTERN, EntityType.LOCATION, 0.86, 0, "all"),
        (CN_PERSON_PATTERN, EntityType.PERSON, 0.92, 1, "zh"),
        (CN_PERSON_RELATION_PATTERN, EntityType.PERSON, 0.88, 2, "zh"),
        (CN_PERSON_TITLE_PATTERN, EntityType.PERSON, 0.90, 1, "zh"),
        (CN_PERSON_LOOSE_PATTERN, EntityType.PERSON, 0.80, 1, "zh"),
        (CN_PERSON_BEFORE_PREDICATE_PATTERN, EntityType.PERSON, 0.88, 1, "zh"),
        (EN_PERSON_CONTEXT_PATTERN, EntityType.PERSON, 0.92, 1, "en"),
        (EN_PERSON_PATTERN, EntityType.PERSON, 0.78, 1, "en"),
    ]
    org_suffixes = (" university", " college", " hospital", " institute", " company", " corporation", " bank", " school", " group", " foundation")
    for regex, entity_type, score, group, spec_language in specs:
        if enabled_types is not None and entity_type not in enabled_types:
            continue
        if scope != "all" and spec_language not in {"all", scope}:
            continue
        for match in (_en_name_matches(text) if regex is EN_PERSON_PATTERN else regex.finditer(text)):
            value = match.group(group)
            if not value:
                continue
            if regex is CN_PERSON_RELATION_PATTERN and not match.group(1) and value[0] in _AMBIGUOUS_SURNAMES:
                continue
            if regex is CN_PERSON_LOOSE_PATTERN and _LOOSE_NOT_NAME.match(value):
                continue
            if regex is CN_PERSON_BEFORE_PREDICATE_PATTERN and value[0] in _AMBIGUOUS_SURNAMES:
                # 前面是动词（请高…、由于…）或“参与”“涉及”时，“高”“于”“方”多半不是姓氏
                start = match.start(group)
                previous = text[start - 1] if start > 0 else ""
                if "\u4e00" <= previous <= "\u9fff" and (previous not in "和与及跟" or text[max(0, start - 2):start] in _NOT_CONJUNCTION):
                    continue
            if entity_type == EntityType.PERSON and regex in {EN_PERSON_PATTERN, EN_PERSON_CONTEXT_PATTERN}:
                words = value.casefold().split()
                if value.casefold().endswith(org_suffixes) or words[-1] in _EN_NOT_NAME_SECOND:
                    continue
                if regex is EN_PERSON_PATTERN and words[0] in _EN_NOT_NAME_FIRST:
                    continue
            if regex is CN_PERSON_TITLE_PATTERN:
                start = match.start(group)
                after_cjk = start > 0 and "\u4e00" <= text[start - 1] <= "\u9fff"
                if any(char in _NAME_STOP_CHARS for char in value[1:]):
                    continue
                if value[0] in _AMBIGUOUS_SURNAMES and (len(value) == 2 or after_cjk):
                    continue
            if scope == "zh" and spec_language == "all" and not any("\u4e00" <= char <= "\u9fff" for char in value):
                continue
            if scope == "en" and spec_language == "all" and not any(char.isascii() and char.isalpha() for char in value):
                continue
            span = _span(match, entity_type, "NER-LITE", score, strategy, group)
            if regex in _CN_PERSON_REGEXES:
                length = _name_length(text, span.start, value)
                if length != len(value):
                    span.end = span.start + length
                    span.text = value[:length]
            if regex in _CN_PERSON_REGEXES:
                cleaned = _clean_cn_name(span.text)
                if cleaned is None:
                    continue
                if cleaned != span.text:
                    span.end = span.start + len(cleaned)
                    span.text = cleaned
            if regex is CN_ORG_PATTERN:
                offset = _trim_org_prefix(value)
                if offset:
                    span.start += offset
                    span.text = value[offset:]
                # 只有“公司”二字作后缀、前面又像一句话时（“寄到公司”“回公司”）不是机构名
                if span.text.endswith("公司") and not span.text.endswith("有限公司") and _BARE_COMPANY_NOISE.search(span.text[:-2]):
                    continue
                suffix = _ORG_SUFFIX_RE.search(span.text)
                if suffix and _ORG_GENERIC_NAME.match(span.text[:suffix.start()]):
                    continue
            if regex is LOCATION_PATTERN and span.start > 0 and text[span.start - 1] in _LOCATION_FALSE_START.get(span.text, ()):
                continue
            found.append(span)
    if (enabled_types is None or EntityType.PERSON in enabled_types) and scope != "en":
        found.extend(_names_after_titles(text, strategy))
        found.extend(_names_in_lists(text, strategy))
    elapsed = max(7, round((time.perf_counter() - started) * 1000))
    scope_label = {"zh": "仅中文", "en": "仅英文", "all": "中文、英文与混合"}[scope]
    return found, TraceStep(key="ner", label="轻量语义识别", duration_ms=elapsed, count=len(found), detail=f"{scope_label}实体识别降级层")


_CN_PERSON_REGEXES = {CN_PERSON_PATTERN, CN_PERSON_RELATION_PATTERN, CN_PERSON_TITLE_PATTERN, CN_PERSON_LOOSE_PATTERN, CN_PERSON_BEFORE_PREDICATE_PATTERN}


def _names_in_lists(text: str, strategy: Strategy) -> list[Span]:
    """顿号列出的一串姓名：每一项都要像姓名（常见姓氏开头、两三个字、不是常用词），有一项不像就整串都不算。"""
    spans: list[Span] = []
    for match in CN_PERSON_LIST_PATTERN.finditer(text):
        items: list[tuple[int, str]] = []
        position = match.start(1)
        for piece in re.split(r"(、|以及|和|与|及)", match.group(1)):
            if piece and piece not in {"、", "以及", "和", "与", "及"}:
                items.append((position, piece))
            position += len(piece)
        if len(items) < 2:
            continue
        valid: list[tuple[int, str]] = []
        for start, value in items:
            value = value[:_name_length(text, start, value)]
            if not 2 <= len(value) <= 4 or _clean_cn_name(value) != value or (len(value) == 2 and value[0] in _AMBIGUOUS_SURNAMES):
                valid = []
                break
            valid.append((start, value))
        for start, value in valid:
            spans.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=start + len(value), text=value, entity_type=EntityType.PERSON,
                              score=0.86, sources=["NER-LITE"], strategy=strategy,
                              status="pending" if 0.86 < settings.confidence_threshold else "accepted"))
    return spans


def _en_name_matches(text: str):
    """两个大写词的候选。第一个词明显不是名字（Contact Wang Fang 里的 Contact）时，从第二个词重新找，
    否则真正的姓名会因为第一个词已被匹配吞掉而漏掉。"""
    position = 0
    while (match := EN_PERSON_PATTERN.search(text, position)):
        first = match.group(1).split()[0]
        if first.casefold() in _EN_NOT_NAME_FIRST:
            position = match.start(1) + len(first)
            continue
        yield match
        position = match.end()


def detect_implicit_spans(
    text: str,
    strategy: Strategy,
    enabled_types: set[EntityType] | None = None,
    language: str = "auto",
) -> tuple[list[Span], TraceStep]:
    """隐性隐私：不含姓名、但能间接指向具体个人的“部门 + 职务”组合（立项书：根据职级、科室关联推断特定个人）。

    单独成一个识别器，不并入 detect_lite_ner_spans：第三层微调与评测使用的上游
    （规则 + 轻量 NER）保持九类标签不变，见 finetune/docs/规格.md「标签体系」。
    """
    started = time.perf_counter()
    found: list[Span] = []
    scope = "all" if language in {"auto", "mixed", "multilingual"} else language
    if enabled_types is None or EntityType.ROLE in enabled_types:
        found = [span for span in _role_spans(text, strategy)
                 if scope == "all" or (scope == "zh") == any("一" <= char <= "鿿" for char in span.text)]
    elapsed = max(1, round((time.perf_counter() - started) * 1000))
    return found, TraceStep(key="implicit", label="隐性隐私识别", duration_ms=elapsed, count=len(found), detail="部门与职务组合")


def _is_user_rule(span: Span) -> bool:
    return "CUSTOM_RULE" in span.sources


def merge_spans(text: str, spans: list[Span]) -> list[Span]:
    """合并各层识别结果：偏移校验、去重、处理重叠。

    原则是覆盖范围只增不减：
    - 位置完全相同：合并来源，类型不一致时标为冲突交给人工；
    - 一个实体落在另一个实体里面（城市名在地址里、隐去词是机构名的一部分）：保留外层实体。
      内层置信度更高且类型不同、又不是用户规则时，外层标为冲突交给人工确认；
    - 两个实体部分重叠：合并成覆盖两者的一个实体，类型取置信度更高的一方，标为冲突交给人工。
    被否决（恢复原文）的实体让位给有效实体。按起点排序后只需与上一个结果比较，复杂度 O(n log n)。
    """
    valid = [s for s in spans if 0 <= s.start < s.end <= len(text) and text[s.start:s.end] == s.text]
    valid.sort(key=lambda s: (s.start, -(s.end - s.start), -(s.score or 0)))
    merged: list[Span] = []
    for candidate in valid:
        last = merged[-1] if merged else None
        if last is None or candidate.start >= last.end:
            merged.append(candidate)
            continue
        if candidate.metadata.get("force_term") and candidate.status != "rejected":
            # 用户明确要求隐去的词不受识别范围限制，与它合并的实体继承这个标记
            last.metadata["force_term"] = True
        if candidate.start == last.start and candidate.end == last.end:
            last.sources = sorted(set(last.sources + candidate.sources))
            if last.status == "rejected" and candidate.status != "rejected":
                last.status = candidate.status
            if last.entity_type != candidate.entity_type:
                last.conflict = True
                last.status = "pending"
                if (candidate.score or 0) > (last.score or 0):
                    last.entity_type = candidate.entity_type
            last.score = max(last.score or 0, candidate.score or 0)
            continue
        if last.status == "rejected" and candidate.status != "rejected":
            # 外层已被恢复原文，里面仍有效的实体照常处理
            merged[-1] = candidate
            continue
        if candidate.status == "rejected":
            # 被否决的实体不参与脱敏；若它的置信度更高，说明各层判断不一致，外层交给人工确认
            if (candidate.score or 0) > (last.score or 0):
                last.conflict = True
                last.status = "pending"
            continue
        if candidate.end <= last.end:
            # 嵌套：保留外层
            if (candidate.score or 0) > (last.score or 0) and candidate.entity_type != last.entity_type and not _is_user_rule(candidate):
                last.conflict = True
                last.status = "pending"
            continue
        # 部分重叠：合并为覆盖两者的实体，避免任何一方没被覆盖的部分漏出
        winner = candidate if (candidate.score or 0, candidate.end - candidate.start) > (last.score or 0, last.end - last.start) else last
        start, end = last.start, candidate.end
        merged[-1] = Span(
            id=winner.id, start=start, end=end, text=text[start:end], entity_type=winner.entity_type,
            score=winner.score, sources=sorted(set(last.sources + candidate.sources)), status="pending", conflict=True,
            strategy=winner.strategy,
            metadata={**winner.metadata, "merged_overlap": True, **({"force_term": True} if last.metadata.get("force_term") else {})},
        )
    return merged
