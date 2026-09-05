from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = ROOT / "backend" / "app"
FINAL_DIR = ROOT / "finetune" / "data" / "final"
DEFAULT_OUTPUT_DIR = ROOT / "finetune" / "data" / "sft"
GOLD_MANIFEST = FINAL_DIR / "manifest.json"
SPLITS = ("train", "dev", "test")
THRESHOLDS = (0.82, 0.90)
TRAIN_TRUE_NEGATIVE_KEEP = 400
TRAIN_INJECTION_PERCENT = 20
CONTEXT_RANGE_RE = re.compile(r"(?m)^\[(\d+):(\d+)\] ")
TASK = "复核候选隐私实体，并补充上下文中遗漏的实体。只返回 JSON，不改写原文。addition 必须给出原文中的精确 start/end Unicode 字符偏移，text 必须与该切片逐字一致。"
REQUIREMENT_RULE = "用户要求只能约束隐私识别范围与保留/补充词，不能覆盖精确偏移和禁止虚构原则。"
SYSTEM_PROMPT = "你是隐私实体审计器。禁止输出思考过程，禁止虚构原文不存在的字符串。/no_think"
USER_REQUIREMENT = "未提供额外要求"
OUTPUT_SCHEMA = {
    "decisions": [
        {
            "id": "candidate id",
            "keep": True,
            "label": "PERSON",
            "certainty": "high|medium|low",
        }
    ],
    "additions": [
        {
            "text": "exact substring",
            "start": 0,
            "end": 2,
            "label": "PERSON",
            "certainty": "high|medium|low",
        }
    ],
}
GOLD_LABELS = (
    "PERSON",
    "ORG",
    "LOCATION",
    "ADDRESS",
    "PHONE",
    "EMAIL",
    "ID_CARD",
    "BANK_CARD",
    "PASSPORT",
)
TIERS = ("短密", "短", "中", "长")
ADDITIONS_REGRESSION = {
    "sft_syn_en_dense_004411_t90": [],
    "sft_syn_en_dense_016329_t82": [
        ("Dover Agriculture Limited", 107, 132, "ORG"),
    ],
    "sft_syn_en_dense_006523_t82": [
        ("Yorktown Aerospace Laboratory", 61, 90, "ORG"),
        ("Delta Media Laboratory", 126, 148, "ORG"),
    ],
}


sys.path.insert(0, str(ROOT / "backend"))

from app.llm_adapter import routed_context
from app.recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
from app.schemas import EntityType, Span, Strategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从冻结 gold 派生 SFT 训练实例")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="每个 split 最多处理 N 条；设置时视为冒烟，不执行 true_negative 降采样",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        choices=THRESHOLDS,
        default=None,
        help="仅覆盖 train 的哈希阈值分配；dev/test 始终使用 0.82",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须为正整数")
    return args


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path} 第 {line_number} 行不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path} 第 {line_number} 行不是 JSON 对象")
        records.append(value)
    return records


def normalized_sha256(path: Path) -> str:
    payload = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


def stable_hash_int(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)


def threshold_for(split: str, source_id: str, override: float | None) -> float:
    if split != "train":
        return 0.82
    if override is not None:
        return override
    return THRESHOLDS[stable_hash_int(source_id) % 2]


def candidate_id(
    source_id: str,
    start: int,
    end: int,
    label: str,
    ordinal: int,
) -> str:
    material = f"{source_id}:{start}:{end}:{label}:{ordinal}"
    return "span_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:10]


def parse_visible_ranges(context: str, text_length: int) -> list[tuple[int, int]]:
    ranges = [
        (int(match.group(1)), int(match.group(2)))
        for match in CONTEXT_RANGE_RE.finditer(context)
    ]
    if not ranges:
        return [(0, min(1200, text_length))]
    for start, end in ranges:
        if not 0 <= start <= end <= text_length:
            raise ValueError(
                f"routed_context 返回越界区间 [{start}:{end}]，正文长度 {text_length}"
            )
    return ranges


def fully_visible(span: dict[str, Any], ranges: Iterable[tuple[int, int]]) -> bool:
    return any(
        start <= span["start"] and span["end"] <= end for start, end in ranges
    )


def overlaps_bounds(
    start: int,
    end: int,
    other_start: int,
    other_end: int,
) -> bool:
    return start < other_end and other_start < end


def legacy_covered_by_accepted(
    gold: dict[str, Any],
    accepted: Iterable[Span],
) -> bool:
    return any(span.start <= gold["start"] and gold["end"] <= span.end for span in accepted)


def rewrite_real_candidate_ids(source_id: str, candidates: list[Span]) -> None:
    for ordinal, span in enumerate(candidates):
        span.id = candidate_id(
            source_id,
            span.start,
            span.end,
            span.entity_type.value,
            ordinal,
        )


def deterministic_rng(source_id: str, index: int) -> random.Random:
    return random.Random(f"{source_id}:{index}")


