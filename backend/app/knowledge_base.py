"""本地知识库：知识图谱泛化在离线或远程查询失败时使用的上位概念层级。

每个实体给出三级上位概念，力度越高取越抽象的一级（附件5：力度决定回溯深度）。
中文语境输出中文概念，英文语境输出英文概念，避免在英文句子里插入中文词。
"""

from __future__ import annotations

import re

from .schemas import EntityType

Levels = tuple[str, str, str]

# ---------------------------------------------------------------- 行政区划

REGION_EN = {
    "华北": "North China", "东北": "Northeast China", "华东": "East China", "华中": "Central China",
    "华南": "South China", "西南": "Southwest China", "西北": "Northwest China",
}

PROVINCE_REGION = {
    "北京": "华北", "天津": "华北", "河北": "华北", "山西": "华北", "内蒙古": "华北",
    "辽宁": "东北", "吉林": "东北", "黑龙江": "东北",
    "上海": "华东", "江苏": "华东", "浙江": "华东", "安徽": "华东", "福建": "华东", "江西": "华东", "山东": "华东", "台湾": "华东",
    "河南": "华中", "湖北": "华中", "湖南": "华中",
    "广东": "华南", "广西": "华南", "海南": "华南", "香港": "华南", "澳门": "华南",
    "重庆": "西南", "四川": "西南", "贵州": "西南", "云南": "西南", "西藏": "西南",
    "陕西": "西北", "甘肃": "西北", "青海": "西北", "宁夏": "西北", "新疆": "西北",
}
MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}
SPECIAL_REGIONS = {"香港", "澳门"}

CITY_PROVINCE = {
    "石家庄": "河北", "唐山": "河北", "保定": "河北", "太原": "山西", "大同": "山西", "呼和浩特": "内蒙古", "包头": "内蒙古",
    "沈阳": "辽宁", "大连": "辽宁", "长春": "吉林", "哈尔滨": "黑龙江", "大庆": "黑龙江",
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "南通": "江苏", "徐州": "江苏", "扬州": "江苏",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "绍兴": "浙江", "嘉兴": "浙江", "金华": "浙江", "台州": "浙江",
    "合肥": "安徽", "芜湖": "安徽", "福州": "福建", "厦门": "福建", "泉州": "福建", "南昌": "江西", "赣州": "江西",
    "济南": "山东", "青岛": "山东", "烟台": "山东", "潍坊": "山东",
    "郑州": "河南", "洛阳": "河南", "开封": "河南", "武汉": "湖北", "宜昌": "湖北", "襄阳": "湖北", "长沙": "湖南", "株洲": "湖南",
    "广州": "广东", "深圳": "广东", "珠海": "广东", "佛山": "广东", "东莞": "广东", "汕头": "广东", "惠州": "广东",
    "南宁": "广西", "桂林": "广西", "柳州": "广西", "海口": "海南", "三亚": "海南",
    "成都": "四川", "绵阳": "四川", "贵阳": "贵州", "遵义": "贵州", "昆明": "云南", "大理": "云南", "丽江": "云南", "拉萨": "西藏",
    "西安": "陕西", "宝鸡": "陕西", "兰州": "甘肃", "西宁": "青海", "银川": "宁夏", "乌鲁木齐": "新疆", "喀什": "新疆",
    "台北": "台湾", "高雄": "台湾",
}
PROVINCE_CAPITALS = {
    "石家庄", "太原", "呼和浩特", "沈阳", "长春", "哈尔滨", "南京", "杭州", "合肥", "福州", "南昌", "济南", "郑州", "武汉",
    "长沙", "广州", "南宁", "海口", "成都", "贵阳", "昆明", "拉萨", "西安", "兰州", "西宁", "银川", "乌鲁木齐", "台北",
}

