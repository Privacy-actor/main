# 队友成果合并与本地验收记录（2026-09-05）

## 做了什么

本轮目标是先保留并合并队友成果，再把真实实验记录接到可演示的产品闭环；不重新训练，不下载本地大模型，不调用付费服务，不推送远端。

### 分支来源与合并

| 来源 | 核对的远端分支 | 提交 | 处理 |
|---|---|---|---|
| 主仓库 Privacy-actor/main | main | 171263b | 集成基线 |
| huyurong-ruc/main | main | a132ae4 | PR #1；保留工作台、复核、项目、历史、批处理改进 |
| HungriestFool/main | feat/llm-finetune-v2 | b366a04 | PR #2；合并数据、SFT、训练/推理脚本与实验归档 |
| HungriestFool/main | feat/llm-finetune | e054391 | 已审阅旧版；不重复覆盖 v2 |

本地分支为 `codex/integrate-team-20260905`。PR #1 合并记录是 `6f858dd`，PR #2 合并记录是 `5907f63`。两位队友分支均通过祖先关系检查。远端分支在 2026-09-05 再次核对，无新增提交。合并后的优化仍在工作区，未提交、未推送。

### 优化文件

- `finetune/scripts/eval_privshield.py`：路径可移植；新增缓存专用模式、来源校验、独立报告目录和前端指标导出；保留原始评分逻辑。
- `backend/app/evaluation.py`：校验评估文件结构、有限数值与指标范围；结构通过不等于科学结论得到独立验证。
- `backend/app/main.py`：导入上述校验；坏文件明确报错，不回退为演示分数；新增评估 JSON 下载接口。
- `backend/scripts/run_benchmark.py`：兼容 gold 的 label 字段，保留 exact-span 口径；记录模型配置、实际 LLM 轨迹、数据指纹和延迟分位数。
- `backend/app/storage.py`：历史预览从最新最终稿读取，兼容已有任务；人工清空最终稿时不回退展示旧文本。
- `frontend/src/pages/Evaluation.tsx`、`Evaluation.css`：真实 B0/B1/B2 对照、摘要切换、分类图表、样本范围提醒、来源指纹、下载与错误态。
- `frontend/src/pages/Workbench.tsx`、`Workbench.css`：修正设置栏挤压和大块空白，调整桌面网格，提供窄屏配置跳转。
- `frontend/src/pages/HistoryDetail.tsx`：显示并复制已保存的最终稿及版本；区分未调用、降级与模型运行状态。
- `frontend/src/api.ts`：提供与前端同源的评估下载地址。
- `backend/tests/test_api.py`、`test_evaluation.py`、`test_benchmark.py`、`finetune/tests/test_eval_privshield.py`：覆盖最终稿、空稿、演示隔离、坏报告、导出、评分兼容和缓存失败保护。
- `backend/reports/experiment_results/latest.json`：本次归档输出复算产生的前端记录。
- `.gitignore`：保留双方忽略项，并忽略新的运行报告目录；旧报告和预测缓存保持不变。

## 实测结果

### 自动测试与构建

从仓库根目录执行（使用现有 `backend/.venv`；不依赖 PATH 或激活环境）：

```powershell
$env:PYTHONUTF8='1'
$env:PRIVSHIELD_LLM_ENABLED='false'
$env:PRIVSHIELD_NER_ENABLED='false'
$env:PRIVSHIELD_SEMANTIC_MODEL_ENABLED='false'
$env:PRIVSHIELD_KNOWLEDGE_GRAPH_REMOTE_ENABLED='false'
$env:PRIVSHIELD_DATABASE_PATH=Join-Path (Get-Location).Path 'runtime-data/qa-backend.db'
Push-Location backend
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location
Push-Location finetune
..\backend\.venv\Scripts\python.exe -m pytest -q
..\backend\.venv\Scripts\python.exe scripts/primitives.py
Pop-Location
Push-Location frontend
npm.cmd run build
Pop-Location
```

本轮执行输出摘录：

```text
...................................................                      [100%]
51 passed in 3.20s

...............                                                          [100%]
15 passed in 0.82s

[13] 英文 PHONE / BANK_CARD · 格式正确且被规则层检出
  PASS  100 组北美电话与 Visa/Mastercard 全部被规则层检出，100 张故意错误卡均未过 Luhn

全部通过。

> privshield-console@0.1.0 build
> tsc -b && vite build
✓ built in 809ms
```

共 66 项 pytest 通过，另有 13 组 primitives 自检通过。构建包含 TypeScript 检查与 Vite 生产打包。未新增格式工具或安装依赖。

### 不调用模型的复算

```powershell
.\backend\.venv\Scripts\python.exe finetune/scripts/eval_privshield.py --cached-only --preds-dir finetune/reports --out-dir finetune/reports/runtime --ui-output backend/reports/experiment_results/latest.json
```

`--cached-only` 会先检查 B1/B2 缓存文件、唯一 ID、SFT 覆盖与 source_id；缺失或不匹配立即失败，不请求模型、不写新报告。缓存来源与运行报告分离；不要把 `--out-dir` 指向归档根目录。复算仍会在本地运行原规则/Lite NER 上游。

```text
gold 800 · sft 796
  窗口可见率:2034/2774 = 0.7332  ← 第三层召回的理论上限
  B0 整体 P/R/F1 = (0.6379310344827587, 0.4609747160131916, 0.5352052754733035)
  B1 整体 P/R/F1 = (0.6105787658106554, 0.5869565217391305, 0.5985346609055043)
  B2 整体 P/R/F1 = (0.8845462713387242, 0.7254974207811349, 0.7971659919028341)
```