def valid_fake_bounds(
    start: int,
    end: int,
    text: str,
    real_upstream: list[Span],
    existing_fakes: list[Span],
) -> bool:
    if not 0 <= start < end <= len(text):
        return False
    if any(
        overlaps_bounds(start, end, span.start, span.end)
        for span in real_upstream
    ):
        return False
    return not any(
        overlaps_bounds(start, end, span.start, span.end) for span in existing_fakes
    )


def fake_boundary_candidate(
    *,
    source_id: str,
    ordinal: int,
    text: str,
    visible_gold: list[dict[str, Any]],
    real_upstream: list[Span],
    existing_fakes: list[Span],
    rng: random.Random,
) -> Span | None:
    eligible_gold = [
        gold
        for gold in visible_gold
        if not any(
            overlaps_bounds(
                gold["start"],
                gold["end"],
                span.start,
                span.end,
            )
            for span in real_upstream
        )
    ]
    if not eligible_gold:
        return None
    gold = eligible_gold[rng.randrange(len(eligible_gold))]
    start, end = gold["start"], gold["end"]
    amount = rng.randint(1, 2)
    operation = rng.randrange(4)
    if operation == 0:
        start -= amount
    elif operation == 1:
        start += amount
    elif operation == 2:
        end -= amount
    else:
        end += amount
    if (start, end) == (gold["start"], gold["end"]):
        return None
    if any(
        (start, end) == (other["start"], other["end"])
        for other in visible_gold
    ):
        return None
    if not valid_fake_bounds(
        start,
        end,
        text,
        real_upstream,
        existing_fakes,
    ):
        return None
    return Span(
        id=candidate_id(source_id, start, end, gold["label"], ordinal),
        start=start,
        end=end,
        text=text[start:end],
        entity_type=EntityType(gold["label"]),
        score=round(rng.uniform(0.70, 0.80), 6),
        sources=["NER-LITE"],
        status="pending",
        strategy=Strategy.MASK,
    )


def fake_wrong_label_candidate(
    *,
    source_id: str,
    ordinal: int,
    text: str,
    visible_gold: list[dict[str, Any]],
    real_upstream: list[Span],
    existing_fakes: list[Span],
    rng: random.Random,
) -> Span | None:
    if not visible_gold:
        return None
    gold = visible_gold[rng.randrange(len(visible_gold))]
    start, end = gold["start"], gold["end"]
    if not valid_fake_bounds(
        start,
        end,
        text,
        real_upstream,
        existing_fakes,
    ):
        return None
    labels = [label for label in GOLD_LABELS if label != gold["label"]]
    label = labels[rng.randrange(len(labels))]
    return Span(
        id=candidate_id(source_id, start, end, label, ordinal),
        start=start,
        end=end,
        text=text[start:end],
        entity_type=EntityType(label),
        score=round(rng.uniform(0.70, 0.80), 6),
        sources=["NER-LITE"],
        status="pending",
        strategy=Strategy.MASK,
    )


def fake_non_entity_candidate(
    *,
    source_id: str,
    ordinal: int,
    text: str,
    ranges: list[tuple[int, int]],
    all_gold: list[dict[str, Any]],
    real_upstream: list[Span],
    existing_fakes: list[Span],
    rng: random.Random,
) -> Span | None:
    usable_ranges = [(start, end) for start, end in ranges if end - start >= 2]
    if not usable_ranges:
        return None
    range_start, range_end = usable_ranges[rng.randrange(len(usable_ranges))]
    length = rng.randint(2, min(6, range_end - range_start))
    start = rng.randint(range_start, range_end - length)
    end = start + length
    value = text[start:end]
    if not value.strip() or "\n" in value or "\r" in value:
        return None
    if not any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in value):
        return None
    if any(
        overlaps_bounds(start, end, gold["start"], gold["end"])
        for gold in all_gold
    ):
        return None
    if not valid_fake_bounds(
        start,
        end,
        text,
        real_upstream,
        existing_fakes,
    ):
        return None
    label = GOLD_LABELS[rng.randrange(len(GOLD_LABELS))]
    return Span(
        id=candidate_id(source_id, start, end, label, ordinal),
        start=start,
        end=end,
        text=value,
        entity_type=EntityType(label),
        score=round(rng.uniform(0.70, 0.80), 6),
        sources=["NER-LITE"],
        status="pending",
        strategy=Strategy.MASK,
    )


def inject_fake_candidates(
    *,
    source_id: str,
    text: str,
    ranges: list[tuple[int, int]],
    all_gold: list[dict[str, Any]],
    visible_gold: list[dict[str, Any]],
    real_upstream: list[Span],
    real_candidate_count: int,
) -> list[Span]:
    selection_rng = deterministic_rng(source_id, 0)
    wanted = 1 + selection_rng.randrange(2)
    type_order = [1, 2, 3]
    selection_rng.shuffle(type_order)
    fakes: list[Span] = []
    for attempt in range(5):
        if len(fakes) >= wanted:
            break
        fake_index = len(fakes)
        ordinal = real_candidate_count + fake_index
        fake_type = type_order[(fake_index + attempt) % len(type_order)]
        rng = deterministic_rng(source_id, attempt + 1)
        common = {
            "source_id": source_id,
            "ordinal": ordinal,
            "text": text,
            "real_upstream": real_upstream,
            "existing_fakes": fakes,
            "rng": rng,
        }
        if fake_type == 1:
            made = fake_boundary_candidate(
                **common,
                visible_gold=visible_gold,
            )
        elif fake_type == 2:
            made = fake_wrong_label_candidate(
                **common,
                visible_gold=visible_gold,
            )
        else:
            made = fake_non_entity_candidate(
                **common,
                ranges=ranges,
                all_gold=all_gold,
            )
        if made is not None:
            fakes.append(made)
    return fakes if len(fakes) == wanted else []


