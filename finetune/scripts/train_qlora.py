#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PrivShield 第三层 QLoRA 微调 (v2)。超参严格按规格决策 31。

v2 相对 v1 的两处修正:
  1. dtype= -> torch_dtype=   (transformers 4.51.3 的参数名)
  2. 不再用 apply_chat_template 渲染整段对话 —— Qwen3 模板会给最后一条
     assistant 消息自动套 <think>\n\n</think>\n\n。改为
     "prompt(add_generation_prompt=True) + 纯 JSON + <|im_end|>",
     与规格「target 是纯 JSON、不带 think 块」一致。

冒烟(约 15 分钟):  python train_qlora.py --smoke
正式(约 2-2.5 小时): python train_qlora.py
"""
import argparse
import json
import os
import sys
import time

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)

IM_END = "<|im_end|>"


def load_jsonl(path, limit=None):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def build_dataset(rows, tok, max_len, tag=""):
    """渲染 + 只在 assistant 段计 loss。target 为纯 JSON,不带 think 块。"""
    feats = []
    n_drop = 0
    for r in rows:
        msgs = r["messages"]
        assert msgs[-1]["role"] == "assistant", f"{r['id']} 最后一条不是 assistant"

        # 关键:prompt 用 add_generation_prompt=True 渲染,结尾恰好是 assistant\n
        # 然后直接拼纯 JSON,绕开模板对最后一条 assistant 的 think 包装
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
        target = msgs[-1]["content"] + IM_END
        full = prompt + target

        ids_prompt = tok(prompt, add_special_tokens=False)["input_ids"]
        ids_full = tok(full, add_special_tokens=False)["input_ids"]

        if len(ids_full) > max_len:
            n_drop += 1
            continue  # 丢弃而非截断:截断的 target 会教模型输出残缺 JSON

        labels = list(ids_full)
        for i in range(min(len(ids_prompt), len(labels))):
            labels[i] = -100

        feats.append({
            "input_ids": ids_full,
            "attention_mask": [1] * len(ids_full),
            "labels": labels,
        })
    if n_drop:
        print(f"[警告] {tag} 丢弃超长实例 {n_drop} 条 (> {max_len} token)")
    return Dataset.from_list(feats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/root/main/finetune/data/sft")
    ap.add_argument("--model", default="/root/models/Qwen3-14B")
    ap.add_argument("--out", default="/root/ckpt")          # 本地盘,不要写 /mnt
    ap.add_argument("--max-seq-len", type=int, default=3072)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--alpha", type=int, default=64)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--bs", type=int, default=2)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--smoke", action="store_true", help="200 条 x 1 epoch")
    args = ap.parse_args()

    if args.smoke:
        args.epochs = 1
        args.out = args.out.rstrip("/") + "_smoke"

    t0 = time.time()
    print("=" * 70)
    print(f"基座   : {args.model}")
    print(f"输出   : {args.out}")
    print(f"LoRA   : r={args.rank} alpha={args.alpha} dropout={args.dropout}")
    print(f"训练   : {args.epochs} epoch · lr={args.lr} · bs={args.bs}x{args.accum}")
    print(f"seq_len: {args.max_seq_len}")
    print("=" * 70)

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    limit = 200 if args.smoke else None
    train_rows = load_jsonl(os.path.join(args.data, "train.jsonl"), limit)
    dev_rows = load_jsonl(os.path.join(args.data, "dev.jsonl"), 50 if args.smoke else None)
    print(f"train {len(train_rows)} · dev {len(dev_rows)}")

    train_ds = build_dataset(train_rows, tok, args.max_seq_len, "train")
    dev_ds = build_dataset(dev_rows, tok, args.max_seq_len, "dev")
    print(f"可用实例: train {len(train_ds)} · dev {len(dev_ds)}")

    # ---- 自检:确认 labels 屏蔽正确、target 不含 think 块 ----
    sample = train_ds[0]
    n_masked = sum(1 for x in sample["labels"] if x == -100)
    n_total = len(sample["labels"])
    kept = tok.decode([x for x in sample["labels"] if x != -100])
    print(f"\n[自检] 首条: 总 {n_total} token,屏蔽 {n_masked},计 loss {n_total - n_masked}")
    print(f"[自检] 计 loss 部分前 160 字:\n{kept[:160]}")
    if n_total - n_masked < 5:
        sys.exit("!!! 计 loss 的 token 太少,labels 屏蔽逻辑有问题,停止 !!!")
    if "<think>" in kept:
        sys.exit("!!! target 里仍有 <think> 块,与规格冲突,停止 !!!")
    if not kept.lstrip().startswith("{"):
        sys.exit(f"!!! target 首字符不是 {{,实际是 {kept.lstrip()[:20]!r},停止 !!!")
    print("[自检] PASS: target 是纯 JSON,无 think 块\n")

    print(f"[计时] 加载基座中,14B NF4 量化约需 3-6 分钟...")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=bnb,
        torch_dtype=torch.bfloat16,
        device_map={"": 0},
        trust_remote_code=True,
        attn_implementation="sdpa",
    )
    print(f"[计时] 基座已加载,用时 {time.time()-t0:.0f} 秒")
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    lora = LoraConfig(
        r=args.rank,
        lora_alpha=args.alpha,
        lora_dropout=args.dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    steps_per_epoch = max(1, len(train_ds) // (args.bs * args.accum))
    print(f"[预估] 每 epoch 约 {steps_per_epoch} 步,共 {steps_per_epoch * args.epochs} 步")

    targs = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.bs,
        gradient_accumulation_steps=args.accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=10,
        save_strategy="epoch",          # 每 epoch 存一个,评测时选最好的不选最后的
        save_total_limit=5,
        eval_strategy="epoch",
        per_device_eval_batch_size=2,
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="paged_adamw_8bit",
        report_to=[],
        seed=20260821,
        disable_tqdm=False,             # 保留进度条与 ETA
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=train_ds,
        eval_dataset=dev_ds,
        data_collator=DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100),
    )

    trainer.train()
    final_dir = os.path.join(args.out, "final")
    model.save_pretrained(final_dir)
    tok.save_pretrained(final_dir)
    print(f"\n完成,总用时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"adapter: {final_dir}")
    print(f"各 epoch checkpoint: {args.out}/checkpoint-*")
    print("下一步: 三个 checkpoint 分别挂 vLLM 评 dev,选最好的,不要默认用最后一个。")


if __name__ == "__main__":
    main()
