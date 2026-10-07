import hashlib
import math
import random
import re
from typing import Any

from .knowledge_base import context_language
from .knowledge_graph import infer_local_levels
from .schemas import EntityType, Span, Strategy
from .semantic_adapter import semantic_encoder


# 差分隐私替换的候选词全集 𝒱：按实体类型和书写体系（中文 / 拉丁）分组，全部为虚构内容。
# 采样只在与原实体同一书写体系的候选中进行，避免中文句子里突然出现英文名。
PSEUDONYM_POOLS: dict[EntityType, dict[str, list[str]]] = {
    EntityType.PERSON: {
        "cjk": ["林清禾", "陈牧川", "周澄远", "沈予宁", "韩书遥", "顾见山", "苏一苇", "叶知秋",
                "唐若溪", "方未晞", "温以宁", "许南乔", "宋青屿", "陆鸣柯", "杜听澜", "程景行"],
        "latin": ["Jordan Ellis", "Taylor Reed", "Morgan Hale", "Casey Lin", "Riley Brooks",
                  "Avery Collins", "Quinn Parker", "Jamie Foster", "Drew Bennett", "Sam Whitfield"],
    },
    EntityType.ORG: {
        "cjk": ["明川大学", "青岚大学", "临溪学院", "北原理工大学", "南屿师范学院",
                "青禾研究院", "远峰研究所", "澄观研究中心",
                "云帆医院", "安和医院", "望海人民医院",
                "安澜银行", "恒通银行", "南屿农商银行",
                "远山科技有限公司", "启衡集团", "星野数据有限公司", "北辰贸易有限公司", "澄明咨询有限公司",
                "云澜市数据管理局", "临溪市卫生健康委员会", "望海市中级人民法院"],
        "latin": ["Harborview College", "Maple Ridge University", "Westbrook University", "Linden College",
                  "Northgate Institute", "Clearwater Research Institute", "Ashford Institute",
                  "Summit Health Clinic", "Riverside General Hospital", "Brookfield Hospital",
                  "Granite Bay Bank", "Harbor Trust Bank",
                  "Bluefield Analytics Ltd", "Crescent Data Corp", "Pinecrest Group", "Silverline Inc",
                  "Riverton City Council", "Department of Civic Records"],
    },
    EntityType.LOCATION: {
        "cjk": ["云澜市", "临溪市", "望海市", "北原市", "南屿市", "青石县", "白沙县", "东港区", "南城区"],
        "latin": ["Riverton", "Lakeside", "Eastport", "Millbrook", "Fairhaven", "Oakridge"],
    },
    EntityType.ADDRESS: {
        "cjk": ["云澜市青石区望湖路18号", "临溪市南城区学府街7号", "望海市东港区海棠路66号",
                "北原市新华区松风巷3号", "南屿市临江区柳岸大道120号"],
        "latin": ["18 Lakeview Road, Riverton", "7 College Street, Millbrook",
                  "66 Harbor Avenue, Eastport", "3 Pine Lane, Fairhaven"],
    },
    EntityType.ROLE: {
        "cjk": ["外科主任", "放射科主任", "儿科主任", "急诊科护士长", "检验科副主任",
                "二年级班主任", "数学系主任", "外语教研室主任", "化学系副主任",
                "教务处处长", "财务处副处长", "人事科科长", "行政办公室主任",
                "人事部经理", "市场部主管", "研发部组长", "销售部总监", "信息中心主任"],
        "latin": ["Head of the Radiology Ward", "Pediatrics Ward Manager",
                  "Chair of the History Department", "Dean of the School of Arts",
                  "Head of Operations", "Director of Finance", "Sales Team Manager", "Data Team Lead"],
    },
}

