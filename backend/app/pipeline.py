import asyncio
import bisect
import re
import time
import uuid

from .anonymizer import pseudonymization_metadata, redact_with_map
from .instruction_parser import parse_instruction
from .knowledge_graph import knowledge_graph
from .llm_adapter import find_all_occurrences, verify_with_llm
from .ner_adapter import ner_adapter
from .recognizers import detect_implicit_spans, detect_lite_ner_spans, detect_rule_spans, merge_spans
from .schemas import DetectRequest, EntityType, Span, Strategy, TraceStep


def _instruction_rules(force_terms: list[str]) -> list[dict]:
    # force：用户明确要求隐去的词，不受识别范围限制（“只脱敏人名，另外隐去星舟”里的“星舟”）
    return [{"id": f"instruction_{index}", "name": "自然语言补充词", "kind": "keyword", "pattern": value,
             "entity_type": EntityType.CUSTOM.value, "enabled": True, "case_sensitive": False, "force": True}
            for index, value in enumerate(force_terms)]


def _matches_language(span, language: str) -> bool:
    if language in {"auto", "mixed", "multilingual"} or span.entity_type not in {EntityType.PERSON, EntityType.ORG, EntityType.LOCATION, EntityType.ADDRESS}:
        return True
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in span.text)
    has_latin = any(char.isascii() and char.isalpha() for char in span.text)
    return has_chinese if language == "zh" else has_latin


class PreservedRanges:
    """保留词在原文中的全部出现位置，以及“实体是否完全落在某个保留词里”的快速判断。

    查找用 re.IGNORECASE 逐字比较，不把整段文本 casefold：casefold 会改变长度（ß→ss），导致位置错位。
    只放行完全落在保留词出现范围内的实体；含有保留词的更长实体（“保留北京”时的
    “北京市海淀区……”地址、“北京协和医院”）照常脱敏。
    """

    def __init__(self, text: str, terms: list[str]):
        ranges = sorted(
            match.span()
            for term in dict.fromkeys(value.strip() for value in terms if value and value.strip())
            for match in re.finditer(re.escape(term), text, re.IGNORECASE)
        )
        self._starts = [start for start, _ in ranges]
        self._reach: list[int] = []
        reach = -1
        for _, end in ranges:
            reach = max(reach, end)
            self._reach.append(reach)

    def __bool__(self) -> bool:
        return bool(self._starts)

    def contains(self, start: int, end: int) -> bool:
        index = bisect.bisect_right(self._starts, start) - 1
        return index >= 0 and self._reach[index] >= end


_KNOWLEDGE_KEYS = ("knowledge_levels", "knowledge_levels_en", "knowledge_source", "knowledge_status", "knowledge_provider", "knowledge_detail")


class _Occupancy:
    """已被实体占用的位置，按码点记录，判断重叠是 O(长度) 而不是 O(实体数)。"""

    def __init__(self, length: int, ranges: list[tuple[int, int]]):
        self._taken = bytearray(length + 1)
        for start, end in ranges:
            self.take(start, end)

    def free(self, start: int, end: int) -> bool:
        return not any(self._taken[start:end])

    def take(self, start: int, end: int) -> None:
        self._taken[start:end] = b"\x01" * (end - start)


def propagate_occurrences(text: str, spans: list[Span], preserved: PreservedRanges | None = None) -> tuple[list[Span], int]:
    """同一实体全文一致：已识别实体的其他出现位置按同样的类型和状态处理。

    与 LLM 补漏的 find-all 使用同一护栏（长度 < 2 不扩展、拉丁串要求词边界），
    已被其他实体覆盖的位置、以及落在保留词范围内的位置不扩展。
    """
    active = [span for span in spans if span.status != "rejected"]
    occupied = _Occupancy(len(text), [(span.start, span.end) for span in active])
    added: list[Span] = []
    seen: set[tuple[str, EntityType]] = set()
    for span in sorted(active, key=lambda item: (-(item.end - item.start), item.start)):
        key = (span.text, span.entity_type)
        if key in seen:
            continue
        seen.add(key)
        for start, end in find_all_occurrences(text, span.text):
            if not occupied.free(start, end) or (preserved and preserved.contains(start, end)):
                continue
            occupied.take(start, end)
            added.append(Span(
                id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=text[start:end], entity_type=span.entity_type,
                score=span.score, sources=list(span.sources), status=span.status, conflict=False, strategy=span.strategy,
                metadata={**{name: span.metadata[name] for name in _KNOWLEDGE_KEYS if name in span.metadata}, "propagated_from": span.id},
            ))
    return sorted(spans + added, key=lambda item: (item.start, item.end)), len(added)


