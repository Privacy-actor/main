# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

PrivShield（隐私盾）— 面向大模型分析场景的中英混合文本隐私擦除与智能脱敏系统。采用"规则 → NER → 可选 Qwen3-14B 核验/补漏 → Span 合并 → 人工复核 → 最终稿编辑与导出"的可审计流水线。

## 开发命令

```bash
# 后端（本机开发，不下载模型即可运行完整闭环）
cd backend
python -m venv .venv && source .venv/bin/activate  # macOS/Linux
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev              # 启动开发服务器，访问 http://127.0.0.1:5173

# 测试
cd backend
python -m pytest -q

# 前端构建
cd frontend
npm run build            # tsc -b && vite build

# API 文档
# 后端运行后访问 http://127.0.0.1:8000/api/docs

# Docker 部署（含 GPU vLLM）
cp .env.example .env
docker compose --profile gpu up -d --build

# 仅前后端（不含 vLLM）
docker compose up -d --build
```

## 架构

### 后端 (`backend/app/`)

FastAPI 应用，模块按职责拆分：

| 模块 | 职责 |
|---|---|
| `main.py` | FastAPI app、所有路由、CORS、中间件、批处理后台任务 |
| `config.py` | `pydantic-settings`，环境变量前缀 `PRIVSHIELD_`，`.env` 自动加载 |
| `pipeline.py` | **核心编排**：规则识别 → 轻量 NER → Transformers NER（并行）→ Span 合并 → LLM 核验 → 知识图谱泛化 → 脱敏替换 |
| `recognizers.py` | 规则引擎（手机号、邮箱、身份证校验位、银行卡 Luhn、护照、地址正则）+ 中/英/混合轻量 NER |
| `ner_adapter.py` | Transformers NER 适配器，延迟加载，模型不可用时自动回退 |
| `llm_adapter.py` | OpenAI-compatible 接口适配（用于 Qwen3-14B），JSON/substring/offset 二次校验，失败降级 |
| `anonymizer.py` | 三种脱敏策略实现：`mask`（一致性掩码）、`pseudonymize`（语义伪名替换）、`generalize`（知识层级泛化） |
| `knowledge_graph.py` | 本地精确映射 + 机构/地点规则推断 + 三级概念链 + 可选远程知识服务（默认关闭，失败回退） |
| `instruction_parser.py` | 自然语言需求解析（如"保留北京地名，隐去上海地名"），确定性解析 + 可选 LLM 解析 |
| `semantic_adapter.py` | sentence-transformers 句向量模型，为伪名替换提供语义相似度评分 |
| `schemas.py` | Pydantic 模型：EntityType(10 类)、Strategy(mask/pseudonymize/generalize)、Span、DetectRequest/Response 等 |
| `storage.py` | SQLite 持久化（WAL 模式）：tasks、audits、settings、projects、rules、batch jobs，含乐观锁版本控制 |

**流水线数据流**：`DetectRequest` → `run_pipeline()` → `(spans, redacted_text, trace, applied_config)` → `DetectResponse`

**环境变量关键配置**（全部见 `.env.example`）：
- `PRIVSHIELD_NER_ENABLED` — 是否加载 Transformers NER 模型
- `PRIVSHIELD_LLM_ENABLED` — 是否启用 LLM 核验
- `PRIVSHIELD_LLM_BASE_URL` — OpenAI-compatible API 地址
- `PRIVSHIELD_LLM_MODEL` — 模型名称（如 `Qwen/Qwen3-14B-AWQ`）

### 前端 (`frontend/src/`)

React 18 + TypeScript + Vite，单页应用，6 个路由页面（lazy loaded）：

| 路由 | 页面 |
|---|---|
| `/workbench` | 隐私工作台 — 文本输入、检测、脱敏预览、实体标注、最终稿编辑器 |
| `/projects` | 项目与规则 — 项目 CRUD、自定义关键词/正则规则管理 |
| `/review` | 人工复核 — 复核队列：接受/拒绝/改类型/补漏/调边界/切换策略 |
| `/batch` | 批量处理 — 多文件上传、代表样本预览、后台 Job 进度、失败清单、ZIP 导出 |
| `/evaluation` | 评估实验室 — Precision/Recall/F1/边界准确率/延迟/吞吐，支持真实实验数据 |
| `/history` | 历史与策略 — 历史任务查看/导出/删除、按实体类型保存默认策略、数据清理 |

核心文件：
- `api.ts` — 所有后端 API 调用的封装客户端
- `types.ts` — TypeScript 类型定义，与后端 Pydantic schema 对应
- `configStore.ts` — 前端配置状态管理

### 浏览器插件 (`browser-extension/`)

Manifest V3 原型：右键菜单读取选中文本 → 调用本地 API 脱敏 → 弹窗编辑/复制/导出 → 可跳转完整工作台。

### 部署

`docker-compose.yml` 定义三个服务：
- `backend` — FastAPI，暴露 8000（内部），健康检查 `/api/v1/health`
- `frontend` — Nginx 反向代理，暴露 `${WEB_PORT:-8080}:80`，依赖 backend healthy
- `vllm` — GPU profile，`vllm/vllm-openai:latest`，暴露 8001

## 关键边界

- 当前无模型环境下仍可运行完整产品闭环（规则 + 轻量 NER + 本地知识泛化）
- 语义伪名替换不应宣称 ε-差分隐私（无数学证明）；知识泛化远程查询默认关闭，开启后会发送实体词
- DOCX/PDF 仅做文本提取与脱敏，不包含 OCR，不保证原版式重建
- 所有配置通过 `PRIVSHIELD_` 前缀环境变量注入，前端通过 `VITE_API_BASE` 覆盖 API 地址
