#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GPU 日第一步:训练之前必须先跑这个。
用真 tokenizer 复核两件事:
  1. chat template 渲染结果是否与派生时的手写模板一致
  2. prompt + target 的真实 token 长度分布,决定 max_seq_len

用法:
  python check_tokens.py --data /root/main/finetune/data/sft --model /root/models/Qwen3-14B
"""
import argparse
import json
import os
from collections import Counter

from transformers import AutoTokenizer


def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    idx = int(len(sorted_vals) * p)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="SFT 数据目录,含 train/dev/test.jsonl")
    ap.add_argument("--model", required=True, help="本地基座模型路径")
    ap.add_argument("--max-seq-len", type=int, default=3072)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)

    # ---------- 检查 1:chat template ----------
    print("=" * 70)
    print("检查 1 · chat template 渲染")
    print("=" * 70)

    probe = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    rendered = tok.apply_chat_template(probe, tokenize=False, add_generation_prompt=True)
    print("默认参数渲染结果(repr):")
    print(repr(rendered))
    print()

    ends_clean = rendered.endswith("<|im_start|>assistant\n")
    has_think = "<think>" in rendered
    print(f"结尾是 '<|im_start|>assistant\\n'  : {ends_clean}")
    print(f"含 <think> 块                     : {has_think}")
    if not ends_clean or has_think:
        print()
        print("!!! 与派生时的手写模板不一致 !!!")
        print("训练用 TRL 的 messages 字段会自动套真模板,所以训练本身没问题,")
        print("但要确认推理端(vLLM)用的是同一个模板。继续前先停下来确认。")
    print()

    # ---------- 检查 2:token 长度 ----------
    print("=" * 70)
    print("检查 2 · 真实 token 长度")
    print("=" * 70)

    resp_marker = "<|im_start|>assistant\n"
    overall = []
    over_limit_ids = []

    for split in ("train", "dev", "test"):
        path = os.path.join(args.data, f"{split}.jsonl")
        if not os.path.exists(path):
            print(f"[跳过] {path} 不存在")
            continue
        rows = load_jsonl(path)

        totals, prompts, targets = [], [], []
        by_lang = {}

        for r in rows:
            msgs = r["messages"]
            assert msgs[-1]["role"] == "assistant", f"{r['id']} 最后一条不是 assistant"

            full = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
            prompt_only = tok.apply_chat_template(
                msgs[:-1], tokenize=False, add_generation_prompt=True
            )

            n_full = len(tok(full, add_special_tokens=False)["input_ids"])
            n_prompt = len(tok(prompt_only, add_special_tokens=False)["input_ids"])

            totals.append(n_full)
            prompts.append(n_prompt)
            targets.append(n_full - n_prompt)

            parts = r.get("source_id", "").split("_")
            lang = parts[1] if len(parts) > 2 else "unknown"
            by_lang.setdefault(lang, []).append(n_full)

            if n_full > args.max_seq_len:
                over_limit_ids.append((split, r["id"], n_full))

            overall.append(n_full)

        s = sorted(totals)
        print(f"\n--- {split} ({len(rows)} 条) ---")
        print(f"  总长 (prompt+target): 均值 {sum(s)/len(s):.0f} · 中位 {pct(s,0.5)} "
              f"· p95 {pct(s,0.95)} · p99 {pct(s,0.99)} · max {s[-1]}")
        sp, st = sorted(prompts), sorted(targets)
        print(f"  仅 prompt           : 中位 {pct(sp,0.5)} · p95 {pct(sp,0.95)} · max {sp[-1]}")
        print(f"  仅 target           : 中位 {pct(st,0.5)} · p95 {pct(st,0.95)} · max {st[-1]}")
        for lang, vals in sorted(by_lang.items()):
            v = sorted(vals)
            print(f"    [{lang:6s}] n={len(v):5d} 中位 {pct(v,0.5):5d} · p95 {pct(v,0.95):5d} · max {v[-1]:5d}")

    # ---------- 结论 ----------
    print()
    print("=" * 70)
    print("结论")
    print("=" * 70)
    o = sorted(overall)
    n_over = len(over_limit_ids)
    ratio = n_over / len(o) if o else 0
    print(f"全库 max_seq_len={args.max_seq_len} 之下:")
    print(f"  超长实例 {n_over} / {len(o)} = {ratio:.2%}")
    print(f"  全库 p99 = {pct(o,0.99)} · max = {o[-1]}")
    print()
    if ratio == 0:
        print(f"  → 建议:维持 max_seq_len={args.max_seq_len}")
    elif ratio < 0.01:
        print(f"  → 建议:维持 {args.max_seq_len},超长的 {n_over} 条直接剔除")
        print("     (被截断的 target 会教模型输出残缺 JSON,比丢掉更糟)")
    else:
        need = pct(o, 0.99)
        suggest = 4096 if need <= 4096 else 8192
        print(f"  → 建议:max_seq_len 提到 {suggest}。A100 40G 放得下,代价是训练变慢约 {suggest/args.max_seq_len:.0%}")
    print()
    if over_limit_ids:
        print("超长实例(最多列 30 条):")
        for split, iid, n in sorted(over_limit_ids, key=lambda x: -x[2])[:30]:
            print(f"  {split:6s} {iid:45s} {n}")
        with open("over_limit_ids.txt", "w", encoding="utf-8") as f:
            for split, iid, n in over_limit_ids:
                f.write(f"{split}\t{iid}\t{n}\n")
        print(f"\n完整清单已写入 over_limit_ids.txt ({len(over_limit_ids)} 条)")


if __name__ == "__main__":
    main()
