# finetune · 第三层(大模型核验与补漏)

负责人:王洋。本目录是第三层的全部工作 —— 合成数据生成、SFT 微调、评测。
**不涉及前端与后端代码,`backend/` 与 `frontend/` 一个字未改。**

---

## 三十秒版本

给后端接上一个微调过的 Qwen3-14B 之后,冻结测试集上的端到端指标:

| 基线 | 精确率 | 召回率 | F1 |
|---|---:|---:|---:|
| 只有规则 + lite NER | 0.6379 | 0.4610 | 0.5352 |
| 接**未微调**的 Qwen3-14B | 0.6106 | 0.5870 | 0.5985 |
| **接微调后的 adapter** | **0.8845** | **0.7255** | **0.7972** |

微调本身贡献 F1 **+0.1986**。跨分布(CLUENER)召回提升 **8.1 倍**。

**详细结果、10 条已知局限、复现步骤,全部在 [`docs/第三层交付报告.md`](docs/第三层交付报告.md)。**

---

## 队友最可能关心的三件事

### 1. 后端要不要改?不用。

`backend/` 与 `frontend/` 零改动。接入微调模型只需要改 `.env` 两行:

```
PRIVSHIELD_LLM_ENABLED=true
PRIVSHIELD_LLM_MODEL=privshield
```

`llm_base_url` 保持默认的 `http://127.0.0.1:8001/v1` 即可 —— vLLM 就起在 8001。

### 2. 不接大模型还能跑吗?能,而且已实测。

`llm_enabled` 默认 `false`,后端进「离线轻量模式」,前端三个演示样例照常出脱敏结果,
识别轨迹里 `14B 核验与补漏` 显示 `skipped`。2026-08-27 本机实测通过。

### 3. 演示当天怎么起?

照抄 [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md),约 40 分钟(含 28GB 模型下载)。
里面有租卡配置、依赖安装、vLLM 启动参数、前端接入,以及九条踩过的坑。

---

## 目录结构

```
docs/
  第三层交付报告.md      ← 主文档:结果 · 局限 · 复现 · 决策依据
  DEMO_RUNBOOK.md        ← 演示当天照着做
  规格.md                ← 数据与派生层的冻结规范
  计划.md                ← 三天执行计划(历史存档,已完成)

scripts/
  primitives.py          原语,13 项自检
  generate.py            合成数据生成器
  scan_missing.py        LLM 全库漏标扫描
  merge_missing.py       漏标合并 + 四道守卫
  split_freeze.py        切分 + trap 分层 + 冻结 manifest
  derive_sft.py          派生层(gold -> SFT 实例),纯函数
  check_tokens.py        训练前的 chat template 与 token 长度复核
  train_qlora.py         QLoRA 训练
  eval_privshield.py     端到端 B0/B1/B2
  eval_model_only.py     纯模型能力(读缓存,不需要 GPU)
  eval_cluener.py        跨分布外部探针
  measure_dup.py         跨 split 近重复测量
  audit_s2k_*.py         一次性数据体检(留作溯源)

data/
  final/                 冻结 gold 5010 条 + manifest.json(带 SHA-256)
  sft/                   派生后的 SFT 实例 4836 条
  raw/cluener_public/    外部评测基准

reports/                 全部评测结果与推理缓存
requirements-gpu.txt     GPU 环境依赖(实测跑通的版本,不要升级)
```

---

## adapter 在哪

LoRA adapter 约 490MB,**不在 git 里**。位置:

- 矩池云网盘 `/mnt/privshield/adapter`(不随机器释放清空)
- 王洋本机备份

演示当天从 `/mnt` 直接拷到租的机器上,见 DEMO_RUNBOOK 第三节。

---

## 想自己跑一遍?

**不需要 GPU 的两件事:**

```bash
# 重新打分(推理结果已缓存在 reports/,换口径不用重跑模型)
python finetune/scripts/eval_model_only.py --preds-dir finetune/reports

# 重新派生 SFT 数据(纯函数,字节级可复现)
python finetune/scripts/derive_sft.py --out-dir finetune/data/sft
```

**需要 GPU 的**:见交付报告第八节。
