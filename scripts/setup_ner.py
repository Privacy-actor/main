"""为墨隐接入多语种 NER 模型 Davlan/xlm-roberta-base-ner-hrl。

用后端虚拟环境里的 Python 在项目根目录运行（Windows 上也可以直接双击 scripts\\安装NER模型.cmd）：

    Windows        backend\\.venv\\Scripts\\python.exe scripts\\setup_ner.py
    macOS / Linux  backend/.venv/bin/python scripts/setup_ner.py

依次完成四步，可以重复运行，已经完成的步骤会跳过：
  1. 安装 PyTorch 与 Transformers（backend/requirements-server.txt），默认源失败时改用清华镜像；
  2. 下载模型到 backend/models/xlm-roberta-base-ner-hrl，先从魔搭社区下载，失败时改用 Hugging Face 镜像和官网；
  3. 用后端的识别代码试运行一段中英混合文本；
  4. 在 backend/.env 中开启 NER，其他配置原样保留。
完成后重启后端即可生效。

启动墨隐.cmd 每次启动时以 --offer 调用本脚本：还没装好时询问一次是否安装，装不装、装没装好都不影响墨隐启动。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MODEL_ID = "Davlan/xlm-roberta-base-ner-hrl"
MODEL_SETTING = "models/xlm-roberta-base-ner-hrl"  # 写进 .env 的相对路径，后端按 backend 目录解析
TARGET = BACKEND / MODEL_SETTING
ENV_FILE = BACKEND / ".env"
# 启动时选了“暂不安装”后写下的记号，以后启动不再询问（backend/models 不提交）
DECLINED = BACKEND / "models" / ".ner-declined"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR = "https://hf-mirror.com"
# 只下载 PyTorch 需要的文件；仓库里的 TensorFlow、Flax、ONNX 权重不下载
CONFIG_PATTERNS = ["*.json", "*.model", "*.txt"]
WEIGHT_FILES = ["model.safetensors", "pytorch_model.bin"]
SAMPLE = "张伟在北京协和医院工作，上周和 Microsoft 的 Emily Chen 在上海见了面。"


def step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def pip_install(*args: str) -> bool:
    """先用默认源安装，失败时改用清华镜像再试一次。"""
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args]
    if subprocess.call(base) == 0:
        return True
    print(f"默认源安装失败，改用镜像 {PIP_MIRROR} 重试。", flush=True)
    return subprocess.call([*base, "-i", PIP_MIRROR]) == 0


def packages_ready() -> bool:
    code = (
        "import torch, transformers, safetensors\n"
        "major, minor = (int(part) for part in torch.__version__.split('+')[0].split('.')[:2])\n"
        "raise SystemExit(0 if (major, minor) >= (2, 6) else 1)\n"
    )
    return subprocess.call([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def install_packages() -> None:
    step("1/4 安装 PyTorch 与 Transformers")
    if packages_ready():
        print("已安装，跳过。")
        return
    print("首次安装需要下载约 200 MB 到 1 GB（视系统而定），请耐心等待。", flush=True)
    if not pip_install("-r", str(BACKEND / "requirements-server.txt")):
        raise SystemExit("依赖安装失败。请检查网络后重新运行；如果使用代理，先确认 pip 能访问外网。")


def weights_present() -> bool:
    return (TARGET / "config.json").exists() and any((TARGET / name).exists() for name in WEIGHT_FILES)


def download_from_modelscope() -> None:
    try:
        import modelscope  # noqa: F401
    except ImportError:
        if not pip_install("modelscope>=1.20"):
            raise RuntimeError("modelscope 安装失败")
    from modelscope import snapshot_download

    snapshot_download(MODEL_ID, local_dir=str(TARGET), allow_patterns=[*CONFIG_PATTERNS, WEIGHT_FILES[0]])
    if not weights_present():
        snapshot_download(MODEL_ID, local_dir=str(TARGET), allow_patterns=[WEIGHT_FILES[1]])


def download_from_hub(endpoint: str | None) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_ID, local_dir=str(TARGET), allow_patterns=[*CONFIG_PATTERNS, WEIGHT_FILES[0]], endpoint=endpoint)
    if not weights_present():
        snapshot_download(MODEL_ID, local_dir=str(TARGET), allow_patterns=[WEIGHT_FILES[1]], endpoint=endpoint)


def download_model(source: str) -> None:
    step(f"2/4 下载模型 {MODEL_ID}")
    if weights_present():
        print(f"已存在：{TARGET}，跳过。")
        return
    TARGET.mkdir(parents=True, exist_ok=True)
    print("约 1.1 GB，下载中断后重新运行会继续。", flush=True)
    sources = {
        "modelscope": ("魔搭社区", download_from_modelscope),
        "hf-mirror": ("Hugging Face 镜像", lambda: download_from_hub(HF_MIRROR)),
        "hf": ("Hugging Face", lambda: download_from_hub(None)),
    }
    order = list(sources) if source == "auto" else [source]
    for key in order:
        name, download = sources[key]
        print(f"从{name}下载 ……", flush=True)
        try:
            download()
        except Exception as exc:  # 网络或站点问题时换下一个来源
            print(f"{name}下载失败：{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            continue
        if weights_present():
            size = sum(path.stat().st_size for path in TARGET.rglob("*") if path.is_file()) / 1024 ** 3
            print(f"下载完成：{TARGET}（{size:.2f} GB）")
            return
        print(f"{name}没有返回完整的模型文件。", flush=True)
    raise SystemExit("模型下载失败。可以稍后重试，或手动下载 config.json、分词器文件和权重文件放到 " + str(TARGET))


def read_sentencepiece_in_python() -> None:
    """sentencepiece 在 Windows 上按系统代码页打开模型文件，路径里有中文（如“新建文件夹”）时找不到文件，
    Transformers 随后会报 “Converting from SentencePiece and Tiktoken failed”。改由 Python 读出文件内容再交给它。"""
    try:
        import sentencepiece
    except ImportError:  # 还没安装时，check_model 会补装后重试
        return

    def load_from_file(self, filename):
        return self.LoadFromSerializedProto(Path(filename).read_bytes())

    sentencepiece.SentencePieceProcessor.LoadFromFile = load_from_file


def run_check() -> int:
    """在子进程中运行：加载模型、补出快速分词器文件，再用后端的 NER 适配器识别示例。"""
    from transformers import AutoTokenizer

    read_sentencepiece_in_python()
    try:
        tokenizer = AutoTokenizer.from_pretrained(str(TARGET), use_fast=True)
    except Exception as exc:
        print(f"分词器加载失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 3
    if not (TARGET / "tokenizer.json").exists():
        # 仓库只有 sentencepiece 文件时，保存一份 tokenizer.json，之后启动不再依赖 sentencepiece
        tokenizer.save_pretrained(str(TARGET))

    import torch

    device = 0 if torch.cuda.is_available() else -1
    os.environ.update({"PRIVSHIELD_NER_ENABLED": "true", "PRIVSHIELD_NER_MODEL": MODEL_SETTING, "PRIVSHIELD_NER_DEVICE": str(device)})
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from app.ner_adapter import ner_adapter
    from app.schemas import Strategy

    spans, trace = asyncio.run(ner_adapter.detect(SAMPLE, Strategy.MASK))
    if trace.status != "done":
        print(f"试运行失败：{ner_adapter.status().get('detail') or trace.detail}")
        return 1
    print(f"示例：{SAMPLE}")
    for span in spans:
        print(f"  {span.entity_type.value:<9}{span.text}（{span.score:.2f}）")
    print(f"识别出 {len(spans)} 个实体，用时 {trace.duration_ms} ms，推理设备：{'GPU' if device == 0 else 'CPU'}")
    print(f"DEVICE={device}")
    return 0


def _run_check_process() -> subprocess.CompletedProcess:
    # 子进程输出统一用 UTF-8，避免中文 Windows 控制台按 GBK 解码出乱码
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(Path(__file__).resolve()), "--check"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def check_model() -> int:
    step("3/4 试运行")
    print("加载模型并识别一段示例文本，第一次可能要一两分钟，这期间窗口没有输出 ……", flush=True)
    result = _run_check_process()
    if result.returncode == 3:
        # 没有 tokenizer.json 时需要 sentencepiece 和 protobuf 转换一次分词器
        print("模型只带 sentencepiece 分词器，安装转换所需的 sentencepiece 和 protobuf ……", flush=True)
        if not pip_install("sentencepiece", "protobuf"):
            raise SystemExit("sentencepiece 安装失败，无法加载分词器。")
        result = _run_check_process()
    output = result.stdout.strip()
    print("\n".join(line for line in output.splitlines() if not line.startswith("DEVICE=")))
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip()[-1500:])
        raise SystemExit("试运行没有通过，没有修改 backend/.env。")
    device = next((line.split("=", 1)[1] for line in output.splitlines() if line.startswith("DEVICE=")), "-1")
    return int(device)


def write_env(device: int) -> None:
    step("4/4 在 backend/.env 中开启 NER")
    values = {"PRIVSHIELD_NER_ENABLED": "true", "PRIVSHIELD_NER_MODEL": MODEL_SETTING, "PRIVSHIELD_NER_DEVICE": str(device)}
    # 按字节读写，保留原有的编码、换行和其他配置（其中可能有密钥，不打印内容）
    raw = ENV_FILE.read_bytes().decode("utf-8", errors="surrogateescape") if ENV_FILE.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in values:
            lines[index] = f"{key}={values[key]}"
            seen.add(key)
    missing = [key for key in values if key not in seen]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# 多语种 NER 模型（由 scripts/setup_ner.py 写入）")
        lines.extend(f"{key}={values[key]}" for key in missing)
    ENV_FILE.write_bytes((newline.join(lines) + newline).encode("utf-8", errors="surrogateescape"))
    print(f"已更新 {ENV_FILE}：" + "，".join(f"{key}={value}" for key, value in values.items()))


def install(args: argparse.Namespace) -> None:
    if not args.skip_install:
        install_packages()
    download_model(args.source)
    device = check_model()
    write_env(device)


def ner_ready() -> bool:
    """模型文件、tokenizer.json 和 backend/.env 里的开关都在，说明已经装好；不导入 torch，启动时一眨眼就能判断。"""
    if not (weights_present() and (TARGET / "tokenizer.json").exists() and ENV_FILE.exists()):
        return False
    text = ENV_FILE.read_bytes().decode("utf-8", errors="replace")
    return any(line.replace(" ", "").lower() == "privshield_ner_enabled=true" for line in text.splitlines())


def offer(args: argparse.Namespace) -> None:
    """启动墨隐.cmd 调用：还没装好时问一次；选跳过、安装失败或中途取消，墨隐都照常启动。"""
    if ner_ready():
        print("NER 模型已安装。")
        return
    if DECLINED.exists():
        print("没有安装 NER 模型，姓名、机构、地点由内置轻量识别器处理。需要时双击 scripts\\安装NER模型.cmd 安装。")
        return
    print("要安装多语种 NER 模型吗？装上后，前后没有“联系人：”这类提示词的姓名、机构、地点也能识别出来。")
    print("需要下载约 1.3 GB（PyTorch 和模型），第一次视网速大约 10 到 20 分钟。装好或选择跳过后，以后启动不再询问。")
    try:
        answer = input("直接按回车开始安装，输入 N 再按回车跳过：").strip().lower()
    except EOFError:  # 没有可以输入的窗口时这次先跳过，下次启动再问
        print("\n这次先跳过。")
        return
    if answer in {"n", "no", "否", "不"}:
        DECLINED.parent.mkdir(parents=True, exist_ok=True)
        DECLINED.write_text("启动时选择了暂不安装 NER 模型。删除本文件后重新启动会再次询问，也可以直接双击 scripts\\安装NER模型.cmd 安装。\n", encoding="utf-8")
        print("已跳过，以后启动不再询问。需要时双击 scripts\\安装NER模型.cmd 安装。")
        return
    fallback = "墨隐照常启动，姓名、机构、地点先由内置轻量识别器处理；下次启动会再询问，也可以双击 scripts\\安装NER模型.cmd 重试。"
    try:
        install(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code)
        print("\nNER 模型这次没有装好。" + fallback)
        return
    except KeyboardInterrupt:
        print("\n已取消安装。" + fallback)
        return
    except Exception as exc:  # 网络、磁盘等意外问题都不能挡住墨隐启动
        print(f"\n安装出错：{type(exc).__name__}: {str(exc)[:200]}\nNER 模型这次没有装好。" + fallback)
        return
    print("\nNER 模型已装好，接下来启动墨隐。")


def main() -> None:
    parser = argparse.ArgumentParser(description="为墨隐安装多语种 NER 模型")
    parser.add_argument("--source", choices=["auto", "modelscope", "hf-mirror", "hf"], default="auto", help="模型下载来源，默认依次尝试魔搭社区、Hugging Face 镜像和官网")
    parser.add_argument("--skip-install", action="store_true", help="跳过依赖安装")
    parser.add_argument("--offer", action="store_true", help="启动墨隐.cmd 使用：还没安装时询问一次，装不装都不影响启动")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check:
        raise SystemExit(run_check())

    if not (BACKEND / "app" / "main.py").exists():
        raise SystemExit("请在墨隐项目里运行本脚本（找不到 backend/app/main.py）。")
    if args.offer:
        offer(args)
        return
    if sys.prefix == sys.base_prefix:
        print("提示：当前不是虚拟环境里的 Python，依赖会装进系统 Python。建议改用 backend/.venv 里的 Python 运行。")
    install(args)
    print("\n完成。重启后端（关掉后端窗口，重新运行“启动墨隐.cmd”）后，部署与插件页的 NER 层会显示模型名。")


if __name__ == "__main__":
    main()
