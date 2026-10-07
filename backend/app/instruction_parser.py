"""自然语言脱敏要求的本地解析（不依赖大模型）。

按分句理解，每句识别：动作（保留 / 隐去）、否定（“不要隐去李明”是保留，“不要保留”是隐去）、
对象（某类实体，如“邮箱”“人名”；或具体的词，如“北京”“星舟”）、范围（“只处理……”）、
方式（掩码、差分隐私替换、泛化；可以只针对某类实体，如“姓名用差分隐私替换”）和力度。

输出字段：
- enabled_entity_types：只处理这些类型（空表示不改范围）
- disabled_entity_types：不处理这些类型（“保留人名”“邮箱不要脱敏”）
- force_types：一定要处理的类型
- preserve_terms / force_terms：保留 / 隐去的具体词
- strategy：整体方式；type_strategies：按类型指定的方式
- privacy_strength：1 轻、2 标准、3 强
"""
import re
from typing import Any

from .llm_adapter import parse_instruction_with_llm
from .schemas import EntityType, Strategy

# 类型的说法。同一处只取最长的说法：“邮箱地址”是邮箱，不是地址；“company names”是机构，不是人名
_TYPE_WORDS: list[tuple[str, EntityType]] = [
    *[(word, EntityType.EMAIL) for word in ("电子邮箱地址", "电子邮件地址", "邮箱地址", "邮件地址", "电子邮箱", "电子邮件", "邮箱", "email addresses", "email address", "e-mail addresses", "e-mail address", "emails", "email", "e-mails", "e-mail")],
    *[(word, EntityType.PHONE) for word in ("手机号码", "电话号码", "联系电话", "手机号", "联系方式", "座机", "电话", "手机", "phone numbers", "phone number", "telephone numbers", "mobile numbers", "phones", "phone", "telephone", "mobile")],
    *[(word, EntityType.ID_CARD) for word in ("身份证号码", "身份证号", "身份证", "证件号码", "证件号", "id card numbers", "id numbers", "id cards", "id card")],
    *[(word, EntityType.BANK_CARD) for word in ("银行卡号", "银行卡", "银行账号", "卡号", "bank card numbers", "bank cards", "bank card", "bank accounts", "bank account", "card numbers")],
    *[(word, EntityType.PASSPORT) for word in ("护照号码", "护照号", "护照", "passport numbers", "passports", "passport")],
    *[(word, EntityType.ADDRESS) for word in ("详细地址", "家庭住址", "通讯地址", "住址", "地址", "门牌号", "street addresses", "street address", "addresses", "address")],
    *[(word, EntityType.ORG) for word in ("机构名称", "机构名", "单位名称", "公司名称", "公司名", "学校名", "医院名", "机构", "组织", "单位", "公司", "学校", "医院", "企业",
                                          "organization names", "organisation names", "company names", "organizations", "organisations", "organization", "organisation", "companies", "company", "orgs")],
    *[(word, EntityType.LOCATION) for word in ("地名", "地点", "地区", "城市", "省份", "区县", "place names", "city names", "location names", "locations", "location", "places", "cities", "city")],
    *[(word, EntityType.ROLE) for word in ("职务身份", "职务", "职位", "职称", "头衔", "job titles", "job title", "positions", "roles")],
    *[(word, EntityType.PERSON) for word in ("真实姓名", "人名", "姓名", "名字", "person names", "personal names", "people's names", "names", "name", "people", "persons", "person")],
]
_TYPE_WORDS.sort(key=lambda item: len(item[0]), reverse=True)
_TYPE_LOOKUP = {word: entity_type for word, entity_type in _TYPE_WORDS}
_TYPE_RE = re.compile("|".join(
    (rf"\b{re.escape(word)}\b" if word.isascii() else re.escape(word)) for word, _ in _TYPE_WORDS
), re.I)

