from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, wait
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FINETUNE = ROOT / "finetune"
INTERIM = FINETUNE / "data" / "interim"
INPUTS = (INTERIM / "s2k.jsonl", INTERIM / "s2kb.jsonl")
DEFAULT_OUTPUT = INTERIM / "missing_spans.jsonl"
MODEL = "qwen3.7-plus"
CONCURRENCY = 8
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

sys.path.insert(0, str(FINETUNE / "scripts"))

from generate import (
    FastInterruptExecutor,
    append_jsonl,
    read_jsonl,
    request_batch,
)


# finetune/docs/规格.md「标签体系」原文，不改写。
LABEL_POLICY = r'''## 二、标签体系

九个标签,与后端 `EntityType` 一致(去掉 `CUSTOM`):

```
PERSON  ORG  LOCATION  ADDRESS  PHONE  EMAIL  ID_CARD  BANK_CARD  PASSPORT
```

**排除 `CUSTOM` 的理由:** 它由运行时用户规则填充,分数恒为 1.0 直接采纳,永远不会成为候选。生成它等于教模型幻觉。

**边界口径(不得由代理自行解释):**

| 判据 | 规则 |
|---|---|
| ADDRESS vs LOCATION | 能定位到门牌的完整地址(含区县 + 路街号)是 **ADDRESS**;地名、行政区、地标是 **LOCATION**。与后端 `ADDRESS_PATTERN` 对齐 |
| 英文样本的 ID_CARD | **不使用。** 后端正则是 `\d{17}[\dXx]`,中国身份证专用。英文文本里的等价物匹配不上,塞进去等于教模型输出下游接不住的东西 |
| ORG 的范围 | 指**可独立识别的机构实体**：公司、学校、医院、政府机关、社会组织。**不包括机构内部的部门名**（产品部、销售部、人事部、财务部、技术部等）——它们不识别任何人或任何机构，且后端实测完全不产生候选。这类词应作为 hard_negative 的陷阱，不得标注为 ORG。 |

**ORG 的范围（补充裁定）**
一律标注，不做上下文判断。是否打码由下游 `risk_level` 决定。

- 政府机关、国际组织（国家统计局、世界卫生组织）→ **标 ORG**
- 含姓氏的店名（王记牛肉面、陈氏中医诊所）→ **标 ORG，但其中的姓氏不标 PERSON**
- 公司内部部门名（产品部、技术部）→ **不标**（维持原判）

**命名设施**
机场、车站、产业园、大厦、科技城等命名设施本身 → **LOCATION**；
若跨度延伸到楼层或门牌（环球金融中心68层）→ **整体 ADDRESS**。

**称谓指代**
小李、张哥、李总、老周、小刘等称谓不标 PERSON——本身不识别任何人，
且开此口子后「那位同事」「他」都需判定，属 v1 明确排除的准标识符范畴。
**全名必须标**（王建国、李芳、刘静、Steven Chow、Jennifer）。

单批注入 **4 个**标签(9–15 个标签的推荐值)。注入过多会让单条文本 PII 密度失真。'''


REQUIRED_RULES = '''
- 省/市/区/县这一级地名独立出现时标 LOCATION；只有跨度延伸到路/街/巷/号/栋/单元/室才标 ADDRESS
- trap 列表里的串是故意不标的，不得报为漏标
- 称谓指代（小李/张哥/李总/王女士）不是漏标
- 公众人物、历史人物不是漏标
- 公司内部部门名不是漏标
- 同一实体重复出现只标了第一次，不是漏标
'''.strip()