# 机构、地点的细分类别。差分隐私替换只在与原实体同一类别的候选中抽样：
# 大学换成大学、医院换成医院，ε 只控制同类候选之间的随机程度。
_CATEGORY_MARKERS: dict[EntityType, list[tuple[str, tuple[str, ...]]]] = {
    EntityType.ORG: [
        ("research", ("研究院", "研究所", "研究中心", "实验室", "institute", "laboratory")),
        ("education", ("大学", "学院", "学校", "中学", "小学", "university", "college", "school", "academy")),
        ("medical", ("医院", "诊所", "卫生院", "hospital", "clinic", "health")),
        ("finance", ("银行", "bank", "trust")),
        ("company", ("公司", "集团", "企业", "corp", "ltd", "inc", "company", "group", "llc")),
        ("government", ("委员会", "法院", "检察院", "政府", "局", "厅", "council", "department", "ministry", "bureau", "court")),
    ],
    EntityType.ROLE: [
        ("medical", ("内科", "外科", "儿科", "妇科", "骨科", "眼科", "口腔", "急诊", "放射", "检验", "护士长", "医师", "病区", "ward")),
        ("education", ("班主任", "系主任", "系副主任", "教研室", "年级", "dean", "faculty", "school", "department chair", "chair of")),
        ("government", ("处长", "科长", "局长", "厅长", "司长", "书记", "办公室")),
        ("corporate", ("经理", "主管", "总监", "组长", "部长", "中心", "manager", "lead", "head of", "director")),
    ],
    EntityType.LOCATION: [
        ("county", ("县",)),
        ("district", ("区",)),
        ("city", ("市",)),
    ],
}


def entity_category(value: str, entity_type: EntityType) -> str | None:
    lowered = value.lower()
    for category, markers in _CATEGORY_MARKERS.get(entity_type, []):
        if any(marker in lowered for marker in markers):
            return category
    # 不带“市”“区”的中文地名（上海、杭州）在文本里多指城市
    if entity_type == EntityType.LOCATION and _script(value) == "cjk":
        return "city"
    return None


# 兼容旧接口：每类实体的全部候选（中文在前）。
SEMANTIC_REPLACEMENTS: dict[EntityType, list[str]] = {
    entity_type: [*pools["cjk"], *pools["latin"]] for entity_type, pools in PSEUDONYM_POOLS.items()
}

# 附件5 §4.2.3：ε 越小，替换越随机，隐私性越强。保护力度越高，ε 越小。
PSEUDONYM_EPSILON = {1: 4.0, 2: 1.0, 3: 0.25}
UTILITY_SENSITIVITY = 1.0
_SYSTEM_RANDOM = random.SystemRandom()


def pseudonymization_metadata(strength: int) -> dict[str, float | str]:
    strength = max(1, min(3, strength))
    encoder = semantic_encoder.status()
    return {
        "mechanism": "exponential",
        "epsilon": PSEUDONYM_EPSILON[strength],
        "utility": "multilingual-minilm-cosine" if encoder["state"] == "ready" else "semantic-feature-cosine-fallback",
        "utility_sensitivity": UTILITY_SENSITIVITY,
        "random_source": "system-cryptographic-rng",
        "semantic_encoder": encoder["model"],
        "semantic_encoder_state": encoder["state"],
    }


def _script(value: str) -> str:
    cjk = sum("一" <= char <= "鿿" for char in value)
    latin = sum(char.isascii() and char.isalpha() for char in value)
    if cjk == 0 and latin > 0:
        return "latin"
    if cjk and latin and latin > cjk * 2:
        return "latin"
    return "cjk"


def _semantic_vector(value: str, entity_type: EntityType) -> tuple[float, ...]:
    length = max(1, len(value))
    chinese = sum("一" <= char <= "鿿" for char in value) / length
    latin = sum(char.isascii() and char.isalpha() for char in value) / length
    digits = sum(char.isdigit() for char in value) / length
    normalized_length = min(length, 40) / 40
    lowered = value.lower()
    organization_tokens = ("大学", "学院", "公司", "集团", "医院", "机构")
    location_tokens = ("省", "市", "区", "县", "路", "街", "号")
    organization = float(any(token in value for token in organization_tokens) or any(token in lowered for token in ("university", "college", "company", "institute", "hospital")))
    location = float(any(token in value for token in location_tokens) or any(token in lowered for token in ("street", "road", "district", "city")))
    semantic_type = float(entity_type in {EntityType.PERSON, EntityType.ORG, EntityType.LOCATION, EntityType.ADDRESS})
    return chinese, latin, digits, normalized_length, organization, location, semantic_type


def _semantic_utility(source: str, candidate: str, entity_type: EntityType) -> float:
    left = _semantic_vector(source, entity_type)
    right = _semantic_vector(candidate, entity_type)
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return 0.0 if denominator == 0 else max(0.0, min(1.0, numerator / denominator))


