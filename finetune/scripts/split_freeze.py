from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "finetune" / "data" / "interim" / "merged_all.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "finetune" / "data" / "final"
SEED = 20260821
TEST_SIZE = 800
DEV_SIZE = 450
TIER_ORDER = ("短密", "短", "中", "长")
LANG_ORDER = ("zh", "en", "mixed")
KIND_ORDER = ("positive", "hard_negative", "true_negative")
LABEL_ORDER = (
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
TEST_QUOTA = Counter({"短密": 290, "短": 300, "中": 160, "长": 50})
RESERVED_TEST_TRAPS = ("销售部", "王记牛肉面")
RESERVED_TEST_TRAP_LIMIT = 100
OVERLAP_THRESHOLD = 0.9


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="冻结 gold 数据的 train/dev/test 切分")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


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


def jsonl_payload(records: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for record in records
    )


def json_payload(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def normalized_sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def normalized_sha256_file(path: Path) -> str:
    return normalized_sha256_bytes(path.read_bytes())


def classify_tier(lang: str, text_length: int, entity_count: int) -> str:
    if lang not in LANG_ORDER:
        raise ValueError(f"未知语种: {lang!r}")
    if not 40 <= text_length <= 4000:
        raise ValueError(f"字符数 {text_length} 超出表 B 的 40-4000 范围")

    short_upper = 300 if lang == "en" else 200
    if text_length <= short_upper:
        return "短密" if entity_count >= 4 else "短"
    if text_length <= 549:
        return "中"
    return "长"


def normalize_records(
    source: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    normalized: list[dict[str, Any]] = []
    changes: Counter[str] = Counter()
    ids: Counter[str] = Counter()
    for source_record in source:
        record = copy.deepcopy(source_record)
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("每条记录必须有非空字符串 id")
        ids[record_id] += 1
        text = record.get("text")
        spans = record.get("spans")
        lang = record.get("lang")
        if not isinstance(text, str) or not isinstance(spans, list):
            raise ValueError(f"{record_id}: text 必须是字符串且 spans 必须是数组")
        tier = classify_tier(lang, len(text), len(spans))
        meta = record.get("meta")
        if not isinstance(meta, dict):
            raise ValueError(f"{record_id}: meta 必须是对象")
        old_tier = meta.get("tier")
        if old_tier != tier:
            changes[f"{old_tier} -> {tier}"] += 1
        meta["tier"] = tier
        normalized.append(record)

    duplicate_ids = sorted(record_id for record_id, count in ids.items() if count > 1)
    if duplicate_ids:
        raise ValueError(f"输入存在重复 id: {duplicate_ids[:20]}")
    return normalized, changes


def record_tier(record: dict[str, Any]) -> str:
    return record["meta"]["tier"]


def has_reserved_trap(record: dict[str, Any], surface: str | None = None) -> bool:
    wanted = set(RESERVED_TEST_TRAPS if surface is None else (surface,))
    return any(
        isinstance(trap, dict) and trap.get("text") in wanted
        for trap in record.get("trap_spans", [])
    )


def group_by_tier(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {tier: [] for tier in TIER_ORDER}
    for record in records:
        groups[record_tier(record)].append(record)
    return groups


def effective_test_quota(
    available: Counter[str],
) -> tuple[Counter[str], Counter[str]]:
    effective: Counter[str] = Counter(
        {tier: min(TEST_QUOTA[tier], available[tier]) for tier in TIER_ORDER}
    )
    shortages: Counter[str] = Counter(
        {
            tier: TEST_QUOTA[tier] - effective[tier]
            for tier in TIER_ORDER
            if TEST_QUOTA[tier] > effective[tier]
        }
    )
    deficit = TEST_SIZE - sum(effective.values())
    for tier in ("短密", "短"):
        extra = min(deficit, available[tier] - effective[tier])
        effective[tier] += extra
        deficit -= extra
    if deficit:
        raise ValueError(
            f"测试集按‘短密 -> 短’补齐后仍缺 {deficit} 条，无法达到固定 800 条"
        )
    return effective, shortages


def select_test(
    pools: dict[str, list[dict[str, Any]]],
    quota: Counter[str],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    candidates = [record for tier in TIER_ORDER for record in pools[tier]]
    rng.shuffle(candidates)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    tier_counts: Counter[str] = Counter()
    priority_count = 0

    for record in candidates:
        if priority_count >= RESERVED_TEST_TRAP_LIMIT:
            break
        tier = record_tier(record)
        if has_reserved_trap(record) and tier_counts[tier] < quota[tier]:
            selected.append(record)
            selected_ids.add(record["id"])
            tier_counts[tier] += 1
            priority_count += 1

    for tier in TIER_ORDER:
        for record in pools[tier]:
            if tier_counts[tier] >= quota[tier]:
                break
            if record["id"] in selected_ids:
                continue
            selected.append(record)
            selected_ids.add(record["id"])
            tier_counts[tier] += 1

    if tier_counts != quota:
        raise RuntimeError(f"测试集档位配额未填满: {dict(tier_counts)} != {dict(quota)}")
    remaining = [record for record in candidates if record["id"] not in selected_ids]
    return selected, remaining, priority_count


def proportional_quota(total: int, available: Counter[str]) -> Counter[str]:
    available_total = sum(available.values())
    if available_total < total:
        raise ValueError(f"剩余池只有 {available_total} 条，无法抽取 {total} 条开发集")
    exact = {tier: total * available[tier] / available_total for tier in TIER_ORDER}
    quota: Counter[str] = Counter({tier: int(exact[tier]) for tier in TIER_ORDER})
    left = total - sum(quota.values())
    order = sorted(TIER_ORDER, key=lambda tier: (-(exact[tier] - quota[tier]), TIER_ORDER.index(tier)))
    for tier in order:
        if not left:
            break
        if quota[tier] < available[tier]:
            quota[tier] += 1
            left -= 1
    if left:
        for tier in TIER_ORDER:
            extra = min(left, available[tier] - quota[tier])
            quota[tier] += extra
            left -= extra
    if left:
        raise RuntimeError(f"开发集比例配额仍缺 {left} 条")
    return quota


def select_dev(
    remaining: list[dict[str, Any]], quota: Counter[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups = group_by_tier(remaining)
    dev = [record for tier in TIER_ORDER for record in groups[tier][: quota[tier]]]
    dev_ids = {record["id"] for record in dev}
    train = [record for record in remaining if record["id"] not in dev_ids]
    return dev, train


def char_5grams(text: str) -> frozenset[str]:
    return frozenset(text[index : index + 5] for index in range(len(text) - 4))


def train_gram_counts(
    train: Iterable[dict[str, Any]], grams: dict[str, frozenset[str]]
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for record in train:
        counts.update(grams[record["id"]])
    return counts


def overlap_with_train(sample_grams: frozenset[str], train_counts: Counter[str]) -> float:
    if not sample_grams:
        return 0.0
    intersection = sum(train_counts[gram] > 0 for gram in sample_grams)
    union = len(sample_grams) + len(train_counts) - intersection
    return intersection / union if union else 0.0


def replacement_overlap(
    candidate_grams: frozenset[str],
    offender_grams: frozenset[str],
    train_counts: Counter[str],
) -> float:
    if not candidate_grams:
        return 0.0
    intersection = sum(
        train_counts[gram] - 1 + (gram in offender_grams) > 0
        for gram in candidate_grams
    )
    removed_unique = sum(
        train_counts[gram] == 1 and gram not in offender_grams
        for gram in candidate_grams
    )
    added_unique = sum(
        train_counts[gram] - (gram in candidate_grams) == 0
        for gram in offender_grams
    )
    prospective_train_size = len(train_counts) - removed_unique + added_unique
    union = len(candidate_grams) + prospective_train_size - intersection
    return intersection / union if union else 0.0


def update_train_counts(
    counts: Counter[str],
    *,
    remove: frozenset[str],
    add: frozenset[str],
) -> None:
    counts.subtract(remove)
    counts += Counter()
    counts.update(add)


def deduplicate_heldout(
    test: list[dict[str, Any]],
    dev: list[dict[str, Any]],
    train: list[dict[str, Any]],
) -> tuple[Counter[str], list[dict[str, Any]], Counter[str]]:
    all_records = test + dev + train
    grams = {record["id"]: char_5grams(record["text"]) for record in all_records}
    counts = train_gram_counts(train, grams)
    swaps: Counter[str] = Counter()
    swap_details: list[dict[str, Any]] = []
    unresolved: Counter[str] = Counter()

    while True:
        swapped = False
        offenders: list[tuple[str, int, dict[str, Any], float]] = []
        for split_name, records in (("test", test), ("dev", dev)):
            for index, record in enumerate(records):
                ratio = overlap_with_train(grams[record["id"]], counts)
                if ratio > OVERLAP_THRESHOLD:
                    offenders.append((split_name, index, record, ratio))
        if not offenders:
            break

        for split_name, index, offender, old_ratio in offenders:
            tier = record_tier(offender)
            candidates = [record for record in train if record_tier(record) == tier]
            if split_name == "test":
                current_priority = sum(has_reserved_trap(record) for record in test)
                offender_priority = int(has_reserved_trap(offender))
                candidates = [
                    record
                    for record in candidates
                    if current_priority
                    - offender_priority
                    + int(has_reserved_trap(record))
                    <= RESERVED_TEST_TRAP_LIMIT
                ]
                prefer_priority = current_priority - offender_priority < RESERVED_TEST_TRAP_LIMIT
                candidates.sort(
                    key=lambda record: (
                        prefer_priority != has_reserved_trap(record),
                        record["id"],
                    )
                )
            else:
                candidates.sort(key=lambda record: record["id"])

            replacement = next(
                (
                    record
                    for record in candidates
                    if replacement_overlap(
                        grams[record["id"]], grams[offender["id"]], counts
                    )
                    <= OVERLAP_THRESHOLD
                ),
                None,
            )
            if replacement is None:
                unresolved[f"{split_name}/{tier}"] += 1
                continue

            update_train_counts(
                counts,
                remove=grams[replacement["id"]],
                add=grams[offender["id"]],
            )
            train.remove(replacement)
            train.append(offender)
            target = test if split_name == "test" else dev
            target[index] = replacement
            swaps[f"{split_name}/{tier}"] += 1
            swap_details.append(
                {
                    "split": split_name,
                    "tier": tier,
                    "removed_id": offender["id"],
                    "replacement_id": replacement["id"],
                    "removed_overlap": round(old_ratio, 6),
                }
            )
            swapped = True
            break

        if not swapped:
            break

    final_unresolved: Counter[str] = Counter()
    for split_name, records in (("test", test), ("dev", dev)):
        for record in records:
            if overlap_with_train(grams[record["id"]], counts) > OVERLAP_THRESHOLD:
                final_unresolved[f"{split_name}/{record_tier(record)}"] += 1
    if final_unresolved:
        raise RuntimeError(
            "同档剩余池无法回填全部 5-gram 高重叠样本: "
            + json.dumps(dict(final_unresolved), ensure_ascii=False)
        )
    return swaps, swap_details, counts


def overlap_histogram(
    records: Iterable[dict[str, Any]], train_counts: Counter[str]
) -> Counter[str]:
    histogram: Counter[str] = Counter()
    for record in records:
        ratio = overlap_with_train(char_5grams(record["text"]), train_counts)
        bucket = min(int(ratio * 10), 9)
        histogram[f"{bucket / 10:.1f}-{(bucket + 1) / 10:.1f}"] += 1
    return histogram


def counter_dict(counter: Counter[str], order: Iterable[str] | None = None) -> dict[str, int]:
    if order is None:
        return {key: counter[key] for key in sorted(counter)}
    return {key: counter[key] for key in order}


def split_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    tiers: Counter[str] = Counter(record_tier(record) for record in records)
    langs: Counter[str] = Counter(record["lang"] for record in records)
    kinds: Counter[str] = Counter(record["kind"] for record in records)
    labels: Counter[str] = Counter(
        span["label"] for record in records for span in record["spans"]
    )
    return {
        "count": len(records),
        "entity_count": sum(labels.values()),
        "distribution": {
            "tier": counter_dict(tiers, TIER_ORDER),
            "lang": counter_dict(langs, LANG_ORDER),
            "kind": counter_dict(kinds, KIND_ORDER),
            "label": counter_dict(labels, LABEL_ORDER),
        },
    }


def trap_sample_counts(records: Iterable[dict[str, Any]]) -> Counter[str]:
    return Counter(
        {
            surface: sum(has_reserved_trap(record, surface) for record in records)
            for surface in RESERVED_TEST_TRAPS
        }
    )


def markdown_counter(counter: Counter[str], order: Iterable[str] | None = None) -> str:
    keys = list(order) if order is not None else sorted(counter)
    return "，".join(f"{key} {counter[key]}" for key in keys) or "无"


def build_report(
    *,
    input_path: Path,
    source_count: int,
    changes: Counter[str],
    available: Counter[str],
    shortages: Counter[str],
    test_quota: Counter[str],
    dev_quota: Counter[str],
    priority_count: int,
    splits: dict[str, list[dict[str, Any]]],
    swaps: Counter[str],
    swap_details: list[dict[str, Any]],
    histograms: dict[str, Counter[str]],
) -> str:
    lines = [
        "# 冻结切分报告",
        "",
        "## 输入与实测归类",
        "",
        f"- 输入：`{input_path}`",
        f"- 总条数：{source_count}",
        f"- 输入档位池：{markdown_counter(available, TIER_ORDER)}",
        f"- `meta.tier` 改写：{sum(changes.values())} 条（{markdown_counter(changes)}）",
        "",
        "## 配额",
        "",
        f"- 测试集名义配额：{markdown_counter(TEST_QUOTA, TIER_ORDER)}",
        f"- 测试集实际配额：{markdown_counter(test_quota, TIER_ORDER)}",
        f"- 不足档位：{markdown_counter(shortages)}",
        f"- 测试集优先保留指定 trap 的样本：{priority_count} 条（上限 {RESERVED_TEST_TRAP_LIMIT}）",
        f"- 开发集比例配额：{markdown_counter(dev_quota, TIER_ORDER)}",
        "",
        "## 指定 trap 的 split 分布（按含该表面串的样本条数）",
        "",
        "| trap 串 | train | dev | test |",
        "|---|---:|---:|---:|",
    ]
    trap_counts = {name: trap_sample_counts(records) for name, records in splits.items()}
    for surface in RESERVED_TEST_TRAPS:
        lines.append(
            f"| {surface} | {trap_counts['train'][surface]} | "
            f"{trap_counts['dev'][surface]} | {trap_counts['test'][surface]} |"
        )

    lines.extend(
        [
            "",
            "## 跨 split 5-gram 去重",
            "",
            f"- 阈值：与 train 5-gram 并集的 Jaccard 重叠比例 > {OVERLAP_THRESHOLD}",
            f"- 同档交换回填：{sum(swaps.values())} 条（{markdown_counter(swaps)}）",
            "",
            "| split | 0.0-0.1 | 0.1-0.2 | 0.2-0.3 | 0.3-0.4 | 0.4-0.5 | 0.5-0.6 | 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-1.0 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    bucket_order = tuple(f"{index / 10:.1f}-{(index + 1) / 10:.1f}" for index in range(10))
    for split_name in ("test", "dev"):
        values = " | ".join(str(histograms[split_name][bucket]) for bucket in bucket_order)
        lines.append(f"| {split_name} | {values} |")
    if swap_details:
        lines.extend(["", "### 被剔除并同档回填的条目", ""])
        for item in swap_details:
            lines.append(
                f"- {item['split']}/{item['tier']}：`{item['removed_id']}` → "
                f"`{item['replacement_id']}`（原重叠比例 {item['removed_overlap']:.6f}）"
            )

    lines.extend(["", "## 最终 split 统计", ""])
    for split_name in ("train", "dev", "test"):
        stats = split_statistics(splits[split_name])
        lines.extend(
            [
                f"### {split_name}",
                "",
                f"- 条数：{stats['count']}",
                f"- 实体数：{stats['entity_count']}",
                f"- 档位：{markdown_counter(Counter(stats['distribution']['tier']), TIER_ORDER)}",
                f"- 语种：{markdown_counter(Counter(stats['distribution']['lang']), LANG_ORDER)}",
                f"- kind：{markdown_counter(Counter(stats['distribution']['kind']), KIND_ORDER)}",
                f"- 标签：{markdown_counter(Counter(stats['distribution']['label']), LABEL_ORDER)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    source = read_jsonl(args.input)
    if len(source) < TEST_SIZE + DEV_SIZE:
        raise ValueError(
            f"输入只有 {len(source)} 条，少于 test {TEST_SIZE} + dev {DEV_SIZE}"
        )
    records, changes = normalize_records(source)
    rng = random.Random(SEED)
    pools = group_by_tier(records)
    for tier in TIER_ORDER:
        rng.shuffle(pools[tier])
    available: Counter[str] = Counter(
        {tier: len(pools[tier]) for tier in TIER_ORDER}
    )

    test_quota, shortages = effective_test_quota(available)
    test, remaining, priority_count = select_test(pools, test_quota, rng)
    remaining_counts: Counter[str] = Counter(record_tier(record) for record in remaining)
    dev_quota = proportional_quota(DEV_SIZE, remaining_counts)
    dev, train = select_dev(remaining, dev_quota)

    swaps, swap_details, final_train_grams = deduplicate_heldout(test, dev, train)
    histograms = {
        "test": overlap_histogram(test, final_train_grams),
        "dev": overlap_histogram(dev, final_train_grams),
    }

    splits = {
        "train": sorted(train, key=lambda record: record["id"]),
        "dev": sorted(dev, key=lambda record: record["id"]),
        "test": sorted(test, key=lambda record: record["id"]),
    }
    payloads = {
        split_name: jsonl_payload(split_records)
        for split_name, split_records in splits.items()
    }
    manifest_splits: dict[str, Any] = {}
    for split_name in ("train", "dev", "test"):
        manifest_splits[split_name] = {
            "file": f"{split_name}.jsonl",
            "sha256": normalized_sha256_bytes(payloads[split_name]),
            **split_statistics(splits[split_name]),
        }
    manifest = {
        "seed": SEED,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input": {
            "file": str(args.input),
            "sha256": normalized_sha256_file(args.input),
            "count": len(source),
        },
        "reserved_test_traps": list(RESERVED_TEST_TRAPS),
        "splits": manifest_splits,
    }
    report = build_report(
        input_path=args.input,
        source_count=len(source),
        changes=changes,
        available=available,
        shortages=shortages,
        test_quota=test_quota,
        dev_quota=dev_quota,
        priority_count=priority_count,
        splits=splits,
        swaps=swaps,
        swap_details=swap_details,
        histograms=histograms,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for split_name, payload in payloads.items():
        (args.out_dir / f"{split_name}.jsonl").write_bytes(payload)
    (args.out_dir / "manifest.json").write_bytes(json_payload(manifest))
    (args.out_dir / "split_report.md").write_bytes(report.encode("utf-8"))

    print(f"train: {len(splits['train'])}")
    print(f"dev: {len(splits['dev'])}")
    print(f"test: {len(splits['test'])}")
    print(f"输出目录: {args.out_dir}")


if __name__ == "__main__":
    main()