def parse_input_paths(value: str) -> tuple[Path, ...]:
    paths = tuple(Path(part.strip()) for part in value.split(",") if part.strip())
    if not paths:
        raise argparse.ArgumentTypeError("--input 至少需要一个文件路径")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="扫描 s2k/s2kb gold 的漏标实体")
    parser.add_argument(
        "--input",
        type=parse_input_paths,
        default=INPUTS,
        metavar="文件1,文件2",
        help="逗号分隔的输入 JSONL 文件；默认读取 s2k.jsonl 与 s2kb.jsonl",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须为正整数")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.batch_id):
        parser.error("--batch-id 只能包含 ASCII 字母、数字、下划线与连字符")
    return args


def gold_entities(record: dict[str, Any]) -> list[dict[str, Any]]:
    text = record["text"]
    return [
        {
            "text": text[span["start"] : span["end"]],
            "label": span["label"],
            "start": span["start"],
            "end": span["end"],
        }
        for span in record["spans"]
    ]


def build_prompt(record: dict[str, Any]) -> str:
    payload = {
        "正文": record["text"],
        "已标注实体列表": gold_entities(record),
        "trap列表": record.get("trap_spans", []),
    }
    return f'''你只做一件事：找出正文里应标注、但不在“已标注实体列表”里的实体。
不要评价已有标注，不要提出边界修改，不要改写正文。

以下是标签口径原文：
{LABEL_POLICY}

本次扫描的额外硬约束：
{REQUIRED_RULES}

输入：
{json.dumps(payload, ensure_ascii=False)}

只返回 JSON，不要 Markdown，不要解释。格式：
{{"missing":[{{"text":"原文中的完整表面串","label":"九类标签之一","reason":"为什么属于漏标"}}]}}
没有漏标时返回：{{"missing":[]}}'''


def parse_missing(raw: str, record: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"missing"}:
        raise ValueError("响应必须是只含 missing 字段的 JSON 对象")
    missing = value["missing"]
    if not isinstance(missing, list):
        raise ValueError("missing 必须是数组")
    normalized = []
    for item in missing:
        if not isinstance(item, dict) or set(item) != {"text", "label", "reason"}:
            raise ValueError("missing 条目必须只含 text/label/reason")
        if not all(isinstance(item[key], str) and item[key] for key in item):
            raise ValueError("missing 的 text/label/reason 必须是非空字符串")
        if item["label"] not in LABELS:
            raise ValueError(f"未知标签: {item['label']}")
        if item["text"] not in record["text"]:
            raise ValueError(f"漏标表面串不在正文中: {item['text']!r}")
        normalized.append(
            {
                "text": item["text"],
                "label": item["label"],
                "reason": item["reason"],
            }
        )
    return {"id": record["id"], "missing": normalized}


def load_inputs(paths: tuple[Path, ...], limit: int | None) -> list[dict[str, Any]]:
    records = [record for path in paths for record in read_jsonl(path)]
    return records if limit is None else records[:limit]


def print_summary(records: list[dict[str, Any]]) -> None:
    labels: Counter[str] = Counter(
        item["label"] for record in records for item in record["missing"]
    )
    affected = sum(bool(record["missing"]) for record in records)
    print(
        json.dumps(
            {
                "总条数": len(records),
                "有漏标条目数": affected,
                "有漏标条目占比": round(affected / len(records), 6)
                if records
                else 0.0,
                "按标签漏标实体数": dict(sorted(labels.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    args = parse_args()
    records = load_inputs(args.input, args.limit)
    existing = read_jsonl(args.out)
    existing_by_id = {record["id"]: record for record in existing}
    selected_ids = {record["id"] for record in records}
    pending = [record for record in records if record["id"] not in existing_by_id]
    stop_path = INTERIM / f"{args.batch_id}.stop"
    stop_event = threading.Event()
    futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
    next_index = 0

    if stop_path.exists():
        stop_path.unlink(missing_ok=True)
        print(
            f"检测到停止文件，已保存 {len(existing_by_id)} 条，"
            "重跑同一条命令即可续跑",
            flush=True,
        )
        return

    print(
        "如需中途停止，在另一个窗口执行："
        f"New-Item finetune\\data\\interim\\{args.batch_id}.stop",
        flush=True,
    )

    def submit_one(executor: FastInterruptExecutor) -> bool:
        nonlocal next_index
        if stop_event.is_set() or stop_path.exists() or next_index >= len(pending):
            return False
        record = pending[next_index]
        next_index += 1
        future = executor.submit(
            request_batch,
            model=MODEL,
            prompt=build_prompt(record),
            dry_run=False,
            requested_tier="中",
            lang=record["lang"],
            sampled={},
            kind="positive",
            sample_count=1,
            seed=next_index,
            trap_sets=[[]],
            stop_event=stop_event,
        )
        futures[future] = record
        return True

    try:
        with FastInterruptExecutor(max_workers=CONCURRENCY) as executor:
            while len(futures) < CONCURRENCY and submit_one(executor):
                pass
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    record = futures.pop(future)
                    outcome = future.result()
                    if outcome["error"]:
                        print(f"{record['id']} 失败：{outcome['error']}", flush=True)
                    else:
                        try:
                            result = parse_missing(outcome["raw"], record)
                        except (json.JSONDecodeError, ValueError) as exc:
                            print(f"{record['id']} 响应校验失败：{exc}", flush=True)
                        else:
                            append_jsonl(args.out, [result])
                            existing_by_id[result["id"]] = result
                    if stop_path.exists():
                        stop_event.set()
                        for active in futures:
                            active.cancel()
                        stop_path.unlink(missing_ok=True)
                        print(
                            f"检测到停止文件，已保存 {len(existing_by_id)} 条，"
                            "重跑同一条命令即可续跑",
                            flush=True,
                        )
                        raise KeyboardInterrupt
                    submit_one(executor)
    except KeyboardInterrupt:
        stop_event.set()
        for future in futures:
            future.cancel()
        if stop_path.exists():
            stop_path.unlink(missing_ok=True)
        print(
            f"已保存 {len(existing_by_id)} 条，重跑同一条命令即可续跑",
            flush=True,
        )
        return

    completed = [existing_by_id[record_id] for record_id in selected_ids if record_id in existing_by_id]
    print_summary(completed)


if __name__ == "__main__":
    main()
