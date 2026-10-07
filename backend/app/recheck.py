"""导出前隐私复检：在最终稿里查找仍可识别的敏感信息。

复检使用与识别层相同的规则和轻量识别器，并额外检查已确认实体的原文是否还出现在最终稿中。
系统生成的替换词、用户明确恢复的原文和自然语言要求里的保留词不算残留。
"""
import bisect
from typing import Any

from .llm_adapter import find_all_occurrences
from .recognizers import detect_implicit_spans, detect_lite_ner_spans, detect_rule_spans
from .schemas import EntityType, Strategy


class _AllowedRanges:
    """允许出现的区域（替换词、恢复的原文、保留词）。只有完全落在其中的发现才忽略：
    含有保留词的更长片段（“北京市海淀区……”）仍要报告。"""

    def __init__(self, ranges: list[tuple[int, int]]):
        ranges = sorted(ranges)
        self._starts = [start for start, _ in ranges]
        self._reach: list[int] = []
        reach = -1
        for _, end in ranges:
            reach = max(reach, end)
            self._reach.append(reach)

    def covers(self, start: int, end: int) -> bool:
        index = bisect.bisect_right(self._starts, start) - 1
        return index >= 0 and self._reach[index] >= end


def _task_rules(applied_config: dict[str, Any], persistent_rules: list[dict]) -> list[dict]:
    rules = [dict(rule) for rule in persistent_rules if rule.get("enabled", True)]
    for index, item in enumerate(applied_config.get("custom_keywords") or []):
        if isinstance(item, dict) and item.get("value"):
            rules.append({"id": f"keyword_{index}", "name": "项目关键词", "kind": "keyword", "pattern": item["value"],
                          "entity_type": item.get("entity_type", "CUSTOM"), "enabled": True,
                          "case_sensitive": bool(item.get("case_sensitive"))})
    for index, item in enumerate(applied_config.get("custom_patterns") or []):
        if isinstance(item, dict) and item.get("pattern"):
            rules.append({"id": f"pattern_{index}", "name": item.get("name", "自定义正则"), "kind": "regex",
                          "pattern": item["pattern"], "entity_type": item.get("entity_type", "CUSTOM"),
                          "enabled": True, "case_sensitive": bool(item.get("case_sensitive"))})
    plan = applied_config.get("instruction_plan") or {}
    for index, value in enumerate(plan.get("force_terms") or []):
        rules.append({"id": f"instruction_{index}", "name": "自然语言补充词", "kind": "keyword", "pattern": value,
                      "entity_type": "CUSTOM", "enabled": True, "case_sensitive": False})
    return rules


def recheck_text(text: str, payload: dict[str, Any], persistent_rules: list[dict]) -> dict[str, Any]:
    applied = payload.get("applied_config") or {}
    replacements = {str(item.get("replacement", "")) for item in payload.get("replacements", []) if item.get("replacement")}
    restored = {str(span.get("text", "")) for span in payload.get("spans", []) if span.get("status") == "rejected"}
    preserved = {str(term) for term in applied.get("preserve_terms", []) if term}

    # 最终稿里由系统生成的替换词所在区域，以及用户主动保留的原文区域
    allowed_list: list[tuple[int, int]] = []
    for value in replacements | restored | preserved:
        if len(value) >= 2:
            allowed_list.extend(find_all_occurrences(text, value))
    allowed_ranges = _AllowedRanges(allowed_list)

    findings: dict[tuple[int, int], dict[str, Any]] = {}

    def add(start: int, end: int, entity_type: str, severity: str, reason: str) -> None:
        if allowed_ranges.covers(start, end):
            return
        key = (start, end)
        current = findings.get(key)
        if current and current["severity"] == "high":
            return
        findings[key] = {"start": start, "end": end, "text": text[start:end], "entity_type": entity_type,
                         "severity": severity, "reason": reason}

    rule_spans, _ = detect_rule_spans(text, Strategy.MASK, _task_rules(applied, persistent_rules), None, True)
    for span in rule_spans:
        add(span.start, span.end, span.entity_type.value, "high", "最终稿中仍有可直接识别的信息")

    language = str(applied.get("language", "auto"))
    lite_spans, _ = detect_lite_ner_spans(text, Strategy.MASK, None, language)
    implicit_spans, _ = detect_implicit_spans(text, Strategy.MASK, None, language)
    for span in lite_spans + implicit_spans:
        reason = "职务加部门可能间接指向具体的人，建议泛化" if span.entity_type == EntityType.ROLE else "疑似姓名、机构或地点，建议人工确认"
        add(span.start, span.end, span.entity_type.value, "medium", reason)

    for span in payload.get("spans", []):
        if span.get("status") == "rejected":
            continue
        value = str(span.get("text", ""))
        if not value or value in restored or value in preserved:
            continue
        for start, end in find_all_occurrences(text, value):
            add(start, end, str(span.get("entity_type", EntityType.CUSTOM.value)), "high", "已识别的敏感原文仍出现在最终稿中")

    ordered = sorted(findings.values(), key=lambda item: (item["start"], item["end"]))
    high = sum(item["severity"] == "high" for item in ordered)
    return {
        "passed": high == 0,
        "high": high,
        "medium": len(ordered) - high,
        "findings": ordered,
        "checked_characters": len(text),
    }
