# PrivShield 演示日运行手册

> 目标:从租卡到前端可用,**40 分钟**。
> 2026-08-27 全流程实测通过,所有命令可直接复制。

---

## 零、三件不可逆的事

1. **区域必须选「16 区」** —— 网盘按区域分,adapter 在 16 区的 `/mnt`,租其他区拿不到。
2. **导出端口填 8000,那是【后端】的端口,不是 vLLM 的。** 前端调用后端,vLLM 只在机器内部被后端调用。端口导出**开机后加不了**,填错只能释放重租。
3. **关掉 VPN。** 矩池云是国内服务器,挂 VPN 会极慢甚至连不上。

```
浏览器/前端 ──外网──> 导出端口 8000(后端 FastAPI) ──127.0.0.1──> vLLM :8001
```

---

## 一、租卡(5 分钟)

| 项 | 值 |
|---|---|
| 平台 | 矩池云 matpool.com |
| **区域** | **16 区**(adapter 在这个区的网盘里) |
| 机型 | **A100 40GB**,¥2.80/GPU·小时,**按时计费** |
| 镜像 | **Pytorch 2.6.0_cuda_12.4**(Ubuntu22.04 / Python 3.11 / CUDA 12.4) |
| 自定义端口 | **HTTP + 8000** |
| VNC | 关 |
| 余额 | 演示按 3 小时算 ≈ ¥9,建议 ≥ ¥50 |

> 计费选「按时」不选「包日」——包日要跑满 21.6 小时才划算。

---

## 二、环境(10 分钟,可与下载并行)

```bash
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

```bash
pip install modelscope
pip install "transformers==4.51.3" "peft==0.15.2" "accelerate==1.6.0" "datasets==3.5.0" "bitsandbytes==0.45.5"
```

```bash
printf "torch==2.6.0\ntransformers==4.51.3\n" > /root/pin.txt
pip install "vllm==0.8.5" -c /root/pin.txt
pip install "fastapi==0.116.1" "uvicorn[standard]==0.35.0" "pydantic-settings==2.10.1" "httpx==0.28.1" "python-multipart==0.0.20" -c /root/pin.txt
```

**验证 torch 没被换掉(每次 pip 之后都要验):**

```bash
python -c "import torch, transformers; print(torch.__version__, torch.cuda.is_available(), transformers.__version__)"
```

期望 `2.6.0+cu124 True 4.51.3`。变了就停下来。

> ⚠️ **不要用 `--no-deps` 保护 torch**,那会跳过版本约束、装出不兼容组合。
> 踩过的坑:fastapi 0.116 配上 starlette 1.6 → `Router.__init__() got an
> unexpected keyword argument 'on_startup'`。正确做法是上面的 `-c pin.txt`。

---

## 三、下模型 + 取 adapter + 拉代码(20 分钟,挂后台)

```bash
mkdir -p /root/models
nohup modelscope download --model Qwen/Qwen3-14B --local_dir /root/models/Qwen3-14B > /root/dl.log 2>&1 &
```

进度:

```bash
tail -f /root/dl.log
```

**adapter(在 16 区网盘,不随机器释放清空):**

```bash
ls /mnt/privshield/adapter || echo "!!! /mnt 空的 —— 检查是不是租错区了,必须 16 区"
cp -r /mnt/privshield/adapter /root/adapter
ls -la /root/adapter/
```

应看到 `adapter_model.safetensors` 约 490MB。
**万一网盘拿不到**,本机 PowerShell 上传(约 8 分钟):

```powershell
scp -P <端口> -r C:\Users\hungl\Desktop\privshield-gpu\adapter root@<主机>:/root/adapter
```

**拉代码(GitHub 直连不稳,用 codeload tarball,实测秒下):**

```bash
cd /root && rm -rf main main.tar.gz
curl --http1.1 -L --fail --retry 5 "https://codeload.github.com/HungriestFool/main/tar.gz/refs/heads/feat/llm-finetune-v2" -o /root/main.tar.gz
mkdir -p /root/main && tar -xzf /root/main.tar.gz --strip-components=1 -C /root/main
ls /root/main/backend/app/main.py
```

---

## 四、起 vLLM(3-5 分钟)

```bash
tmux new -s vllm
```

进 tmux 后:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model /root/models/Qwen3-14B \
  --enable-lora \
  --lora-modules privshield=/root/adapter \
  --max-lora-rank 32 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --host 127.0.0.1 --port 8001
```

> `--max-lora-rank 32` **不能省**,vLLM 默认只支持到 16,不写直接启动失败。

看到 `Application startup complete` 后按 `Ctrl+B` 松开、再按 `D` 退出。验证:

```bash
curl -s http://127.0.0.1:8001/v1/models | python -m json.tool | grep '"id"'
```

必须同时看到 `/root/models/Qwen3-14B` 和 `privshield`。

---

## 五、起后端(2 分钟)

