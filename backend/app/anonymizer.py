import hashlib

from .schemas import EntityType, Span, Strategy


PSEUDONYMS = {
    EntityType.PERSON: ["林清", "陈默", "Alex Morgan", "周然"],
    EntityType.ORG: ["某研究机构", "某高校", "Northbridge Institute"],
    EntityType.LOCATION: ["某地区", "华东某市", "Central District"],
    EntityType.ADDRESS: ["某市某区某街道", "[REDACTED ADDRESS]"],
}


def replacement_for(span: Span, strategy: Strategy, counters: dict[EntityType, dict[str, int]]) -> str:
    entity_type = span.entity_type
    if strategy == Strategy.GENERALIZE:
        return {
            EntityType.PERSON: "某人", EntityType.ORG: "某机构", EntityType.LOCATION: "某地区",
            EntityType.ADDRESS: "某地详细地址", EntityType.PHONE: "某联系电话", EntityType.EMAIL: "某邮箱",
            EntityType.ID_CARD: "某身份证件", EntityType.BANK_CARD: "某银行卡号",
        }[entity_type]
    if strategy == Strategy.PSEUDONYMIZE:
        pool = PSEUDONYMS.get(entity_type)
        if pool:
            index = int(hashlib.sha256(span.text.encode("utf-8")).hexdigest(), 16) % len(pool)
            return pool[index]
        if entity_type == EntityType.EMAIL:
            value = int(hashlib.sha256(span.text.encode("utf-8")).hexdigest()[:8], 16)
            return f"user{value % 900 + 100}@example.org"
        digits = "".join(c for c in span.text if c.isdigit())
        if entity_type == EntityType.PHONE and len(digits) >= 7:
            return digits[:3] + "****" + digits[-4:]
        return "*" * len(span.text)
    mapping = counters.setdefault(entity_type, {})
    if span.text not in mapping:
        mapping[span.text] = len(mapping) + 1
    return f"【{entity_type.value}-{mapping[span.text]:03d}】"


def redact_text(text: str, spans: list[Span], strategy: Strategy | None) -> str:
    accepted = [s for s in spans if s.status != "rejected" and 0 <= s.start < s.end <= len(text) and text[s.start:s.end] == s.text]
    counters: dict[EntityType, dict[str, int]] = {}
    replacements = [(s.start, s.end, replacement_for(s, strategy or s.strategy, counters)) for s in accepted]
    result = text
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result
