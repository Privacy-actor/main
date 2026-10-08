# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

墨隐 Moyin — 面向大模型分析场景的多语种（中英混合）文本隐私识别与可控脱敏系统。流水线为“规则 → NER（含隐性隐私识别）→ 大模型核查（置信度 < 0.90）→ 合并 → 知识图谱分级 → 脱敏 → 人工复核 → 最终稿编辑 → 导出前复检”。唯一蓝本是《立项书》与“附件5”申报书。

产品名只出现在 `frontend/src/brand.ts` 和 `browser-extension/manifest.json`。内部标识保持旧名：环境变量前缀 `PRIVSHIELD_`、数据库 `privshield.db`、localStorage 键 `privshield.*`，不要改动，否则会丢用户的本地数据。

## 开发命令

```bash
# 后端（Windows 用 backend\.venv\Scripts\python.exe，不要用 PATH 上的 python）
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev              # http://127.0.0.1:5173，/api 代理到 8000

# 测试
cd backend && python -m pytest -q        # 162 项（未装 Transformers 时跳过 1 项）；tests/conftest.py 用临时数据库并关闭所有模型，不读 .env 里的真实配置
cd frontend && npm test                  # 15 项（node --test + Vite ssrLoadModule）
cd frontend && npm run build             # tsc -b && vite build

# 第三层（finetune）相关自检
python -m pytest -q finetune/tests
python finetune/scripts/primitives.py
```

## 架构

### 后端 (`backend/app/`)

| 模块 | 职责 |
|---|---|
| `main.py` | 路由、CORS（含 chrome-extension 来源）、参数校验错误统一为中文 422（不回显输入）、批处理后台任务（可取消，同一文件共用替换记忆）、`DEFAULT_POLICIES`、大模型设置接口 `/llm/settings`、`/llm/models`、`/llm/test` |
| `config.py` | `pydantic-settings`，前缀 `PRIVSHIELD_`；`confidence_threshold=0.90`（立项书口径）；`model_settings_editable` 控制能否在网页里改模型 |
| `model_settings.py` | 本地、云端大模型的运行时设置（服务地址、模型、密钥），存在 settings 表的 `llm_endpoints`，没设置过的一侧沿用 `.env`；密钥只回显尾号，换主机不带旧密钥 |
| `documents.py` | 上传文件解析：文档整份为一段（超长按段切开），CSV/JSON 每行一段并按“列名：值”排列，列名作识别提示；编码自动识别；导出时还原 CSV/JSON |
| `pipeline.py` | 编排：规则、轻量 NER、隐性隐私、Transformers NER 并行 → 合并 → 大模型核查 → 同名补全 → 知识图谱 → 脱敏；保留词只放行完全落在其中的实体；合并与替换在线程里做 |
| `recognizers.py` | 规则（含座机、护照号上下文排除、全角与零宽字符规整；`CUE_PATTERNS` 见下）、用户正则（第三方 `regex` 模块，带超时）、轻量 NER（中英文地址、顿号列出的姓名、亲属与对接关系后的姓名）、`detect_implicit_spans`（职务身份 ROLE）、`merge_spans`（覆盖范围只增不减，O(n log n)） |
| `llm_adapter.py` | OpenAI 兼容接口；本地/云端端点来自 `model_settings`；补漏按 find-all 落地；`json_payload` 容忍 `<think>` 和代码块；服务不支持 `response_format` 时去掉重试；本机地址不走代理环境变量 |
| `ner_adapter.py` | Transformers NER：相对路径按 backend 目录解析，启动时后台预加载，失败后 5 分钟内不重试，状态 `/models.ner_status` |
| `anonymizer.py` | 掩码、差分隐私替换（指数机制，ε 1:4 / 2:1 / 3:0.25，按类别取候选，候选不够时生成，不撞名、不取文中出现过的名字）、知识图谱泛化（按句子语言，不重新露出已隐去的实体）；`RedactionMemory` 保存编号与替换词（任务里的 `redaction_memory`，复核后不变）；`infer_replacements` 为改版前的任务补出替换映射 |
| `knowledge_base.py` | 内置上位概念层级与行政区划（213 条），`local_levels`、`context_language` |
| `knowledge_graph.py` | 本地层级 + 可选远程 CN-Probase / CN-DBpedia，失败回退 |
| `instruction_parser.py` | 自然语言要求解析（保留词、额外隐去词、范围、方式、力度），可选 LLM 解析。“只保留后四位”这类部分隐去的要求放进 `ignored_clauses`、不改设置；“只……”缩小范围必须对应明确的类型或词；LLM 的结果只能补充，缩小范围只认本地解析 |
| `recheck.py` | 导出前隐私复检 |
| `storage.py` | SQLite（WAL）：任务（`pending`、`project_id` 单独成列，分页查找）、审计（HMAC 哈希）、设置、项目、规则、批处理、复核队列；乐观锁版本 |

实体类型 11 类：PERSON、ORG、LOCATION、ADDRESS、PHONE、EMAIL、ID_CARD、BANK_CARD、PASSPORT、ROLE、CUSTOM。偏移一律按 Unicode 码点计算，前端用 `Array.from` 对齐。

### 与第三层微调的约定（重要）

`finetune/` 的派生与评测直接 import 后端：`detect_rule_spans`、`detect_lite_ner_spans`、`merge_spans`、`routed_context`、`EntityType`。因此：

