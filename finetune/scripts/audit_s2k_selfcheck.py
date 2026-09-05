from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "finetune" / "data" / "interim"
INPUTS = (INTERIM / "s2k.jsonl", INTERIM / "s2kb.jsonl")
OUTPUT = INTERIM / "s2k_selfcheck.md"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_bytes().decode("utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    records = [record for path in INPUTS for record in read_jsonl(path)]
    positive_empty = [
        record
        for record in records
        if record.get("kind") == "positive" and not record.get("spans")
    ]
    negative_nonempty = [
        record
        for record in records
        if record.get("kind") == "true_negative" and record.get("spans")
    ]
    trap_surfaces: Counter[str] = Counter(
        trap["text"]
        for record in records
        for trap in record.get("trap_spans", [])
    )
    trap_total = trap_surfaces.total()
    top10_total = sum(count for _, count in trap_surfaces.most_common(10))
    top10_ratio = top10_total / trap_total if trap_total else 0.0

    lines = [
        "# s2k + s2kb 零成本全量自检",
        "",
        f"数据总数：{len(records)}。",
        "",
        "## positive 但 spans 为空",
        "",
        f"共 **{len(positive_empty)}** 条。",
        "",
    ]
    if positive_empty:
        lines.extend(["| id | 正文前 60 字 |", "|---|---|"])
        for record in positive_empty:
            preview = record["text"][:60].replace("|", "\\|").replace("\n", "↵")
            lines.append(f"| {record['id']} | {preview} |")
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## true_negative 但 spans 非空",
            "",
            f"共 **{len(negative_nonempty)}** 条。",
            "",
        ]
    )
    if negative_nonempty:
        lines.extend(f"- `{record['id']}`" for record in negative_nonempty)
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## trap 表面串分布",
            "",
            f"trap 出现总数：**{trap_total}**。",
            f"Top 10 合计：**{top10_total}**，占 **{top10_ratio:.2%}**。",
            "",
            "| 排名 | trap 表面串 | 出现次数 |",
            "|---:|---|---:|",
        ]
    )
    for rank, (surface, count) in enumerate(trap_surfaces.most_common(), start=1):
        escaped_surface = surface.replace("|", "\\|")
        lines.append(f"| {rank} | {escaped_surface} | {count} |")

    OUTPUT.write_bytes(("\n".join(lines).rstrip() + "\n").encode("utf-8"))
    print(
        json.dumps(
            {
                "records": len(records),
                "positive_empty": len(positive_empty),
                "true_negative_nonempty": len(negative_nonempty),
                "trap_total": trap_total,
                "trap_unique": len(trap_surfaces),
                "top10_total": top10_total,
                "top10_ratio": round(top10_ratio, 6),
                "output": str(OUTPUT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