```bash
cat > /root/main/backend/.env << 'ENVEOF'
PRIVSHIELD_APP_ENV=production
PRIVSHIELD_DATABASE_PATH=data/privshield.db
PRIVSHIELD_CORS_ORIGINS=*
PRIVSHIELD_LLM_ENABLED=true
PRIVSHIELD_LLM_BASE_URL=http://127.0.0.1:8001/v1
PRIVSHIELD_LLM_API_KEY=local-token
PRIVSHIELD_LLM_MODEL=privshield
PRIVSHIELD_LLM_TIMEOUT_SECONDS=45
PRIVSHIELD_LLM_MAX_RETRIES=1
PRIVSHIELD_LLM_MAX_CONCURRENCY=2
PRIVSHIELD_NER_ENABLED=false
PRIVSHIELD_SEMANTIC_MODEL_ENABLED=false
PRIVSHIELD_KNOWLEDGE_GRAPH_REMOTE_ENABLED=false
ENVEOF
cat /root/main/backend/.env
```

> 这个 `.env` **只属于演示环境**,不要提交进仓库 —— 仓库默认 `llm_enabled=false`,
> 是为了让没有模型的人也能跑。

```bash
tmux new -s api
```

进 tmux 后:

```bash
cd /root/main/backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> `--host 0.0.0.0` 不能省,否则外网访问不到。

`Ctrl+B` `D` 退出。**两步健康检查:**

```bash
curl -s http://127.0.0.1:8000/api/v1/health | python -m json.tool
curl -s http://127.0.0.1:8000/api/v1/models | python -m json.tool | head -5
```

判据:`health` 的 `mode` 是 **`llm`**(不是 `lightweight`);`models` 的
`active` 是 **`privshield`**、`enabled` 是 **`true`**。

**端到端冒烟:**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/detect -H 'Content-Type: application/json' \
 -d '{"text":"采访对象姓名：王洋，现就读于中国人民大学。联系电话是13800138000，邮箱 wang.yang@example.com，住在北京市海淀区中关村大街59号。"}' \
 | python -c "import sys,json;d=json.load(sys.stdin);print(d['redacted_text']);print([t for t in d['trace'] if t['key']=='llm'])"
```

`trace` 里 `llm` 那步 `status` 应为 `done`(不是 `skipped`/`degraded`)。

---

## 六、前端接入(本机跑,不在服务器上)

矩池云实例页「HTTP 8000端口」标签 → 复制「访问链接」,形如
`https://px-cloudN.matpool.com:PORT?token=XXXX`。
**`?token=` 只给浏览器落地页用,API 调用不需要带。每次租用 URL 都会变。**

先在本机验证外网通:

```powershell
curl.exe -s "https://px-cloudN.matpool.com:PORT/api/v1/health"
```

### 路线 B(实测跑通,推荐)—— 改 vite proxy,无 CORS

编辑 `frontend/vite.config.ts`,把 target 换成上面的 URL:

```ts
server: { proxy: { '/api': {
  target: 'https://px-cloudN.matpool.com:PORT',
  changeOrigin: true,
  secure: true,
} } },
```

```powershell
cd C:\Users\hungl\Desktop\main\frontend
npm install     # 首次才需要
npm run dev
```

打开 `http://localhost:5173`。

**演示结束后一定要还原,别把矩池云地址提交进仓库:**

```powershell
git checkout -- frontend/vite.config.ts frontend/package-lock.json
```

### 路线 A(备选)—— 用 .env.local,不动 tracked 文件

```powershell
Set-Content C:\Users\hungl\Desktop\main\frontend\.env.local "VITE_API_BASE=https://px-cloudN.matpool.com:PORT/api/v1"
```

**必须带 `/api/v1`**(`api.ts` 的兜底值就是 `/api/v1`)。这条会触发 CORS,
需要把服务器 `.env` 的 `PRIVSHIELD_CORS_ORIGINS` 改成 `http://localhost:5173` 并重启后端。
**改完必须重启 `npm run dev`,Vite 不热加载 .env。**

---

## 七、演示当天

### 分工

**队友演示 / 你开机**:你按第一到五节开好机器 → 把访问 URL 发给队友 →
他本地改 `vite.config.ts` 的 target 跑前端。**提前 30 分钟开机,别卡点。**

### 三个演示样例的实测表现(2026-08-27)

| 样例 | 有 LLM | 讲法 |
|---|---|---|
| **① 中英混合访谈** | ✅ 7 个实体全抓,地址由第三层补充 | **主打这条**,最稳 |
| ② 客户服务记录 | ⚠️ 银行卡 `6222021001116247`(Luhn 不过)**没补上** | 换成 `...248`(合法卡号)可稳过;或诚实讲「该小类召回 0.714」 |
| ③ 英文邮件 | ⚠️ `Sarah Johnson` 被**主动拒绝**,人名泄漏 | 见下 |

**样例③ 那个反转值得主动讲**:`Hi, I am Sarah Johnson.` 整句落在
`routed_context` 窗口外(只选中 `[23:84]` 和 `[84:138]`),模型收到候选却在可见
上下文里找不到该串,按「禁止虚构原文不存在的字符串」判 `keep=false`。
**这是路由层的架构约束,不是模型缺陷,改进方向是「候选所在句子应无条件纳入窗口」。**
主动说出原因和改进方向,比藏着强。