- 职务身份（ROLE）由单独的 `detect_implicit_spans` 产生，**不要并入** `detect_lite_ner_spans`，第三层上游保持九类标签；
- `verify_with_llm` 的提示词字段与 `finetune/scripts/derive_sft.py` 的训练样本同构（task、entity_types、context、candidates、user_requirement、requirement_rule、output_schema），`entity_types` 与候选都排除 `PRODUCT_ONLY_TYPES`（ROLE）；不要增删字段；
- `finetune/scripts/primitives.py` 第 [5] 项断言“九标签 + CUSTOM == 后端 EntityType”，加入 ROLE 后该项不再成立，是否把 ROLE 写进 `finetune/docs/规格.md`「标签体系」由项目组决定；
- `CUE_PATTERNS`（紧跟“身份证”“银行卡号”等说法、不验校验位的号码）只在产品流水线和导出前复检中启用（`detect_rule_spans(..., include_cues=True)`）；第三层上游按默认参数调用，不含这两条，primitives.py 第 [8] 项“校验位错误由规则层静默丢弃”仍成立；
- 改动 `recognizers.py` / `llm_adapter.py` / `schemas.py` 会让 `derive_sft.py` 报训练数据过期，属于预期行为。

### 前端 (`frontend/src/`)

React 19 + React Router 7 + Vite 8 + TypeScript 7，lucide-react 图标，fontsource 字体（Noto Serif SC 标题、Noto Sans SC / IBM Plex 正文）。

| 路由 | 页面 |
|---|---|
| `/workbench` | 工作台：输入与处理方案 → 识别轨迹、原文/结果对照、逐条复核（原文里拖选文字可直接补充为实体）、最终稿、复检与导出 |
| `/batch` | 批量处理：文件/文件夹、样本试跑、后台任务、分布、ZIP/CSV/JSON |
| `/review` | 人工复核队列 |
| `/projects`、`/projects/:id` | 项目方案与项目规则 |
| `/rules` | 全局规则、模板、内置规则、按类型设置方式 |
| `/history` | 任务记录与操作记录 |
| `/system` | 引擎状态、NER 安装、大模型与部署方式（本地/云端模型自选）、插件安装；锚点 `#ner`、`#models`、`#plugin` |
| `/guide` | 上手指南：准备清单、示例流程、方式对照、常见问题；首次打开 `/` 时进入（localStorage `privshield.guideSeen`） |

路由用 `createBrowserRouter`（数据路由），项目编辑和工作台最终稿用 `useBlocker` 在离开前保存或询问。

关键文件：`brand.ts`（产品名）、`design-system.css`（颜色、字体、组件）、`styles.css`（外框与实体高亮）、`hooks/AppContext.tsx`（引擎状态与当前项目；服务断开时保留当前项目）、`configStore.ts`（方案与草稿的本地存储）、`components/ModelEndpointForm.tsx`（大模型设置表单）、`lib/`（实体标签、文本偏移、导出、报告、规则模板、模型服务预设 `modelProviders.ts`、面板内滚动）。

`pages/Evaluation.*`、`pages/HistoryDetail.tsx`、`components/StrategyModeSelector.tsx`、`components/ReviewDecision.tsx` 是旧界面留下的空文件，不再被引用，可以删除。

设计约定：瓷白底 `#f1f3f5`、墨色 `#1b2333`、钴蓝 `#2b47c9`，每类实体一种颜料色（`--e-PERSON` 等）；掩码显示为墨条；底部固定一层淡水墨远山（`InkBackdrop`）。避免渐变、通用卡片堆叠和全大写标签。

### 浏览器插件 (`browser-extension/`)

Manifest V3。`shared.js`（设置、带超时的接口调用、项目方案：选了项目时用项目的整套方案）、`service-worker.js`（右键菜单、`Alt+Shift+M`、找到获得焦点的框架、同一标签页一次只处理一段）、`page-tools.js`（注入页面的工具：读取选区并记下位置，写回前核对内容，提示条）、`popup.*`、`options.*`。默认连接 `http://127.0.0.1:8000/api/v1`，工作台地址 `http://127.0.0.1:5173`。

### 部署

`docker-compose.yml`：`backend`（FastAPI，8000 内部）、`frontend`（Nginx，`${WEB_PORT:-8080}:80`）、`vllm`（gpu profile，8001）。本地轻量方案用 Ollama（`http://127.0.0.1:11434/v1`），在部署页选择即可。NER 模型用 `scripts/setup_ner.py` 下载到 `backend/models/`（已加入 .gitignore）；`启动墨隐.cmd` 每次启动以 `--offer` 调用它，还没装好时询问一次，选跳过会写下 `backend/models/.ner-declined`，装不装都照常启动。启动脚本会检查 Python ≥ 3.11（后端用到 `enum.StrEnum`、`asyncio.TaskGroup`）。

## 关键边界

- 无模型环境下仍可运行完整闭环（规则 + 轻量 NER + 隐性隐私 + 本地知识层级）。
- 差分隐私替换只对候选抽取这一步使用指数机制，不宣称整段文本的端到端 ε-差分隐私。
- 远程知识图谱、云端大模型默认关闭，开启后会发送实体词或待核查句子。
- DOCX/PDF 仅提取文字，不做 OCR、不重建版式。
- 不要提交 `.env`（含密钥）、`backend/data`、`runtime-data`、`node_modules`、`dist`。
