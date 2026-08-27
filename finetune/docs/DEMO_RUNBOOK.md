# PrivShield 演示日运行手册

> 目标:从租卡到前端可用,**40 分钟**。
> 今天(2026-08-27)花了 4 小时是因为在摸索;照本手册执行不需要再摸索一次。
> 【待填】标记的地方需要在今天的联调测试后补上确切值。

---

## 零、最重要的一件事

**租机器时「高级配置 → 自定义端口导出」要填的是【后端的端口】,不是 vLLM 的端口。**

前端调用的是后端 FastAPI,不是 vLLM。vLLM 只在机器内部被后端调用,不需要对外暴露。

```
浏览器/前端  ──外网──>  导出端口(后端 FastAPI)  ──127.0.0.1──>  vLLM
```

端口导出**开机后加不了**,填错只能释放重租。

---

## 一、租卡(5 分钟)

| 项 | 值 |
|---|---|
| 平台 | 矩池云 matpool.com |
| 机型 | **A100 40GB**,¥2.80/GPU·小时,按时计费 |
| 镜像 | **Pytorch 2.6.0_cuda_12.4**(Ubuntu22.04 / Python 3.11 / CUDA 12.4) |
| 自定义端口 | **HTTP + 后端端口【待填】** |
| VNC | 关 |
| 余额 | 演示按 3 小时算,≈ ¥9,建议余额 ≥ ¥50 |

> 只做演示不训练的话,RTX 4090(¥2.20/h)也够,但 24GB 显存要把
> `--gpu-memory-utilization` 调低、`--max-model-len` 调到 2048。**没测过,演示别冒险。**

---

## 二、环境(10 分钟,可与下载并行)

```bash
# 1) 换源
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 2) 装依赖。torch 已由镜像预装,一律不要动它
pip install modelscope
pip install "transformers==4.51.3" "peft==0.15.2" "accelerate==1.6.0" \
            "datasets==3.5.0" "bitsandbytes==0.45.5"
echo "torch==2.6.0" > /root/pin.txt
pip install "vllm==0.8.5" -c /root/pin.txt
pip install fastapi pydantic pydantic-settings httpx requests python-multipart uvicorn --no-deps

# 3) 验证 torch 没被换掉。必须是 2.6.0+cu124 True
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

**任何一次 pip 之后都要验 torch。** 今天 `pip install fastapi` 顺手把
`typing_extensions` 升了级 —— 那次没出事,下次不一定。

---

## 三、下模型 + 取 adapter(20 分钟,挂后台)

```bash
mkdir -p /root/models
nohup modelscope download --model Qwen/Qwen3-14B \
  --local_dir /root/models/Qwen3-14B > /root/dl.log 2>&1 &

# adapter 在网盘里,不随机器释放清空
cp -r /mnt/privshield/adapter /root/adapter
ls -la /root/adapter/          # 应有 adapter_model.safetensors (约 490MB)
```

进度:`tail -f /root/dl.log`

**拉代码**(GitHub 直连不稳,用 codeload tarball,今天验过秒下):

```bash
cd /root && rm -rf main main.tar.gz
curl --http1.1 -L --fail --retry 5 \
  "https://codeload.github.com/HungriestFool/main/tar.gz/refs/heads/feat/llm-finetune-v2" \
  -o /root/main.tar.gz
mkdir -p /root/main && tar -xzf /root/main.tar.gz --strip-components=1 -C /root/main
```

---

## 四、起 vLLM(3-5 分钟)

**vLLM 走 8001(后端默认去找的地址),不占导出端口。**

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
  --gpu-memory-utilization 0.90 \
  --host 127.0.0.1 --port 8001
```

`--max-lora-rank 32` **不能省**,vLLM 默认只支持到 16,不写直接启动失败。

看到 `Application startup complete` 后 `Ctrl+B` 松开、按 `D` 退出。验证:

```bash
curl -s http://127.0.0.1:8001/v1/models | python -m json.tool | grep '"id"'
# 必须同时看到 /root/models/Qwen3-14B 和 privshield
```

---

## 五、起后端(2 分钟)

```bash
cat > /root/main/backend/.env << 'ENVEOF'
【待填:今天联调后把实际可用的 .env 内容抄到这里】
ENVEOF
```

```bash
tmux new -s api
```

