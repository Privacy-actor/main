
面向大模型分析的中英混合文本隐私擦除与智能脱敏系统。系统不是让大模型直接改写全文，而是由规则、NER、选择性 14B 核验/补漏、Span 合并、人工复核和确定性脱敏构成可审计流水线。

## 已实现功能

- 隐私工作台：文本粘贴、TXT/CSV/JSON 导入、八类实体高亮、来源与置信度、三层处理轨迹、人工接受/拒绝/改类/新增、三种策略预览、TXT 和审计 JSON 导出；
- 正式后端：手机、邮箱、身份证校验、银行卡 Luhn 校验；Transformers NER 延迟加载；Qwen3-14B OpenAI-compatible 调用；低置信候选复核和高风险句补漏；substring/offset 二次校验；
- 人工复核：低置信度和冲突候选队列、快捷键操作、SQLite 审计；
- 批处理：UTF-8 TXT、CSV、JSON，结果预览和 CSV 下载；
- 评估实验室：P/R/F1、分类召回率、边界准确率、JSON 合法率、延迟、吞吐、显存、消融和错误案例；把真实 benchmark 结果写入 `backend/reports/experiment_results/latest.json` 后会自动替换演示数据；
- 历史与策略：任务历史、风险等级、各类型默认策略、模型路由配置和操作日志；
- 三档运行：正式 GPU、规则 + NER 降级、纯离线演示。

## 服务器一键部署（推荐）

要求：Ubuntu 22.04/24.04、NVIDIA 驱动、Docker、Docker Compose、NVIDIA Container Toolkit。24 GB 显存使用默认 AWQ；48 GB 显存可把模型改成 `Qwen/Qwen3-14B`。

```bash
cd privacy-redactor
cp .env.example .env
# 如需修改端口、模型或 Hugging Face Token：nano .env
docker compose --profile gpu up -d --build
docker compose ps
docker compose logs -f vllm
```

第一次运行会在服务器下载 NER 和 Qwen 权重。vLLM 日志出现服务就绪后，浏览器打开：

默认让 NER 在 CPU 运行，把整张 GPU 留给 Qwen3-14B，避免 24 GB 显卡上两个模型争抢显存。48 GB 显存且希望加速 NER 时，可将 `PRIVSHIELD_NER_DEVICE` 改为 `0`，并为 backend 容器增加 GPU 访问。

```text
http://你的服务器公网IP:8080
```

防火墙只需放行前端端口：

```bash
sudo ufw allow 8080/tcp
```

检查接口：

```bash
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1:8001/v1/models -H 'Authorization: Bearer local-token'
```

## 48 GB 显卡运行 BF16

编辑 `.env`：

```env
VLLM_MODEL=Qwen/Qwen3-14B
VLLM_SERVED_NAME=Qwen/Qwen3-14B
PRIVSHIELD_LLM_MODEL=Qwen/Qwen3-14B
VLLM_GPU_MEMORY_UTILIZATION=0.90
```

然后重建 vLLM 和后端：

```bash
docker compose --profile gpu up -d --force-recreate vllm backend
```

## 模型服务已单独部署时

无需启动 Compose 里的 vLLM。将 `.env` 的 `PRIVSHIELD_LLM_BASE_URL` 指向现有 OpenAI-compatible `/v1` 地址，然后：

```bash
docker compose up -d --build
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

打开 `http://localhost:5173`。默认 `.env` 不存在时，NER/LLM 均关闭，后端自动使用轻量识别器验证完整界面与流程。

## 测试与构建

```bash
cd backend && python -m pytest -q
cd ../frontend && npm run build
```

API 文档：`http://localhost:8000/api/docs`。

冻结测试集为 JSONL 后，可生成评估页读取的真实结果：

```bash
cd backend
python scripts/run_benchmark.py data/gold/test.jsonl --api http://127.0.0.1:8000/api/v1
```

## 重要说明

- 评估页首次显示的是明确标记的演示数据，不应当写入结项报告；完成冻结测试集实验后用真实 `latest.json` 替换。
- 真实敏感文本应让应用、NER 和 vLLM 部署在同一受控服务器/内网，不要把原文发送到公共第三方 API。
- 首次模型下载可能需要较长时间；下载完成后 Hugging Face 缓存保存在 Docker volume 中，重启不会重复下载。
