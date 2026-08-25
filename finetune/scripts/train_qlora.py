#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PrivShield 第三层 QLoRA 微调。超参严格按规格决策 31。

冒烟(先跑这个,约 15 分钟):
  python train_qlora.py --smoke

正式(约 2-2.5 小时):
  python train_qlora.py

依赖版本已在 setup 步骤中固定,不要升级。
"""
import argparse
import json
import os
import sys

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

RESPONSE_MARKER = "<|im_start|>assistant\n"


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


def build_dataset(rows, tok, max_len):
    """渲染 + 只在 assistant 段计 loss。"""
    feats = []
    n_trunc = 0
    for r in rows:
        msgs = r["messages"]
        full = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)

        ids_full = tok(full, add_special_tokens=False)["input_ids"]
        ids_prompt = tok(prompt, add_special_tokens=False)["input_ids"]

        if len(ids_full) > max_len:
            n_trunc += 1
            continue  # 丢弃而非截断:截断的 target 会教模型输出残缺 JSON

        labels = list(ids_full)
        for i in range(min(len(ids_prompt), len(labels))):
            labels[i] = -100  # prompt 段不计 loss

        feats.append({
            "input_ids": ids_full,
            "attention_mask": [1] * len(ids_full),
            "labels": labels,
        })
    if n_trunc:
        print(f"[警告] 丢弃超长实例 {n_trunc} 条 (> {max_len} token)")
    return Dataset.from_list(feats), n_trunc


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
    ap.add_argument("--smoke", action="store_true", help="200 条 × 1 epoch")
    args = ap.parse_args()

    if args.smoke:
        args.epochs = 1
        args.out = args.out.rstrip("/") + "_smoke"

    print("=" * 70)
    print(f"基座   : {args.model}")
    print(f"输出   : {args.out}")
    print(f"LoRA   : r={args.rank} alpha={args.alpha} dropout={args.dropout}")
    print(f"训练   : {args.epochs} epoch · lr={args.lr} · bs={args.bs}×{args.accum}")
    print(f"seq_len: {args.max_seq_len}")
    print("=" * 70)

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    limit = 200 if args.smoke else None
    train_rows = load_jsonl(os.path.join(args.data, "train.jsonl"), limit)
    dev_rows = load_jsonl(os.path.join(args.data, "dev.jsonl"), 50 if args.smoke else None)
    print(f"train {len(train_rows)} · dev {len(dev_rows)}")

    train_ds, _ = build_dataset(train_rows, tok, args.max_seq_len)
    dev_ds, _ = build_dataset(dev_rows, tok, args.max_seq_len)

    # 自检:确认 labels 屏蔽正确
    sample = train_ds[0]
    n_masked = sum(1 for x in sample["labels"] if x == -100)
    n_total = len(sample["labels"])
    print(f"[自检] 首条:总 {n_total} token,屏蔽 {n_masked},计 loss {n_total - n_masked}")
    kept = tok.decode([x for x in sample["labels"] if x != -100])
    print(f"[自检] 计 loss 的部分前 200 字:\n{kept[:200]}")
    if n_total - n_masked < 5:
        sys.exit("!!! 计 loss 的 token 太少,labels 屏蔽逻辑有问题,停止 !!!")

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=bnb,
        dtype=torch.bfloat16,
        device_map={"": 0},
        trust_remote_code=True,
        attn_implementation="sdpa",
    )
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
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=train_ds,
        eval_dataset=dev_ds,
        data_collator=DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100),
    )

    trainer.train()
    model.save_pretrained(os.path.join(args.out, "final"))
    tok.save_pretrained(os.path.join(args.out, "final"))
    print(f"\n完成。adapter 在 {args.out}/final,各 epoch checkpoint 在 {args.out}/checkpoint-*")
    print("下一步:把三个 checkpoint 分别挂到 vLLM 上评 dev,选最好的,不要默认用最后一个。")


if __name__ == "__main__":
    main()
