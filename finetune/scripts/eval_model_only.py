#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
纯模型能力评测 —— 剥离路由层与上游两层,只看模型本身。

与 eval_privshield.py 的区别:
  eval_privshield.py  端到端,把模型输出套回流水线,量的是整个系统
  本脚本              直接比对「模型输出的 JSON」与「gold target JSON」,
                      量的是模型在这个任务上的能力

不调用 vLLM,只读 eval_privshield.py 已缓存的 preds_B1.jsonl / preds_B2.jsonl。
因此可以反复重跑、也可以在释放机器之后在本机跑。

用法:
  python eval_model_only.py
  python eval_model_only.py --preds-dir /root/eval --out /root/eval/model_only.md
"""
import argparse
import json
import os
import time
from collections import Counter, defaultdict

LABELS = ["PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE",
          "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT"]
VALID_CERT = {"high", "medium", "low"}


def load_jsonl(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def safe_parse(content):
    if not content:
        return None
    s = content.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("\n") + 1:] if "\n" in s else s
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def score_one(pred_obj, gold_obj, cand_ids, doc_text, M, groups):
    """把一条样本的各项计数累加进 M。"""
    def bump(key, n=1):
        M[key] += n
        for g in groups:
            M[f"{g}||{key}"] += n

    bump("n")

    if pred_obj is None:
        bump("parse_fail")
        return
    bump("parse_ok")

    # ---------- A 格式遵从 ----------
    has_d = isinstance(pred_obj.get("decisions"), list)
    has_a = isinstance(pred_obj.get("additions"), list)
    if has_d and has_a:
        bump("schema_ok")
    extra = set(pred_obj.keys()) - {"decisions", "additions"}
    if extra:
        bump("extra_fields")

    pd = pred_obj.get("decisions") or []
    pa = pred_obj.get("additions") or []

    # ---------- B decisions 通道 ----------
    gd = {d["id"]: d for d in (gold_obj.get("decisions") or []) if isinstance(d, dict)}
    seen = set()
    for d in pd:
        if not isinstance(d, dict):
            continue
        bump("dec_out")
        did = d.get("id")
        if did in cand_ids:
            bump("dec_id_hit")          # id 回抄正确
        else:
            bump("dec_id_miss")
            continue
        if d.get("certainty") in VALID_CERT:
            bump("dec_cert_ok")
        if did in seen:
            bump("dec_dup")
            continue
        seen.add(did)

        g = gd.get(did)
        if g is None:
            continue
        bump("dec_matched")
        gk, pk = bool(g.get("keep")), bool(d.get("keep"))
        gl, pl = g.get("label"), d.get("label")
        if gk == pk:
            bump("dec_keep_ok")
        if gk:
            bump("dec_gold_keep")
            if pk:
                bump("dec_gold_keep_hit")
        else:
            bump("dec_gold_rej")
            if not pk:
                bump("dec_gold_rej_hit")   # 敢不敢拒绝,这项最能区分微调前后
        if gk == pk and gl == pl:
            bump("dec_joint_ok")
    bump("dec_gold_total", len(gd))
    bump("dec_covered", len(seen & set(gd.keys())))

    # ---------- C additions 通道 ----------
    gset = {(a.get("text"), a.get("label")) for a in (gold_obj.get("additions") or [])
            if isinstance(a, dict)}
    pset = set()
    for a in pa:
        if not isinstance(a, dict):
            continue
        bump("add_out")
        surf, lab = a.get("text"), a.get("label")
        if lab in LABELS:
            bump("add_label_valid")
        else:
            bump("add_label_invalid")
        if a.get("certainty") in VALID_CERT:
            bump("add_cert_ok")
        if isinstance(surf, str) and surf:
            if surf in doc_text:
                bump("add_grounded")     # 表面串确实在原文里
            else:
                bump("add_halluc")       # 幻觉:原文没有这个串
            st, en = a.get("start"), a.get("end")
            if isinstance(st, int) and isinstance(en, int) and doc_text[st:en] == surf:
                bump("add_offset_ok")
            pset.add((surf, lab))

    tp = gset & pset
    bump("add_tp", len(tp))
    bump("add_fp", len(pset - gset))
    bump("add_fn", len(gset - pset))
    for _, lab in tp:
        M[f"L::{lab}::tp"] += 1
    for _, lab in pset - gset:
        M[f"L::{lab}::fp"] += 1
    for _, lab in gset - pset:
        M[f"L::{lab}::fn"] += 1

    # ---------- D 整体 ----------
    gd_key = {(k, bool(v.get("keep")), v.get("label")) for k, v in gd.items()}
    pd_key = {(d.get("id"), bool(d.get("keep")), d.get("label"))
              for d in pd if isinstance(d, dict)}
    if gd_key == pd_key and gset == pset:
        bump("exact")


def pctf(m, num, den, default="—"):
    d = m.get(den, 0)
    return f"{m.get(num,0)}/{d} = {m.get(num,0)/d:.4f}" if d else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sft", default="/root/main/finetune/data/sft/test.jsonl")
    ap.add_argument("--gold", default="/root/main/finetune/data/final/test.jsonl")
    ap.add_argument("--preds-dir", default="/root/eval")
    ap.add_argument("--out", default="/root/eval/model_only.md")
    args = ap.parse_args()

    sft = load_jsonl(args.sft)
    gold = {g["id"]: g for g in load_jsonl(args.gold)}
    print(f"sft {len(sft)} · gold {len(gold)}")

    tags = []
    for t in ("B1", "B2"):
        p = os.path.join(args.preds_dir, f"preds_{t}.jsonl")
        if os.path.exists(p):
            tags.append((t, {x["id"]: x for x in load_jsonl(p)}))
            print(f"  {t}: {len(tags[-1][1])} 条预测")
        else:
            print(f"  {t}: 缺 {p},跳过")
    if not tags:
        raise SystemExit("没有任何预测文件,先跑 eval_privshield.py")

    stats = {}
    for tag, preds in tags:
        M = Counter()
        lats = []
        for row in sft:
            g = gold.get(row["source_id"])
            if g is None:
                continue
            prompt = json.loads(row["messages"][1]["content"])
            cand_ids = {c["id"] for c in prompt.get("candidates", [])}
            gold_obj = safe_parse(row["messages"][2]["content"]) or {}
            pr = preds.get(row["id"], {})
            lats.append(pr.get("latency", 0))
            content = pr.get("content") if pr.get("ok") else None
            groups = (f"lang={g.get('lang','?')}",
                      f"tier={g.get('meta',{}).get('tier','?')}")
            score_one(safe_parse(content), gold_obj, cand_ids, g["text"], M, groups)
        M["lat_avg"] = sum(lats) / len(lats) if lats else 0
        stats[tag] = M
        print(f"  {tag} 打分完成")

    # ---------------- 报告 ----------------
    L = ["# 纯模型能力评测(剥离路由与上游)", "",
         f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 样本:{len(sft)} 条测试集实例",
         "- 口径:直接比对模型输出的 JSON 与 gold target JSON,不经过流水线",
         "- B1 = 未微调的 Qwen3-14B · B2 = 微调后(同一基座 + LoRA)", "",
         "## 0. 总览", "",
         "| 指标 | B1 未微调 | B2 微调后 | 变化 |", "|---|---:|---:|---:|"]

    def row(name, num, den, pct=True):
        cells = []
        vals = []
        for t in ("B1", "B2"):
            if t not in stats:
                cells.append("—"); vals.append(None); continue
            m = stats[t]
            d = m.get(den, 0)
            v = m.get(num, 0) / d if d else 0
            vals.append(v)
            cells.append(f"{v:.4f}" if pct else f"{m.get(num,0)}")
        delta = ""
        if vals[0] is not None and vals[1] is not None:
            delta = f"**{vals[1]-vals[0]:+.4f}**"
        L.append(f"| {name} | {cells[0]} | {cells[1]} | {delta} |")

    row("JSON 解析成功率", "parse_ok", "n")
    row("schema 齐全率(有 decisions+additions)", "schema_ok", "n")
    row("整条完全正确率", "exact", "n")
    L.append("")

    L += ["## 1. decisions 通道 —— 复核候选的能力", "",
          "| 指标 | B1 未微调 | B2 微调后 | 变化 |", "|---|---:|---:|---:|"]
    row("id 回抄准确率(抄错则该决策被后端静默丢弃)", "dec_id_hit", "dec_out")
    row("候选覆盖率(该判的判了没有)", "dec_covered", "dec_gold_total")
    row("keep 判断准确率", "dec_keep_ok", "dec_matched")
    row("keep+label 联合准确率", "dec_joint_ok", "dec_matched")
    row("该保留的保留了(gold keep=true)", "dec_gold_keep_hit", "dec_gold_keep")
    row("**该拒绝的拒绝了(gold keep=false)**", "dec_gold_rej_hit", "dec_gold_rej")
    row("certainty 取值合法率", "dec_cert_ok", "dec_out")
    L.append("")

    L += ["## 2. additions 通道 —— 补漏的能力", "",
          "| 指标 | B1 未微调 | B2 微调后 | 变化 |", "|---|---:|---:|---:|"]
    for t in ("B1", "B2"):
        if t in stats:
            m = stats[t]
            m["_p"], m["_r"], m["_f"] = prf(m["add_tp"], m["add_fp"], m["add_fn"])
    for nm, key in (("精确率", "_p"), ("召回率", "_r"), ("F1", "_f")):
        cells, vals = [], []
        for t in ("B1", "B2"):
            v = stats[t][key] if t in stats else None
            vals.append(v)
            cells.append(f"{v:.4f}" if v is not None else "—")
        d = f"**{vals[1]-vals[0]:+.4f}**" if None not in vals else ""
        L.append(f"| {nm} | {cells[0]} | {cells[1]} | {d} |")
    row("label 取值合法率", "add_label_valid", "add_out")
    row("**表面串在原文中(反幻觉)**", "add_grounded", "add_out")
    row("偏移精确匹配率 text[start:end]==text", "add_offset_ok", "add_out")
    L.append("")

    L += ["### additions 分标签 F1", "",
          "| 标签 | B1 P | B1 R | B1 F1 | B2 P | B2 R | B2 F1 | F1 变化 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for lab in LABELS:
        cells, f1s = [], []
        for t in ("B1", "B2"):
            if t not in stats:
                cells += ["—"] * 3; f1s.append(None); continue
            m = stats[t]
            p, r, f = prf(m.get(f"L::{lab}::tp", 0), m.get(f"L::{lab}::fp", 0),
                          m.get(f"L::{lab}::fn", 0))
            cells += [f"{p:.3f}", f"{r:.3f}", f"{f:.3f}"]
            f1s.append(f)
        d = f"**{f1s[1]-f1s[0]:+.3f}**" if None not in f1s else ""
        L.append(f"| {lab} | " + " | ".join(cells) + f" | {d} |")
    L.append("")

    L += ["## 3. 按语种 / 档位分组(additions F1)", "",
          "| 分组 | B1 F1 | B2 F1 | 变化 |", "|---|---:|---:|---:|"]
    gkeys = sorted({k.split("||")[0] for t in stats for k in stats[t]
                    if "||" in k and k.split("||")[0].startswith(("lang=", "tier="))})
    for g in gkeys:
        f1s = []
        for t in ("B1", "B2"):
            if t not in stats:
                f1s.append(None); continue
            m = stats[t]
            _, _, f = prf(m.get(f"{g}||add_tp", 0), m.get(f"{g}||add_fp", 0),
                          m.get(f"{g}||add_fn", 0))
            f1s.append(f)
        d = f"**{f1s[1]-f1s[0]:+.4f}**" if None not in f1s else ""
        L.append(f"| {g} | {f1s[0]:.4f} | {f1s[1]:.4f} | {d} |")
    L.append("")

    L += ["## 4. 其他", "",
          "| 指标 | B1 | B2 |", "|---|---:|---:|"]
    for nm, key in (("平均延迟(秒)", "lat_avg"),
                    ("输出 decisions 条数", "dec_out"),
                    ("输出 additions 条数", "add_out"),
                    ("幻觉表面串条数", "add_halluc"),
                    ("多余顶层字段的样本数", "extra_fields")):
        c = [f"{stats[t].get(key,0):.2f}" if key == "lat_avg" else f"{stats[t].get(key,0)}"
             for t in ("B1", "B2") if t in stats]
        L.append(f"| {nm} | " + " | ".join(c) + " |")
    L.append("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\n报告写入 {args.out}\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