_STRATEGY_WORDS: list[tuple[str, Strategy]] = [
    *[(word, Strategy.GENERALIZE) for word in ("知识图谱泛化", "上位概念", "模糊化", "泛化", "generalization", "generalize", "generalise")],
    *[(word, Strategy.PSEUDONYMIZE) for word in ("差分隐私替换", "差分隐私", "语义替换", "虚构名字", "假名", "化名", "伪名", "pseudonymization", "pseudonymize", "pseudonymise", "pseudonyms", "pseudonym", "fake names")],
    *[(word, Strategy.MASK) for word in ("占位符", "掩码", "打码", "mask with placeholders", "placeholders", "placeholder", "masking", "mask")],
]
_STRATEGY_WORDS.sort(key=lambda item: len(item[0]), reverse=True)
_STRATEGY_RE = re.compile("|".join(
    (rf"\b{re.escape(word)}\b" if word.isascii() else re.escape(word)) for word, _ in _STRATEGY_WORDS
), re.I)
_STRATEGY_LOOKUP = {word.lower(): strategy for word, strategy in _STRATEGY_WORDS}

_KEEP_VERB = r"保留|保持原样|保持不变|不动|别动|(?<![a-z])(?:keep|preserve|retain|leave)(?![a-z])"
_HIDE_VERB = r"隐去|隐藏|屏蔽|脱敏|去掉|抹去|删掉|删除|遮盖|遮住|打码|处理|(?<![a-z])(?:redact|hide|remove|anonymi[sz]e|censor|obscure|blank out)(?![a-z])"
_VERB_RE = re.compile(rf"(?P<keep>{_KEEP_VERB})|(?P<hide>{_HIDE_VERB})", re.I)
# 动词前的否定：“不要隐去”“不用脱敏”“别处理”“无需”“do not redact”“don't keep”
_NEGATION_BEFORE = re.compile(r"(?:不要|不用|不必|无需|无须|不需要|不需|别|勿|不|切勿|do not|don't|dont|never|no need to|not)\s*(?:再|去|要|用|必)?\s*$", re.I)
_ONLY_RE = re.compile(r"(?:仅仅|仅|只需要|只需|只要|只|only|just)", re.I)
# 只保留或只隐去一部分（“电话号码只保留后四位”“只显示姓氏”“身份证中间八位打码”）：系统按整个实体处理，
# 这类要求不改任何设置，在预览里说明，避免被误读成“只处理某类实体”而把其他隐私全部放过
_PART_RE = re.compile(
    r"(?:前|后|末|最后|开头|结尾|头|尾|中间)\s*[0-9０-９一二两三四五六七八九十几]+\s*(?:位|个?字符|个?数字|个?字母|个字|字)"
    r"|(?:前|后|中间)几位|姓氏|首字母|首字(?!母)|区号|号段|域名部分|出生年份|出生日期部分"
    r"|\b(?:last|first|middle)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|few)\s+(?:digits?|characters?|chars?|letters?|numbers?)\b"
    r"|\b(?:initials?|surnames?|last names?|family names?|area codes?)\b",
    re.I,
)

_STRENGTH_WORDS: list[tuple[str, int]] = [
    *[(word, 3) for word in ("最高强度", "最强", "高强度", "强保护", "最严格", "严格", "彻底", "strongest", "strictest", "strict", "maximum", "strong")],
    *[(word, 1) for word in ("尽量保留语义", "低强度", "轻度", "宽松", "较弱", "light", "lenient", "minimal")],
    *[(word, 2) for word in ("标准强度", "中等强度", "中等", "标准", "适中", "standard", "medium", "moderate")],
]
_STRENGTH_WORDS.sort(key=lambda item: len(item[0]), reverse=True)

