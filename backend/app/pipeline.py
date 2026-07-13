import asyncio
import time

from .anonymizer import pseudonymization_metadata, redact_text
from .instruction_parser import parse_instruction
from .knowledge_graph import knowledge_graph
from .llm_adapter import verify_with_llm
from .ner_adapter import ner_adapter
from .recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
from .schemas import DetectRequest, EntityType, Strategy, TraceStep


def _instruction_rules(force_terms: list[str]) -> list[dict]:
    return [{"id": f"instruction_{index}", "name": "自然语言补充词", "kind": "keyword", "pattern": value,
             "entity_type": EntityType.CUSTOM.value, "enabled": True, "case_sensitive": False}
            for index, value in enumerate(force_terms)]


def _matches_language(span, language: str) -> bool:
    if language in {"auto", "mixed", "multilingual"} or span.entity_type not in {EntityType.PERSON, EntityType.ORG, EntityType.LOCATION, EntityType.ADDRESS}:
        return True
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in span.text)
    has_latin = any(char.isascii() and char.isalpha() for char in span.text)
    return has_chinese if language == "zh" else has_latin


def _preserved_ranges(text: str, terms: list[str]) -> list[tuple[int, int]]:
    ranges = []
    for term in dict.fromkeys(value.strip() for value in terms if value.strip()):
        start = 0
        while True:
            index = text.casefold().find(term.casefold(), start)
            if index < 0:
                break
            ranges.append((index, index + len(term)))
            start = index + max(1, len(term))
    return ranges


async def run_pipeline(request: DetectRequest, policies: dict[EntityType, Strategy] | None = None, persistent_rules: list[dict] | None = None):
    strategy = request.strategy
    strength = request.privacy_strength
    enabled = set(request.enabled_entity_types)
    preserve_terms = list(request.preserve_terms)
    custom_rules = [dict(rule) for rule in (persistent_rules or []) if rule.get("enabled", True)] + [
        {"id": f"keyword_{index}", "name": "项目关键词", "kind": "keyword", "pattern": item.value,
         "entity_type": item.entity_type.value, "enabled": True, "case_sensitive": item.case_sensitive}
        for index, item in enumerate(request.custom_keywords)
    ] + [
        {"id": f"pattern_{index}", "name": item.name, "kind": "regex", "pattern": item.pattern,
         "entity_type": item.entity_type.value, "enabled": True, "case_sensitive": item.case_sensitive}
        for index, item in enumerate(request.custom_patterns)
    ]
    instruction_plan = None
    if request.instruction and request.instruction.strip():
        started = time.perf_counter()
        instruction_plan = await parse_instruction(request.instruction.strip(), request.use_llm)
        if instruction_plan.get("enabled_entity_types"):
            enabled = {EntityType(value) for value in instruction_plan["enabled_entity_types"]}
        preserve_terms.extend(instruction_plan.get("preserve_terms", []))
        custom_rules.extend(_instruction_rules(instruction_plan.get("force_terms", [])))
        if instruction_plan.get("strategy"):
            strategy = Strategy(instruction_plan["strategy"])
        if instruction_plan.get("privacy_strength"):
            strength = int(instruction_plan["privacy_strength"])
        instruction_trace = TraceStep(key="instruction", label="自然语言需求解析", duration_ms=max(1, round((time.perf_counter()-started)*1000)), count=len(instruction_plan.get("preserve_terms", []))+len(instruction_plan.get("force_terms", [])), detail=f"{instruction_plan.get('parser', 'deterministic')} · 与菜单配置合并")
    else:
        instruction_trace = TraceStep(key="instruction", label="自然语言需求解析", duration_ms=0, count=0, status="skipped", detail="本次使用菜单配置")

    rule_job = asyncio.to_thread(detect_rule_spans, request.text, strategy, custom_rules, enabled)
    lite_job = asyncio.to_thread(detect_lite_ner_spans, request.text, strategy, enabled, request.language)
    model_job = ner_adapter.detect(request.text, strategy)
    (rule_spans, rule_trace), (lite_spans, lite_trace), (model_spans, model_trace) = await asyncio.gather(rule_job, lite_job, model_job)
    model_spans = [span for span in model_spans if span.entity_type in enabled and _matches_language(span, request.language)]
    merged = merge_spans(request.text, rule_spans + lite_spans + model_spans)
    preserved = _preserved_ranges(request.text, preserve_terms)
    merged = [span for span in merged if not any(span.start < end and span.end > start for start, end in preserved)]

    if request.use_llm:
        merged, llm_trace = await verify_with_llm(request.text, merged, strategy, request.instruction)
        merged = merge_spans(request.text, merged)
        merged = [span for span in merged if span.entity_type in enabled and _matches_language(span, request.language) and not any(span.start < end and span.end > start for start, end in preserved)]
    else:
        llm_trace = TraceStep(key="llm", label="14B 核验与补漏", duration_ms=0, count=0, status="skipped", detail="本次请求已关闭 LLM")
    if policies:
        for span in merged:
            span.strategy = policies.get(span.entity_type, strategy)

    generalization_active = strategy == Strategy.GENERALIZE or any(span.strategy == Strategy.GENERALIZE for span in merged)
    if generalization_active:
        knowledge_trace = await knowledge_graph.enrich_spans(merged)
    else:
        knowledge_trace = TraceStep(key="knowledge", label="知识图谱分级", duration_ms=0, count=0, status="skipped", detail="当前策略未启用泛化")

    started = time.perf_counter()
    include_pending = request.risk_level == "strict"
    redacted = redact_text(request.text, merged, None if policies else strategy, strength, include_pending=include_pending)
    pseudonym_active = strategy == Strategy.PSEUDONYMIZE or any(span.strategy == Strategy.PSEUDONYMIZE for span in merged)
    mechanism = pseudonymization_metadata(strength)
    mechanism_detail = f"、指数机制 ε={mechanism['epsilon']}" if pseudonym_active else ""
    merge_trace = TraceStep(key="merge", label="合并与脱敏", duration_ms=max(3, round((time.perf_counter()-started)*1000)), count=len(merged), detail=f"偏移校验、冲突消歧、强度 {strength}/3{mechanism_detail}、{'包含待确认实体' if include_pending else '仅处理已确认实体'}、从后向前替换")
    traces = [instruction_trace, rule_trace, model_trace if model_trace.status != "skipped" else lite_trace, llm_trace, knowledge_trace, merge_trace]
    applied_config = {
        "project_id": request.project_id, "language": request.language, "risk_level": request.risk_level, "strategy": strategy.value,
        "privacy_strength": strength, "deployment_mode": request.deployment_mode,
        "enabled_entity_types": [item.value for item in sorted(enabled, key=lambda item: item.value)],
        "preserve_terms": list(dict.fromkeys(preserve_terms)), "custom_rule_count": len(custom_rules),
        "instruction_plan": instruction_plan,
        "pseudonymization": pseudonymization_metadata(strength),
        "knowledge_graph": knowledge_graph.status(),
        "pending_policy": "include" if include_pending else "accepted-only",
    }
    return merged, redacted, traces, applied_config
