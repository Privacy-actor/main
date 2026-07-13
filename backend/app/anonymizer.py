import hashlib
import math
import random

from .knowledge_graph import infer_local_levels
from .schemas import EntityType, Span, Strategy
from .semantic_adapter import semantic_encoder


SEMANTIC_REPLACEMENTS = {
    EntityType.PERSON: ["林清", "陈默", "Alex Morgan", "周然", "李安", "Taylor Reed"],
    EntityType.ORG: ["华东某高校", "某研究机构", "Northbridge Institute", "某科技公司"],
    EntityType.LOCATION: ["华东某市", "某沿海城市", "Central District", "某省会城市"],
    EntityType.ADDRESS: ["某市某区学府路18号", "某城区中心大道88号", "[REDACTED ADDRESS]"],
}

PSEUDONYM_EPSILON = {1: 0.25, 2: 1.0, 3: 4.0}
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


def _semantic_vector(value: str, entity_type: EntityType) -> tuple[float, ...]:
    length = max(1, len(value))
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in value) / length
    latin = sum(char.isascii() and char.isalpha() for char in value) / length
    digits = sum(char.isdigit() for char in value) / length
    normalized_length = min(length, 40) / 40
    lowered = value.lower()
    organization_tokens = ("\u5927\u5b66", "\u5b66\u9662", "\u516c\u53f8", "\u96c6\u56e2", "\u533b\u9662", "\u673a\u6784")
    location_tokens = ("\u7701", "\u5e02", "\u533a", "\u53bf", "\u8def", "\u8857", "\u53f7")
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


def _sample_semantic_pseudonym(span: Span, pool: list[str], strength: int) -> str:
    epsilon = PSEUDONYM_EPSILON[strength]
    utilities = semantic_encoder.cosine_scores(span.text, pool)
    if utilities is None:
        utilities = [_semantic_utility(span.text, candidate, span.entity_type) for candidate in pool]
    weights = [math.exp(epsilon * utility / (2 * UTILITY_SENSITIVITY)) for utility in utilities]
    return _SYSTEM_RANDOM.choices(pool, weights=weights, k=1)[0]


def knowledge_levels_for(span: Span) -> tuple[str, str, str]:
    enriched_levels = span.metadata.get("knowledge_levels")
    if isinstance(enriched_levels, list) and len(enriched_levels) >= 3 and all(isinstance(item, str) and item for item in enriched_levels[:3]):
        return tuple(enriched_levels[:3])
    levels, _, _ = infer_local_levels(span.text, span.entity_type)
    return levels



def replacement_for(span: Span, strategy: Strategy, counters: dict[EntityType, dict[str, int | str]], strength: int = 2) -> str:
    entity_type = span.entity_type
    strength = max(1, min(3, strength))
    custom_replacement = span.metadata.get("custom_replacement")
    if isinstance(custom_replacement, str) and custom_replacement:
        return custom_replacement
    if strategy == Strategy.GENERALIZE:
        levels = knowledge_levels_for(span)
        return span.metadata.get("generalization") or levels[strength - 1]
    if strategy == Strategy.PSEUDONYMIZE:
        pool = SEMANTIC_REPLACEMENTS.get(entity_type)
        if pool:
            mapping = counters.setdefault(entity_type, {})
            key = f"pseudonym:{span.text}"
            if key not in mapping:
                mapping[key] = _sample_semantic_pseudonym(span, pool, strength)
            return str(mapping[key])
        if entity_type == EntityType.EMAIL:
            value = int(hashlib.sha256(span.text.encode("utf-8")).hexdigest()[:8], 16)
            domains = ["example.org", "example.net", "masked.invalid"]
            return f"user{value % 900 + 100}@{domains[strength - 1]}"
        digits = "".join(c for c in span.text if c.isdigit())
        if entity_type == EntityType.PHONE and len(digits) >= 7:
            visible = 4 if strength == 1 else 2 if strength == 2 else 0
            return (digits[:3] + "*" * max(4, len(digits) - 3 - visible) + digits[-visible:]) if visible else "*" * len(digits)
        if entity_type in {EntityType.ID_CARD, EntityType.BANK_CARD, EntityType.PASSPORT}:
            visible = 4 if strength == 1 else 2 if strength == 2 else 0
            return "*" * max(0, len(span.text) - visible) + (span.text[-visible:] if visible else "")
        return "*" * len(span.text)
    mapping = counters.setdefault(entity_type, {})
    key = f"mask:{span.text}"
    if key not in mapping:
        mapping[key] = sum(isinstance(value, int) for value in mapping.values()) + 1
    return f"\u3010{entity_type.value}-{int(mapping[key]):03d}\u3011"



def redact_text(text: str, spans: list[Span], strategy: Strategy | None, strength: int = 2, include_pending: bool = True) -> str:
    allowed_statuses = {"accepted", "pending"} if include_pending else {"accepted"}
    accepted = [s for s in spans if s.status in allowed_statuses and 0 <= s.start < s.end <= len(text) and text[s.start:s.end] == s.text]
    counters: dict[EntityType, dict[str, int | str]] = {}
    replacements = [(s.start, s.end, replacement_for(s, strategy or s.strategy, counters, strength)) for s in accepted]
    result = text
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result