_CLAUSE_SPLIT = re.compile(
    r"[，；;。！!？?\n]|,(?=\s*(?:and\s+)?(?:keep|preserve|retain|leave|redact|hide|remove|mask|pseudonymi[sz]e|generali[sz]e|use|do not|don't|only)\b)"
    r"|但是|但|而且|并且|同时|另外|此外|然后|\bbut\b|\balso\b"
    r"|\band\b(?=\s+(?:keep|preserve|retain|leave|redact|hide|remove|mask|pseudonymi[sz]e|generali[sz]e|replace|use|do not|don't)\b)",
    re.I,
)
_FILLER_PREFIX = re.compile(r"^(?:请|麻烦|帮我|需要|要|把|将|对|对于|给|使用|用|所有的?|全部的?|一切|任何|这些|那些|其中的?|文中的?|里面的?|please|the|all|any|every|of)\s*", re.I)
_FILLER_SUFFIX = re.compile(r"\s*(?:都|也|一律|全部|统一|一下|吧|啊|呢|了|即可|就好|就行|要|需要|请|必须|不要|不用|不必|无需|别|please|too|as well|them|it)$", re.I)
_TERM_PREFIX = re.compile(r"^(?:项目代号|内部代号|代号|项目名称|名称为?|名叫|叫做?|名为)(?:是|为)?")
_GENERIC = {"所有", "全部", "其他", "其余", "其它", "这些", "那些", "内容", "信息", "隐私", "隐私信息", "敏感信息", "文本", "文字", "原文", "结果", "它们", "它",
            "everything", "anything", "all", "them", "it", "text", "the text", "information", "data", "content"}


def _types_in(text: str) -> list[EntityType]:
    return list(dict.fromkeys(_TYPE_LOOKUP.get(match.group(0).lower(), _TYPE_LOOKUP.get(match.group(0))) for match in _TYPE_RE.finditer(text)))


def _clean(value: str) -> str:
    value = value.strip().strip("“”‘’「」『』\"' ")
    for _ in range(3):
        before = value
        value = _FILLER_PREFIX.sub("", value).strip()
        value = _FILLER_SUFFIX.sub("", value).strip()
        value = value.strip("“”‘’「」『』\"' ")
        if value == before:
            break
    return value


# 只表示类别、不会是专名一部分的词，可以直接去掉；“医院”“学校”“公司”可能是名字的一部分（“和平医院”），
# 只在“上海的医院”这种带“的”的说法里才去掉
_NAME_SUFFIX_WORDS = {"机构", "组织", "单位", "公司", "学校", "医院", "企业", "城市"}
_CATEGORY_RE = re.compile("|".join(
    (rf"\b{re.escape(word)}\b" if word.isascii() else re.escape(word)) for word, _ in _TYPE_WORDS if word not in _NAME_SUFFIX_WORDS
), re.I)
_SUFFIX_AFTER_DE_RE = re.compile("的(?:" + "|".join(_NAME_SUFFIX_WORDS) + ")")


def _literal(value: str) -> str:
    """去掉类型词后剩下的具体词：“北京的地名”→“北京”，“项目代号星舟”→“星舟”，“人名”→“”，“和平医院”不变。"""
    value = _clean(value)
    if value in _NAME_SUFFIX_WORDS:
        return ""
    value = _SUFFIX_AFTER_DE_RE.sub(" ", value)
    value = _CATEGORY_RE.sub(" ", value)
    value = re.sub(r"(?:的|之|相关|有关|相应|里|中|方面)+\s*$", "", value.strip())
    value = re.sub(r"\s+(?:的)?\s*$", "", value)
    value = _clean(_TERM_PREFIX.sub("", _clean(value)))
    value = re.sub(r"(?:的)$", "", value).strip()
    # 单个字不当作保留词或隐去词：隐去“涉”会把文中每个“涉”都盖掉
    if not value or value.lower() in _GENERIC or len(value) < 2 or len(value) > 40 or not re.search(r"\w", value):
        return ""
    return value


# “患者的部分”“项目相关信息”这类说法没有具体的词可以查找，按暂不支持处理
_VAGUE_OBJECT = re.compile(r"(?:部分|内容|信息|地方|段落|句子|片段|细节)$")


def _negated(clause: str, position: int) -> bool:
    return bool(_NEGATION_BEFORE.search(clause[:position]))


