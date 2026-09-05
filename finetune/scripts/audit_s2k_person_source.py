from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "finetune" / "data" / "interim"
INPUTS = (INTERIM / "s2k.jsonl", INTERIM / "s2kb.jsonl")
OUTPUT = INTERIM / "s2k_person_source.md"
GUIDES = ("我叫", "姓名", "联系人", "联系方式", "客户", "申请人")

sys.path.insert(0, str(ROOT / "backend"))

from app.recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
from app.schemas import Strategy


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_bytes().decode("utf-8").splitlines()
        if line.strip()
    ]


def source_bucket(sources: list[str]) -> str:
    values = set(sources)
    if "RULE" in values and "NER-LITE" in values:
        return "规则层 + lite NER"
    if "RULE" in values:
        return "仅规则层"
    if "NER-LITE" in values:
        return "仅 lite NER"
    return "其他"


def main() -> None:
    records = [record for path in INPUTS for record in read_jsonl(path)]
    sources: Counter[str] = Counter()
    guide_totals: Counter[str] = Counter()
    guide_hits: Counter[str] = Counter()
    gold_person_total = 0

    for record in records:
        text = record["text"]
        rule_spans, _ = detect_rule_spans(text, Strategy.MASK)
        lite_spans, _ = detect_lite_ner_spans(
            text,
            Strategy.MASK,
            language=record["lang"],
        )
        merged = merge_spans(text, rule_spans + lite_spans)
        gold_person = {
            (span["start"], span["end"], span["label"])
            for span in record["spans"]
            if span["label"] == "PERSON"
        }
        gold_person_total += len(gold_person)
        for span in merged:
            key = (span.start, span.end, span.entity_type.value)
            if key in gold_person:
                sources[source_bucket(span.sources)] += 1

        for span in record["spans"]:
            if span["label"] != "PERSON":
                continue
            language = record["lang"]
            prefix = text[max(0, span["start"] - 8) : span["start"]]
            guide_totals[language] += 1
            if any(guide in prefix for guide in GUIDES):
                guide_hits[language] += 1

    strict_tp = sources.total()
    lines = [
        "# s2k + s2kb PERSON 命中来源",
        "",
        "## 实际调用",
        "",
        "```python",
        "from app.recognizers import detect_rule_spans, detect_lite_ner_spans, merge_spans",
        "from app.schemas import Strategy",
        "",
        "detect_rule_spans(text, Strategy.MASK)",
        'detect_lite_ner_spans(text, Strategy.MASK, language=record["lang"])',
        "merge_spans(text, rule_spans + lite_spans)",
        "```",
        "",
        "严格匹配键为 `(start, end, label)`。",
        "",
        "## 严格 TP 的 sources",
        "",
        f"PERSON 严格 TP：**{strict_tp}**。",
        "",
        "| 来源 | 数量 | 占严格 TP |",
        "|---|---:|---:|",
    ]
    for bucket in ("仅规则层", "仅 lite NER", "规则层 + lite NER", "其他"):
        count = sources[bucket]
        ratio = count / strict_tp if strict_tp else 0.0
        lines.append(f"| {bucket} | {count} | {ratio:.2%} |")

    lines.extend(
        [
            "",
            "## PERSON 前方 8 字符引导词",
            "",
            f"全库 gold PERSON：**{gold_person_total}**。",
            "",
            "引导词：`我叫 / 姓名 / 联系人 / 联系方式 / 客户 / 申请人`。",
            "",
            "| 语种 | gold PERSON | 命中引导词 | 比例 |",
            "|---|---:|---:|---:|",
        ]
    )
    for language in ("zh", "en", "mixed"):
        total = guide_totals[language]
        hit = guide_hits[language]
        lines.append(
            f"| {language} | {total} | {hit} | "
            f"{(hit / total if total else 0.0):.2%} |"
        )

    OUTPUT.write_bytes(("\n".join(lines).rstrip() + "\n").encode("utf-8"))
    print(
        json.dumps(
            {
                "records": len(records),
                "gold_person": gold_person_total,
                "strict_tp": strict_tp,
                "sources": dict(sources),
                "guide_totals": dict(guide_totals),
                "guide_hits": dict(guide_hits),
                "output": str(OUTPUT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
