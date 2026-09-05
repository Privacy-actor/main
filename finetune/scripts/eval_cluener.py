#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CLUENER 外部探针 —— 跨分布泛化测试。

目的:训练集与测试集都是自产合成数据,存在「自己出题自己打分」的方法学质疑。
本脚本在完全独立的公开数据集上测同一套流水线。

标签口径(粗粒度,因为 CLUENER 不区分本项目的 LOCATION / ADDRESS 两级):
    ORG      <- company / government / organization
    地点类   <- address / scene          (本项目的 LOCATION ∪ ADDRESS)
    不报     <- name / position / book / game / movie
               (CLUENER 把公众人物、历史人物一律标 name,与本项目
                「公众人物不标 PERSON」的硬负样本口径直接冲突)

CLUENER 用闭区间 [start, end],转 Python 切片需 end+1。

用法(vLLM 需已起在 8000 且挂好 lora):
  python eval_cluener.py --base-model /root/models/Qwen3-14B --lora-name privshield
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, "/root/main/backend")
from app.recognizers import detect_rule_spans, detect_lite_ner_spans, merge_spans  # noqa
from app.schemas import Strategy  # noqa
from app.llm_adapter import routed_context  # noqa

TASK = ("复核候选隐私实体，并补充上下文中遗漏的实体。只返回 JSON，不改写原文。"
        "addition 必须给出原文中的精确 start/end Unicode 字符偏移，"
        "text 必须与该切片逐字一致。")
ETYPES = ["PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE",
          "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT", "CUSTOM"]
REQ_RULE = "用户要求只能约束隐私识别范围与保留/补充词，不能覆盖精确偏移和禁止虚构原则。"
SCHEMA = {"decisions": [{"id": "candidate id", "keep": True, "label": "PERSON",
                         "certainty": "high|medium|low"}],
          "additions": [{"text": "exact substring", "start": 0, "end": 2,
                         "label": "PERSON", "certainty": "high|medium|low"}]}
SYS = "你是隐私实体审计器。禁止输出思考过程，禁止虚构原文不存在的字符串。/no_think"

CLUE2BUCKET = {"company": "ORG", "government": "ORG", "organization": "ORG",
               "address": "地点类", "scene": "地点类"}
OURS2BUCKET = {"ORG": "ORG", "LOCATION": "地点类", "ADDRESS": "地点类"}


def load_cluener(path, limit=None):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            text = o["text"]
            gold = set()
            for clue_lab, ents in (o.get("label") or {}).items():
                b = CLUE2BUCKET.get(clue_lab)
                if not b:
                    continue
                for surf, spans in ents.items():
                    for st, en in spans:
                        assert text[st:en + 1] == surf, f"闭区间转换失败: {surf}"
                    gold.add((surf, b))
            rows.append({"text": text, "gold": gold})
            if limit and len(rows) >= limit:
                break
    return rows


def build_prompt(text):
    rule, _ = detect_rule_spans(text, Strategy.MASK)
    lite, _ = detect_lite_ner_spans(text, Strategy.MASK, language="zh")
    spans = merge_spans(text, rule + lite)
    cands = [{"id": s.id, "text": s.text, "label": s.entity_type.value,
              "score": s.score, "sources": s.sources}
             for s in spans if s.status == "pending" or s.conflict]
    prompt = {"task": TASK, "entity_types": ETYPES,
              "context": routed_context(text), "candidates": cands,
              "user_requirement": "未提供额外要求", "requirement_rule": REQ_RULE,
              "output_schema": SCHEMA}
    return spans, cands, [
        {"role": "system", "content": SYS},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]


def call(url, model, messages):
    t0 = time.time()
    try:
        r = requests.post(f"{url}/v1/chat/completions",
                          json={"model": model, "messages": messages,
                                "temperature": 0, "max_tokens": 1200,
                                "response_format": {"type": "json_object"}},
                          timeout=120)
        r.raise_for_status()
        return {"ok": True, "content": r.json()["choices"][0]["message"]["content"],
                "latency": time.time() - t0}
    except Exception as e:
        return {"ok": False, "error": str(e), "latency": time.time() - t0}