class _Plan:
    def __init__(self):
        self.enabled: list[EntityType] = []
        self.disabled: list[EntityType] = []
        self.force_types: list[EntityType] = []
        self.preserve: list[str] = []
        self.force: list[str] = []
        self.strategy: Strategy | None = None
        self.type_strategies: dict[EntityType, Strategy] = {}
        self.strength: int | None = None
        self.ignored: list[str] = []

    def result(self) -> dict[str, Any]:
        disabled = [item for item in dict.fromkeys(self.disabled) if item not in self.force_types]
        enabled = [item for item in dict.fromkeys(self.enabled) if item not in disabled]
        unique = lambda values: list(dict.fromkeys(value for value in values if value))
        return {
            "enabled_entity_types": [item.value for item in enabled],
            "disabled_entity_types": [item.value for item in disabled],
            "force_types": [item.value for item in dict.fromkeys(self.force_types)],
            "preserve_terms": unique(self.preserve),
            "force_terms": [term for term in unique(self.force) if term not in self.preserve],
            "strategy": self.strategy.value if self.strategy else None,
            "type_strategies": {key.value: value.value for key, value in self.type_strategies.items()},
            "privacy_strength": self.strength,
            # 没有对应设置、按默认方式处理的分句（只保留后四位这类部分隐去的要求）
            "ignored_clauses": list(dict.fromkeys(self.ignored)),
            "parser": "deterministic",
        }


def _parse_clause(clause: str, plan: _Plan) -> None:
    clause = clause.strip().strip("“”\"' ")
    if not clause:
        return
    if _PART_RE.search(clause):
        plan.ignored.append(clause)
        return
    for word, level in _STRENGTH_WORDS:
        index = clause.lower().find(word.lower())
        if index >= 0 and not _negated(clause, index):
            plan.strength = level
            break

    only = _ONLY_RE.search(clause)
    strategies = [match for match in _STRATEGY_RE.finditer(clause) if not _negated(clause, match.start())]
    if strategies:
        strategy = _STRATEGY_LOOKUP[strategies[0].group(0).lower()]
        types = _types_in(_STRATEGY_RE.sub(" ", clause))
        if types:
            # “姓名用差分隐私替换”“只对姓名做差分隐私替换”：只改这一类实体的方式，其他实体照常处理
            for entity_type in types:
                plan.type_strategies[entity_type] = strategy
        else:
            plan.strategy = strategy
        return
    if _STRATEGY_RE.search(clause) and not _VERB_RE.search(_STRATEGY_RE.sub(" ", clause)):
        return  # 只有被否定的方式（“不要泛化”），没有别的要求

    verbs = list(_VERB_RE.finditer(clause))
    if only:
        target = clause[only.end():]
        verb = _VERB_RE.search(target)
        keep_only = bool(verb and verb.group("keep") and not _negated(target, verb.start()))
        if not keep_only:
            # “只处理姓名”“仅针对电话和邮箱”“只隐去星舟”：缩小范围。范围只认明确的实体类型或明确要隐去的词，
            # 看不懂的（“只要后面的”“只显示一部分”）不改范围，否则其他隐私会被整类放过
            obj = target[verb.end():] if verb else target
            types = _types_in(obj)
            if types:
                plan.enabled.extend(types)
                return
            literal = _literal(_TYPE_RE.sub(" ", obj))
            if literal and verb and not _VAGUE_OBJECT.search(literal):
                plan.force.append(literal)
                plan.enabled.append(EntityType.CUSTOM)
                return
            if literal or not verbs:
                plan.ignored.append(clause)
                return
        # “只保留北京”：保留这些，其他照常处理，与“保留北京”相同
    if not verbs:
        return
    verb = verbs[0]
    keep = bool(verb.group("keep"))
    if _negated(clause, verb.start()):
        keep = not keep
    after = clause[verb.end():]
    before = clause[:verb.start()]
    before = _NEGATION_BEFORE.sub("", before)
    obj = after if _clean(after) else before
    for part in _split_objects(obj):
        types = _types_in(part)
        literal = _literal(part)
        if literal and _VAGUE_OBJECT.search(literal) and not types:
            plan.ignored.append(clause)
        elif literal:
            (plan.preserve if keep else plan.force).append(literal)
        elif types:
            (plan.disabled if keep else plan.force_types).extend(types)