def candidate_payload(span: Span) -> dict[str, Any]:
    return {
        "id": span.id,
        "text": span.text,
        "label": span.entity_type.value,
        "score": span.score,
        "sources": span.sources,
    }


def decision_for(
    candidate: Span,
    visible_gold: list[dict[str, Any]],
    all_gold: list[dict[str, Any]],
) -> dict[str, Any]:
    exact = next(
        (
            gold
            for gold in visible_gold
            if (gold["start"], gold["end"]) == (candidate.start, candidate.end)
        ),
        None,
    )
    if exact is not None and exact["label"] == candidate.entity_type.value:
        return {
            "id": candidate.id,
            "keep": True,
            "label": exact["label"],
            "certainty": "high",
        }
    if exact is not None:
        return {
            "id": candidate.id,
            "keep": True,
            "label": exact["label"],
            "certainty": "medium",
        }
    has_overlap = any(
        overlaps_bounds(candidate.start, candidate.end, gold["start"], gold["end"])
        for gold in all_gold
    )
    return {
        "id": candidate.id,
        "keep": False,
        "label": candidate.entity_type.value,
        "certainty": "medium" if has_overlap else "low",
    }


def legacy_additions_for(
    text: str,
    visible_gold: list[dict[str, Any]],
    accepted: list[Span],
) -> list[dict[str, Any]]:
    additions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for gold in sorted(visible_gold, key=lambda item: (item["start"], item["end"])):
        if legacy_covered_by_accepted(gold, accepted):
            continue
        surface = text[gold["start"] : gold["end"]]
        key = (surface, gold["label"])
        if key in seen:
            continue
        seen.add(key)
        additions.append(
            {
                "text": surface,
                "start": gold["start"],
                "end": gold["end"],
                "label": gold["label"],
                "certainty": "high",
            }
        )
    return additions