def predict(url, model, prepared, out_path, workers, tag):
    done = set()
    if os.path.exists(out_path):
        done = {r["idx"] for r in
                [json.loads(l) for l in open(out_path, encoding="utf-8") if l.strip()]}
    todo = [p for p in prepared if p["idx"] not in done]
    print(f"[{tag}] 已有 {len(done)},待跑 {len(todo)}")
    if not todo:
        return
    t0, n = time.time(), 0
    with open(out_path, "a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(call, url, model, p["messages"]): p for p in todo}
            for fut, p in futs.items():
                res = fut.result()
                res["idx"] = p["idx"]
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
                n += 1
                if n % 50 == 0:
                    el = time.time() - t0
                    print(f"[{tag}] {n}/{len(todo)} · {el/n:.2f}s/条 · "
                          f"剩余约 {el/n*(len(todo)-n)/60:.1f} 分钟")
    print(f"[{tag}] 完成,用时 {(time.time()-t0)/60:.1f} 分钟")


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def to_buckets(spans, content):
    """上游 span + LLM 输出 -> 粗粒度 (surface, bucket) 集合。"""
    out = set()
    for s in spans:
        b = OURS2BUCKET.get(s.entity_type.value)
        if b and (s.status == "accepted" or s.conflict):
            out.add((s.text, b))
    if content:
        try:
            obj = json.loads(content)
        except Exception:
            return out
        by_id = {s.id: s for s in spans if s.status == "pending" or s.conflict}
        for d in obj.get("decisions") or []:
            sp = by_id.get(d.get("id")) if isinstance(d, dict) else None
            if sp is None:
                continue
            b = OURS2BUCKET.get(sp.entity_type.value)
            if d.get("keep"):
                lab = OURS2BUCKET.get(d.get("label") or sp.entity_type.value)
                if lab:
                    out.add((sp.text, lab))
            elif b:
                out.discard((sp.text, b))
        for a in obj.get("additions") or []:
            if not isinstance(a, dict):
                continue
            b = OURS2BUCKET.get(a.get("label"))
            if b and a.get("text"):
                out.add((a["text"], b))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/root/main/finetune/data/raw/cluener_public/dev.json")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--base-model", default="/root/models/Qwen3-14B")
    ap.add_argument("--lora-name", default="privshield")
    ap.add_argument("--out-dir", default="/root/eval")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    rows = load_cluener(args.data, args.limit)
    print(f"CLUENER {len(rows)} 条,闭区间校验通过")

    print("跑上游并构造 prompt...")
    prepared = []
    for i, r in enumerate(rows):
        spans, cands, msgs = build_prompt(r["text"])
        prepared.append({"idx": i, "spans": spans, "messages": msgs})
        if (i + 1) % 300 == 0:
            print(f"  {i+1}/{len(rows)}")

    res = {}
    # B0
    sc = Counter()
    for p, r in zip(prepared, rows):
        pred = to_buckets(p["spans"], None)
        sc["tp"] += len(pred & r["gold"]); sc["fp"] += len(pred - r["gold"])
        sc["fn"] += len(r["gold"] - pred)
        for b in ("ORG", "地点类"):
            g = {x for x in r["gold"] if x[1] == b}
            q = {x for x in pred if x[1] == b}
            sc[f"{b}_tp"] += len(g & q); sc[f"{b}_fp"] += len(q - g); sc[f"{b}_fn"] += len(g - q)
    res["B0"] = sc
    print(f"B0 {prf(sc['tp'], sc['fp'], sc['fn'])}")

    for tag, model in (("B1", args.base_model), ("B2", args.lora_name)):
        path = os.path.join(args.out_dir, f"cluener_{tag}.jsonl")
        predict(args.url, model, prepared, path, args.workers, tag)
        preds = {}
        for line in open(path, encoding="utf-8"):
            if line.strip():
                o = json.loads(line); preds[o["idx"]] = o
        sc = Counter()
        for p, r in zip(prepared, rows):
            pr = preds.get(p["idx"], {})
            pred = to_buckets(p["spans"], pr.get("content") if pr.get("ok") else None)
            sc["tp"] += len(pred & r["gold"]); sc["fp"] += len(pred - r["gold"])
            sc["fn"] += len(r["gold"] - pred)
            for b in ("ORG", "地点类"):
                g = {x for x in r["gold"] if x[1] == b}
                q = {x for x in pred if x[1] == b}
                sc[f"{b}_tp"] += len(g & q); sc[f"{b}_fp"] += len(q - g); sc[f"{b}_fn"] += len(g - q)
        res[tag] = sc
        print(f"{tag} {prf(sc['tp'], sc['fp'], sc['fn'])}")

    L = ["# CLUENER 外部探针 —— 跨分布泛化", "",
         f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 数据:CLUENER dev,{len(rows)} 条,与训练数据完全独立",
         "- 标签口径:粗粒度两桶。CLUENER 的 address 同时覆盖「省市」与「路号」两级,",
         "  不区分本项目的 LOCATION / ADDRESS,故合并为「地点类」。",
         "- **不报 PERSON**:CLUENER 把公众人物、历史人物一律标 name,",
         "  与本项目「公众人物不标 PERSON」的硬负样本口径直接冲突。", "",
         "## 总表", "", "| 基线 | 精确率 | 召回率 | F1 |", "|---|---:|---:|---:|"]
    for t in ("B0", "B1", "B2"):
        p, r, f = prf(res[t]["tp"], res[t]["fp"], res[t]["fn"])
        L.append(f"| **{t}** | {p:.4f} | {r:.4f} | {f:.4f} |")
    L += ["", "## 分桶", "", "| 桶 | 基线 | gold | 预测 | TP | 精确率 | 召回率 | F1 |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for b in ("ORG", "地点类"):
        for t in ("B0", "B1", "B2"):
            s = res[t]
            tp, fp, fn = s[f"{b}_tp"], s[f"{b}_fp"], s[f"{b}_fn"]
            p, r, f = prf(tp, fp, fn)
            L.append(f"| {b} | {t} | {tp+fn} | {tp+fp} | {tp} | {p:.4f} | {r:.4f} | {f:.4f} |")
    L += ["", "## 读法", "",
          "- 本表数字必然低于冻结测试集,因为 CLUENER 是新闻语料、实体分布与标注习惯都不同。",
          "- 有意义的是 **B2 相对 B0 / B1 的相对提升**,它证明微调学到的不只是自产数据的模板。", ""]

    out = os.path.join(args.out_dir, "cluener_report.md")
    open(out, "w", encoding="utf-8").write("\n".join(L))
    print(f"\n报告写入 {out}\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