# 拼音或英文写法的中国城市
CITY_LATIN = {
    "beijing": "北京", "peking": "北京", "shanghai": "上海", "tianjin": "天津", "chongqing": "重庆", "guangzhou": "广州",
    "shenzhen": "深圳", "hangzhou": "杭州", "nanjing": "南京", "wuhan": "武汉", "chengdu": "成都", "xi'an": "西安", "xian": "西安",
    "suzhou": "苏州", "changsha": "长沙", "zhengzhou": "郑州", "qingdao": "青岛", "xiamen": "厦门", "harbin": "哈尔滨",
    "shenyang": "沈阳", "dalian": "大连", "hefei": "合肥", "jinan": "济南", "kunming": "昆明", "fuzhou": "福州",
    "nanchang": "南昌", "ningbo": "宁波", "hong kong": "香港", "macau": "澳门", "macao": "澳门", "taipei": "台北",
}

# 海外城市：(英文名, 中文名, 国家英文, 国家中文, 大洲英文, 大洲中文)
FOREIGN_CITIES = [
    ("London", "伦敦", "the UK", "英国", "Europe", "欧洲"), ("Manchester", "曼彻斯特", "the UK", "英国", "Europe", "欧洲"),
    ("Edinburgh", "爱丁堡", "the UK", "英国", "Europe", "欧洲"), ("Paris", "巴黎", "France", "法国", "Europe", "欧洲"),
    ("Berlin", "柏林", "Germany", "德国", "Europe", "欧洲"), ("Munich", "慕尼黑", "Germany", "德国", "Europe", "欧洲"),
    ("Rome", "罗马", "Italy", "意大利", "Europe", "欧洲"), ("Madrid", "马德里", "Spain", "西班牙", "Europe", "欧洲"),
    ("Amsterdam", "阿姆斯特丹", "the Netherlands", "荷兰", "Europe", "欧洲"), ("Zurich", "苏黎世", "Switzerland", "瑞士", "Europe", "欧洲"),
    ("Moscow", "莫斯科", "Russia", "俄罗斯", "Europe", "欧洲"),
    ("Tokyo", "东京", "Japan", "日本", "Asia", "亚洲"), ("Osaka", "大阪", "Japan", "日本", "Asia", "亚洲"),
    ("Kyoto", "京都", "Japan", "日本", "Asia", "亚洲"), ("Seoul", "首尔", "South Korea", "韩国", "Asia", "亚洲"),
    ("Singapore", "新加坡", "Southeast Asia", "东南亚", "Asia", "亚洲"), ("Bangkok", "曼谷", "Thailand", "泰国", "Asia", "亚洲"),
    ("Kuala Lumpur", "吉隆坡", "Malaysia", "马来西亚", "Asia", "亚洲"), ("Dubai", "迪拜", "the UAE", "阿联酋", "Asia", "亚洲"),
    ("New York", "纽约", "the US", "美国", "North America", "北美"), ("San Francisco", "旧金山", "the US", "美国", "North America", "北美"),
    ("Los Angeles", "洛杉矶", "the US", "美国", "North America", "北美"), ("Boston", "波士顿", "the US", "美国", "North America", "北美"),
    ("Chicago", "芝加哥", "the US", "美国", "North America", "北美"), ("Seattle", "西雅图", "the US", "美国", "North America", "北美"),
    ("Washington", "华盛顿", "the US", "美国", "North America", "北美"), ("Toronto", "多伦多", "Canada", "加拿大", "North America", "北美"),
    ("Vancouver", "温哥华", "Canada", "加拿大", "North America", "北美"), ("Sydney", "悉尼", "Australia", "澳大利亚", "Oceania", "大洋洲"),
    ("Melbourne", "墨尔本", "Australia", "澳大利亚", "Oceania", "大洋洲"),
]
_FOREIGN_BY_NAME = {name.casefold(): row for row in FOREIGN_CITIES for name in (row[0], row[1])}

# ---------------------------------------------------------------- 机构

