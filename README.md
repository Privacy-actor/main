面向大模型分析场景的中英混合文本隐私擦除与智能脱敏系统。系统采用“规则 → NER → 可选 Qwen3-14B 核验/补漏 → Span 合并 → 人工复核 → 最终稿编辑与导出”的可审计流水线，既可在无模型环境下演示完整闭环，也可部署到本地或云服务器接入真实模型。

## 当前完成度

立项书中承诺的**软件功能闭环已经补齐**：

- 图形化工作台、透明识别轨迹和自动脱敏预览；
- 最终文字编辑器，可自由修改任意位置并自动保存；
- 单文件、多文件和整个文件夹批处理；
- 代表样本预览、持久化后台任务、实时进度、失败清单和 ZIP 导出；
- 批处理结果逐项进入工作台复核；
- 菜单配置与自然语言个性化要求；
- 项目级配置、自定义关键词/正则规则和实体类型开关；
- 浏览器插件 + 本地服务原型；
- Docker 本地私有化部署和云服务器部署。

当前剩余工作只应集中在真实数据与模型实验：建设并冻结 gold 测试集、部署 Qwen3-14B 做对照测试、根据错误决定是否 QLoRA，以及使用真实指标做前端视觉与答辩收尾。详见 `立项功能对照检查.md`。

## 功能清单

### 1. 识别与脱敏流水线

- 规则识别：手机号、邮箱、身份证校验、银行卡 Luhn 校验、护照号、地址等；
- NER：支持 Transformers 模型延迟加载，模型不可用时自动回退到中文、英文及中英混合轻量识别器；
- LLM：通过 OpenAI-compatible 接口调用 Qwen3-14B，对低置信候选进行核验并对高风险文本补漏；
- 安全校验：LLM 输出 JSON、substring 和 offset 二次验证，失败自动重试或降级；
- 十类实体：`PERSON`、`ORG`、`LOCATION`、`ADDRESS`、`PHONE`、`EMAIL`、`ID_CARD`、`BANK_CARD`、`PASSPORT`、`CUSTOM`；
- 三种策略：一致性掩码、语义伪名替换、知识层级泛化；
- 知识泛化支持本地精确映射、机构/地点规则推断、三级概念链、查询接口和可选远程知识服务，远程不可用时自动回退；
- 低/中/高三级保护强度、严格/标准风险模式，以及按实体单独调整类型、策略和自定义替换词。

### 2. 项目、规则与自然语言要求

- 创建、修改、删除和选择项目；
- 保存项目级风险等级、默认策略、保护强度、实体类型范围、LLM/策略开关与部署模式；
- 自定义关键词和正则规则 CRUD，可指定实体类型、大小写敏感和启停状态；
- 自然语言要求解析，例如“保留北京地名，隐去上海地名，使用最高强度泛化”；
- 原始自然语言要求会继续传给 LLM 核验层，而不是只在前端展示。

### 3. 人工复核与最终文字编辑器

- 接受、拒绝、改实体类型、补充遗漏实体、调整边界；
- 恢复误判内容，切换全局或单实体脱敏策略，并可给任一实体指定自定义替换词；
- 原文修改后立即提示旧 Span 失效并要求重新检测；
- 最终稿可自由编辑任意位置，支持撤销、重做、查找替换、恢复自动结果、差异查看和复制；
- 1.6 秒自动保存，也可手动保存；
- 乐观版本号避免多标签页静默覆盖；
- TXT 与审计 JSON 导出均使用人工修订后的最终稿；
- 审计记录版本、长度、变化字符数和 SHA-256 摘要，不在编辑日志中重复保存整段敏感文本。

### 4. 文件与文件夹批处理

- 支持 TXT、Markdown、CSV、JSON、DOCX 和 PDF 文本提取；
- 支持单文件、多文件和浏览器文件夹选择；
- 处理前展示文件列表、基本信息和首个代表样本的脱敏预览；
- SQLite 持久化后台 Job，页面轮询显示真实进度、成功数和失败数；
- 每个成功文件保存为完整任务，可从批处理页进入工作台逐项复核；
- 可下载 CSV、JSON、失败清单和包含逐文件最终稿、manifest 的 ZIP；
- 文件夹相对路径在 ZIP 中保留，并进行安全路径清理。

> DOCX/PDF 当前做文本提取与脱敏，不包含扫描件 OCR，也不承诺原版式重建。这不影响文本隐私处理闭环。

