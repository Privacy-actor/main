import re
import time
import uuid
from dataclasses import dataclass

from .schemas import EntityType, Span, Strategy, TraceStep


@dataclass(frozen=True)
class PatternSpec:
    entity_type: EntityType
    regex: re.Pattern[str]
    score: float
    validator: object | None = None


def _luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 12 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
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
    PatternSpec(EntityType.PHONE, re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"), 0.96),
    PatternSpec(EntityType.ID_CARD, re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"), 0.995, _cn_id),
    PatternSpec(EntityType.BANK_CARD, re.compile(r"(?<!\d)(?:\d[ -]?){12,19}(?!\d)"), 0.97, _luhn),
]


ORG_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z·&]{2,30}(?:大学|学院|医院|研究院|委员会|有限公司|集团|银行|University|College|Hospital|Institute|Company|Corp(?:oration)?|Ltd\.?)(?![\u4e00-\u9fff])", re.I)
ADDRESS_PATTERN = re.compile(r"(?:(?:北京市|上海市|天津市|重庆市|[\u4e00-\u9fff]{2,7}省[\u4e00-\u9fff]{2,7}市)?(?:海淀区|朝阳区|浦东新区|[\u4e00-\u9fff]{2,5}(?:区|县)))[\u4e00-\u9fff0-9]{1,20}(?:路|街|道|巷|号|栋|单元|室)(?:[\u4e00-\u9fff0-9-]{0,12})")
LOCATION_PATTERN = re.compile(r"(?:北京市|上海市|天津市|重庆市|浙江省|江苏省|广东省|四川省|海淀区|朝阳区|杭州|北京|上海|广州|深圳|London|Beijing|Shanghai|New York)", re.I)
CN_PERSON_PATTERN = re.compile(r"(?:(?:我叫|姓名[：:]?|联系人[：:]?|采访对象[：:]?)\s*)([\u4e00-\u9fff·]{2,5})")
EN_PERSON_PATTERN = re.compile(r"\b(?:Mr\.?|Ms\.?|Dr\.?)?\s*([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,})\b")


def _span(match: re.Match[str], entity_type: EntityType, source: str, score: float, strategy: Strategy, group: int = 0) -> Span:
    start, end = match.span(group)
    return Span(
        id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=match.group(group),
        entity_type=entity_type, score=score, sources=[source], strategy=strategy,
        status="pending" if score < 0.82 else "accepted",
    )


def detect_rule_spans(text: str, strategy: Strategy) -> tuple[list[Span], TraceStep]:
    started = time.perf_counter()
    found: list[Span] = []
    for spec in PATTERNS:
        for match in spec.regex.finditer(text):
            raw = match.group(0)
            if spec.validator and not spec.validator(raw):
                continue
            found.append(_span(match, spec.entity_type, "RULE", spec.score, strategy))
    elapsed = max(4, round((time.perf_counter() - started) * 1000))
    return found, TraceStep(key="rule", label="结构化规则", duration_ms=elapsed, count=len(found), detail="正则、校验码与 Luhn 校验")


def detect_lite_ner_spans(text: str, strategy: Strategy) -> tuple[list[Span], TraceStep]:
    started = time.perf_counter()
    found: list[Span] = []
    specs = [
        (ORG_PATTERN, EntityType.ORG, 0.88, 0),
        (ADDRESS_PATTERN, EntityType.ADDRESS, 0.91, 0),
        (LOCATION_PATTERN, EntityType.LOCATION, 0.83, 0),
        (CN_PERSON_PATTERN, EntityType.PERSON, 0.86, 1),
        (EN_PERSON_PATTERN, EntityType.PERSON, 0.78, 1),
    ]
    for regex, entity_type, score, group in specs:
        for match in regex.finditer(text):
            found.append(_span(match, entity_type, "NER-LITE", score, strategy, group))
    elapsed = max(7, round((time.perf_counter() - started) * 1000))
    return found, TraceStep(key="ner", label="轻量语义识别", duration_ms=elapsed, count=len(found), detail="无需下载权重的演示识别器，可替换为 Transformers NER")


def merge_spans(text: str, spans: list[Span]) -> list[Span]:
    valid = [s for s in spans if s.end <= len(text) and text[s.start:s.end] == s.text]
    valid.sort(key=lambda s: (s.start, -(s.end - s.start), -(s.score or 0)))
    merged: list[Span] = []
    for candidate in valid:
        duplicate = next((s for s in merged if s.start == candidate.start and s.end == candidate.end), None)
        if duplicate:
            duplicate.sources = sorted(set(duplicate.sources + candidate.sources))
            if duplicate.entity_type != candidate.entity_type:
                duplicate.conflict = True
                duplicate.status = "pending"
                if (candidate.score or 0) > (duplicate.score or 0):
                    duplicate.entity_type = candidate.entity_type
                    duplicate.text = candidate.text
            duplicate.score = max(duplicate.score or 0, candidate.score or 0)
            continue
        overlap = next((s for s in merged if candidate.start < s.end and candidate.end > s.start), None)
        if overlap:
            # A rejected LLM candidate must never evict a still-active overlapping
            # candidate; otherwise redaction could silently disclose that region.
            candidate_key = (candidate.status != "rejected", candidate.score or 0, candidate.end - candidate.start)
            overlap_key = (overlap.status != "rejected", overlap.score or 0, overlap.end - overlap.start)
            winner = candidate if candidate_key > overlap_key else overlap
            winner.conflict = True
            winner.status = "pending"
            if winner is candidate:
                merged.remove(overlap)
                merged.append(candidate)
            continue
        merged.append(candidate)
    merged.sort(key=lambda s: s.start)
    return merged
