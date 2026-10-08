"""把大模型的回答还原成原文。

脱敏后的文字交给大模型分析，回答里出现的是【PERSON-001】、替换后的假名或泛化后的上位词。
这里按任务保存的替换映射（replacements）把它们换回原来的实体，只用任务自己的映射，不做猜测：

- 掩码编号容忍大模型改写格式：【PERSON-001】、PERSON-001、[PERSON_001]、Person 1 都能认出；
  编号在任务里不存在时保持原样，并列出来；
- 差分隐私替换词、自定义替换词、泛化词按原样查找；同一个词对应多个原词时不换，列出来让人判断；
  泛化词也是普通词语，换回的地方单独标出，提醒核对；
- 被大模型改写过的称呼（把“林清禾”写成“林老师”）找不到，保持原样。

传入多个任务时，排在前面的优先（插件按最近一次脱敏在前的顺序传入）；同一个编号在不同任务里指不同的人时，
按排在前面的任务还原，并在结果里注明。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MASK_REPLACEMENT = re.compile(r"^【([A-Z_]+)-(\d+)】$")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]")
# 大模型常把【】换成方括号，或者干脆去掉
_OPEN_BRACKETS = "【[［〔"
_CLOSE_BRACKETS = "】]］〕"


@dataclass
class _Target:
    """一个可以换回的替换词：它在各任务里对应的原词，按任务顺序排列。"""
    entity_type: str
    strategy: str
    by_task: list[tuple[str, list[str]]] = field(default_factory=list)  # (任务 id, 该任务里对应的原词，去重)

    def add(self, task_id: str, original: str) -> None:
        if self.by_task and self.by_task[-1][0] == task_id:
            if original not in self.by_task[-1][1]:
                self.by_task[-1][1].append(original)
        else:
            self.by_task.append((task_id, [original]))

    def resolve(self) -> tuple[str | None, list[str], str]:
        """返回 (换回的原词, 所有候选, 说明)。说明为空表示可以直接换回。"""
        task_id, originals = self.by_task[0]
        candidates: list[str] = []
        for _, items in self.by_task:
            for item in items:
                if item not in candidates:
                    candidates.append(item)
        if len(originals) > 1:
            return None, candidates, "ambiguous"
        if len(candidates) > 1:
            return originals[0], candidates, "conflict"
        return originals[0], candidates, ""


def _type_pattern(entity_type: str) -> str:
    # ID_CARD 也认 ID-CARD、ID CARD、IDCARD
    return r"[\s_\-－]?".join(re.escape(part) for part in entity_type.split("_"))


def _normalize_type(value: str) -> str:
    return re.sub(r"[^A-Za-z]", "", value).upper()


def _collect(tasks: list[dict[str, Any]]) -> tuple[dict[tuple[str, int], _Target], dict[str, _Target]]:
    masks: dict[tuple[str, int], _Target] = {}
    words: dict[str, _Target] = {}
    for task in tasks:
        task_id = str(task.get("task_id") or task.get("id") or "")
        text = task.get("text") or ""
        spans = {str(span.get("id")): span for span in task.get("spans") or [] if isinstance(span, dict)}
        for item in task.get("replacements") or []:
            replacement = str(item.get("replacement") or "")
            start, end = item.get("start"), item.get("end")
            span = spans.get(str(item.get("span_id")))
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
                original = text[start:end]
            elif span is not None:
                original = str(span.get("text") or "")
            else:
                continue
            if not replacement or not original or replacement == original:
                continue
            entity_type = str(item.get("entity_type") or (span or {}).get("entity_type") or "CUSTOM")
            strategy = str(item.get("strategy") or "")
            match = _MASK_REPLACEMENT.match(replacement)
            if match:
                key = (match.group(1), int(match.group(2)))
                masks.setdefault(key, _Target(match.group(1), "mask")).add(task_id, original)
            else:
                words.setdefault(replacement, _Target(entity_type, strategy)).add(task_id, original)
    return masks, words


def _mask_matches(text: str, masks: dict[tuple[str, int], _Target]):
    """回答里所有像掩码编号的写法。只认任务里出现过的类型，避免把普通单词当成编号。"""
    types = sorted({entity_type for entity_type, _ in masks}, key=len, reverse=True)
    if not types:
        return
    alternatives = "|".join(_type_pattern(entity_type) for entity_type in types)
    pattern = re.compile(
        rf"(?P<open>[{re.escape(_OPEN_BRACKETS)}]\s*)?(?<![A-Za-z0-9_])(?P<type>{alternatives})"
        rf"(?P<sep>\s?[\-_－–—]\s?|\s)?0*(?P<num>\d{{1,6}})(?![\d])(?P<close>\s*[{re.escape(_CLOSE_BRACKETS)}])?",
        re.IGNORECASE,
    )
    by_normalized = {_normalize_type(entity_type): entity_type for entity_type in types}
    for match in pattern.finditer(text):
        entity_type = by_normalized.get(_normalize_type(match.group("type")))
        if entity_type is None:
            continue
        number = int(match.group("num"))
        start, end = match.start(), match.end()
        # 括号成对出现时一起换掉，只有一边时不动它
        if not (match.group("open") and match.group("close")):
            if match.group("open"):
                start = match.start("type")
            if match.group("close"):
                end = match.end("num")
        bracketed = bool(match.group("open") and match.group("close"))
        yield start, end, (entity_type, number), bracketed or bool(match.group("sep") and match.group("sep").strip())


def _joins(edge: str, neighbour: str) -> bool:
    """替换词边上的字和回答里紧挨着的字连成一个词：英文和数字不能从中间切开，星号掩码不能只换一截。"""
    if _ASCII_WORD.match(edge):
        return bool(_ASCII_WORD.match(neighbour))
    return edge in "*＊" and neighbour == edge


def _word_matches(text: str, word: str):
    position = text.find(word)
    while position != -1:
        end = position + len(word)
        before_ok = position == 0 or not _joins(word[0], text[position - 1])
        after_ok = end == len(text) or not _joins(word[-1], text[end])
        if before_ok and after_ok:
            yield position, end
        position = text.find(word, position + 1)


def restore_text(text: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """返回还原后的文字、每处换回的位置（按 Unicode 码点，指还原后的文字）和没能换回的说明。"""
    masks, words = _collect(tasks)
    candidates: list[tuple[int, int, str, Any]] = []  # (start, end, kind, key)
    unknown: dict[str, int] = {}
    for start, end, key, formatted in _mask_matches(text, masks):
        if key in masks:
            candidates.append((start, end, "mask", key))
        elif formatted:
            unknown[text[start:end]] = unknown.get(text[start:end], 0) + 1
    for word in words:
        for start, end in _word_matches(text, word):
            candidates.append((start, end, "word", word))

    # 位置靠前的优先，同一位置取更长的（“某三甲医院”优先于“医院”）
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen = []
    cursor = 0
    for candidate in candidates:
        if candidate[0] >= cursor:
            chosen.append(candidate)
            cursor = candidate[1]

    pieces: list[str] = []
    items: list[dict[str, Any]] = []
    notes: dict[tuple[str, str], dict[str, Any]] = {}
    position = 0
    output_length = 0
    for start, end, kind, key in chosen:
        target = masks[key] if kind == "mask" else words[key]
        original, options, reason = target.resolve()
        surface = text[start:end]
        if reason:
            note = notes.setdefault((reason, surface if kind == "mask" else key), {
                "text": surface if kind == "mask" else key, "reason": reason, "count": 0,
                "candidates": options[:6], "entity_type": target.entity_type,
            })
            note["count"] += 1
            if original is not None:
                note["chosen"] = original
        if original is None:
            continue
        gap = text[position:start]
        pieces.append(gap)
        output_length += len(gap)
        items.append({
            "start": output_length, "end": output_length + len(original), "original": original, "replaced": surface,
            "entity_type": target.entity_type, "strategy": target.strategy, "task_id": target.by_task[0][0],
            # 泛化词是普通词语，大模型也可能在别的意思上用它，换回的地方提醒核对
            "check": target.strategy == "generalize",
        })
        pieces.append(original)
        output_length += len(original)
        position = end
    pieces.append(text[position:])

    unresolved = list(notes.values())
    unresolved.extend({"text": token, "reason": "unknown", "count": count} for token, count in unknown.items())
    return {
        "text": "".join(pieces),
        "items": items,
        "restored": len(items),
        "unresolved": unresolved,
    }