def candidate_pool(span: Span, used: set[str] | None = None, document: str = "") -> list[str]:
    """同类型、同书写体系、未被本文档其他实体占用、不等于原文、也没有出现在原文里的候选词。

    内置候选用完后，按同一类别生成补充候选（“云帆”+原机构名的“医院”后缀等），保证不同实体不会撞名，
    也不会把另一个真实存在于文中的名字当成替换词。
    """
    pools = PSEUDONYM_POOLS.get(span.entity_type)
    if not pools:
        return []
    script = _script(span.text)
    used = used or set()

    def usable(item: str) -> bool:
        return item != span.text and item not in used and not (document and item in document)

    base = [item for item in pools[script] if item != span.text]
    category = entity_category(span.text, span.entity_type)
    if category:
        same = [item for item in base if entity_category(item, span.entity_type) == category]
        if len(same) >= 2:
            base = same
    available = [item for item in base if usable(item)]
    if available:
        return available
    generated = [item for item in _generated_candidates(span, script) if usable(item)]
    if len(generated) > 40:
        generated = _SYSTEM_RANDOM.sample(generated, 40)
    return generated


_CJK_SURNAMES = "林陈周沈韩顾苏叶唐方温许宋陆杜程江夏贺魏秦孟姚邵"
_CJK_GIVEN_NAMES = ["清禾", "牧川", "澄远", "予宁", "书遥", "见山", "一苇", "知秋", "若溪", "未晞", "以宁", "南乔", "青屿", "鸣柯", "听澜",
                    "景行", "星澜", "云舒", "安歌", "晚舟", "闻笛", "望舒", "子衿", "书禾", "慕白", "微澜", "映川", "知远", "怀瑾", "思齐"]
_LATIN_FIRST_NAMES = ["Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn", "Jamie", "Drew", "Sam",
                      "Alex", "Robin", "Cameron", "Rowan", "Skyler", "Emerson", "Hayden", "Reese", "Parker", "Dakota"]
_LATIN_LAST_NAMES = ["Ellis", "Reed", "Hale", "Lin", "Brooks", "Collins", "Foster", "Bennett", "Whitfield", "Hayes",
                     "Carter", "Sutton", "Lane", "Mercer", "Hollis", "Prescott", "Winslow", "Ashby", "Marlow", "Thorne"]
_CJK_PREFIXES = ["云帆", "安和", "望海", "青禾", "远峰", "澄观", "临溪", "明川", "青岚", "北原", "南屿", "星野", "北辰", "澄明", "启衡",
                 "恒通", "安澜", "东篱", "西岭", "松涛", "竹溪", "梧桐", "杏林", "白鹭", "青松", "银杏", "枫林", "柏川", "芷兰", "若水"]
_LATIN_PREFIXES = ["Harbor", "Maple", "Cedar", "Birch", "Willow", "Aspen", "Summit", "Clearwater", "Brook", "Pine",
                   "Linden", "Ashford", "Westbrook", "Northgate", "Riverside", "Granite", "Silver", "Crescent", "Bluefield", "Oakridge"]
_CJK_ORG_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "研究院", "研究所", "研究中心", "实验室", "大学", "学院", "中学", "小学", "学校",
                     "医院", "诊所", "卫生院", "银行", "集团", "公司", "委员会", "法院", "检察院", "政府", "事务所", "基金会", "协会", "中心", "局", "厅")
_LATIN_ORG_SUFFIXES = ("University", "College", "Institute", "Hospital", "Clinic", "Bank", "Group", "Inc", "Ltd", "Corp", "Council",
                       "Department", "School", "Academy", "Laboratory", "Foundation", "Association", "Center", "Centre")