# 知名高校：名称 → 所在城市
UNIVERSITY_CITY = {
    "北京大学": "北京", "清华大学": "北京", "中国人民大学": "北京", "北京师范大学": "北京", "北京航空航天大学": "北京",
    "北京理工大学": "北京", "中国农业大学": "北京", "北京外国语大学": "北京", "中央民族大学": "北京",
    "复旦大学": "上海", "上海交通大学": "上海", "同济大学": "上海", "华东师范大学": "上海", "上海财经大学": "上海",
    "浙江大学": "杭州", "南京大学": "南京", "东南大学": "南京", "武汉大学": "武汉", "华中科技大学": "武汉",
    "中山大学": "广州", "华南理工大学": "广州", "四川大学": "成都", "电子科技大学": "成都", "西安交通大学": "西安",
    "哈尔滨工业大学": "哈尔滨", "中国科学技术大学": "合肥", "南开大学": "天津", "天津大学": "天津", "厦门大学": "厦门",
    "山东大学": "济南", "吉林大学": "长春", "中南大学": "长沙", "湖南大学": "长沙", "重庆大学": "重庆", "兰州大学": "兰州",
    "东北大学": "沈阳", "大连理工大学": "大连", "深圳大学": "深圳", "苏州大学": "苏州",
}
UNIVERSITY_LATIN = {
    "peking university": "北京", "tsinghua university": "北京", "renmin university of china": "北京", "renmin university": "北京",
    "fudan university": "上海", "shanghai jiao tong university": "上海", "tongji university": "上海", "zhejiang university": "杭州",
    "nanjing university": "南京", "wuhan university": "武汉", "sun yat-sen university": "广州",
}
FOREIGN_UNIVERSITIES = {
    "harvard university": "the US", "stanford university": "the US", "massachusetts institute of technology": "the US", "mit": "the US",
    "yale university": "the US", "princeton university": "the US", "columbia university": "the US",
    "university of oxford": "the UK", "oxford university": "the UK", "university of cambridge": "the UK", "cambridge university": "the UK",
    "imperial college london": "the UK", "university of tokyo": "Japan", "national university of singapore": "Southeast Asia",
}

# 原有的精确条目保留，测试与演示依赖这些层级
EXACT_HIERARCHY: dict[str, Levels] = {
    "中国人民大学": ("北京高校", "高等院校", "教育机构"),
    "北京大学": ("北京高校", "高等院校", "教育机构"),
    "清华大学": ("北京高校", "高等院校", "教育机构"),
    "复旦大学": ("上海高校", "高等院校", "教育机构"),
    "上海交通大学": ("上海高校", "高等院校", "教育机构"),
    "北京市": ("华北直辖市", "中国城市", "地理区域"),
    "上海市": ("华东直辖市", "中国城市", "地理区域"),
    "广州市": ("华南省会城市", "中国城市", "地理区域"),
    "深圳市": ("华南副省级城市", "中国城市", "地理区域"),
    "海淀区": ("北京城区", "城市辖区", "地理区域"),
    "浦东新区": ("上海城区", "城市辖区", "地理区域"),
}

GENERIC_LEVELS: dict[EntityType, Levels] = {
    # 人名没有自然的多级上位词，保留“某某”这样的称呼位，句子读起来仍然通顺
    EntityType.PERSON: ("某某", "某人", "某人"),
    EntityType.ORG: ("某同类机构", "某机构", "组织实体"),
    EntityType.LOCATION: ("某同级地区", "某地区", "地理区域"),
    EntityType.ADDRESS: ("某市某区", "某地详细地址", "地理位置"),
    EntityType.PHONE: ("尾号已隐藏的电话", "某联系电话", "联系方式"),
    EntityType.EMAIL: ("某域名邮箱", "某邮箱", "电子联系方式"),
    EntityType.ID_CARD: ("某证件号码", "某身份证件", "身份标识"),
    EntityType.BANK_CARD: ("某支付卡号", "某银行卡号", "金融账户标识"),
    EntityType.PASSPORT: ("某护照号码", "某护照", "旅行证件"),
    EntityType.ROLE: ("某部门负责人", "管理人员", "工作人员"),
    EntityType.CUSTOM: ("某自定义敏感项", "敏感内容", "受保护信息"),
}

GENERIC_LEVELS_EN: dict[EntityType, Levels] = {
    EntityType.PERSON: ("someone", "someone", "a person"),
    EntityType.ORG: ("an organization", "an institution", "an entity"),
    EntityType.LOCATION: ("a place", "a region", "somewhere"),
    EntityType.ADDRESS: ("an address in the city", "a street address", "a location"),
    EntityType.PHONE: ("a phone number", "a contact number", "contact details"),
    EntityType.EMAIL: ("an email address", "an email account", "contact details"),
    EntityType.ID_CARD: ("an ID number", "an identity document", "an identifier"),
    EntityType.BANK_CARD: ("a card number", "a bank card", "a financial account"),
    EntityType.PASSPORT: ("a passport number", "a passport", "a travel document"),
    EntityType.ROLE: ("a department head", "a manager", "a staff member"),
    EntityType.CUSTOM: ("a confidential item", "sensitive content", "protected information"),
}


