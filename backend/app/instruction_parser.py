import re
from typing import Any

from .llm_adapter import parse_instruction_with_llm
from .schemas import EntityType, Strategy


TYPE_ALIASES: dict[EntityType, tuple[str, ...]] = {
    EntityType.PERSON: ("人名", "姓名", "名字", "person", "name"),
    EntityType.ORG: ("机构", "组织", "公司", "学校", "医院", "organization", "company"),
    EntityType.LOCATION: ("地名", "地点", "地区", "城市", "location", "place"),
    EntityType.ADDRESS: ("地址", "住址", "详细地址", "address"),
    EntityType.PHONE: ("电话", "手机号", "手机", "phone", "telephone"),
    EntityType.EMAIL: ("邮箱", "电子邮件", "email", "e-mail"),
    EntityType.ID_CARD: ("身份证", "证件号", "身份证号", "id card"),
    EntityType.BANK_CARD: ("银行卡", "银行卡号", "bank card"),
    EntityType.PASSPORT: ("护照", "护照号", "passport"),
}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))



_TERM_TYPE_SUFFIX = re.compile(r"(?:的)?(?:人名|姓名|名字|机构|组织|公司|学校|医院|地名|地点|地区|城市|详细地址|地址|手机号|手机|电话|邮箱|电子邮件|身份证号?|证件号|银行卡号?|护照号?)$", re.I)


def _clean_explicit_term(value: str) -> str:
    value = value.strip().strip("“”\"\' ")
    value = _TERM_TYPE_SUFFIX.sub("", value).strip()
    return value


def parse_instruction_locally(instruction: str) -> dict[str, Any]:
    lowered = instruction.lower()
    mentioned = [entity_type for entity_type, aliases in TYPE_ALIASES.items() if any(alias.lower() in lowered for alias in aliases)]
    only_scope = bool(re.search(r"(?:仅|只|只需|只要|only).{0,8}(?:脱敏|处理|隐藏|mask|redact)", lowered))

    preserve_terms: list[str] = []
    force_terms: list[str] = []
    for match in re.finditer(r"(?:保留|不要脱敏|无需脱敏|keep)\s*[“\"']?([^，。；;\n\"'”]{1,80})", instruction, re.I):
        phrase = match.group(1).strip()
        phrase = re.split(r"(?:但|并且|同时|except|but)", phrase, maxsplit=1)[0].strip()
        cleaned = _clean_explicit_term(phrase)
        if cleaned and cleaned not in {"所有", "全部"}:
            preserve_terms.append(cleaned)
    for match in re.finditer(r"(?:必须脱敏|额外脱敏|隐去|隐藏|屏蔽|redact)\s*[“\"']?([^，。；;\n\"'”]{1,80})", instruction, re.I):
        phrase = match.group(1).strip()
        cleaned = _clean_explicit_term(phrase)
        if cleaned and cleaned not in {"所有", "全部"}:
            force_terms.append(cleaned)

    strategy = None
    if any(word in lowered for word in ("泛化", "模糊化", "上位概念", "generalize")):
        strategy = Strategy.GENERALIZE
    elif any(word in lowered for word in ("差分隐私", "语义替换", "伪名", "pseudonym")):
        strategy = Strategy.PSEUDONYMIZE
    elif any(word in lowered for word in ("掩码", "mask", "占位符")):
        strategy = Strategy.MASK

    strength = None
    if any(word in lowered for word in ("最高强度", "高强度", "严格", "强保护")):
        strength = 3
    elif any(word in lowered for word in ("低强度", "轻度", "尽量保留语义")):
        strength = 1
    elif "中等" in lowered or "标准" in lowered:
        strength = 2

    return {
        "enabled_entity_types": [item.value for item in mentioned] if only_scope else [],
        "preserve_terms": _unique(preserve_terms),
        "force_terms": _unique(force_terms),
        "strategy": strategy.value if strategy else None,
        "privacy_strength": strength,
        "parser": "deterministic",
    }


async def parse_instruction(instruction: str, use_llm: bool = True) -> dict[str, Any]:
    local = parse_instruction_locally(instruction)
    if use_llm:
        llm = await parse_instruction_with_llm(instruction)
        if llm:
            merged = llm.model_dump(mode="json")
            for key in ("enabled_entity_types", "preserve_terms", "force_terms"):
                if not merged.get(key):
                    merged[key] = local[key]
            if merged.get("strategy") is None:
                merged["strategy"] = local["strategy"]
            if merged.get("privacy_strength") is None:
                merged["privacy_strength"] = local["privacy_strength"]
            merged["parser"] = "llm+deterministic-fallback"
            return merged
    return local