def _generated_candidates(span: Span, script: str) -> list[str]:
    entity_type = span.entity_type
    if entity_type == EntityType.PERSON:
        if script == "latin":
            return [f"{first} {last}" for first in _LATIN_FIRST_NAMES for last in _LATIN_LAST_NAMES]
        return [surname + given for surname in _CJK_SURNAMES for given in _CJK_GIVEN_NAMES]
    if entity_type == EntityType.ORG:
        if script == "latin":
            last_word = span.text.rstrip(".").split()[-1] if span.text.split() else ""
            suffix = next((item for item in _LATIN_ORG_SUFFIXES if item.lower() == last_word.lower()), "Group")
            return [f"{prefix} {suffix}" for prefix in _LATIN_PREFIXES]
        suffix = next((item for item in _CJK_ORG_SUFFIXES if span.text.endswith(item)), "机构")
        return [prefix + suffix for prefix in _CJK_PREFIXES]
    if entity_type == EntityType.LOCATION:
        if script == "latin":
            return [prefix + ending for prefix in _LATIN_PREFIXES for ending in ("ton", "field", "port")]
        suffix = span.text[-1] if span.text[-1:] in {"市", "县", "区", "镇", "省", "州"} else "市"
        return [prefix + suffix for prefix in _CJK_PREFIXES]
    if entity_type == EntityType.ADDRESS:
        if script == "latin":
            return [f"{number} {prefix} Road, {town}ton" for number in (8, 18, 27, 66) for prefix in _LATIN_PREFIXES[:10] for town in _LATIN_PREFIXES[10:14]]
        return [f"{city}市{district}区{street}路{number}号" for city in _CJK_PREFIXES[:6] for district in _CJK_PREFIXES[6:10]
                for street in _CJK_PREFIXES[10:14] for number in (8, 18)]
    if entity_type == EntityType.ROLE:
        title = next((item for item in ("副主任", "主任", "副处长", "处长", "科长", "经理", "主管", "总监", "组长", "部长", "护士长", "院长", "所长")
                      if span.text.endswith(item)), "负责人")
        if script == "latin":
            return [f"{prefix} Team Lead" for prefix in _LATIN_PREFIXES]
        return [f"{prefix}{unit}{title}" for prefix in ("综合", "行政", "后勤", "发展", "质控", "规划", "信息", "宣传", "采购", "运营")
                for unit in ("部", "办公室", "中心")]
    return []


def _sample_semantic_pseudonym(span: Span, pool: list[str], strength: int) -> str:
    epsilon = PSEUDONYM_EPSILON[strength]
    utilities = semantic_encoder.cosine_scores(span.text, pool)
    if utilities is None:
        utilities = [_semantic_utility(span.text, candidate, span.entity_type) for candidate in pool]
    weights = [math.exp(epsilon * utility / (2 * UTILITY_SENSITIVITY)) for utility in utilities]
    return _SYSTEM_RANDOM.choices(pool, weights=weights, k=1)[0]


def knowledge_levels_for(span: Span, lang: str = "zh") -> tuple[str, str, str]:
    key = "knowledge_levels_en" if lang == "en" else "knowledge_levels"
    enriched_levels = span.metadata.get(key)
    if isinstance(enriched_levels, list) and len(enriched_levels) >= 3 and all(isinstance(item, str) and item for item in enriched_levels[:3]):
        return tuple(enriched_levels[:3])
    levels, _, _ = infer_local_levels(span.text, span.entity_type, lang)
    return levels


class RedactionMemory:
    """一次替换用到的记忆：掩码编号和差分隐私替换词。

    存成可写入 JSON 的字典（任务里保存为 redaction_memory），后续复核、切换方式时复用，
    保证同一实体的编号和替换词不变；同一文件的多段批处理共用一份，编号全文件一致。
    """

    def __init__(self, store: dict | None = None):
        self.store = store if store is not None else {}
        self.masks: dict[str, int] = self.store.setdefault("mask", {})
        self.pseudonyms: dict[str, str] = self.store.setdefault("pseudonym", {})
        self._mask_max: dict[str, int] = {}
        self._used: dict[str, set[str]] = {}

    def mask_number(self, entity_type: EntityType, value: str) -> int:
        key = f"{entity_type.value}|{value}"
        if key not in self.masks:
            if entity_type.value not in self._mask_max:
                prefix = f"{entity_type.value}|"
                self._mask_max[entity_type.value] = max((int(number) for name, number in self.masks.items() if name.startswith(prefix)), default=0)
            self._mask_max[entity_type.value] += 1
            self.masks[key] = self._mask_max[entity_type.value]
        return int(self.masks[key])

    def pseudonym(self, entity_type: EntityType, value: str, strength: int, create) -> str:
        key = f"{strength}|{entity_type.value}|{value}"
        if key not in self.pseudonyms:
            group = f"{strength}|{entity_type.value}|"
            if group not in self._used:
                self._used[group] = {str(item) for name, item in self.pseudonyms.items() if name.startswith(group)}
            created = create(self._used[group])
            self.pseudonyms[key] = created
            self._used[group].add(created)
        return str(self.pseudonyms[key])