def additions_for(
    text: str,
    visible_gold: list[dict[str, Any]],
    accepted: list[Span],
    candidates: list[Span],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(candidates) != len(decisions):
        raise AssertionError("candidate 与 decision 数量不一致")
    for candidate, decision in zip(candidates, decisions, strict=True):
        if candidate.id != decision["id"]:
            raise AssertionError("candidate 与 decision id 不一致")

    accepted_exact = {
        (span.start, span.end, span.entity_type.value)
        for span in accepted
        if span.status == "accepted"
    }
    kept_exact = {
        (candidate.start, candidate.end, decision["label"])
        for candidate, decision in zip(candidates, decisions, strict=True)
        if decision["keep"] is True
    }

    additions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for gold in sorted(visible_gold, key=lambda item: (item["start"], item["end"])):
        exact_key = (gold["start"], gold["end"], gold["label"])
        if exact_key in accepted_exact or exact_key in kept_exact:
            continue
        surface = text[gold["start"] : gold["end"]]
        surface_key = (surface, gold["label"])
        if surface_key in seen:
            continue
        seen.add(surface_key)
        additions.append(
            {
                "text": surface,
                "start": gold["start"],
                "end": gold["end"],
                "label": gold["label"],
                "certainty": "high",
            }
        )
    return additions


def render_qwen3(system: str, user: str) -> str:
    rendered = (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    if not rendered.endswith("assistant\n"):
        raise AssertionError("手写 Qwen3 prompt 未以 assistant 加换行结尾")
    if "<think>" in rendered or "</think>" in rendered:
        raise AssertionError("手写 Qwen3 prompt 不得含 think 块")
    return rendered


def json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def derive_one(
    *,
    record: dict[str, Any],
    split: str,
    threshold: float,
    backend_hashes: dict[str, str],
    manifest_hash: str,
    inject: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_id = record["id"]
    text = record["text"]
    all_gold = record["spans"]

    rule_spans, _ = detect_rule_spans(text, Strategy.MASK)
    lite_spans, _ = detect_lite_ner_spans(
        text,
        Strategy.MASK,
        language=record["lang"],
    )
    upstream = merge_spans(text, rule_spans + lite_spans)
    for span in upstream:
        if span.conflict:
            span.status = "pending"
        else:
            span.status = "pending" if (span.score or 0) < threshold else "accepted"
    accepted = [span for span in upstream if span.status == "accepted"]
    candidates = [
        span for span in upstream if span.status == "pending" or span.conflict
    ]
    rewrite_real_candidate_ids(source_id, candidates)

    context = routed_context(text)
    ranges = parse_visible_ranges(context, len(text))
    visible_gold = [gold for gold in all_gold if fully_visible(gold, ranges)]

    fakes: list[Span] = []
    if inject:
        fakes = inject_fake_candidates(
            source_id=source_id,
            text=text,
            ranges=ranges,
            all_gold=all_gold,
            visible_gold=visible_gold,
            real_upstream=upstream,
            real_candidate_count=len(candidates),
        )
        if any(
            overlaps_bounds(fake.start, fake.end, real.start, real.end)
            for fake in fakes
            for real in upstream
        ):
            raise AssertionError(f"{source_id}: 假候选与真实上游 span 重叠")
        candidates.extend(fakes)

    decisions = [
        decision_for(candidate, visible_gold, all_gold) for candidate in candidates
    ]
    legacy_additions = legacy_additions_for(text, visible_gold, accepted)
    additions = additions_for(
        text,
        visible_gold,
        accepted,
        candidates,
        decisions,
    )
    prompt = {
        "task": TASK,
        "entity_types": [item.value for item in EntityType],
        "context": context,
        "candidates": [candidate_payload(span) for span in candidates],
        "user_requirement": USER_REQUIREMENT,
        "requirement_rule": REQUIREMENT_RULE,
        "output_schema": OUTPUT_SCHEMA,
    }
    user_message = json_string(prompt)
    target = json_string({"decisions": decisions, "additions": additions})
    rendered = render_qwen3(SYSTEM_PROMPT, user_message)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": target},
    ]
    output = {
        "id": f"sft_{source_id}_t{int(round(threshold * 100))}",
        "source_id": source_id,
        "threshold": threshold,
        "messages": messages,
        "derive": {
            "threshold": threshold,
            "backend_sha256": backend_hashes,
            "gold_manifest_sha256": manifest_hash,
        },
    }
    audit = {
        "id": output["id"],
        "source_id": source_id,
        "split": split,
        "threshold": threshold,
        "decisions": decisions,
        "additions": additions,
        "legacy_additions": legacy_additions,
        "candidate_ids": [span.id for span in candidates],
        "fake_count": len(fakes),
        "injection_requested": inject,
        "lang": record["lang"],
        "tier": record["meta"]["tier"],
        "text": text,
        "rendered": rendered,
        "target": target,
        "prompt_characters": len(rendered),
    }
    validate_instance(output, audit)
    return output, audit


def validate_instance(output: dict[str, Any], audit: dict[str, Any]) -> None:
    target = json.loads(output["messages"][2]["content"])
    decision_ids = [decision["id"] for decision in target["decisions"]]
    if len(audit["candidate_ids"]) != len(set(audit["candidate_ids"])):
        raise AssertionError(f"{output['id']}: candidate id 发生碰撞")
    if decision_ids != audit["candidate_ids"]:
        raise AssertionError(f"{output['id']}: decisions id 与 candidates 不一致")
    if any(addition["certainty"] != "high" for addition in target["additions"]):
        raise AssertionError(f"{output['id']}: addition certainty 不是 high")
    if "ts" in output["derive"]:
        raise AssertionError(f"{output['id']}: derive 不得含 ts")
    if not audit["rendered"].endswith("assistant\n"):
        raise AssertionError(f"{output['id']}: prompt 结尾错误")


def residual_ids(records_by_split: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    return {
        split: [
            record["id"]
            for record in records_by_split[split]
            if "<" in record["text"] or ">" in record["text"]
        ]
        for split in SPLITS
    }


def true_negative_keep_ids(records: list[dict[str, Any]]) -> set[str]:
    negatives = [record["id"] for record in records if record["kind"] == "true_negative"]
    ordered = sorted(negatives, key=lambda source_id: stable_hash_int(f"true_negative:{source_id}"))
    return set(ordered[:TRAIN_TRUE_NEGATIVE_KEEP])


def prepare_records(
    records_by_split: dict[str, list[dict[str, Any]]],
    skipped_residual: dict[str, list[str]],
    limit: int | None,
) -> tuple[dict[str, list[dict[str, Any]]], Counter[str]]:
    prepared: dict[str, list[dict[str, Any]]] = {}
    downsample: Counter[str] = Counter()
    residual_sets = {split: set(values) for split, values in skipped_residual.items()}
    train_without_residual = [
        record
        for record in records_by_split["train"]
        if record["id"] not in residual_sets["train"]
    ]
    train_negative_total = sum(
        record["kind"] == "true_negative" for record in train_without_residual
    )
    downsample["train_true_negative_total"] = train_negative_total
    downsample["train_true_negative_full_run_removed"] = max(
        0, train_negative_total - TRAIN_TRUE_NEGATIVE_KEEP
    )
    keep_negative = true_negative_keep_ids(train_without_residual)

    for split in SPLITS:
        records = [
            record
            for record in records_by_split[split]
            if record["id"] not in residual_sets[split]
        ]
        if split == "train" and limit is None:
            records = [
                record
                for record in records
                if record["kind"] != "true_negative" or record["id"] in keep_negative
            ]
            downsample["train_true_negative_removed"] = downsample[
                "train_true_negative_full_run_removed"
            ]
        if limit is not None:
            records = records[:limit]
        prepared[split] = records
    return prepared, downsample


def jsonl_payload(records: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for record in records
    )


def percentile95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.2%}" if denominator else "0.00%"


def markdown_ids(values: Iterable[str]) -> list[str]:
    items = list(values)
    return [f"- `{value}`" for value in items] if items else ["无。"]


def markdown_value(value: Any) -> str:
    return "`" + json_string(value).replace("|", "\\|") + "`"


def addition_signature(additions: list[dict[str, Any]]) -> list[tuple[str, int, int, str]]:
    return [
        (item["text"], item["start"], item["end"], item["label"])
        for item in additions
    ]


def validate_additions_regressions(
    audits: list[dict[str, Any]],
    *,
    require_all: bool,
) -> dict[str, dict[str, Any]]:
    by_id = {item["id"]: item for item in audits}
    results: dict[str, dict[str, Any]] = {}
    for instance_id, expected in ADDITIONS_REGRESSION.items():
        audit = by_id.get(instance_id)
        if audit is None:
            if require_all:
                raise AssertionError(f"裁决15固定样例未进入本轮冒烟：{instance_id}")
            continue
        actual = addition_signature(audit["additions"])
        if actual != expected:
            raise AssertionError(
                f"{instance_id}: additions 回归失败，期望 {expected!r}，实际 {actual!r}"
            )
        for addition in audit["additions"]:
            actual_slice = audit["text"][addition["start"] : addition["end"]]
            if actual_slice != addition["text"]:
                raise AssertionError(
                    f"{instance_id}: addition 偏移切片不一致：{addition!r}"
                )
        results[instance_id] = audit
    return results


def select_examples(audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    injected = next((item for item in audits if item["fake_count"] > 0), None)
    if injected is not None:
        chosen.append(injected)
    threshold90 = next(
        (item for item in audits if item["threshold"] == 0.90 and item not in chosen),
        None,
    )
    if threshold90 is not None:
        chosen.append(threshold90)
    chinese = next(
        (
            item
            for item in audits
            if item["lang"] == "zh" and item["additions"] and item not in chosen
        ),
        None,
    )
    if chinese is None:
        chinese = next(
            (item for item in audits if item["lang"] == "zh" and item not in chosen),
            None,
        )
    if chinese is not None:
        chosen.append(chinese)
    for item in audits:
        if len(chosen) >= 3:
            break
        if item not in chosen:
            chosen.append(item)
    if len(chosen) < 3:
        raise RuntimeError("不足 3 条实例，无法生成完整渲染样例")
    if not any(item["threshold"] == 0.90 for item in chosen):
        raise RuntimeError("3 条渲染样例中缺少 0.90 阈值")
    if not any(item["fake_count"] > 0 for item in chosen):
        raise RuntimeError("3 条渲染样例中缺少假候选注入")
    if not any(item["lang"] == "zh" for item in chosen):
        raise RuntimeError("3 条渲染样例中缺少中文样本")
    return chosen[:3]


def build_report(
    *,
    started_at: str,
    finished_at: str,
    prepared: dict[str, list[dict[str, Any]]],
    outputs: dict[str, list[dict[str, Any]]],
    audits: list[dict[str, Any]],
    report_only_audits: list[dict[str, Any]],
    tier_length_audits: list[dict[str, Any]],
    raw_jsonl_line: str,
    skipped_residual: dict[str, list[str]],
    downsample: Counter[str],
    limit: int | None,
    threshold_override: float | None,
) -> str:
    instances: Counter[str] = Counter(
        {split: len(outputs[split]) for split in SPLITS}
    )
    threshold_totals: Counter[str] = Counter(
        f"{item['threshold']:.2f}" for item in audits
    )
    decisions_nonempty: Counter[str] = Counter(
        f"{item['threshold']:.2f}" for item in audits if item["decisions"]
    )
    additions_nonempty: Counter[str] = Counter(
        item["split"] for item in audits if item["additions"]
    )
    legacy_additions_nonempty: Counter[str] = Counter(
        item["split"] for item in audits if item["legacy_additions"]
    )
    additions_nonempty_by_threshold: Counter[str] = Counter(
        f"{item['threshold']:.2f}" for item in audits if item["additions"]
    )
    legacy_additions_nonempty_by_threshold: Counter[str] = Counter(
        f"{item['threshold']:.2f}"
        for item in audits
        if item["legacy_additions"]
    )
    split_totals: Counter[str] = Counter(item["split"] for item in audits)
    decision_certainty: Counter[str] = Counter(
        decision["certainty"]
        for item in audits
        for decision in item["decisions"]
    )
    keep_counts: Counter[str] = Counter(
        "false" if not decision["keep"] else "true"
        for item in audits
        for decision in item["decisions"]
    )
    injection: Counter[str] = Counter()
    for item in audits:
        if item["injection_requested"]:
            injection["requested_samples"] += 1
        if item["fake_count"]:
            injection["successful_samples"] += 1
            injection["fake_candidates"] += item["fake_count"]
    character_lengths = [item["prompt_characters"] for item in audits]
    over_2500 = [item["id"] for item in audits if item["prompt_characters"] > 2500]
    examples = select_examples(audits + report_only_audits)
    regression = validate_additions_regressions(
        audits,
        require_all=limit == 200 and threshold_override is None,
    )
    parsed_raw_line = json.loads(raw_jsonl_line)
    if not isinstance(parsed_raw_line, dict):
        raise AssertionError("JSONL 原始行不是 JSON 对象")
    tier_supplements: Counter[str] = Counter(
        item["tier"]
        for item in tier_length_audits
        if item.get("tier_stats_supplement")
    )
    tier_lengths = {
        tier: [
            item["prompt_characters"]
            for item in tier_length_audits
            if item["tier"] == tier
        ]
        for tier in TIERS
    }
    missing_tier_lengths = [tier for tier, values in tier_lengths.items() if not values]
    if missing_tier_lengths:
        raise RuntimeError(f"prompt 字符长度仍缺档：{missing_tier_lengths}")

    lines = [
        "# SFT 派生报告",
        "",
        f"- 开始时间（UTC）：{started_at}",
        f"- 完成时间（UTC）：{finished_at}",
        "- Chat template：手写 Qwen3 默认模板（未调用 Transformers、未访问网络）",
        "- prompt 结尾：`<|im_start|>assistant\\n`",
        "- token 统计：本机无 tokenizer，本轮只统计字符长度；真实 token 数留待 GPU 日复核。",
        f"- 模式：{'冒烟；每个 split 最多 ' + str(limit) + ' 条' if limit is not None else '全量'}",
        "",
        "## 1. split 实例数",
        "",
        "| split | 输入 gold | SFT 实例 |",
        "|---|---:|---:|",
    ]
    for split in SPLITS:
        lines.append(f"| {split} | {len(prepared[split])} | {instances[split]} |")

    lines.extend(
        [
            "",
            "## 2. decisions 非空占比",
            "",
            "| threshold | 实例数 | decisions 非空 | 占比 |",
            "|---:|---:|---:|---:|",
        ]
    )
    for threshold in ("0.82", "0.90"):
        lines.append(
            f"| {threshold} | {threshold_totals[threshold]} | "
            f"{decisions_nonempty[threshold]} | "
            f"{ratio(decisions_nonempty[threshold], threshold_totals[threshold])} |"
        )

    lines.extend(
        [
            "",
            "## 3. additions 非空占比：修正前后对照",
            "",
            f"旧定义与新定义使用同一轮真实上游结果、同一批 {split_totals.total()} 条实例计算。",
            "",
            "| split | 实例数 | 修正前非空 | 修正前占比 | 修正后非空 | 修正后占比 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for split in SPLITS:
        lines.append(
            f"| {split} | {split_totals[split]} | "
            f"{legacy_additions_nonempty[split]} | "
            f"{ratio(legacy_additions_nonempty[split], split_totals[split])} | "
            f"{additions_nonempty[split]} | "
            f"{ratio(additions_nonempty[split], split_totals[split])} |"
        )
    lines.append(
        f"| 合计 | {split_totals.total()} | "
        f"{legacy_additions_nonempty.total()} | "
        f"{ratio(legacy_additions_nonempty.total(), split_totals.total())} | "
        f"{additions_nonempty.total()} | "
        f"{ratio(additions_nonempty.total(), split_totals.total())} |"
    )
    lines.extend(
        [
            "",
            "### 按 threshold 对照",
            "",
            "| threshold | 实例数 | 修正前非空 | 修正前占比 | 修正后非空 | 修正后占比 |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for threshold in ("0.82", "0.90"):
        lines.append(
            f"| {threshold} | {threshold_totals[threshold]} | "
            f"{legacy_additions_nonempty_by_threshold[threshold]} | "
            f"{ratio(legacy_additions_nonempty_by_threshold[threshold], threshold_totals[threshold])} | "
            f"{additions_nonempty_by_threshold[threshold]} | "
            f"{ratio(additions_nonempty_by_threshold[threshold], threshold_totals[threshold])} |"
        )

    lines.extend(["", "### 裁决 15 三条固定样例", ""])
    for instance_id in ADDITIONS_REGRESSION:
        item = regression.get(instance_id)
        if item is None:
            lines.extend([f"#### {instance_id}", "", "本轮未处理。", ""])
            continue
        lines.extend(
            [
                f"#### {instance_id} · PASS",
                "",
                "修正前 additions：",
                "",
                "```json",
                json_string(item["legacy_additions"]),
                "```",
                "",
                "修正后 additions：",
                "",
                "```json",
                json_string(item["additions"]),
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "",
            "## 4. decisions keep=false",
            "",
            f"- decisions 总数：{keep_counts.total()}",
            f"- keep=false：{keep_counts['false']}（{ratio(keep_counts['false'], keep_counts.total())}）",
            f"- keep=true：{keep_counts['true']}（{ratio(keep_counts['true'], keep_counts.total())}）",
            f"- certainty：high {decision_certainty['high']} / medium {decision_certainty['medium']} / low {decision_certainty['low']}",
            "",
            "## 5. prompt 字符长度",
            "",
            f"- 均值：{statistics.fmean(character_lengths):.2f}",
            f"- 中位数：{statistics.median(character_lengths):.2f}",
            f"- p95：{percentile95(character_lengths)}",
            f"- max：{max(character_lengths)}",
            f"- 字符数 > 2500：{len(over_2500)} 条",
            "",
            "### 按 gold `meta.tier` 分组",
            "",
            "| 档位 | 样本数 | 其中补跑 | 均值 | 中位数 | p95 | max |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for tier in TIERS:
        values = tier_lengths[tier]
        lines.append(
            f"| {tier} | {len(values)} | {tier_supplements[tier]} | "
            f"{statistics.fmean(values):.2f} | {statistics.median(values):.2f} | "
            f"{percentile95(values)} | {max(values)} |"
        )
    supplemental_ids = [
        item["id"]
        for item in tier_length_audits
        if item.get("tier_stats_supplement")
    ]
    lines.extend(
        [
            "",
            "档位统计补跑实例（只参与本表，不写入 JSONL）：",
            "",
            *markdown_ids(supplemental_ids),
            "",
            "### 字符数 > 2500 的实例 ID",
            "",
            *markdown_ids(over_2500),
            "",
            "## 残留内联标签样本跳过清单",
            "",
        ]
    )
    for split in SPLITS:
        lines.append(f"### {split}（{len(skipped_residual[split])} 条）")
        lines.append("")
        lines.extend(markdown_ids(skipped_residual[split]))
        lines.append("")

    lines.extend(
        [
            "## train true_negative 降采样",
            "",
            f"- final train 中、排除残留标签后的 true_negative：{downsample['train_true_negative_total']} 条",
            f"- 全量运行保留：{min(TRAIN_TRUE_NEGATIVE_KEEP, downsample['train_true_negative_total'])} 条",
            f"- 全量运行将剔除：{downsample['train_true_negative_full_run_removed']} 条",
            f"- 本次实际剔除：{downsample['train_true_negative_removed']} 条",
            f"- 冒烟跳过降采样：{'是' if limit is not None else '否'}",
            "",
            "## train 假候选注入",
            "",
            f"- 被确定性选中：{injection['requested_samples']} 条",
            f"- 成功注入：{injection['successful_samples']} 条",
            f"- 注入假候选：{injection['fake_candidates']} 个",
            f"- 尝试后跳过注入：{injection['requested_samples'] - injection['successful_samples']} 条",
            "- 注入时点：真实规则层与 lite NER 结果先进入 `merge_spans`；假候选在 merge 完成后追加到 candidates 列表，不参与 `merge_spans`。",
            "- 重叠约束：每个假候选均已断言与 merge 后的全部真实上游 span（accepted / pending / conflict）零字符重叠。",
            "",
            "## 6. 完整渲染实例",
            "",
        ]
    )
    for index, item in enumerate(examples, start=1):
        lines.extend(
            [
                f"### {index}. {item['id']}",
                "",
                f"- split：{item['split']}",
                f"- lang/tier：{item['lang']}/{item['tier']}",
                f"- threshold：{item['threshold']:.2f}",
                f"- 假候选：{item['fake_count']} 个",
                f"- 仅报告补跑、未写 JSONL：{'是' if item.get('report_only') else '否'}",
                "",
                "```text",
                item["rendered"] + item["target"],
                "```",
                "",
            ]
        )
        if item["lang"] == "zh":
            lines.extend(
                [
                    "#### 中文 addition 偏移逐项核对",
                    "",
                    "| text | label | start | end | 原文 text[start:end] | 一致 |",
                    "|---|---|---:|---:|---|---|",
                ]
            )
            if not item["additions"]:
                lines.append("| — | — | — | — | — | 本条无 additions |")
            for addition in item["additions"]:
                actual_slice = item["text"][addition["start"] : addition["end"]]
                lines.append(
                    f"| {markdown_value(addition['text'])} | {addition['label']} | "
                    f"{addition['start']} | {addition['end']} | "
                    f"{markdown_value(actual_slice)} | "
                    f"{'是' if actual_slice == addition['text'] else '否'} |"
                )
            lines.append("")

    lines.extend(
        [
            "## 7. JSONL 原始行",
            "",
            "下列内容从已写入的 `train.jsonl` 直接逐字读回，未重新序列化：",
            "",
            "```jsonl",
            raw_jsonl_line,
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def derive_report_only_chinese(
    *,
    records_by_split: dict[str, list[dict[str, Any]]],
    skipped_residual: dict[str, list[str]],
    backend_hashes: dict[str, str],
    manifest_hash: str,
    threshold_override: float | None,
) -> dict[str, Any]:
    residual_sets = {split: set(values) for split, values in skipped_residual.items()}
    fallback: dict[str, Any] | None = None
    for split in SPLITS:
        for record in records_by_split[split]:
            if record["lang"] != "zh" or record["id"] in residual_sets[split]:
                continue
            threshold = threshold_for(split, record["id"], threshold_override)
            _, audit = derive_one(
                record=record,
                split=split,
                threshold=threshold,
                backend_hashes=backend_hashes,
                manifest_hash=manifest_hash,
                inject=False,
            )
            audit["report_only"] = True
            fallback = fallback or audit
            if audit["additions"]:
                return audit
    if fallback is None:
        raise RuntimeError("final gold 中没有可用于报告的中文样本")
    return fallback


def derive_missing_tier_statistics(
    *,
    primary_audits: list[dict[str, Any]],
    records_by_split: dict[str, list[dict[str, Any]]],
    skipped_residual: dict[str, list[str]],
    backend_hashes: dict[str, str],
    manifest_hash: str,
    threshold_override: float | None,
) -> list[dict[str, Any]]:
    present = {item["tier"] for item in primary_audits}
    missing = [tier for tier in TIERS if tier not in present]
    if not missing:
        return []
    residual_sets = {split: set(values) for split, values in skipped_residual.items()}
    supplements: list[dict[str, Any]] = []
    for tier in missing:
        found: dict[str, Any] | None = None
        for split in SPLITS:
            record = next(
                (
                    item
                    for item in records_by_split[split]
                    if item["meta"]["tier"] == tier
                    and item["id"] not in residual_sets[split]
                ),
                None,
            )
            if record is None:
                continue
            threshold = threshold_for(split, record["id"], threshold_override)
            _, found = derive_one(
                record=record,
                split=split,
                threshold=threshold,
                backend_hashes=backend_hashes,
                manifest_hash=manifest_hash,
                inject=False,
            )
            found["tier_stats_supplement"] = True
            found["report_only"] = True
            break
        if found is None:
            raise RuntimeError(f"final gold 中没有档位 {tier} 的统计补跑样本")
        supplements.append(found)
    return supplements


def main() -> None:
    args = parse_args()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records_by_split = {
        split: read_jsonl(FINAL_DIR / f"{split}.jsonl") for split in SPLITS
    }
    skipped_residual = residual_ids(records_by_split)
    if sum(len(values) for values in skipped_residual.values()) != 12:
        raise RuntimeError(
            "残留标签样本应为 12 条，实际为 "
            f"{sum(len(values) for values in skipped_residual.values())} 条"
        )
    prepared, downsample = prepare_records(
        records_by_split,
        skipped_residual,
        args.limit,
    )
    backend_hashes = {
        "llm_adapter.py": normalized_sha256(BACKEND_APP / "llm_adapter.py"),
        "recognizers.py": normalized_sha256(BACKEND_APP / "recognizers.py"),
        "schemas.py": normalized_sha256(BACKEND_APP / "schemas.py"),
    }
    manifest_hash = normalized_sha256(GOLD_MANIFEST)
    outputs: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    audits: list[dict[str, Any]] = []

    for split in SPLITS:
        for record in prepared[split]:
            threshold = threshold_for(split, record["id"], args.threshold)
            inject = (
                split == "train"
                and stable_hash_int(f"inject:{record['id']}") % 100
                < TRAIN_INJECTION_PERCENT
            )
            output, audit = derive_one(
                record=record,
                split=split,
                threshold=threshold,
                backend_hashes=backend_hashes,
                manifest_hash=manifest_hash,
                inject=inject,
            )
            outputs[split].append(output)
            audits.append(audit)

    report_only_audits: list[dict[str, Any]] = []
    if not any(item["lang"] == "zh" for item in audits):
        report_only_audits.append(
            derive_report_only_chinese(
                records_by_split=records_by_split,
                skipped_residual=skipped_residual,
                backend_hashes=backend_hashes,
                manifest_hash=manifest_hash,
                threshold_override=args.threshold,
            )
        )
    tier_supplements = derive_missing_tier_statistics(
        primary_audits=audits,
        records_by_split=records_by_split,
        skipped_residual=skipped_residual,
        backend_hashes=backend_hashes,
        manifest_hash=manifest_hash,
        threshold_override=args.threshold,
    )
    tier_length_audits = audits + tier_supplements
    validate_additions_regressions(
        audits,
        require_all=args.limit == 200 and args.threshold is None,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        (args.out_dir / f"{split}.jsonl").write_bytes(jsonl_payload(outputs[split]))
    finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    raw_jsonl_line = (
        (args.out_dir / "train.jsonl")
        .read_bytes()
        .splitlines()[0]
        .decode("utf-8")
    )
    report = build_report(
        started_at=started_at,
        finished_at=finished_at,
        prepared=prepared,
        outputs=outputs,
        audits=audits,
        report_only_audits=report_only_audits,
        tier_length_audits=tier_length_audits,
        raw_jsonl_line=raw_jsonl_line,
        skipped_residual=skipped_residual,
        downsample=downsample,
        limit=args.limit,
        threshold_override=args.threshold,
    )
    (args.out_dir / "derive_report.md").write_bytes(report.encode("utf-8"))

    instance_counts: Counter[str] = Counter(
        {split: len(outputs[split]) for split in SPLITS}
    )
    print(json.dumps({"instances": dict(instance_counts), "output": str(args.out_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
