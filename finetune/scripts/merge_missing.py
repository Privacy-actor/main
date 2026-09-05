from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "finetune" / "data" / "interim"
GOLD_PATHS = (INTERIM / "s2k.jsonl", INTERIM / "s2kb.jsonl")
MISSING_PATH = INTERIM / "missing_spans.jsonl"
MERGED_PATH = INTERIM / "merged.jsonl"
REJECTED_PATH = INTERIM / "merge_rejected.jsonl"
REPORT_PATH = INTERIM / "merge_report.md"
LABELS = {
    "PERSON",
    "ORG",
    "LOCATION",
    "ADDRESS",
    "PHONE",
    "EMAIL",
    "ID_CARD",
    "BANK_CARD",
    "PASSPORT",
}
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
SHOP_TRAP = "店名中的姓氏不是 PERSON"
ZH_ORG_SUFFIXES = (
    "大学",
    "学院",
    "医院",
    "研究院",
    "研究所",
    "设计院",
    "集团",
    "有限公司",
    "协会",
    "合作社",
    "实验室",
    "诊所",
    "事务所",
    "基金会",
    "商会",
    "银行",
)
EN_ORG_SUFFIXES = (
    "University",
    "College",
    "Hospital",
    "Corporation",
    "Limited",
    "Ltd",
    "Inc",
    "LLC",
    "Institute",
    "Laboratory",
    "Foundation",
    "Partners",
    "Group",
    "Holdings",
    "Association",
)
SAMPLE_SEED = 1300
SAMPLE_SIZE = 200

sys.path.insert(0, str(ROOT / "finetune" / "scripts"))

from primitives import find_all


def interim_input_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() and path.parent == Path("."):
        return INTERIM / path
    return path


def gold_paths(value: str) -> tuple[Path, ...]:
    paths = tuple(
        interim_input_path(part.strip()) for part in value.split(",") if part.strip()
    )
    if not paths:
        raise argparse.ArgumentTypeError("--gold 至少需要一个文件路径")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合并漏标扫描结果并校验 gold")
    parser.add_argument(
        "--gold",
        type=gold_paths,
        default=GOLD_PATHS,
        metavar="文件1,文件2",
        help="逗号分隔的 gold JSONL；仅文件名按 interim 目录解析",
    )
    parser.add_argument(
        "--missing",
        type=interim_input_path,
        default=MISSING_PATH,
        help="漏标扫描结果 JSONL；仅文件名按 interim 目录解析",
    )
    parser.add_argument("--out-merged", type=Path, default=MERGED_PATH)
    parser.add_argument("--out-rejected", type=Path, default=REJECTED_PATH)
    parser.add_argument("--out-report", type=Path, default=REPORT_PATH)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_bytes().decode("utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    payload = b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for record in records
    )
    path.write_bytes(payload)


def normalized_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def overlaps(start: int, end: int, span: dict[str, Any]) -> bool:
    return start < span["end"] and span["start"] < end


def reject(
    rejected: list[dict[str, Any]],
    reasons: Counter[str],
    *,
    record_id: str,
    missing_index: int,
    item: dict[str, Any],
    reason: str,
    start: int | None = None,
    end: int | None = None,
) -> None:
    reasons[reason] += 1
    rejected.append(
        {
            "id": record_id,
            "missing_index": missing_index,
            "missing": item,
            "start": start,
            "end": end,
            "reason": reason,
        }
    )


def is_org_suffix(surface: str) -> bool:
    if surface.endswith(ZH_ORG_SUFFIXES):
        return True
    folded = surface.casefold()
    return any(folded.endswith(suffix.casefold()) for suffix in EN_ORG_SUFFIXES)


def count_labels(records: list[dict[str, Any]]) -> Counter[str]:
    return Counter(span["label"] for record in records for span in record["spans"])