def replacement_for(
    span: Span, strategy: Strategy, memory: RedactionMemory, strength: int = 2, lang: str = "zh", document: str = "",
    hidden: frozenset[str] = frozenset(),
) -> str:
    """hidden：本次要隐去的其他实体原文。泛化结果里不能重新露出它们，
    例如文中“北京”已被泛化时，地址不能泛化成“北京市海淀区”，改用更上一级的概念。"""
    entity_type = span.entity_type
    strength = max(1, min(3, strength))
    custom_replacement = span.metadata.get("custom_replacement")
    if isinstance(custom_replacement, str) and custom_replacement:
        return custom_replacement
    if strategy == Strategy.GENERALIZE:
        if span.metadata.get("generalization"):
            return str(span.metadata["generalization"])
        levels = knowledge_levels_for(span, lang)
        others = [item for item in hidden if item != span.text]
        for level in levels[strength - 1:]:
            if not any(item in level for item in others):
                return level
        return levels[-1]
    if strategy == Strategy.PSEUDONYMIZE:
        if entity_type in PSEUDONYM_POOLS:
            def create(used: set[str]) -> str:
                pool = candidate_pool(span, used, document)
                return _sample_semantic_pseudonym(span, pool, strength) if pool else "*" * len(span.text)
            return memory.pseudonym(entity_type, span.text, strength, create)
        if entity_type == EntityType.EMAIL:
            domains = ["example.org", "example.net", "masked.invalid"]
            # 按出现顺序编号，不同邮箱不会撞到同一个替换值，也不从原邮箱推出任何信息
            return memory.pseudonym(entity_type, span.text, strength, lambda used: f"user{len(used) + 1:03d}@{domains[strength - 1]}")
        digits = "".join(c for c in span.text if c.isdigit())
        if entity_type == EntityType.PHONE and len(digits) >= 7:
            visible = 4 if strength == 1 else 2 if strength == 2 else 0
            return (digits[:3] + "*" * max(4, len(digits) - 3 - visible) + digits[-visible:]) if visible else "*" * len(digits)
        if entity_type in {EntityType.ID_CARD, EntityType.BANK_CARD, EntityType.PASSPORT}:
            visible = 4 if strength == 1 else 2 if strength == 2 else 0
            return "*" * max(0, len(span.text) - visible) + (span.text[-visible:] if visible else "")
        return "*" * len(span.text)
    return f"【{entity_type.value}-{memory.mask_number(entity_type, span.text):03d}】"


def redact_with_map(
    text: str,
    spans: list[Span],
    strategy: Strategy | None,
    strength: int = 2,
    include_pending: bool = True,
    memory: dict | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """脱敏并返回替换映射。

    映射中的 start/end 是原文位置，out_start/out_end 是结果文本中的位置，均按 Unicode 码点计。
    前端用它在原文与结果之间做联动高亮。memory 是替换记忆（见 RedactionMemory），传入时原地更新。
    """
    allowed_statuses = {"accepted", "pending"} if include_pending else {"accepted"}
    active = sorted(
        (s for s in spans if s.status in allowed_statuses and 0 <= s.start < s.end <= len(text) and text[s.start:s.end] == s.text),
        key=lambda s: (s.start, s.end),
    )
    state = RedactionMemory(memory)
    hidden = frozenset(span.text for span in active if len(span.text) >= 2)
    pieces: list[str] = []
    segments: list[dict[str, Any]] = []
    cursor = 0
    output_length = 0
    for span in active:
        if span.start < cursor:
            continue  # 重叠的 Span 只处理第一个，避免结果错位
        effective = strategy or span.strategy
        # 泛化按实体所在句子的语言给出上位概念，英文句子里不插入中文词
        lang = context_language(text, span.start, span.end) if effective == Strategy.GENERALIZE else "zh"
        replacement = replacement_for(span, effective, state, strength, lang, text, hidden)
        gap = text[cursor:span.start]
        pieces.append(gap)
        output_length += len(gap)
        segments.append({
            "span_id": span.id, "start": span.start, "end": span.end,
            "out_start": output_length, "out_end": output_length + len(replacement),
            "replacement": replacement, "strategy": effective.value, "entity_type": span.entity_type.value,
        })
        pieces.append(replacement)
        output_length += len(replacement)
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), segments


_MASK_TOKEN = re.compile(r"^【[A-Z_]+-\d+】$")