进 tmux 后:

```bash
cd /root/main/backend
【待填:启动命令】
```

`Ctrl+B` `D` 退出。健康检查:

```bash
【待填:健康检查 URL】
```

---

## 六、前端接入

矩池云实例页「HTTP 端口」标签页给出的访问链接形如:

```
https://px-cloudN.matpool.com:PORT?token=XXXX
```

**这个 URL 每次租用都会变**,演示当天要现取。

把前端的 API базовый地址改成这个 URL(去掉 `?token=` 之后的部分先试;
若 401 再把 token 作为 query 或 header 带上)。

【待填:前端改哪个文件的哪一行】

---

## 七、演示当天分工

**如果队友演示、你开机:**

1. 你按第一到五节开好机器,拿到访问 URL
2. 把 URL 发给队友,他在自己电脑上跑前端指过来
3. **提前 30 分钟开机**,别卡着点

**如果你自己演示:** 同上,前端跑在自己机器上。

**演示要点的三个样例**(前端 Workbench.tsx 第 11-15 行硬编码的按钮):

- 中英混合访谈 —— 7 个候选,2 个待复核。看混合语种
- 客户服务记录 —— 银行卡 `6222021001116247` 的 Luhn 余数是 9,**规则层会静默丢弃**,
  必须由第三层补回来。**这个样例最能说明第三层的价值,重点讲**
- 英文邮件 —— 5 个候选,1 个待复核

---

## 八、演示后

```bash
# 释放机器前确认网盘还在
df -h /mnt && ls /mnt/privshield/
```

矩池云页面「停止并释放」。**本地盘全部清空,`/mnt` 保留。**

---

## 九、故障排查(今天踩过的)

| 症状 | 原因 | 处理 |
|---|---|---|
| `du` 显示 /mnt 文件是 0 字节 | fx 文件系统报不准 | 看 `df -h` 和 `ls -la` |
| GitHub clone `stream not closed cleanly` | HTTP/2 | 加 `-c http.version=HTTP/1.1`,或用 codeload tarball |
| `ghfast.top` 超时 | 该代理不通 | 用 codeload tarball |
| pip 装不上 modelscope | 清华源抽风 | 换 `https://mirrors.aliyun.com/pypi/simple/` |
| vLLM 启动报 lora rank | 默认只到 16 | `--max-lora-rank 32` |
| 终端中文乱码 | 控制台代码页 | `Get-Content -Encoding UTF8` / 服务器上 `chcp 65001` |
| 训练/推理时显存 OOM | 14B 吃 38/40GB | 不要同时跑训练和 vLLM |

---

## 十、今天的关键数字(答辩备查)

**冻结测试集 800 条,(surface,label) 集合口径:**

| 基线 | 精确率 | 召回率 | F1 |
|---|---:|---:|---:|
| B0 只有前两层 | 0.6379 | 0.4610 | 0.5352 |
| B1 接未微调 14B | 0.6106 | 0.5870 | 0.5985 |
| B2 接微调后 | **0.8845** | **0.7255** | **0.7972** |

- B2 窗口内口径:精确率 0.8535 · 召回率 **0.9505**
- 路由可见率上限 0.7332;系统召回上限 ≈ 0.767,B2 实测 0.7255(达上限 94.6%)
- CLUENER 跨分布探针:B0 F1 .0785 → B2 F1 .4494,**召回 8.1 倍**
- 纯模型能力:整条完全正确率 .2374 → .8618;该拒绝的拒绝了 .5442 → .9944;
  id 回抄准确率 .7858 → **1.0000**

**已知局限(主动说,别等着被问):**

1. 路由 `routed_context` 压住召回上限 0.767 —— 架构约束,改路由才能突破
2. PASSPORT 精确率 B0/B1/B2 全是 .5868 —— 规则层 0.99 直接采纳,第三层不可见
3. LOCATION 最弱(F1 .5217),训练样本仅 289 个
4. ID_CARD 与 B0 持平,全库仅 59 个,专项补批失败
5. 长档训练样本仅 56 条
6. 训练与测试均为自产合成数据,跨分布证据只有 CLUENER 一个粗粒度探针
7. AWQ 基座的量化迁移损失未测,部署前需补
8. `user_requirement` 恒为默认值,指令跟随能力未经训练
