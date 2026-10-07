#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PrivShield 第三层评测。产出 B0 / B1 / B2 三条基线的完整对比。

  B0 = 只有第一二层(规则 + lite NER),不接第三层
  B1 = 接未微调的 Qwen3-14B
  B2 = 接微调后的 adapter

比对口径:(surface, label) 集合,不是 (start,end,label)。

前置:vLLM 已用 --enable-lora 起在 127.0.0.1:8000,同时服务基座与 adapter。

用法:
  # 只算 B0(不需要 vLLM,秒出)
  python eval_privshield.py --only-b0

  # 全量
  python eval_privshield.py \
      --base-model /root/models/Qwen3-14B \
      --lora-name privshield
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.recognizers import detect_rule_spans, detect_lite_ner_spans, merge_spans  # noqa: E402
from app.schemas import Strategy  # noqa: E402
from app.llm_adapter import routed_context  # noqa: E402

LABELS = ["PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE",
          "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT"]
WINDOW_RE = re.compile(r"\[(\d+):(\d+)\]")


# ----------------------------------------------------------------- 基础工具
def load_jsonl(path, limit=None):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
                if limit and len(out) >= limit:
                    break
    return out


def run_upstream(text, lang):
    rule, _ = detect_rule_spans(text, Strategy.MASK)
    lite, _ = detect_lite_ner_spans(text, Strategy.MASK, language=lang)
    return merge_spans(text, rule + lite)


def visible_windows(text):
    ctx = routed_context(text)
    wins = [(int(a), int(b)) for a, b in WINDOW_RE.findall(ctx)]
    if not wins:
        wins = [(0, min(1200, len(text)))]
    return wins


def in_window(s, e, wins):
    return any(ws <= s and e <= we for ws, we in wins)


def find_all(text, needle):
    """与后端 find-all 同口径:长度<2 不做;拉丁串要词边界。"""
    if len(needle) < 2:
        return []
    out, i = [], 0
    latin = bool(re.match(r"^[A-Za-z0-9@._+\-\s]+$", needle))
    while True:
        j = text.find(needle, i)
        if j < 0:
            break
        ok = True
        if latin:
            lb = j == 0 or not text[j - 1].isalnum()
            rb = j + len(needle) >= len(text) or not text[j + len(needle)].isalnum()
            ok = lb and rb
        if ok:
            out.append((j, j + len(needle)))
        i = j + 1
    return out


# ----------------------------------------------------------------- 指标
def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


class Scorer:
    """(surface, label) 集合口径,micro 平均。"""

    def __init__(self):
        self.tp = self.fp = self.fn = 0
        self.by_label = defaultdict(lambda: [0, 0, 0])
        self.by_group = defaultdict(lambda: [0, 0, 0])

    def add(self, gold_set, pred_set, groups=()):
        tp = gold_set & pred_set
        fp = pred_set - gold_set
        fn = gold_set - pred_set
        self.tp += len(tp); self.fp += len(fp); self.fn += len(fn)
        for bucket, items, idx in ((tp, tp, 0), (fp, fp, 1), (fn, fn, 2)):
            for _, lab in items:
                self.by_label[lab][idx] += 1
        for g in groups:
            self.by_group[g][0] += len(tp)
            self.by_group[g][1] += len(fp)
            self.by_group[g][2] += len(fn)

    def overall(self):
        return prf(self.tp, self.fp, self.fn)


# ----------------------------------------------------------------- 推理
def build_messages(sft_row):
    return sft_row["messages"][:2]