def _role_levels(value: str, lang: str) -> Levels:
    """职务身份：去掉具体部门，只保留职务的大类。"""
    if lang == "en":
        lowered = value.casefold()
        if "ward" in lowered:
            return ("a ward manager", "a healthcare worker", "a staff member")
        if any(word in lowered for word in ("school", "faculty", "dean")):
            return ("a faculty administrator", "an academic staff member", "a staff member")
        return ("a department head", "a manager", "a staff member")
    if value.endswith(("主任医师", "副主任医师")):
        return ("某科室医生", "医务人员", "工作人员")
    if value.endswith("护士长"):
        return ("某科室护士长", "医务人员", "工作人员")
    if value.endswith("班主任"):
        return ("某班班主任", "教师", "工作人员")
    if "教研室" in value or value.endswith("系主任"):
        return ("某系负责人", "教师", "工作人员")
    return GENERIC_LEVELS[EntityType.ROLE]

_INDUSTRY = [
    (("科技", "信息", "数据", "网络", "软件", "智能", "电子", "通信"), "某科技公司"),
    (("贸易", "商贸", "进出口"), "某贸易公司"),
    (("咨询", "顾问"), "某咨询公司"),
    (("医药", "生物", "制药", "健康"), "某医药企业"),
    (("金融", "投资", "证券", "基金", "保险", "资本"), "某金融企业"),
    (("教育", "培训"), "某教育企业"),
    (("传媒", "文化", "影视", "广告"), "某文化传媒公司"),
    (("制造", "机械", "汽车", "材料", "能源", "化工"), "某制造企业"),
    (("物流", "运输", "快递"), "某物流公司"),
    (("地产", "置业", "建设", "建筑"), "某地产建筑企业"),
]


def _strip_admin_suffix(value: str) -> str:
    for suffix in ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市"):
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            return value[: -len(suffix)]
    return value


def _province_of(name: str) -> str | None:
    if name in PROVINCE_REGION:
        return name
    return CITY_PROVINCE.get(name)


def _location_prefix(value: str) -> str | None:
    """机构名开头的地名（北京协和医院 → 北京）。"""
    for name in sorted([*PROVINCE_REGION, *CITY_PROVINCE], key=len, reverse=True):
        if value.startswith(name):
            return name
    return None


def _city_levels(name: str, lang: str) -> Levels | None:
    """中国城市或省份的三级上位概念。"""
    if name in SPECIAL_REGIONS:
        return ("a special administrative region", "a city in South China", "a place in China") if lang == "en" else ("特别行政区", "华南城市", "中国境内")
    province = _province_of(name)
    if not province:
        return None
    region = PROVINCE_REGION[province]
    if lang == "en":
        area = REGION_EN[region]
        if name in PROVINCE_REGION and name not in MUNICIPALITIES:
            return (f"a province in {area}", "a province of China", "a place in China")
        return (f"a city in {area}", "a Chinese city", "a city in Asia")
    if name in MUNICIPALITIES:
        return ("直辖市", f"{region}城市", "中国城市")
    if name in PROVINCE_REGION:
        return (f"{region}省份", "中国省级行政区", "中国境内")
    return (f"{province}省会" if name in PROVINCE_CAPITALS else f"{province}城市", f"{region}城市", "中国城市")


def _foreign_levels(row: tuple[str, str, str, str, str, str], lang: str) -> Levels:
    _, _, country_en, country_zh, continent_en, continent_zh = row
    if lang == "en":
        return (f"a city in {country_en}", f"a city in {continent_en}", "a city abroad")
    return (f"{country_zh}城市", f"{continent_zh}城市", "海外城市")


_ADDRESS_PARTS = re.compile(
    r"(?P<prov>[一-鿿]{2,4}?(?:省|自治区))?(?P<city>[一-鿿]{2,4}?市)?(?P<dist>[一-鿿]{1,5}?(?:区|县|旗))?"
)


