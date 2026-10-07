"""Run exact-span evaluation against a JSONL gold set.

Each line: {"text": "...", "spans": [{"start": 0, "end": 2, "entity_type": "PERSON"}]}
The backend must already be running. Results are written to the file consumed by the UI.
"""
import argparse
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx


def span_key(span):
    entity_type = span.get("entity_type") or span.get("label")
    if not entity_type:
        raise ValueError("gold/predicted span 缺少 entity_type 或 label")
    return span["start"], span["end"], entity_type


def score(predicted, gold):
    pred = {span_key(span) for span in predicted if span.get("status") != "rejected"}
    truth = {span_key(span) for span in gold}
    return len(pred & truth), len(pred - truth), len(truth - pred), pred, truth


def safe_ratio(a, b):
    return a / b if b else 0.0


def percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--name", help="实验名称；不提供时由后端配置生成")
    parser.add_argument("--no-llm", action="store_true", help="仅评测非 LLM 流水线")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "reports" / "experiment_results" / "latest.json")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        parser.error("数据集为空；未调用后端，未写入结果")
    for row in rows:
        for span in row["spans"]:
            span_key(span)
    totals = Counter(); by_type = defaultdict(Counter); latencies = []
    llm_statuses = Counter()
    with httpx.Client(timeout=180) as client:
        model_response = client.get(f"{args.api}/models")
        model_response.raise_for_status()
        model_config = model_response.json()
        use_llm = not args.no_llm
        model_enabled = bool(model_config.get("enabled")) and use_llm
        system_name = args.name or ("级联 + " + str(model_config.get("active", "LLM")) if model_enabled else "规则 + NER（未启用 LLM）")
        for row in rows:
            started = time.perf_counter()
            response = client.post(f"{args.api}/detect", json={"text": row["text"], "strategy": "mask", "use_llm": use_llm, "risk_level": "strict", "language": "auto", "use_policies": False})
            response.raise_for_status()
            result = response.json()
            prediction = result["spans"]
            llm_steps = [step for step in result.get("trace", []) if step.get("key") == "llm"]
            llm_statuses.update([step.get("status", "unknown") for step in llm_steps] or ["not-recorded"])
            latencies.append((time.perf_counter() - started) * 1000)
            tp, fp, fn, pred, truth = score(prediction, row["spans"])
            totals.update(tp=tp, fp=fp, fn=fn)
            for entity_type in {x[2] for x in pred | truth}:
                p = {x for x in pred if x[2] == entity_type}; g = {x for x in truth if x[2] == entity_type}
                by_type[entity_type].update(tp=len(p & g), fp=len(p - g), fn=len(g - p))
    precision = safe_ratio(totals["tp"], totals["tp"] + totals["fp"])
    recall = safe_ratio(totals["tp"], totals["tp"] + totals["fn"])
    f1 = safe_ratio(2 * precision * recall, precision + recall)
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    dataset_sha256 = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    average_latency = sum(latencies) / max(1, len(latencies))
    output = {
        "is_demo": False,
        "run_id": f"benchmark-{created_at.replace(':', '').replace('-', '')}",
        "dataset": args.dataset.name,
        "created_at": created_at,
        "notice": f"真实实验数据：{args.dataset.name}，{len(rows)} 条，exact-span 指标",
        "systems": [{"name": system_name, "precision": precision, "recall": recall, "f1": f1, "latency": round(average_latency, 2), "records": len(rows)}],
        "categories": [{"name": name, "recall": safe_ratio(c["tp"], c["tp"] + c["fn"])} for name, c in sorted(by_type.items())],
        "metadata": {
            "source": "backend/scripts/run_benchmark.py",
            "verified": False,
            "evaluation_scope": "live-api",
            "latency_scope": "api-request",
            "category_system": system_name,
            "dataset": str(args.dataset),
            "dataset_sha256": dataset_sha256,
            "records": len(rows),
            "metric": "exact-span",
            "api": args.api,
            "model": model_config.get("active"),
            "llm_enabled": model_enabled,
            "llm_statuses": dict(llm_statuses),
            "warnings": ["指标来自真实检测 API；该 API 会写入任务和审计，请使用隔离测试数据库。", "当前后端未启用 LLM。" if not model_enabled else "模型配置不等于调用成功；请检查 LLM 状态计数与降级情况。"],
            "latency_p50_ms": round(percentile(latencies, 0.50), 2),
            "latency_p95_ms": round(percentile(latencies, 0.95), 2),
            "throughput_records_per_second": round(len(rows) / max(0.001, sum(latencies) / 1000), 3),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = args.output.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary_path.replace(args.output)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