这些是归档预测在当前代码和数据上的复算值，不是本机刚跑出的 14B 推理，不是对新数据的泛化保证。缓存未包含完整的原始请求指纹，ID/source_id/SHA-256 一致不能代替独立重跑。`metadata.verified` 保持 false。

B0 使用 800 条；B1/B2 覆盖 796 条。不据此报告公平的 B2−B0 增益，也不通过删减冻结集凑齐样本。模型请求平均耗时不等于业务端到端延迟，B0 未记录的耗时不补造。

### 文件与合并完整性

```text
PR1 ancestor status: 0
PR2 ancestor status: 0
train: SHA-256 MATCH (3760 records)
dev: SHA-256 MATCH (450 records)
test: SHA-256 MATCH (800 records)
Archived report/cache unchanged status: 0
Primitives/spec unchanged status: 0
```

`git diff --check` 没有空白错误；Windows Git 提示部分工作区文件会从 LF 转为 CRLF，不是合并冲突。

### 浏览器验收

- 用明确标注的虚构中英文本完成检测、接受英文姓名候选、人工补充漏检姓名、保存最终稿 v1。
- 重新打开历史详情，显示人工最终稿和 v1，不显示旧自动稿；复制按钮反馈“已复制最终稿”。
- 历史模型状态显示“LLM 未调用”，与实际处理轨迹一致。
- 项目配置在浏览器创建、关闭 14B 选项、保存并从列表读回；使用的是独立预览数据库。
- 评估 B0/B2 摘要切换正确；B0 不显示虚构耗时；导出按钮触发浏览器下载事件。
- 前端代理下载接口返回 200 和 `attachment; filename="privshield-evaluation.json"`；自动测试验证下载 JSON 与页面接口同源。
- 390 像素视口下评估页与工作台无整页横向溢出；配置链接可滚动到设置区；验收后恢复桌面视口。
- 人工复核和批处理页面完成加载冒烟检查；批处理完整业务路径由后端测试覆盖，本轮没有声称完成浏览器文件上传全流程。
- 上述页面检查后，浏览器 warning/error 日志为空。截图保存于忽略目录 `runtime-data/previews/`。

## 遇到的问题

- 合并后真实评估文件有三组模型，原演示用例假设四组；已让演示测试使用独立的缺失报告路径，避免污染真实结果。
- 首次前端 Blob 下载事件等待超时；改为同源服务端附件下载后，浏览器事件与接口响应均通过。
- 历史详情之前直接使用 redacted_text，忽略人工 final_text；历史预览同样可能残留旧内容。已修复，并覆盖空最终稿。
- 轻量识别对虚构样例中的“王小明”漏检；本轮通过人工编辑补正，不把一次样例转成永久词表断言。
- primitives 自检仍报告既有跨类型混淆：`PASS  静默丢弃 179/200 · 被误判成 BANK_CARD 21/200(后端已知跨类型混淆)`；本轮未擅自改变规则或实体语义。

## 我做过但没被要求的判断

- 默认展示 B2 摘要，保留 B0/B1 按钮与完整原始数值，不用最大值算法选择“最佳”。
- 对损坏报告返回明确错误，而不是用看似漂亮的演示分数掩盖。
- 预览和自动测试使用不同的 runtime-data 数据库，不写入用户原有任务库。
- 不引入新 UI 框架、依赖、训练配置或永久实体规则；保留已有视觉配色与队友流程。

## 需要人拍板的

规格的「数据切分」及评测相关文字仍写冻结 test 为 600 条，而已有冻结文件与 manifest 是 800 条；B1/B2 的 SFT/缓存覆盖 796 条。需要项目负责人确认正式论文和答辩采用的规模与覆盖解释。在此之前，本轮不改规格、不重建测试集、不把 796/800 混为同一对照，也不宣称本轮提高了识别准确率。

## 前端预览与再次启动

本轮预览在 `http://127.0.0.1:5173/evaluation`，工作台在 `/workbench`，后端在 `http://127.0.0.1:8000`，只监听本机。预览数据是虚构验收数据。

如关闭后需要重启，在仓库根目录的后端终端中执行：

```powershell
$env:PYTHONUTF8='1'
$env:PRIVSHIELD_LLM_ENABLED='false'
$env:PRIVSHIELD_NER_ENABLED='false'
$env:PRIVSHIELD_SEMANTIC_MODEL_ENABLED='false'
$env:PRIVSHIELD_KNOWLEDGE_GRAPH_REMOTE_ENABLED='false'
$env:PRIVSHIELD_DATABASE_PATH=Join-Path (Get-Location).Path 'runtime-data/preview-20260905.db'
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在另一终端进入 `frontend`，执行 `npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort`。界面应明确显示 14B 未启用；已有评估报告仍可展示，不需要下载模型。

如另做业务 API 的 exact-span 实验，使用 `backend/scripts/run_benchmark.py <测试集路径> --api http://127.0.0.1:8000/api/v1 --no-llm --output runtime-data/api-benchmark.json`。该接口会持久化任务，务必先切换隔离数据库；输出到独立文件，不覆盖当前缓存复算展示。不要把 exact-span 与 surface-label 的分数直接合并比较。