### 5. 历史、策略、评估与数据治理

- 历史任务查看、复核、导出和删除；
- 按保留天数清理历史原文与对应审计记录；
- 按实体类型保存默认脱敏策略；
- 复核队列集中处理低置信度、模型冲突和边界异常；
- 评估页支持 Precision、Recall、F1、边界准确率、JSON 合法率、延迟、吞吐、消融和错误案例；
- `backend/reports/experiment_results/latest.json` 存在时自动显示真实实验结果，否则明确标记为演示数据。

### 6. 浏览器插件

`browser-extension/` 是可运行的 Manifest V3 原型：

- 读取网页选中文本或当前页可见文字；
- 右键菜单或工具栏启动本地脱敏；
- 选择策略、保护强度和是否启用 14B；
- 在弹窗内编辑、复制、导出 TXT；
- 一键进入完整工作台继续实体复核、最终稿编辑和审计。

安装方法见 `browser-extension/README.md`。

## 目录结构

```text
privacy-redactor/
├─ backend/                 FastAPI、SQLite、识别/脱敏流水线、测试与 benchmark
├─ frontend/                React + TypeScript + Vite 管理界面
├─ browser-extension/       Chrome/Edge Manifest V3 插件
├─ scripts/                 服务器部署辅助脚本
├─ docker-compose.yml       前端、后端与可选 vLLM 服务
└─ 立项功能对照检查.md       原立项承诺逐项验收表
```

## 本机开发（不下载模型）

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

前端另开终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。未配置 NER/LLM 时，后端自动使用规则、轻量识别器和本地知识层级，仍可验证完整产品流程。API 文档位于 `http://127.0.0.1:8000/api/docs`。

## 服务器一键部署

部署配置与脚本已准备好，但本次没有在本机下载或运行 Qwen3-14B。正式实验时要求 Ubuntu 22.04/24.04、NVIDIA 驱动、Docker、Docker Compose、NVIDIA Container Toolkit。24 GB 显存建议使用 AWQ 版本；更大显存可使用 BF16。

```bash
cd privacy-redactor
cp .env.example .env
docker compose --profile gpu up -d --build
docker compose ps
docker compose logs -f vllm
```

默认由 Nginx 暴露前端：

```text
http://你的服务器公网IP:8080
```

建议只开放前端端口：

```bash
sudo ufw allow 8080/tcp
```

健康检查：

```bash
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1:8001/v1/models -H 'Authorization: Bearer local-token'
```

### 48 GB 显卡运行 BF16

在 `.env` 中设置：

```env
VLLM_MODEL=Qwen/Qwen3-14B
VLLM_SERVED_NAME=Qwen/Qwen3-14B
PRIVSHIELD_LLM_MODEL=Qwen/Qwen3-14B
VLLM_GPU_MEMORY_UTILIZATION=0.90
```

然后重建：

```bash
docker compose --profile gpu up -d --force-recreate vllm backend
```

### 接入已有模型服务

如 Qwen 已单独部署，只需将 `PRIVSHIELD_LLM_BASE_URL` 指向现有 OpenAI-compatible `/v1` 地址，然后启动前后端：

```bash
docker compose up -d --build
```

## 测试、构建与真实评估

```bash
cd backend
python -m pytest -q

cd ../frontend
npm run build
```

冻结测试集为 JSONL 后运行：

```bash
cd backend
python scripts/run_benchmark.py data/gold/test.jsonl --api http://127.0.0.1:8000/api/v1
```

建议至少比较：规则、NER、规则 + NER、级联 + Qwen3-14B，并报告 Precision、Recall、F1、边界准确率、漏检率、过度脱敏率、p50/p95 延迟、吞吐和典型错误。

## 重要边界

- “语义伪名替换”已实现为稳定、可审计的候选替换机制；在没有完整数学证明前，不应在论文或答辩中宣称已获得严格的 ε-差分隐私保证。
- 知识泛化已使用本地实体层级、常见机构/行政区规则、精确映射和查询接口实现；可选远程适配器默认关闭且失败自动回退。启用远程查询会发送实体词，只能连接可信内网或受控服务。
- 当前核心覆盖中文、英文和中英混合文本；“多语种效果优良”必须由后续冻结测试集实验支持，不能在尚无数据时提前宣称。
- 真实敏感文本应让应用、NER 和 vLLM 部署在同一受控服务器或内网，避免把原文发送到公共第三方 API。