def _split_objects(obj: str) -> list[str]:
    """“张伟和李娜”“北京、上海的地名”拆成多个对象。名字里本来就有“和”的（“协和医院”“和平医院”）不拆：
    拆开后某一边不到两个字，或者只剩“医院”这类后缀，就并回去。"""
    # 常用词里的“和”“与”“及”不是并列（涉及、及时、参与、和平、和谐）
    pieces = re.split(r"(以及|和(?![平谐睦蔼善气好])|(?<![参给赋])与|(?<![涉以普危波提顾企触论遍殃])及(?![时格早])|、|跟|(?<![a-z])and(?![a-z])|,)", obj, flags=re.I)
    parts = [pieces[0]]
    for index in range(1, len(pieces), 2):
        separator, part = pieces[index], pieces[index + 1]
        if len(part.strip()) < 2 or part.strip() in _NAME_SUFFIX_WORDS or len(parts[-1].strip()) < 2:
            parts[-1] += separator + part
        else:
            parts.append(part)
    if len(parts) > 1:
        # “北京和上海的地名”：后半句的类型词也作用于前面的词
        tail = _TYPE_RE.search(parts[-1])
        if tail and not _TYPE_RE.search(parts[0]):
            parts = [part if _TYPE_RE.search(part) else f"{part}的{tail.group(0)}" for part in parts]
    return parts


def parse_instruction_locally(instruction: str) -> dict[str, Any]:
    plan = _Plan()
    for clause in _CLAUSE_SPLIT.split(instruction):
        _parse_clause(clause, plan)
    return plan.result()


async def parse_instruction(instruction: str, use_llm: bool = True, deployment_mode: str = "local") -> dict[str, Any]:
    local = parse_instruction_locally(instruction)
    if use_llm:
        llm = await parse_instruction_with_llm(instruction, deployment_mode)
        if llm:
            merged = llm.model_dump(mode="json")
            # 模型只能补充，不能悄悄放宽：缩小处理范围只认本地解析出的明确说法；模型给出的保留词、不处理的类型
            # 必须在要求原文里出现过，并且不在“只保留后四位”这类暂不支持的分句里
            understood = instruction
            for clause in local["ignored_clauses"]:
                understood = understood.replace(clause, " ")
            mentioned = {item.value for item in _types_in(understood)}
            unique = lambda values: list(dict.fromkeys(value for value in values if value))
            merged["enabled_entity_types"] = local["enabled_entity_types"]
            merged["disabled_entity_types"] = unique(local["disabled_entity_types"] + [value for value in merged.get("disabled_entity_types") or [] if value in mentioned])
            merged["preserve_terms"] = unique(local["preserve_terms"] + [
                term.strip() for term in merged.get("preserve_terms") or [] if term.strip() and term.strip() in understood and not _PART_RE.search(term)
            ])
            merged["force_terms"] = unique(local["force_terms"] + [term.strip() for term in merged.get("force_terms") or [] if term.strip() and not _PART_RE.search(term)])
            merged["force_types"] = unique(local["force_types"] + list(merged.get("force_types") or []))
            if not merged.get("type_strategies"):
                merged["type_strategies"] = local["type_strategies"]
            if merged.get("strategy") is None:
                merged["strategy"] = local["strategy"]
            if merged.get("privacy_strength") is None:
                merged["privacy_strength"] = local["privacy_strength"]
            merged["ignored_clauses"] = local["ignored_clauses"]
            merged["parser"] = "llm+deterministic-fallback"
            return merged
    return local