### 最该让老师看的一屏

不是脱敏结果,是**识别轨迹面板**:

```
rule 4ms → ner 7ms → llm 4349ms(privshield) → merge 3ms
```

三层架构在页面上一目了然。第三层补出来的 span 带
`sources: ["LLM","NER-LITE"]` 和 `metadata.llm_model: "privshield"`。

### 演示前的浏览器准备

用**无痕窗口**或临时禁用扩展 —— Zotero 之类的插件会在控制台报错(`zotero.js:304`),
台上弹出来很难看。

---

## 八、演示后

```bash
df -h /mnt && ls /mnt/privshield/
```

矩池云页面「**停止并释放**」。本地盘全清空,`/mnt`(16 区)保留。

本机还原前端:

```powershell
cd C:\Users\hungl\Desktop\main
git checkout -- frontend/vite.config.ts frontend/package-lock.json
git status --short      # 应该是空的
```

---

## 九、故障排查(全部实际踩过)

| 症状 | 原因 | 处理 |
|---|---|---|
| `/mnt` 是空的 | 网盘按区域分,租错区了 | 必须 16 区;或从本机 scp 上传 adapter |
| 后端起不来 `[Errno 10048] 端口占用` | **VS Code 的端口转发残留**(远程机器释放后不自动清理) | `netstat -ano \| findstr :8000` 若有 `Code.exe`,在 VS Code 底部「端口/PORTS」面板停止转发 |
| 前端页面一直转圈 | 同上,VS Code 占了 5173 | 同上,或 `npm run dev -- --port 5180` |
| `Router.__init__() got an unexpected keyword argument 'on_startup'` | fastapi 与 starlette 版本不匹配(用了 `--no-deps`) | 按第二节用 `-c pin.txt` 重装 fastapi |
| vLLM 启动报 lora rank | 默认只支持到 16 | 加 `--max-lora-rank 32` |
| `curl` 通但浏览器打不开 | 系统代理残留 / TUN 虚拟网卡 | 查 `ProxyEnable`;Vite 打印出 `198.18.x.x` 说明 VPN 没退干净 |
| GitHub clone `stream not closed cleanly` | HTTP/2 | 用第三节的 codeload tarball |
| pip 装不上 modelscope | 清华源抽风 | 换 `https://mirrors.aliyun.com/pypi/simple/` |
| 终端中文乱码 | 控制台代码页 | PowerShell 加 `-Encoding UTF8`;服务器上 `chcp 65001` |
| `du` 显示 /mnt 文件是 0 字节 | fx 文件系统报不准 | 看 `df -h` 和 `ls -la` |
| 训练时显存 OOM | 14B 吃 38/40GB | 不要同时跑训练和 vLLM |

---

## 十、关键数字(答辩备查)

**冻结测试集 800 条,`(surface, label)` 集合口径:**

| 基线 | 精确率 | 召回率 | F1 |
|---|---:|---:|---:|
| B0 只有前两层 | 0.6379 | 0.4610 | 0.5352 |
| B1 接未微调 14B | 0.6106 | 0.5870 | 0.5985 |
| **B2 接微调后** | **0.8845** | **0.7255** | **0.7972** |

- 微调本身贡献 F1 **+0.1986**(占第三层总贡献的 76%)
- B2 窗口内口径:精确率 0.8535 · 召回率 **0.9505**
- 路由可见率 0.7332;系统召回上限 ≈ 0.767,B2 实测 0.7255(**达上限 94.6%**)
- **B1 在 9 类里有 4 类倒退**(PHONE/EMAIL/LOCATION/ID_CARD);B2 五升四平零退
- CLUENER 跨分布:B0 F1 .0785 → B2 F1 .4494,**召回 8.1 倍**
- 纯模型能力:整条完全正确率 .2374 → **.8618**;该拒绝的拒绝了 .5442 → **.9944**;
  id 回抄准确率 .7858 → **1.0000**
- 训练:A100 用时 2h27m,显存峰值 38.4/40GB,**花费 ¥15.7**

**已知局限(主动说,别等着被问)—— 完整 10 条见 `docs/第三层交付报告.md` 第六节:**

1. 路由 `routed_context` 压住召回上限 0.767,改路由才能突破
2. 路由遮住候证时第三层会**主动拒绝正确的上游检出**(样例③ 即是)
3. 规则层 0.99 直接采纳的误报第三层不可见,PASSPORT 精确率 B0/B1/B2 全是 .5868
4. LOCATION 最弱(F1 .5217),训练样本仅 289 个
5. ID_CARD 与 B0 持平,全库仅 59 个
6. 长档训练样本仅 56 条
7. 训练与测试均为自产合成数据,跨分布证据只有 CLUENER 一个粗粒度探针
8. `find_all` 词边界守卫在中文夹拉丁时失效(约 0.7% 实体)
9. AWQ 基座的量化迁移损失未测,部署前需补
10. `user_requirement` 恒为默认值,指令跟随未训练
