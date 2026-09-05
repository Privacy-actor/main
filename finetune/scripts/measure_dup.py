from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
FINAL_DIR = ROOT / "finetune" / "data" / "final"
OUTPUT_PATH = ROOT / "finetune" / "data" / "interim" / "dup_report.md"
NGRAM_SIZE = 5
HISTOGRAM_WIDTH = 0.05
HIGH_THRESHOLD = 0.5


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


def char_ngrams(text: str) -> frozenset[str]:
    if len(text) < NGRAM_SIZE:
        return frozenset({text}) if text else frozenset()
    return frozenset(
        text[index : index + NGRAM_SIZE]
        for index in range(len(text) - NGRAM_SIZE + 1)
    )


def build_inverted_index(
    train_grams: list[frozenset[str]],
) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for train_index, grams in enumerate(train_grams):
        for gram in grams:
            index.setdefault(gram, []).append(train_index)
    return index


def max_jaccard(
    held_grams: frozenset[str],
    train: list[dict[str, Any]],
    train_grams: list[frozenset[str]],
    index: dict[str, list[int]],
) -> tuple[float, int]:
    intersections: Counter[int] = Counter()
    for gram in held_grams:
        intersections.update(index.get(gram, ()))

    best_score = 0.0
    best_index = 0
    for train_index, intersection in intersections.items():
        union = len(held_grams) + len(train_grams[train_index]) - intersection
        score = intersection / union if union else 1.0
        if score > best_score or (
            score == best_score
            and train[train_index]["id"] < train[best_index]["id"]
        ):
            best_score = score
            best_index = train_index
    return best_score, best_index


def histogram_bucket(score: float) -> str:
    bucket = min(int(score / HISTOGRAM_WIDTH), 19)
    lower = bucket * HISTOGRAM_WIDTH
    upper = (bucket + 1) * HISTOGRAM_WIDTH
    return f"{lower:.2f}-{upper:.2f}"


def measure_split(
    records: list[dict[str, Any]],
    train: list[dict[str, Any]],
    train_grams: list[frozenset[str]],
    index: dict[str, list[int]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    results: list[dict[str, Any]] = []
    histogram: Counter[str] = Counter()
    for record in records:
        score, train_index = max_jaccard(
            char_ngrams(record["text"]), train, train_grams, index
        )
        histogram[histogram_bucket(score)] += 1
        results.append(
            {
                "heldout_id": record["id"],
                "train_id": train[train_index]["id"],
                "score": score,
                "heldout_text": record["text"],
                "train_text": train[train_index]["text"],
            }
        )
    return results, histogram


def display_text(text: str) -> str:
    return text[:80].replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def histogram_rows(histogram: Counter[str]) -> Iterable[str]:
    for bucket in range(20):
        lower = bucket * HISTOGRAM_WIDTH
        upper = (bucket + 1) * HISTOGRAM_WIDTH
        label = f"{lower:.2f}-{upper:.2f}"
        yield f"| {label} | {histogram[label]} |"


def report_split(
    split_name: str,
    results: list[dict[str, Any]],
    histogram: Counter[str],
) -> list[str]:
    high = sum(result["score"] > HIGH_THRESHOLD for result in results)
    lines = [
        f"## {split_name}",
        "",
        f"- 条目数：{len(results)}",
        f"- max-Jaccard > {HIGH_THRESHOLD}：{high}（{high / len(results):.4%}）",
        "",
        "### max-Jaccard 分布",
        "",
        "| 区间 | 条目数 |",
        "|---|---:|",
        *histogram_rows(histogram),
        "",
        "### max-Jaccard 最高的 10 条",
        "",
        "| held-out id | train id | Jaccard | held-out 正文前 80 字 | train 正文前 80 字 |",
        "|---|---|---:|---|---|",
    ]
    top = sorted(
        results,
        key=lambda result: (-result["score"], result["heldout_id"], result["train_id"]),
    )[:10]
    for result in top:
        lines.append(
            f"| {result['heldout_id']} | {result['train_id']} | "
            f"{result['score']:.6f} | {display_text(result['heldout_text'])} | "
            f"{display_text(result['train_text'])} |"
        )
    lines.append("")
    return lines


def main() -> None:
    started = time.perf_counter()
    train = read_jsonl(FINAL_DIR / "train.jsonl")
    dev = read_jsonl(FINAL_DIR / "dev.jsonl")
    test = read_jsonl(FINAL_DIR / "test.jsonl")
    train_grams = [char_ngrams(record["text"]) for record in train]
    index = build_inverted_index(train_grams)

    test_results, test_histogram = measure_split(
        test, train, train_grams, index
    )
    dev_results, dev_histogram = measure_split(dev, train, train_grams, index)
    elapsed = time.perf_counter() - started

    report = [
        "# 跨 split 字符 5-gram 近重复测量",
        "",
        f"- train：{len(train)} 条",
        f"- test：{len(test)} 条",
        f"- dev：{len(dev)} 条",
        "- 相似度：逐条与 train 每条计算的精确字符 5-gram Jaccard 最大值",
        f"- 用时：{elapsed:.3f} 秒",
        "",
        *report_split("test", test_results, test_histogram),
        *report_split("dev", dev_results, dev_histogram),
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(("\n".join(report).rstrip() + "\n").encode("utf-8"))

    test_high = sum(result["score"] > HIGH_THRESHOLD for result in test_results)
    dev_high = sum(result["score"] > HIGH_THRESHOLD for result in dev_results)
    print(f"train={len(train)} test={len(test)} dev={len(dev)}")
    print(f"test max-Jaccard > {HIGH_THRESHOLD}: {test_high}")
    print(f"dev max-Jaccard > {HIGH_THRESHOLD}: {dev_high}")
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"output={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