def _address_levels(value: str, lang: str) -> Levels | None:
    if lang == "en":
        tail = value.rsplit(",", 1)[-1].strip().casefold()
        if tail in _FOREIGN_BY_NAME:
            row = _FOREIGN_BY_NAME[tail]
            return (f"an address in {row[0]}", f"an address in {row[2]}", "a street address")
        if tail in CITY_LATIN:
            region = PROVINCE_REGION[_province_of(CITY_LATIN[tail]) or "北京"]
            return (f"an address in {tail.title()}", f"an address in {REGION_EN[region]}", "a street address")
        return None
    match = _ADDRESS_PARTS.match(value)
    if not match or not (match.group("city") or match.group("dist")):
        return None
    province, city, district = match.group("prov"), match.group("city"), match.group("dist")
    city_name = _strip_admin_suffix(city) if city else None
    province_name = _strip_admin_suffix(province) if province else (_province_of(city_name) if city_name else None)
    region = PROVINCE_REGION.get(province_name or "", "")
    if city and district:
        broader = f"{province_name}省" if province_name and province_name not in MUNICIPALITIES and province_name not in SPECIAL_REGIONS else f"{region}地区" if region else "中国境内"
        return (f"{city}{district}", city, broader)
    if city:
        return (city, f"{province_name}省" if province_name and province_name not in MUNICIPALITIES else f"{region}地区" if region else "中国境内", "中国境内")
    return (district, "某市辖区", "地理位置")


def _org_levels(value: str, lang: str) -> tuple[Levels, str] | None:
    lowered = value.casefold()
    if lang == "en":
        if lowered in FOREIGN_UNIVERSITIES:
            return (f"a university in {FOREIGN_UNIVERSITIES[lowered]}", "an academic institution", "an organization"), "依据内置高校列表"
        if lowered in UNIVERSITY_LATIN:
            region = PROVINCE_REGION[_province_of(UNIVERSITY_LATIN[lowered]) or "北京"]
            return (f"a university in {REGION_EN[region]}", "an academic institution", "an organization"), "依据内置高校列表"
        rules = [
            (("university", "college", "academy", "school"), ("a university", "an academic institution", "an organization")),
            (("institute", "laboratory", "lab", "research"), ("a research institute", "a research organization", "an organization")),
            (("hospital", "clinic", "medical", "health"), ("a hospital", "a healthcare provider", "an organization")),
            (("bank", "trust", "capital", "securities"), ("a bank", "a financial institution", "an organization")),
            (("council", "department", "ministry", "bureau", "court", "agency"), ("a government agency", "a public body", "an organization")),
            (("company", "corp", "ltd", "inc", "group", "llc", "technologies", "analytics"), ("a company", "a business", "an organization")),
        ]
        for markers, levels in rules:
            if any(re.search(rf"\b{re.escape(marker)}\b", lowered) for marker in markers):
                return levels, "依据机构名称特征推断"
        return None
    if value in UNIVERSITY_CITY:
        return (f"{UNIVERSITY_CITY[value]}高校", "高等院校", "教育机构"), "命中内置高校列表"
    place = _location_prefix(value)
    if any(word in value for word in ("大学", "学院")):
        return (f"{place}高校" if place else "某高校", "高等院校", "教育机构"), "依据高校名称特征推断"
    if any(word in value for word in ("中学", "小学", "学校")):
        return (f"{place}某中小学" if place else "某中小学", "中小学校", "教育机构"), "依据学校名称特征推断"
    if any(word in value for word in ("研究院", "研究所", "研究中心", "实验室")):
        return (f"{place}科研机构" if place else "某科研机构", "科研机构", "组织机构"), "依据科研机构名称特征推断"
    if any(word in value for word in ("医院", "诊所", "卫生院")):
        return (f"{place}某医院" if place else "某医院", "医疗机构", "公共服务机构"), "依据医疗机构名称特征推断"
    if "银行" in value:
        return ("某商业银行", "金融机构", "组织机构"), "依据银行名称特征推断"
    if "法院" in value or "检察院" in value:
        return (f"{place}司法机关" if place else "某司法机关", "司法机关", "公共机构"), "依据机构名称特征推断"
    if any(word in value for word in ("委员会", "政府", "局", "厅")):
        return (f"{place}政府部门" if place else "某政府部门", "政府机关", "公共机构"), "依据机构名称特征推断"
    if any(word in value for word in ("公司", "集团", "企业")):
        industry = next((label for words, label in _INDUSTRY if any(word in value for word in words)), "某公司")
        return (industry, "企业", "组织机构"), "依据企业名称特征推断"
    return None