async def run_pipeline(
    request: DetectRequest,
    policies: dict[EntityType, Strategy] | None = None,
    persistent_rules: list[dict] | None = None,
    memory: dict | None = None,
    hint_spans: list[Span] | None = None,
):
    """完整流水线。memory 是替换记忆（掩码编号、差分隐私替换词），同一文件的多段共用一份，
    保证同一实体全文件编号一致；hint_spans 是调用方已知的实体（如 CSV 中“姓名”列的整格内容）。"""
    strategy = request.strategy
    strength = request.privacy_strength
    enabled = set(request.enabled_entity_types)
    preserve_terms = list(request.preserve_terms)
    custom_rules = [dict(rule) for rule in (persistent_rules or []) if rule.get("enabled", True)] + [
        {"id": f"keyword_{index}", "name": "项目关键词", "kind": "keyword", "pattern": item.value,
         "entity_type": item.entity_type.value, "enabled": True, "case_sensitive": item.case_sensitive, "force": True}
        for index, item in enumerate(request.custom_keywords)
    ] + [
        {"id": f"pattern_{index}", "name": item.name, "kind": "regex", "pattern": item.pattern,
         "entity_type": item.entity_type.value, "enabled": True, "case_sensitive": item.case_sensitive, "force": True}
        for index, item in enumerate(request.custom_patterns)
    ]
    instruction_plan = None
    type_strategies: dict[EntityType, Strategy] = {}
    if request.instruction and request.instruction.strip():
        started = time.perf_counter()
        instruction_plan = await parse_instruction(request.instruction.strip(), request.use_llm, request.deployment_mode)
        if instruction_plan.get("enabled_entity_types"):
            enabled = {EntityType(value) for value in instruction_plan["enabled_entity_types"]}
        enabled |= {EntityType(value) for value in instruction_plan.get("force_types") or []}
        enabled -= {EntityType(value) for value in instruction_plan.get("disabled_entity_types") or []}
        preserve_terms.extend(instruction_plan.get("preserve_terms", []))
        custom_rules.extend(_instruction_rules(instruction_plan.get("force_terms", [])))
        if instruction_plan.get("strategy"):
            strategy = Strategy(instruction_plan["strategy"])
        if instruction_plan.get("privacy_strength"):
            strength = int(instruction_plan["privacy_strength"])
        type_strategies = {EntityType(key): Strategy(value) for key, value in (instruction_plan.get("type_strategies") or {}).items()}
        ignored = instruction_plan.get("ignored_clauses") or []
        detail = f"{instruction_plan.get('parser', 'deterministic')} · 与菜单配置合并"
        if ignored:
            detail += f"；{len(ignored)} 句要求暂不支持（如只保留后几位），这部分按默认方式整段处理"
        instruction_trace = TraceStep(key="instruction", label="自然语言需求解析", duration_ms=max(1, round((time.perf_counter()-started)*1000)), count=len(instruction_plan.get("preserve_terms", []))+len(instruction_plan.get("force_terms", [])), detail=detail)
    else:
        instruction_trace = TraceStep(key="instruction", label="自然语言需求解析", duration_ms=0, count=0, status="skipped", detail="本次使用菜单配置")

    rule_job = asyncio.to_thread(detect_rule_spans, request.text, strategy, custom_rules, enabled, True)
    lite_job = asyncio.to_thread(detect_lite_ner_spans, request.text, strategy, enabled, request.language)
    implicit_job = asyncio.to_thread(detect_implicit_spans, request.text, strategy, enabled, request.language)
    model_job = ner_adapter.detect(request.text, strategy)
    (rule_spans, rule_trace), (lite_spans, lite_trace), (implicit_spans, implicit_trace), (model_spans, model_trace) = await asyncio.gather(rule_job, lite_job, implicit_job, model_job)
    model_spans = [span for span in model_spans if span.entity_type in enabled and _matches_language(span, request.language)]
    # 启用 NER 模型时，模型与轻量识别器同时运行，轨迹里合并计数；隐性隐私识别在界面上也归入 NER 层
    if model_trace.status == "skipped":
        ner_trace = lite_trace
    else:
        ner_trace = model_trace.model_copy(update={
            "count": model_trace.count + lite_trace.count,
            "duration_ms": max(model_trace.duration_ms, lite_trace.duration_ms),
            "detail": f"{model_trace.detail}；{lite_trace.label} {lite_trace.count} 处",
        })
    if implicit_spans:
        ner_trace = ner_trace.model_copy(update={"count": ner_trace.count + len(implicit_spans), "detail": f"{ner_trace.detail}；{implicit_trace.label} {len(implicit_spans)} 处"})
    hints = [span for span in (hint_spans or []) if span.entity_type in enabled]
    preserved = PreservedRanges(request.text, preserve_terms)

    def keep(span: Span) -> bool:
        in_scope = span.entity_type in enabled or bool(span.metadata.get("force_term"))
        return in_scope and not preserved.contains(span.start, span.end)

    # 合并、补全和替换都是纯计算，放到线程里做，长文本不会卡住其他请求
    merged = await asyncio.to_thread(lambda: [span for span in merge_spans(request.text, rule_spans + lite_spans + implicit_spans + model_spans + hints) if keep(span)])

    if request.use_llm:
        merged, llm_trace = await verify_with_llm(request.text, merged, strategy, request.instruction, request.deployment_mode)
        merged = await asyncio.to_thread(lambda: [span for span in merge_spans(request.text, merged) if keep(span) and _matches_language(span, request.language)])
    else:
        llm_trace = TraceStep(key="llm", label="LLM 核查与补漏", duration_ms=0, count=0, status="skipped", detail="本次处理关闭了大模型核查")
    merged, propagated = await asyncio.to_thread(propagate_occurrences, request.text, merged, preserved)
    per_span = bool(policies or type_strategies)
    for span in merged:
        # 按类型设置的方式 < 自然语言里针对某类实体的要求（“姓名用差分隐私替换”）
        span.strategy = (policies or {}).get(span.entity_type, strategy)
        span.strategy = type_strategies.get(span.entity_type, span.strategy)

    generalization_active = strategy == Strategy.GENERALIZE or any(span.strategy == Strategy.GENERALIZE for span in merged)
    if generalization_active:
        knowledge_trace = await knowledge_graph.enrich_spans(merged)
    else:
        knowledge_trace = TraceStep(key="knowledge", label="知识图谱分级", duration_ms=0, count=0, status="skipped", detail="当前策略未启用泛化")

    started = time.perf_counter()
    include_pending = request.risk_level == "strict"
    redacted, replacements = await asyncio.to_thread(redact_with_map, request.text, merged, None if per_span else strategy, strength, include_pending, memory)
    pseudonym_active = strategy == Strategy.PSEUDONYMIZE or any(span.strategy == Strategy.PSEUDONYMIZE for span in merged)
    mechanism = pseudonymization_metadata(strength)
    mechanism_detail = f"、指数机制 ε={mechanism['epsilon']}" if pseudonym_active else ""
    merge_trace = TraceStep(key="merge", label="合并与脱敏", duration_ms=max(3, round((time.perf_counter()-started)*1000)), count=len(merged), detail=f"偏移校验、冲突消歧、同名补全 {propagated} 处、强度 {strength}/3{mechanism_detail}、{'包含待确认实体' if include_pending else '仅处理已确认实体'}")
    traces = [instruction_trace, rule_trace, ner_trace, llm_trace, knowledge_trace, merge_trace]
    requested = request.model_dump(mode="json", exclude={"text", "project_id", "persist"})
    applied_config = {
        **requested,
        # 用户提交的原始方案。下面几项会被自然语言要求改写成实际生效的值；重新打开任务时界面恢复的是这一份，
        # 免得把“保留北京”解析出的保留词等写回方案，悄悄带到下一段文本
        "requested": requested,
        "project_id": request.project_id, "language": request.language, "risk_level": request.risk_level, "strategy": strategy.value,
        "privacy_strength": strength, "deployment_mode": request.deployment_mode,
        "enabled_entity_types": [item.value for item in sorted(enabled, key=lambda item: item.value)],
        "preserve_terms": list(dict.fromkeys(preserve_terms)), "custom_rule_count": len(custom_rules),
        "instruction_plan": instruction_plan,
        "pseudonymization": pseudonymization_metadata(strength),
        "knowledge_graph": knowledge_graph.status(),
        "pending_policy": "include" if include_pending else "accepted-only",
    }
    return merged, redacted, traces, applied_config, replacements