def main() -> None:
    args = parse_args()
    hashes_before = {str(path): normalized_sha256(path) for path in args.gold}
    original = [record for path in args.gold for record in read_jsonl(path)]
    missing_rows = read_jsonl(args.missing)
    merged = copy.deepcopy(original)
    by_id = {record["id"]: record for record in merged}
    original_by_id = {record["id"]: record for record in original}
    if len(by_id) != len(original):
        raise ValueError("gold 中存在重复 id")

    missing_id_counts: Counter[str] = Counter(row.get("id") for row in missing_rows)
    duplicate_missing_ids = sorted(
        record_id for record_id, count in missing_id_counts.items() if count > 1
    )
    unknown_missing_ids = sorted(set(missing_id_counts) - set(by_id))

    missing_total = sum(len(row.get("missing", [])) for row in missing_rows)
    rejected: list[dict[str, Any]] = []
    rejection_reasons: Counter[str] = Counter()
    candidate_order = 0
    candidates_by_id: dict[str, list[dict[str, Any]]] = {}
    item_keys: set[tuple[str, int]] = set()
    accepted_item_keys: set[tuple[str, int]] = set()

    for scan_row in missing_rows:
        record_id = scan_row.get("id")
        if record_id not in by_id:
            for missing_index, item in enumerate(scan_row.get("missing", [])):
                item_keys.add((str(record_id), missing_index))
                reject(
                    rejected,
                    rejection_reasons,
                    record_id=str(record_id),
                    missing_index=missing_index,
                    item=item,
                    reason="扫描结果 id 不在 gold 中",
                )
            continue
        record = by_id[record_id]
        original_spans = original_by_id[record_id]["spans"]
        text = record["text"]
        traps = record.get("trap_spans", [])
        for missing_index, item in enumerate(scan_row.get("missing", [])):
            key = (record_id, missing_index)
            item_keys.add(key)
            surface = item.get("text")
            label = item.get("label")
            if not isinstance(surface, str) or len(surface) < 2:
                reject(
                    rejected,
                    rejection_reasons,
                    record_id=record_id,
                    missing_index=missing_index,
                    item=item,
                    reason="守卫1：表面串长度 < 2",
                )
                continue
            if label not in LABELS:
                reject(
                    rejected,
                    rejection_reasons,
                    record_id=record_id,
                    missing_index=missing_index,
                    item=item,
                    reason="守卫2：label 不在九类内",
                )
                continue
            positions = find_all(text, surface)
            if not positions:
                reject(
                    rejected,
                    rejection_reasons,
                    record_id=record_id,
                    missing_index=missing_index,
                    item=item,
                    reason="定位失败：find_all 未找到表面串",
                )
                continue
            forbidden_trap = any(
                trap.get("text") == surface and trap.get("trap") != SHOP_TRAP
                for trap in traps
            )
            for start, end in positions:
                if any(overlaps(start, end, span) for span in original_spans):
                    reject(
                        rejected,
                        rejection_reasons,
                        record_id=record_id,
                        missing_index=missing_index,
                        item=item,
                        reason="守卫3：与已有 span 重叠",
                        start=start,
                        end=end,
                    )
                    continue
                if forbidden_trap:
                    reject(
                        rejected,
                        rejection_reasons,
                        record_id=record_id,
                        missing_index=missing_index,
                        item=item,
                        reason="守卫4：表面串属于非店名类 trap",
                        start=start,
                        end=end,
                    )
                    continue
                candidate_order += 1
                candidates_by_id.setdefault(record_id, []).append(
                    {
                        "start": start,
                        "end": end,
                        "label": label,
                        "surface": surface,
                        "missing_index": missing_index,
                        "item": item,
                        "order": candidate_order,
                    }
                )

    added_span_count = 0
    for record_id, candidates in candidates_by_id.items():
        kept: list[dict[str, Any]] = []
        for candidate in sorted(
            candidates,
            key=lambda value: (
                -(value["end"] - value["start"]),
                value["order"],
                value["start"],
                value["end"],
                value["label"],
            ),
        ):
            if any(overlaps(candidate["start"], candidate["end"], prior) for prior in kept):
                reject(
                    rejected,
                    rejection_reasons,
                    record_id=record_id,
                    missing_index=candidate["missing_index"],
                    item=candidate["item"],
                    reason="新增 span 相互重叠：较短或等长后出现",
                    start=candidate["start"],
                    end=candidate["end"],
                )
                continue
            kept.append(candidate)
            accepted_item_keys.add((record_id, candidate["missing_index"]))
        record = by_id[record_id]
        record["spans"].extend(
            {
                "start": candidate["start"],
                "end": candidate["end"],
                "label": candidate["label"],
            }
            for candidate in kept
        )
        record["spans"].sort(key=lambda span: (span["start"], span["end"], span["label"]))
        added_span_count += len(kept)

    location_to_org: list[dict[str, Any]] = []
    for record in merged:
        for original_span in original_by_id[record["id"]]["spans"]:
            if original_span["label"] != "LOCATION":
                continue
            surface = record["text"][
                original_span["start"] : original_span["end"]
            ]
            if is_org_suffix(surface):
                target = next(
                    span
                    for span in record["spans"]
                    if span["start"] == original_span["start"]
                    and span["end"] == original_span["end"]
                    and span["label"] == "LOCATION"
                )
                target["label"] = "ORG"
                location_to_org.append(
                    {
                        "id": record["id"],
                        "start": original_span["start"],
                        "end": original_span["end"],
                        "surface": surface,
                    }
                )
        record["spans"].sort(key=lambda span: (span["start"], span["end"], span["label"]))

    original_positive_empty = [
        record["id"]
        for record in original
        if record["kind"] == "positive" and not record["spans"]
    ]
    positive_kept: list[str] = []
    converted_to_negative: list[str] = []
    for record_id in original_positive_empty:
        record = by_id[record_id]
        if record["spans"]:
            positive_kept.append(record_id)
        else:
            record["kind"] = "true_negative"
            converted_to_negative.append(record_id)

    validation_failures: dict[str, list[dict[str, Any]]] = {
        "越界或空区间": [],
        "span 重叠": [],
        "非法 label": [],
    }
    for record in merged:
        text = record["text"]
        spans = record["spans"]
        for span in spans:
            if not (
                isinstance(span.get("start"), int)
                and isinstance(span.get("end"), int)
                and 0 <= span["start"] < span["end"] <= len(text)
            ):
                validation_failures["越界或空区间"].append(
                    {"id": record["id"], "span": span, "text_length": len(text)}
                )
            if span.get("label") not in LABELS:
                validation_failures["非法 label"].append(
                    {"id": record["id"], "span": span}
                )
        ordered = sorted(spans, key=lambda span: (span["start"], span["end"]))
        for left, right in zip(ordered, ordered[1:]):
            if right["start"] < left["end"]:
                validation_failures["span 重叠"].append(
                    {"id": record["id"], "left": left, "right": right}
                )

    counts_before = count_labels(original)
    counts_after = count_labels(merged)
    total_entities = counts_after.total()
    total_characters = sum(len(record["text"]) for record in merged)
    density = total_entities * 1000 / total_characters
    item_rejected = item_keys - accepted_item_keys
    item_partial = {
        key
        for key in accepted_item_keys
        if any(
            rejection["id"] == key[0]
            and rejection["missing_index"] == key[1]
            for rejection in rejected
        )
    }

    hashes_after = {str(path): normalized_sha256(path) for path in args.gold}
    if hashes_before != hashes_after:
        raise RuntimeError("原始 gold 输入哈希发生变化，拒绝写输出")

    write_jsonl(args.out_merged, merged)
    write_jsonl(args.out_rejected, rejected)

    report = [
        "# missing spans 合并报告",
        "",
        "## 输入与不可变性",
        "",
        f"- gold：{len(original)} 条。",
        f"- missing 扫描结果：{len(missing_rows)} 条。",
        f"- missing 中重复 ID：{len(duplicate_missing_ids)}。",
        f"- missing 中未知 ID：{len(unknown_missing_ids)}。",
        "- 原始 gold 合并前后归一化 SHA-256 一致：PASS。",
        "",
        "## 1. missing 合并",
        "",
        f"- missing 项总数：**{missing_total}**。",
        f"- 至少新增一个 span 的 missing 项：**{len(accepted_item_keys)}**。",
        f"- 完全未新增 span 的 missing 项：**{len(item_rejected)}**。",
        f"- 部分位置通过、部分位置被拒的 missing 项：**{len(item_partial)}**。",
        f"- find-all 后实际新增 span：**{added_span_count}**。",
        f"- 拒绝记录：**{len(rejected)}**。",
        "",
        "| 拒绝理由 | 数量 |",
        "|---|---:|",
    ]
    rejection_order = (
        "守卫1：表面串长度 < 2",
        "守卫2：label 不在九类内",
        "守卫3：与已有 span 重叠",
        "守卫4：表面串属于非店名类 trap",
        "定位失败：find_all 未找到表面串",
        "新增 span 相互重叠：较短或等长后出现",
        "扫描结果 id 不在 gold 中",
    )
    for reason in rejection_order:
        report.append(f"| {reason} | {rejection_reasons[reason]} |")

    report.extend(
        [
            "",
            "说明：守卫3只检查原有 gold span；与 trap_spans 重叠本身不算守卫3。守卫4按同条记录中 trap 的表面串精确匹配。新增候选按跨度长度降序保留；等长冲突按 missing 输入顺序保留先出现者。",
            "",
            "## 2. 九类标签合并前后",
            "",
            "| 标签 | 合并前 | 合并后 | 变化 |",
            "|---|---:|---:|---:|",
        ]
    )
    for label in LABEL_ORDER:
        report.append(
            f"| {label} | {counts_before[label]} | {counts_after[label]} | "
            f"{counts_after[label] - counts_before[label]:+d} |"
        )

    report.extend(
        [
            "",
            "## 3. 原有 LOCATION → ORG",
            "",
            f"共 **{len(location_to_org)}** 条改判。",
            "",
            "| id | start:end | 表面串 |",
            "|---|---:|---|",
        ]
    )
    for item in location_to_org:
        surface = item["surface"].replace("|", "\\|")
        report.append(
            f"| {item['id']} | {item['start']}:{item['end']} | {surface} |"
        )

    report.extend(
        [
            "",
            "## 4. 54 条 positive 空 spans 分流",
            "",
            f"- 原始 positive 空 spans：{len(original_positive_empty)}。",
            f"- 合并后非空、保持 positive：{len(positive_kept)}。",
            f"- 合并后仍为空、改为 true_negative：{len(converted_to_negative)}。",
            "",
            "### 保持 positive",
            "",
        ]
    )
    report.extend(f"- `{record_id}`" for record_id in positive_kept)
    if not positive_kept:
        report.append("无。")
    report.extend(["", "### 改为 true_negative", ""])
    report.extend(f"- `{record_id}`" for record_id in converted_to_negative)
    if not converted_to_negative:
        report.append("无。")

    report.extend(["", "## 5. 全量结构校验", ""])
    for category in ("越界或空区间", "span 重叠", "非法 label"):
        failures = validation_failures[category]
        report.append(
            f"- {category}：{'PASS' if not failures else 'FAIL'}，{len(failures)} 条。"
        )
        if failures:
            report.extend(
                f"  - `{failure['id']}`：`{json.dumps(failure, ensure_ascii=False)}`"
                for failure in failures
            )

    sample_records = random.Random(SAMPLE_SEED).sample(
        merged, min(SAMPLE_SIZE, len(merged))
    )
    report.extend(
        [
            "",
            f"## 6. {len(sample_records)} 条 span 对照人工抽查",
            "",
            f"抽样 seed：{SAMPLE_SEED}。",
            "",
        ]
    )
    for index, record in enumerate(sample_records, start=1):
        report.append(f"### {index}. {record['id']}")
        report.append("")
        if not record["spans"]:
            report.append("无 span。")
        else:
            report.extend(
                f"- `{span['start']}:{span['end']}` {span['label']} → "
                f"`{record['text'][span['start']:span['end']].replace('`', 'ˋ')}`"
                for span in record["spans"]
            )
        report.append("")

    report.extend(
        [
            "## 7. 合并后总体",
            "",
            f"- 总条数：**{len(merged)}**。",
            f"- 总实体数：**{total_entities}**。",
            f"- 总字符数：**{total_characters}**。",
            f"- 整体实体密度：**{density:.4f} 个/千字符**。",
            "",
        ]
    )
    args.out_report.write_bytes(("\n".join(report).rstrip() + "\n").encode("utf-8"))

    print(
        json.dumps(
            {
                "gold": len(original),
                "missing_rows": len(missing_rows),
                "missing_items": missing_total,
                "accepted_items": len(accepted_item_keys),
                "rejected_items": len(item_rejected),
                "added_spans": added_span_count,
                "rejection_records": len(rejected),
                "rejection_reasons": dict(rejection_reasons),
                "location_to_org": len(location_to_org),
                "positive_kept": len(positive_kept),
                "converted_to_true_negative": len(converted_to_negative),
                "validation_failures": {
                    category: len(failures)
                    for category, failures in validation_failures.items()
                },
                "merged_entities": total_entities,
                "density_per_1000": round(density, 4),
                "outputs": [
                    str(args.out_merged),
                    str(args.out_rejected),
                    str(args.out_report),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