def local_levels(term: str, entity_type: EntityType, lang: str = "zh") -> tuple[Levels, str, str]:
    """返回 (三级上位概念, 来源, 说明)。来源：local_exact / local_inferred / type_fallback。"""
    cleaned = term.strip()
    lang = "en" if lang == "en" else "zh"
    if lang == "zh" and cleaned in EXACT_HIERARCHY:
        return EXACT_HIERARCHY[cleaned], "local_exact", "命中内置实体层级"
    lowered = cleaned.casefold()

    if entity_type == EntityType.LOCATION:
        if lowered in _FOREIGN_BY_NAME:
            return _foreign_levels(_FOREIGN_BY_NAME[lowered], lang), "local_exact", "命中内置海外城市"
        chinese_name = CITY_LATIN.get(lowered) or _strip_admin_suffix(cleaned)
        levels = _city_levels(chinese_name, lang)
        if levels:
            return levels, "local_exact", "命中内置行政区划"
        if lang == "zh":
            if cleaned.endswith(("区", "县", "旗")):
                return ("某同市辖区", "城市辖区", "地理区域"), "local_inferred", "依据区县后缀推断"
            if cleaned.endswith(("市", "州", "盟")):
                return ("某同区域城市", "城市", "地理区域"), "local_inferred", "依据城市后缀推断"
            if cleaned.endswith(("省", "自治区")):
                return ("某同区域省份", "省级行政区", "地理区域"), "local_inferred", "依据省级行政区后缀推断"
    elif entity_type == EntityType.ADDRESS:
        levels = _address_levels(cleaned, lang)
        if levels:
            return levels, "local_inferred", "依据地址中的行政区划逐级上溯"
    elif entity_type == EntityType.ORG:
        found = _org_levels(cleaned, lang)
        if found:
            levels, detail = found
            return levels, "local_exact" if "内置" in detail else "local_inferred", detail
    elif entity_type == EntityType.ROLE:
        return _role_levels(cleaned, lang), "local_inferred", "去掉具体部门，保留职务大类"

    generic = GENERIC_LEVELS_EN if lang == "en" else GENERIC_LEVELS
    return generic.get(entity_type, generic[EntityType.CUSTOM]), "type_fallback", "使用实体类型通用层级"


def knowledge_entry_count() -> int:
    return len(EXACT_HIERARCHY) + len(UNIVERSITY_CITY) + len(UNIVERSITY_LATIN) + len(FOREIGN_UNIVERSITIES) + len(PROVINCE_REGION) + len(CITY_PROVINCE) + len(FOREIGN_CITIES)


# 英文句号后面跟空白时也是句子边界：中英混合文本里英文句子和后面的中文句子分开判断语言
_SENTENCE_BREAK = re.compile(r"[\n。！？!?；;]|\.(?=\s|$)")


def context_language(text: str, start: int, end: int, window: int = 48) -> str:
    """实体所在句子的语言：英文单词明显多于中文时视为英文语境。只看同一句，避免上一行的中文影响判断。"""
    left_break = max((match.end() for match in _SENTENCE_BREAK.finditer(text, max(0, start - window), start)), default=max(0, start - window))
    right_match = _SENTENCE_BREAK.search(text, end, min(len(text), end + window))
    right_end = right_match.start() if right_match else min(len(text), end + window)
    around = text[left_break:start] + " " + text[end:right_end]
    latin_words = len(re.findall(r"[A-Za-z]+", around))
    cjk = sum("一" <= char <= "鿿" for char in around)
    if not latin_words and not cjk:
        own = text[start:end]
        return "en" if re.search(r"[A-Za-z]", own) and not re.search(r"[一-鿿]", own) else "zh"
    return "en" if latin_words > cjk / 1.7 else "zh"
