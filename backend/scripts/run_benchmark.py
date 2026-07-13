"""Run exact-span evaluation against a JSONL gold set.

Each line: {"text": "...", "spans": [{"start": 0, "end": 2, "entity_type": "PERSON"}]}
The backend must already be running. Results are written to the file consumed by the UI.
"""
import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx


def score(predicted, gold):
    pred = {(x["start"], x["end"], x["entity_type"]) for x in predicted if x.get("status") != "rejected"}
    truth = {(x["start"], x["end"], x["entity_type"]) for x in gold}
    return len(pred & truth), len(pred - truth), len(truth - pred), pred, truth


def safe_ratio(a, b):
    return a / b if b else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--name", default="级联 + 14B")
    parser.add_argument("--output", type=Path, default=Path("reports/experiment_results/latest.json"))
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    totals = Counter(); by_type = defaultdict(Counter); latencies = []
    with httpx.Client(timeout=180) as client:
        for row in rows:
            started = time.perf_counter()
            response = client.post(f"{args.api}/detect", json={"text": row["text"], "strategy": "mask", "use_llm": True, "risk_level": "strict", "language": "auto"})
            response.raise_for_status()
            prediction = response.json()["spans"]
            latencies.append((time.perf_counter() - started) * 1000)
            tp, fp, fn, pred, truth = score(prediction, row["spans"])
            totals.update(tp=tp, fp=fp, fn=fn)
            for entity_type in {x[2] for x in pred | truth}:
                p = {x for x in pred if x[2] == entity_type}; g = {x for x in truth if x[2] == entity_type}
                by_type[entity_type].update(tp=len(p & g), fp=len(p - g), fn=len(g - p))
    precision = safe_ratio(totals["tp"], totals["tp"] + totals["fp"])
    recall = safe_ratio(totals["tp"], totals["tp"] + totals["fn"])
    f1 = safe_ratio(2 * precision * recall, precision + recall)
    output = {
        "notice": f"真实实验数据：{args.dataset.name}，{len(rows)} 条，exact-span 指标",
        "systems": [{"name": args.name, "precision": precision, "recall": recall, "f1": f1, "latency": sum(latencies) / max(1, len(latencies))}],
        "categories": [{"name": name, "recall": safe_ratio(c["tp"], c["tp"] + c["fn"])} for name, c in sorted(by_type.items())],
        "metadata": {"dataset": str(args.dataset), "records": len(rows), "metric": "exact-span", "api": args.api},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