def infer_replacements(text: str, spans: list[dict[str, Any]], redacted: str, max_steps: int = 20000) -> list[dict[str, Any]]:
    """改版前保存的任务没有替换映射。按原文中未被替换的片段在结果里对齐，推出每处替换；对不齐时返回空列表。"""
    candidates = sorted(
        (s for s in spans if s.get("status") != "rejected" and isinstance(s.get("start"), int) and isinstance(s.get("end"), int)
         and 0 <= s["start"] < s["end"] <= len(text) and text[s["start"]:s["end"]] == s.get("text")),
        key=lambda s: (s["start"], s["end"]),
    )
    chosen: list[dict[str, Any]] = []
    cursor = 0
    for span in candidates:
        if span["start"] >= cursor:
            chosen.append(span)
            cursor = span["end"]
    if not chosen:
        return []
    gaps = [text[:chosen[0]["start"]]] + [text[a["end"]:b["start"]] for a, b in zip(chosen, chosen[1:])] + [text[chosen[-1]["end"]:]]
    limit = len(redacted) - len(gaps[-1])
    if limit < len(gaps[0]) or not redacted.startswith(gaps[0]) or not redacted.endswith(gaps[-1]):
        return []

    def plausible(span: dict[str, Any], value: str) -> bool:
        if not value:
            return False
        if span.get("strategy") == Strategy.MASK.value:
            return value == span["text"] or bool(_MASK_TOKEN.match(value))
        return True

    # 深度优先：第 i 个替换从 position 开始，后面紧跟原文的第 i+1 段间隔
    ends: list[int] = []
    stack: list[tuple[int, int, int]] = [(0, len(gaps[0]), -1)]  # (span index, start in redacted, last tried end)
    steps = 0
    while stack:
        steps += 1
        if steps > max_steps:
            return []
        index, start, tried = stack[-1]
        gap = gaps[index + 1]
        if index == len(chosen) - 1:
            end = limit if tried < limit and plausible(chosen[index], redacted[start:limit]) else -1
        elif gap:
            end = redacted.find(gap, max(start + 1, tried + 1), limit)
            while end != -1 and not plausible(chosen[index], redacted[start:end]):
                end = redacted.find(gap, end + 1, limit)
        else:
            # 相邻实体之间没有间隔，只能靠掩码记号断开
            match = re.match(r"【[A-Z_]+-\d+】", redacted[start:limit])
            end = start + match.end() if match and tried < start + match.end() else -1
        if end == -1:
            stack.pop()
            if ends:
                ends.pop()
            continue
        stack[-1] = (index, start, end)
        if index == len(chosen) - 1:
            ends.append(end)
            break
        ends.append(end)
        stack.append((index + 1, end + len(gap), -1))
    if len(ends) != len(chosen):
        return []
    segments: list[dict[str, Any]] = []
    position = len(gaps[0])
    for index, (span, end) in enumerate(zip(chosen, ends)):
        value = redacted[position:end]
        if value != span["text"]:
            strategy = Strategy.MASK.value if _MASK_TOKEN.match(value) else (span.get("strategy") or Strategy.PSEUDONYMIZE.value)
            if strategy == Strategy.MASK.value and not _MASK_TOKEN.match(value):
                strategy = Strategy.PSEUDONYMIZE.value
            segments.append({"span_id": span["id"], "start": span["start"], "end": span["end"], "out_start": position, "out_end": end,
                             "replacement": value, "strategy": strategy, "entity_type": span.get("entity_type", EntityType.CUSTOM.value)})
        position = end + len(gaps[index + 1])
    return segments


def redact_text(text: str, spans: list[Span], strategy: Strategy | None, strength: int = 2, include_pending: bool = True) -> str:
    return redact_with_map(text, spans, strategy, strength, include_pending)[0]


def memory_from_replacements(replacements: list[dict[str, Any]], spans: list[dict[str, Any]], strength: int) -> dict:
    """旧任务没有保存替换记忆时，从已有的替换映射推出来，复核后编号和替换词保持不变。"""
    texts = {str(span.get("id")): str(span.get("text", "")) for span in spans}
    store: dict[str, dict] = {"mask": {}, "pseudonym": {}}
    for item in replacements:
        value = texts.get(str(item.get("span_id")))
        entity_type = str(item.get("entity_type", ""))
        replacement = str(item.get("replacement", ""))
        if not value or not entity_type or not replacement:
            continue
        if item.get("strategy") == Strategy.MASK.value:
            match = re.match(r"^【[A-Z_]+-(\d+)】$", replacement)
            if match:
                store["mask"].setdefault(f"{entity_type}|{value}", int(match.group(1)))
        elif item.get("strategy") == Strategy.PSEUDONYMIZE.value:
            store["pseudonym"].setdefault(f"{strength}|{entity_type}|{value}", replacement)
    return store