def call_vllm(url, model, messages, timeout=120):
    import requests

    t0 = time.time()
    try:
        r = requests.post(
            f"{url}/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 1200,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return {"ok": True, "content": content, "latency": time.time() - t0}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "latency": time.time() - t0}


def predict_all(url, model, sft_rows, out_path, workers=16, tag=""):
    if os.path.exists(out_path):
        done = {r["id"] for r in load_jsonl(out_path)}
        todo = [r for r in sft_rows if r["id"] not in done]
        print(f"[{tag}] 续跑:已有 {len(done)},待跑 {len(todo)}")
    else:
        todo = sft_rows
        print(f"[{tag}] 全新:{len(todo)} 条")
    if not todo:
        return

    t0, n = time.time(), 0
    with open(out_path, "a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(call_vllm, url, model, build_messages(r)): r for r in todo}
            for fut in futs:
                pass
            for fut, row in futs.items():
                res = fut.result()
                res["id"] = row["id"]
                res["source_id"] = row["source_id"]
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
                n += 1
                if n % 25 == 0:
                    el = time.time() - t0
                    eta = el / n * (len(todo) - n)
                    print(f"[{tag}] {n}/{len(todo)} · {el/n:.2f}s/条 · 剩余约 {eta/60:.1f} 分钟")
    print(f"[{tag}] 完成,用时 {(time.time()-t0)/60:.1f} 分钟")


# ----------------------------------------------------------------- 应用 LLM 输出
def apply_llm(text, spans, prompt_obj, content):
    """把 LLM 的 decisions/additions 套到上游 spans 上,返回最终 span 集与诊断。"""
    diag = {"json_ok": False, "id_total": 0, "id_hit": 0,
            "add_total": 0, "add_offset_ok": 0}

    cands = prompt_obj.get("candidates", [])
    pend = [s for s in spans if s.status == "pending" or s.conflict]
    id2span = {}
    for i, c in enumerate(cands):
        if i < len(pend):
            id2span[c["id"]] = pend[i]

    final = {(s.text, s.entity_type.value) for s in spans
             if s.status == "accepted" or s.conflict}

    if content is None:
        return final, diag
    try:
        obj = json.loads(content)
    except Exception:
        return final, diag
    diag["json_ok"] = True

    rejected = set()
    relabel = {}
    for d in obj.get("decisions", []) or []:
        diag["id_total"] += 1
        sp = id2span.get(d.get("id"))
        if sp is None:
            continue
        diag["id_hit"] += 1
        if not d.get("keep", True):
            rejected.add((sp.text, sp.entity_type.value))
        else:
            lab = d.get("label") or sp.entity_type.value
            relabel[(sp.text, sp.entity_type.value)] = lab

    # 重建:先把 pending 里被 keep 的加进来
    for key, lab in relabel.items():
        final.add((key[0], lab))
    for key in rejected:
        final.discard(key)

    for a in obj.get("additions", []) or []:
        diag["add_total"] += 1
        surf, lab = a.get("text"), a.get("label")
        if not surf or lab not in LABELS:
            continue
        st, en = a.get("start"), a.get("end")
        if isinstance(st, int) and isinstance(en, int) and text[st:en] == surf:
            diag["add_offset_ok"] += 1
        if find_all(text, surf):          # find-all 落地,与后端同口径
            final.add((surf, lab))
    return final, diag


# ----------------------------------------------------------------- 报告
def fmt_table(name, sc):
    p, r, f = sc.overall()
    lines = [f"### {name}", "",
             f"- 整体:精确率 **{p:.4f}** · 召回率 **{r:.4f}** · F1 **{f:.4f}**",
             f"- TP {sc.tp} · FP {sc.fp} · FN {sc.fn}", "",
             "| 标签 | gold | 预测 | TP | 精确率 | 召回率 | F1 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for lab in LABELS:
        tp, fp, fn = sc.by_label[lab]
        pp, rr, ff = prf(tp, fp, fn)
        lines.append(f"| {lab} | {tp+fn} | {tp+fp} | {tp} | {pp:.4f} | {rr:.4f} | {ff:.4f} |")
    lines.append("")
    if sc.by_group:
        lines += ["| 分组 | TP | FP | FN | 精确率 | 召回率 | F1 |", "|---|---:|---:|---:|---:|---:|---:|"]
        for g in sorted(sc.by_group):
            tp, fp, fn = sc.by_group[g]
            pp, rr, ff = prf(tp, fp, fn)
            lines.append(f"| {g} | {tp} | {fp} | {fn} | {pp:.4f} | {rr:.4f} | {ff:.4f} |")
        lines.append("")
    return lines


def build_ui_metrics(results, gold_path, gold_count, visibility, created_at, cached_only, provenance=None):
    names = {
        "B0": "B0 · 仅规则 + Lite NER",
        "B1": "B1 · 级联 + 未微调 14B",
        "B2": "B2 · 级联 + 微调 adapter",
    }
    systems = []
    system_records = {}
    for tag in ("B0", "B1", "B2"):
        if tag not in results:
            continue
        scorer, _, diagnostics = results[tag]
        precision, recall, f1 = scorer.overall()
        record_count = diagnostics.get("n", gold_count) if diagnostics else gold_count
        system_records[tag] = record_count
        item = {
            "name": names[tag],
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "records": record_count,
        }
        if diagnostics and diagnostics.get("latency_avg") is not None:
            item["latency"] = round(diagnostics["latency_avg"] * 1000, 2)
        systems.append(item)

    category_tag = "B2" if "B2" in results else "B1" if "B1" in results else "B0"
    category_scorer = results[category_tag][0]
    categories = []
    for label in LABELS:
        tp, _, fn = category_scorer.by_label[label]
        categories.append({"name": label, "recall": prf(tp, 0, fn)[1], "gold": tp + fn})

    cache_note = "使用已缓存模型输出，未重新调用模型。" if cached_only else "模型评测模式可复用已有缓存；实际调用范围见原始推理记录。"
    warnings = [
        "这是离线表面串集合评测，不等于真实 API 的 exact-span 检测或字符边界评测。",
        "模型请求延迟不等于含识别、复核、存储的完整系统延迟。",
    ]
    if len(set(system_records.values())) > 1:
        warnings.append("B0 与 B1/B2 的评测样本数量不同，暂不据此报告 B2−B0 的公平增益。")
    if cached_only:
        warnings.append("旧缓存未记录完整请求指纹；本次校验的是缓存 ID、来源映射与当前文件指纹。")
    return {
        "is_demo": False,
        "run_id": f"privshield-third-layer-{created_at.replace(':', '').replace('-', '')}",
        "dataset": Path(gold_path).name,
        "created_at": created_at,
        "notice": f"离线实验记录：{Path(gold_path).name}；评测口径为 surface-label micro。{cache_note}",
        "systems": systems,
        "categories": categories,
        "metadata": {
            "source": "finetune/scripts/eval_privshield.py",
            "verified": False,
            "evaluation_scope": "offline-cache-replay" if cached_only else "offline-model-evaluation",
            "metric": "surface-label micro",
            "gold_path": str(gold_path),
            "gold_count": gold_count,
            "system_records": system_records,
            "window_visibility": visibility,
            "cached_only": cached_only,
            "category_system": category_tag,
            "latency_scope": "model-request",
            "warnings": warnings,
            "provenance": provenance or {},
        },
    }


def load_prediction_cache(path, sft_rows):
    if not path.is_file():
        raise ValueError(f"缓存不存在:{path}")
    predictions = {}
    for prediction in load_jsonl(path):
        prediction_id = prediction.get("id")
        if not prediction_id or prediction_id in predictions:
            raise ValueError(f"缓存 ID 缺失或重复:{prediction_id}")
        predictions[prediction_id] = prediction
    for row in sft_rows:
        prediction = predictions.get(row["id"])
        if prediction is None:
            raise ValueError(f"缓存缺少预测:{row['id']}")
        if prediction.get("source_id") != row["source_id"]:
            raise ValueError(f"缓存 source_id 不匹配:{row['id']}")
    return predictions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, default=ROOT / "finetune" / "data" / "final" / "test.jsonl")
    ap.add_argument("--sft", type=Path, default=ROOT / "finetune" / "data" / "sft" / "test.jsonl")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--base-model", default="/root/models/Qwen3-14B")
    ap.add_argument("--lora-name", default="privshield")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "finetune" / "reports" / "runtime")
    ap.add_argument("--ui-output", type=Path)
    ap.add_argument("--cached-only", action="store_true")
    ap.add_argument("--preds-dir", type=Path, help="离线缓存来源；报告仍写入 --out-dir")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-b0", action="store_true")
    args = ap.parse_args()

    if args.limit is not None and args.limit <= 0:
        ap.error("--limit 必须大于零")
    if args.preds_dir and not args.cached_only:
        ap.error("--preds-dir 仅用于 --cached-only，避免覆盖归档缓存")
    gold_rows = load_jsonl(args.gold, args.limit)
    sft_rows = load_jsonl(args.sft)
    gold_by_src = {g["id"]: g for g in gold_rows}
    sft_rows = [s for s in sft_rows if s["source_id"] in gold_by_src]
    if not gold_rows or (not args.only_b0 and not sft_rows):
        ap.error("评测集合为空，未写入报告")
    prediction_dir = args.preds_dir or args.out_dir
    cached_predictions = {}
    if args.cached_only and not args.only_b0:
        try:
            for tag in ("B1", "B2"):
                cached_predictions[tag] = load_prediction_cache(prediction_dir / f"preds_{tag}.jsonl", sft_rows)
        except ValueError as exc:
            ap.error(f"{exc}；未调用模型，未写入报告")
    os.makedirs(args.out_dir, exist_ok=True)
    print(f"gold {len(gold_rows)} · sft {len(sft_rows)}")

    # ---------- 跑上游,同时算窗口可见率 ----------
    print("\n[1/4] 跑真实上游...")
    up = {}
    vis_tot = vis_seen = 0
    for i, g in enumerate(gold_rows):
        text = g["text"]
        spans = run_upstream(text, g.get("lang", "auto"))
        wins = visible_windows(text)
        up[g["id"]] = {"spans": spans, "wins": wins, "text": text}
        for sp in g["spans"]:
            vis_tot += 1
            if in_window(sp["start"], sp["end"], wins):
                vis_seen += 1
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(gold_rows)}")
    visibility = vis_seen / vis_tot if vis_tot else 0
    print(f"  窗口可见率:{vis_seen}/{vis_tot} = {visibility:.4f}  ← 第三层召回的理论上限")

    def groups_of(g):
        return (f"lang={g.get('lang','?')}",
                f"tier={g.get('meta',{}).get('tier','?')}",
                f"kind={g.get('kind','?')}")

    # ---------- B0 ----------
    print("\n[2/4] 算 B0(不接第三层)...")
    b0 = Scorer(); b0w = Scorer()
    for g in gold_rows:
        u = up[g["id"]]
        gold_set = {(s["text"] if "text" in s else u["text"][s["start"]:s["end"]], s["label"])
                    for s in g["spans"]}
        gold_win = {(u["text"][s["start"]:s["end"]], s["label"]) for s in g["spans"]
                    if in_window(s["start"], s["end"], u["wins"])}
        pred = {(s.text, s.entity_type.value) for s in u["spans"]}
        b0.add(gold_set, pred, groups_of(g))
        b0w.add(gold_win, pred)
    print(f"  B0 整体 P/R/F1 = {b0.overall()}")

    results = {"B0": (b0, b0w, {})}

    if not args.only_b0:
        for tag, model in (("B1", args.base_model), ("B2", args.lora_name)):
            if args.cached_only:
                print(f"\n[3/4] {tag} 离线读取缓存（不请求模型）...")
            else:
                print(f"\n[3/4] {tag} 推理(model={model})...")
            pred_path = prediction_dir / f"preds_{tag}.jsonl"
            if args.cached_only:
                preds = cached_predictions[tag]
            else:
                predict_all(args.url, model, sft_rows, pred_path, args.workers, tag)
                preds = load_prediction_cache(pred_path, sft_rows)
            sc = Scorer(); scw = Scorer()
            D = Counter(); lat = []
            for s in sft_rows:
                g = gold_by_src[s["source_id"]]
                u = up[g["id"]]
                prompt_obj = json.loads(s["messages"][1]["content"])
                pr = preds.get(s["id"], {})
                latency = pr.get("latency")
                if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency) and latency >= 0:
                    lat.append(latency)
                content = pr.get("content") if pr.get("ok") else None
                final, diag = apply_llm(u["text"], u["spans"], prompt_obj, content)
                for k, v in diag.items():
                    D[k] += int(v)
                D["n"] += 1
                gold_set = {(u["text"][sp["start"]:sp["end"]], sp["label"]) for sp in g["spans"]}
                gold_win = {(u["text"][sp["start"]:sp["end"]], sp["label"]) for sp in g["spans"]
                            if in_window(sp["start"], sp["end"], u["wins"])}
                sc.add(gold_set, final, groups_of(g))
                scw.add(gold_win, final)
            D["latency_avg"] = sum(lat) / len(lat) if lat else None
            results[tag] = (sc, scw, D)
            print(f"  {tag} 整体 P/R/F1 = {sc.overall()}")

    # ---------- 报告 ----------
    print("\n[4/4] 写报告...")
    L = ["# PrivShield 第三层评测报告", "",
         f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 测试集:{args.gold}({len(gold_rows)} 条)",
         "- 执行方式:离线缓存复算，未重新调用模型；缓存缺少完整原始请求指纹，不等同于独立重跑验证。" if args.cached_only else "- 执行方式:模型评测（已有缓存可被复用）；不是实时业务 API 压测。",
         "- 比对口径:(surface, label) 集合,micro 平均", "",
         "## 0. 主表 —— 微调前后对比", "",
         "| 基线 | 说明 | 精确率 | 召回率 | F1 |", "|---|---|---:|---:|---:|"]
    desc = {"B0": "只有规则 + lite NER,不接第三层",
            "B1": "接未微调的 Qwen3-14B",
            "B2": "接微调后的 adapter"}
    for tag in ("B0", "B1", "B2"):
        if tag in results:
            p, r, f = results[tag][0].overall()
            L.append(f"| **{tag}** | {desc[tag]} | {p:.4f} | {r:.4f} | {f:.4f} |")
    L.append("")
    if "B2" in results and "B0" in results and len(sft_rows) == len(gold_rows):
        p0, r0, f0 = results["B0"][0].overall()
        p2, r2, f2 = results["B2"][0].overall()
        L += ["**B2 相对 B0 的提升(delta)**", "",
              f"- 精确率 {p2-p0:+.4f} · 召回率 {r2-r0:+.4f} · F1 {f2-f0:+.4f}", ""]
    elif "B2" in results:
        L += [f"**样本范围说明:** B0 为 {len(gold_rows)} 条，B1/B2 为 {len(sft_rows)} 条；不计算跨样本增益。", ""]
    if "B2" in results and "B1" in results:
        p1, r1, f1 = results["B1"][0].overall()
        p2, r2, f2 = results["B2"][0].overall()
        L += ["**B2 与 B1 在当前样本上的数值差异（不单独证明因果贡献）**", "",
              f"- 精确率 {p2-p1:+.4f} · 召回率 {r2-r1:+.4f} · F1 {f2-f1:+.4f}", ""]

    L += ["## 1. 架构约束:路由窗口可见率", "",
          f"- 窗口内可见 gold 实体 / 全部 gold 实体 = {vis_seen}/{vis_tot} = **{visibility:.4f}**",
          "- 第三层只能看到 `routed_context` 选中的句子,窗口外的实体结构上不可能被识别。",
          "- 因此下面「窗口内口径」才是第三层能力的真实度量;「端到端口径」包含了路由层的损失。", ""]

    for tag in ("B0", "B1", "B2"):
        if tag not in results:
            continue
        sc, scw, D = results[tag]
        L += [f"## 2.{tag} 详细指标", ""]
        L += fmt_table(f"{tag} · 端到端口径(对全部 gold)", sc)
        L += fmt_table(f"{tag} · 窗口内口径(只对可见 gold)", scw)
        if D:
            n = max(1, D["n"])
            L += ["#### 诊断指标", "",
                  f"- JSON 解析成功率:{D['json_ok']}/{n} = {D['json_ok']/n:.4f}",
                  f"- id 回抄准确率:{D['id_hit']}/{max(1,D['id_total'])} = {D['id_hit']/max(1,D['id_total']):.4f}",
                  f"- addition 偏移精确匹配率:{D['add_offset_ok']}/{max(1,D['add_total'])} = "
                  f"{D['add_offset_ok']/max(1,D['add_total']):.4f}",
                   f"- 平均延迟:{D['latency_avg']:.2f} 秒" if D['latency_avg'] is not None else "- 平均延迟:未记录", ""]

    path = args.out_dir / "eval_report.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\n报告已写入 {path}")
    print("\n".join(L[:30]))

    if args.ui_output:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        evidence_paths = {"gold": args.gold, "sft": args.sft}
        if not args.only_b0:
            evidence_paths.update({tag: prediction_dir / f"preds_{tag}.jsonl" for tag in ("B1", "B2")})
        provenance = {name: {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in evidence_paths.items()}
        ui_output = build_ui_metrics(results, args.gold, len(gold_rows), visibility, created_at, args.cached_only, provenance)
        if args.only_b0:
            ui_output["metadata"]["evaluation_scope"] = "local-baseline"
            ui_output["notice"] = "本地重新运行规则与 Lite NER 基线；未调用任何大模型。"
        args.ui_output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = args.ui_output.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(ui_output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary_path.replace(args.ui_output)
        print(f"前端指标已写入 {args.ui_output}")


if __name__ == "__main__":
    main()
