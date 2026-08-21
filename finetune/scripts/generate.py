"""阶段一：单次生成一条 gold 记录。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
from typing import Any

import httpx
import yaml

from primitives import (
    ParseError,
    expand_spans,
    make_bank_card,
    make_email,
    make_id_card,
    make_org_name,
    make_passport,
    make_person_name,
    make_phone,
    parse_inline_tagged,
    find_all,
    slot_rng,
)


FINETUNE_DIR = Path(__file__).resolve().parents[1]
AXES_PATH = FINETUNE_DIR / "configs" / "axes.yaml"
DASHSCOPE_CHAT_COMPLETIONS = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)

# 唯一数值来源：规格「长度分档」的表 A。
TIER_TARGETS = {
    "短密": {"code": "dense", "zh": "50-160", "en": "150-400", "entities": (4, 6)},
    "短": {"code": "short", "zh": "50-150", "en": "150-400", "entities": (1, 3)},
    "中": {"code": "mid", "zh": "300-800", "en": "700-1100", "entities": (2, 6)},
    "长": {"code": "long", "zh": "1500-3000", "en": "1500-3000", "entities": (0, 3)},
}
TIER_CODES = {name: target["code"] for name, target in TIER_TARGETS.items()}
MAX_ENTITY_DENSITY_PER_THOUSAND = 60.0
MAX_ENTITY_COUNT = 25
STRUCTURED_LABELS = ("PHONE", "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT")
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
STRUCTURED_TAG_RE = re.compile(
    r"<(PHONE|EMAIL|ID_CARD|BANK_CARD|PASSPORT)>(.*?)</\1>", re.S
)
SAMPLE_BLOCK_RE = re.compile(r"<SAMPLE>\s*(.*?)\s*</SAMPLE>", re.S)
LABEL_LEAK_RE = re.compile(
    r"(?<![A-Za-z_])(?:PERSON|ORG|LOCATION|ADDRESS|PHONE|EMAIL|ID_CARD|BANK_CARD|PASSPORT)(?![A-Za-z_])"
)
META_LEAK_RE = re.compile(
    r"标注|实体数|占位符|内联|跨度|span|本条|本次生成|训练数据|陷阱|标为",
    re.I,
)
ENTITY_COUNT_LEAK_RE = re.compile(
    r"(?:[零一二三四五六七八九十百两\d]+\s*个\s*实体|实体\s*(?:共|总计|一共|共有)?\s*[零一二三四五六七八九十百两\d]+)"
)

LANG_WEIGHTS = {"zh": 0.50, "en": 0.30, "mixed": 0.20}
TIER_WEIGHTS = {"短密": 0.05, "短": 0.25, "中": 0.40, "长": 0.30}
KIND_WEIGHTS = {"positive": 0.70, "hard_negative": 0.20, "true_negative": 0.10}
DEFAULT_MODEL_WEIGHTS = {
    "glm-5.2": 0.40,
    "deepseek-v4-pro": 0.30,
    "qwen3.7-plus": 0.30,
}
BATCH_SIZE_BY_TIER = {"短密": 8, "短": 8, "中": 4, "长": 2}
MAX_PILOT_SAMPLES = 5000
MAX_API_CALLS = 2500
MAX_WALL_SECONDS = 7200.0
MAX_SAMPLE_ATTEMPTS = 3
MAX_SCHEDULING_MISSES = 25
SOFT_QUOTA_FACTOR = 1.25
MAX_NETWORK_FAILURES_PER_MODEL = 5
NETWORK_BACKOFF_SECONDS = (2, 4, 8, 16, 32)
INTERIM_DIR = FINETUNE_DIR / "data" / "interim"
CALLS_PATH = INTERIM_DIR / "calls.jsonl"


def parse_model_allocations(value: str) -> dict[str, int]:
    allocations: dict[str, int] = {}
    for item in value.split(","):
        try:
            model, count_text = item.rsplit(":", 1)
            count = int(count_text)
        except (ValueError, TypeError) as exc:
            raise argparse.ArgumentTypeError(
                "--models 格式必须为 model:count,model:count"
            ) from exc
        if not model or count <= 0:
            raise argparse.ArgumentTypeError("模型名不能为空且条数必须为正整数")
        if model in allocations:
            raise argparse.ArgumentTypeError(f"--models 含重复模型: {model}")
        allocations[model] = count
    if not allocations:
        raise argparse.ArgumentTypeError("--models 不能为空")
    return allocations


def default_model_allocations(total: int) -> dict[str, int]:
    exact = {model: total * weight for model, weight in DEFAULT_MODEL_WEIGHTS.items()}
    allocated = {model: int(value) for model, value in exact.items()}
    for model in sorted(exact, key=lambda name: (-(exact[name] - allocated[name]), name))[
        : total - sum(allocated.values())
    ]:
        allocated[model] += 1
    return allocated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 PrivShield gold 记录")
    parser.add_argument("--tier", choices=TIER_TARGETS)
    parser.add_argument("--lang", choices=("zh", "en", "mixed"))
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument("--model")
    model_group.add_argument("--models", type=parse_model_allocations)
    parser.add_argument("--seed", default=100, type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--pilot",
        type=int,
        metavar="N",
        help="批量生成条数；当前最多 5000，支持 checkpoint 续跑",
    )
    parser.add_argument("--batch-id")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency 必须为正整数")
    if args.status:
        if not args.batch_id:
            parser.error("--status 必须提供 --batch-id")
        if args.pilot is not None or args.model is not None or args.models is not None:
            parser.error("--status 不接受 --pilot/--model/--models")
        return args
    if args.pilot is None:
        if args.model is None:
            parser.error("单次模式必须提供 --model")
        if args.tier is None or args.lang is None:
            parser.error("单次模式必须同时提供 --tier 与 --lang")
    else:
        if args.models is None:
            if args.dry_run:
                args.models = {"dry-run": args.pilot}
            else:
                args.models = default_model_allocations(args.pilot)
        if args.tier is not None or args.lang is not None:
            parser.error("批量模式由配额调度档位与语种，不接受 --tier/--lang")
        if not 1 <= args.pilot <= MAX_PILOT_SAMPLES:
            parser.error(f"--pilot 必须在 1..{MAX_PILOT_SAMPLES} 之间")
        if args.batch_id is None:
            args.batch_id = f"pilot{args.pilot}_dryrun" if args.dry_run else "pilot60"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", args.batch_id):
            parser.error("--batch-id 只能包含 ASCII 字母、数字、下划线与连字符")
        if sum(args.models.values()) != args.pilot:
            parser.error(
                f"--models 条数合计 {sum(args.models.values())}，必须等于 --pilot {args.pilot}"
            )
    return args


def load_axes() -> dict[str, Any]:
    axes = yaml.safe_load(AXES_PATH.read_bytes())
    if not isinstance(axes, dict):
        raise ValueError("axes.yaml 顶层必须是对象")
    for key in ("domain_zh", "domain_en", "format", "tone", "label_groups"):
        if not isinstance(axes.get(key), list) or not axes[key]:
            raise ValueError(f"axes.yaml 的 {key} 必须是非空列表")
    checksum = axes.get("checksum_validity")
    if not isinstance(checksum, dict):
        raise ValueError("axes.yaml 缺少 checksum_validity")
    valid_ratio = float(checksum["valid_ratio"])
    invalid_ratio = float(checksum["invalid_ratio"])
    if not 0.0 <= valid_ratio <= 1.0 or abs(valid_ratio + invalid_ratio - 1.0) > 1e-9:
        raise ValueError("checksum_validity 比例非法")
    exclusions = axes.get("tone_tier_exclude")
    if not isinstance(exclusions, dict):
        raise ValueError("axes.yaml 缺少 tone_tier_exclude")
    return axes


def make_sample_id(seed: int, lang: str, tier: str) -> str:
    digest = hashlib.sha256(f"{seed}:{lang}:{tier}".encode("utf-8")).digest()
    sequence = int.from_bytes(digest[:8], "big") % 1_000_000
    return f"syn_{lang}_{TIER_CODES[tier]}_{sequence:06d}"


def sample_axes(
    axes: dict[str, Any], tier: str, lang: str, seed: int
) -> dict[str, Any]:
    rng = random.Random(seed)
    domains = axes["domain_en"] if lang == "en" else axes["domain_zh"]
    groups = axes["label_groups"][-2:] if tier == "短密" else axes["label_groups"]
    if tier == "短密" and (len(groups) != 2 or any(len(group) != 5 for group in groups)):
        raise ValueError("短密档要求 axes.yaml 最后两个 label_group 都恰好包含 5 个标签")

    labels = list(rng.choice(groups))
    if lang == "en":
        labels = ["PASSPORT" if label == "ID_CARD" else label for label in labels]

    tier_code = TIER_CODES[tier]
    exclusions = axes["tone_tier_exclude"]
    tones = [
        tone for tone in axes["tone"] if tier_code not in exclusions.get(tone, [])
    ]
    if not tones:
        raise ValueError(f"档位 {tier} 没有可用 tone")

    return {
        "domain": rng.choice(domains),
        "format": rng.choice(axes["format"]),
        "tone": rng.choice(tones),
        "label_group": labels,
    }


def seed_entities(
    seed: int, lang: str, tier: str, labels: list[str]
) -> dict[str, list[str]]:
    """用独立 slot_rng 给 prompt 注入可复现的人名与机构名。"""
    sample_id = make_sample_id(seed, lang, tier)
    if lang == "mixed":
        locales = ("zh", "en")
    else:
        locales = ("en", "en") if lang == "en" else ("zh", "zh")
    people = []
    orgs = []
    if "PERSON" in labels:
        people = [
            make_person_name(slot_rng(seed, sample_id, f"person_seed:{index}"), locale)
            for index, locale in enumerate(locales)
        ]
    if "ORG" in labels:
        orgs = [
            make_org_name(slot_rng(seed, sample_id, f"org_seed:{index}"), locale)
            for index, locale in enumerate(locales)
        ]
    return {"people": people, "orgs": orgs}


def seed_instruction(seeds: dict[str, list[str]]) -> str:
    parts = []
    if seeds["people"]:
        parts.append("本条中的人物姓名使用：" + "、".join(seeds["people"]) + "。")
    if seeds["orgs"]:
        parts.append("机构名使用：" + "、".join(seeds["orgs"]) + "。")
    return "\n".join(parts)


def build_prompt(
    tier: str,
    lang: str,
    sampled: dict[str, Any],
    kind: str = "positive",
    entity_seeds: dict[str, list[str]] | None = None,
) -> str:
    target = TIER_TARGETS[tier]
    length = target["en"] if lang == "en" else target["zh"]
    minimum, maximum = (0, 0) if kind == "true_negative" else target["entities"]
    locale = {
        "zh": "使用自然中文；姓名、地址和地名采用中文语境。",
        "en": "Write natural English using entities appropriate to an English-language locale.",
        "mixed": "使用自然的中英混合文本，两种语言必须都出现在正文中。",
    }[lang]
    labels = ", ".join(sampled["label_group"])
    placeholders = " ".join(f"{{{{{label}}}}}" for label in STRUCTURED_LABELS)
    sparse = (
        "\n文中要自然地出现若干句子，提到“地址”“电话”“联系人”“邮箱”"
        "这类词，但并不给出具体的个人信息。例如“请把地址发给我”“那个电话"
        "一直没打通”“联系人还没定”。这类句子和真正含个人信息的句子应当"
        "交替出现。"
        if tier == "长"
        else ""
    )
    annotation_rules = (
        """本条不使用任何实体标签，也不得出现 PHONE、EMAIL、ID_CARD、BANK_CARD、PASSPORT 占位符。"""
        if kind == "true_negative"
        else f"""本条可使用的标签：{labels}
label_group 只是允许标签池，不要求每个标签都出现。

ORG 标注可独立识别的公司、学校、医院、政府机关、国际组织或社会组织；含姓氏的店名整体标 ORG，但其中姓氏不标 PERSON。公司内部的产品部、销售部、人事部、财务部、技术部等部门名不得标成 ORG。
机场、车站、产业园、大厦、科技城等命名设施标 LOCATION；若跨度延伸到楼层或门牌则整体标 ADDRESS。小李、张哥、李总、老周、小刘等称谓不标 PERSON，全名必须标 PERSON。

必须用成对的内联标签标注实体，例如 <PERSON>张伟</PERSON>。
PHONE、EMAIL、ID_CARD、BANK_CARD、PASSPORT 的标签内容只能分别写成以下占位符，禁止自行编写号码或邮箱：
{placeholders}
示例：<PHONE>{{{{PHONE}}}}</PHONE>、<BANK_CARD>{{{{BANK_CARD}}}}</BANK_CARD>。"""
    )
    seeds_rule = seed_instruction(entity_seeds or {"people": [], "orgs": []})
    return f"""生成一条用于隐私实体识别的合成文本，只输出正文，不要解释，不要 Markdown 代码块。

场景：{sampled['domain']}
载体：{sampled['format']}
语气：{sampled['tone']}
语言约束：{locale}
长度档：{tier}（{length} 字符）
实体数量：{minimum}–{maximum} 个
{annotation_rules}
{seeds_rule}
不要输出未闭合标签、嵌套标签、列表、标题或 JSON。{sparse}"""


def build_batch_prompt(
    tier: str,
    lang: str,
    sampled: dict[str, Any],
    kind: str,
    sample_count: int,
    seed: int,
    trap_sets: list[list[tuple[str, str]]] | None = None,
) -> str:
    base = build_prompt(tier, lang, sampled, kind)
    if kind == "hard_negative":
        kind_rule = """
样本性质：hard_negative。每条正文必须把后面为该条指定的陷阱串原样、自然地写入正文，不得生硬拼接。店名整体按 ORG 规则标注，但店名中的姓氏不标 PERSON；其他陷阱串不得放进实体标签。正文可以同时包含按规则标注的真实 PII。"""
    elif kind == "true_negative":
        kind_rule = """
样本性质：true_negative。每条正文必须完全不含任何个人信息，不得输出任何实体标签、结构化 PII 占位符或 trap 占位符。"""
    else:
        kind_rule = "\n样本性质：positive。按上述规则生成并标注真实 PII。"
    per_sample = []
    for index in range(sample_count):
        seeds = (
            seed_entities(seed + index, lang, tier, sampled["label_group"])
            if kind != "true_negative" else {"people": [], "orgs": []}
        )
        directives = [f"第 {index + 1} 条：", seed_instruction(seeds)]
        if kind == "hard_negative":
            selected = (trap_sets or [])[index]
            directives.append("正文中要自然地提到：" + "、".join(surface for _, surface in selected) + "。")
        per_sample.append("\n".join(part for part in directives if part))
    return f"""{base}
{kind_rule}

每条的确定性内容要求如下；不得在正文中复述这些要求：
{chr(10).join(per_sample)}

本次必须生成 {sample_count} 条彼此明显不同的正文。严格按以下格式输出，不要添加编号、解释或代码块：
<SAMPLE>
第一条正文
</SAMPLE>
<SAMPLE>
第二条正文
</SAMPLE>
依此类推，必须恰好输出 {sample_count} 个 SAMPLE 块。"""


def fake_response(
    lang: str, sampled: dict[str, Any], tier: str, seed: int
) -> str:
    seeds = seed_entities(seed, lang, tier, sampled["label_group"])
    values = {
        "zh": {
            "PERSON": seeds["people"][0] if seeds["people"] else "张伟",
            "ORG": seeds["orgs"][0] if seeds["orgs"] else "明远科技有限公司",
            "LOCATION": "杭州",
            "ADDRESS": "杭州市西湖区文三路88号",
        },
        "en": {
            "PERSON": seeds["people"][0] if seeds["people"] else "Alex Chen",
            "ORG": seeds["orgs"][0] if seeds["orgs"] else "Northwind Analytics",
            "LOCATION": "Seattle",
            "ADDRESS": "120 Pine Street, Seattle",
        },
        "mixed": {
            "PERSON": seeds["people"][0] if seeds["people"] else "张伟",
            "ORG": seeds["orgs"][0] if seeds["orgs"] else "Northwind 杭州团队",
            "LOCATION": "Hangzhou",
            "ADDRESS": "杭州市西湖区文三路88号",
        },
    }[lang]
    _, maximum = TIER_TARGETS[tier]["entities"]
    active_labels = sampled["label_group"][:maximum]
    parts = []
    for label in active_labels:
        value = f"{{{{{label}}}}}" if label in STRUCTURED_LABELS else values[label]
        parts.append(f"{label}=<{label}>{value}</{label}>")
    if lang == "en":
        return "Dry-run sample: " + "; ".join(parts) + "."
    if lang == "mixed":
        return "Dry-run mixed sample: 测试记录；" + "；".join(parts) + "。"
    return "测试记录：" + "；".join(parts) + "。"


def fake_batch_response(
    tier: str,
    lang: str,
    sampled: dict[str, Any],
    kind: str,
    sample_count: int,
    seed: int,
    trap_sets: list[list[tuple[str, str]]] | None = None,
) -> str:
    target_chars = {
        "短密": {"zh": 110, "en": 260},
        "短": {"zh": 110, "en": 260},
        "中": {"zh": 450, "en": 820},
        "长": {"zh": 1650, "en": 1650},
    }[tier]["en" if lang == "en" else "zh"]
    entity_count = {"短密": 4, "短": 2, "中": 3, "长": 2}[tier]
    blocks: list[str] = []
    for index in range(sample_count):
        rng = random.Random(seed * 1009 + index)
        seeds = seed_entities(seed + index, lang, tier, sampled["label_group"])
        values = {
            "PERSON": seeds["people"][0] if seeds["people"] else ("Alex Chen" if lang == "en" else "张伟"),
            "ORG": seeds["orgs"][0] if seeds["orgs"] else ("Northwind Analytics" if lang == "en" else "明远科技有限公司"),
            "LOCATION": "Seattle" if lang == "en" else ("Hangzhou" if lang == "mixed" else "杭州"),
            "ADDRESS": "120 Pine Street, Seattle" if lang == "en" else "杭州市西湖区文三路88号",
        }
        prefix_parts: list[str] = []
        if kind != "true_negative":
            for label in sampled["label_group"][:entity_count]:
                value = f"{{{{{label}}}}}" if label in STRUCTURED_LABELS else values[label]
                prefix_parts.append(f"<{label}>{value}</{label}>")
        if kind == "hard_negative":
            for trap_type, surface in (trap_sets or [])[index]:
                if trap_type == "店名中的姓氏不是 PERSON" and "ORG" in sampled["label_group"]:
                    prefix_parts.append(f"<ORG>{surface}</ORG>")
                else:
                    prefix_parts.append(surface)
        prefix = " ".join(prefix_parts)
        if lang == "en":
            alphabet = "abcdefghijklmnopqrstuvwxyz"
            filler = " ".join(
                "".join(rng.choice(alphabet) for _ in range(9))
                for _ in range(target_chars // 10 + 8)
            )
        elif lang == "mixed":
            alphabet = "数据隐私系统审计记录流程安全服务"
            filler = "".join(rng.choice(alphabet) for _ in range(target_chars))
            filler = "mixed dry run " + filler
        else:
            alphabet = "数据隐私系统审计记录流程安全服务"
            filler = "".join(rng.choice(alphabet) for _ in range(target_chars))
        body = (prefix + " " + filler).strip()
        blocks.append(f"<SAMPLE>\n{body}\n</SAMPLE>")
    return "\n".join(blocks)


def call_dashscope_with_usage(model: str, prompt: str) -> tuple[str, dict[str, int]]:
    response = httpx.post(
        DASHSCOPE_CHAT_COMPLETIONS,
        headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 1.0,
        },
        timeout=180.0,
    )
    if not response.is_success:
        print("=== API 原始响应 ===")
        print(response.text)
        response.raise_for_status()
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        print("=== API 原始响应 ===")
        print(response.text)
        raise
    if not isinstance(content, str) or not content:
        print("=== API 原始响应 ===")
        print(response.text)
        raise ValueError("API 未返回非空文本 content")
    payload = response.json()
    usage = payload.get("usage") or {}
    return content, {
        "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage.get("completion_tokens", 0) or 0),
    }


def call_dashscope(model: str, prompt: str) -> str:
    content, _ = call_dashscope_with_usage(model, prompt)
    return content


def parse_batch_response(raw: str, expected_count: int) -> list[str]:
    matches = list(SAMPLE_BLOCK_RE.finditer(raw))
    residual = SAMPLE_BLOCK_RE.sub("", raw).strip()
    if residual:
        raise ParseError(f"SAMPLE 块外存在多余内容: {residual[:120]!r}")
    if len(matches) != expected_count:
        raise ParseError(
            f"SAMPLE 块数量 {len(matches)}，期望 {expected_count}"
        )
    samples = [match.group(1).strip() for match in matches]
    if any(not sample for sample in samples):
        raise ParseError("SAMPLE 块正文不能为空")
    return samples


def replace_structured_placeholders(
    raw: str, seed: int, sample_id: str, checksum_valid: bool, lang: str
) -> str:
    structured_tags = list(STRUCTURED_TAG_RE.finditer(raw))
    tagged_tokens = []
    for match in structured_tags:
        label, value = match.group(1), match.group(2)
        expected = f"{{{{{label}}}}}"
        if value != expected:
            raise ParseError(f"结构化标签 {label} 必须只包含占位符 {expected}")
        tagged_tokens.append(label)

    all_tokens = PLACEHOLDER_RE.findall(raw)
    if all_tokens != tagged_tokens:
        raise ParseError("占位符必须位于同类型的结构化标签内部")

    counters: defaultdict[str, int] = defaultdict(int)

    def replacement(match: re.Match[str]) -> str:
        label = match.group(1)
        index = counters[label]
        counters[label] += 1
        rng = slot_rng(seed, sample_id, f"{label}:{index}")
        if lang == "mixed":
            locale = "zh" if rng.random() < 0.70 else "en"
        else:
            locale = "en" if lang == "en" else "zh"
        if label == "PHONE":
            return make_phone(rng, locale)
        if label == "EMAIL":
            return make_email(rng)
        if label == "ID_CARD":
            return make_id_card(rng, valid=checksum_valid)
        if label == "BANK_CARD":
            return make_bank_card(rng, valid=checksum_valid, locale=locale)
        if label == "PASSPORT":
            return make_passport(rng, locale)
        raise ParseError(f"未知占位符: {label}")

    replaced = PLACEHOLDER_RE.sub(replacement, raw)
    if PLACEHOLDER_RE.search(replaced):
        raise ParseError("替换后仍残留占位符")
    return replaced


def flatten_traps(axes: dict[str, Any]) -> list[tuple[str, str]]:
    traps = axes.get("traps")
    if not isinstance(traps, dict) or not traps:
        raise ValueError("axes.yaml 的 traps 必须是非空对象")
    flattened: list[tuple[str, str]] = []
    for trap_type, surfaces in traps.items():
        if not isinstance(surfaces, list) or not surfaces:
            raise ValueError(f"trap {trap_type} 必须是非空列表")
        for surface in surfaces:
            if not isinstance(surface, str) or not surface:
                raise ValueError(f"trap {trap_type} 含非法字符串")
            flattened.append((str(trap_type), surface))
    return flattened


def select_traps(
    axes: dict[str, Any], seed: int, count: int = 2
) -> list[tuple[str, str]]:
    flattened = flatten_traps(axes)
    if len(flattened) < count:
        raise ValueError(f"traps 总数不足 {count}")
    return random.Random(seed).sample(flattened, count)


def build_trap_spans(
    text: str,
    selected: list[tuple[str, str]],
    spans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trap_spans: list[dict[str, Any]] = []
    for trap_type, surface in selected:
        positions = find_all(text, surface)
        if not positions:
            raise ParseError(f"模型未在正文中写入指定 trap: {surface!r}")
        for start, end in positions:
            overlaps = [span for span in spans if not (end <= span["start"] or span["end"] <= start)]
            if trap_type == "店名中的姓氏不是 PERSON":
                if any(span["label"] == "PERSON" for span in overlaps):
                    raise ParseError(f"店名中的姓氏被标成 PERSON: {surface!r} [{start}:{end}]")
                if any(span["label"] != "ORG" for span in overlaps):
                    raise ParseError(f"店名 trap 与非 ORG span 重叠: {surface!r} [{start}:{end}]")
            elif overlaps:
                raise ParseError(f"trap 被实体 span 覆盖: {surface!r} [{start}:{end}]")
            trap_spans.append(
                {"start": start, "end": end, "text": surface, "trap": trap_type}
            )
    trap_spans.sort(key=lambda item: (item["start"], item["end"], item["trap"]))
    return trap_spans


def prompt_leakage_hits(text: str, spans: list[dict[str, Any]]) -> list[str]:
    """确定性检查正文是否复述生成/标注指令；实体 span 内容不检查。"""
    def covered(start: int, end: int) -> bool:
        return any(span["start"] <= start and end <= span["end"] for span in spans)

    hits: list[str] = []
    for name, pattern in (
        ("标签名裸露", LABEL_LEAK_RE),
        ("元话语", META_LEAK_RE),
        ("计数自述", ENTITY_COUNT_LEAK_RE),
    ):
        for match in pattern.finditer(text):
            if not covered(match.start(), match.end()):
                hits.append(f"{name}:{match.group(0)!r}@{match.start()}")
    return hits


def reject_prompt_leakage(text: str, spans: list[dict[str, Any]]) -> None:
    hits = prompt_leakage_hits(text, spans)
    if hits:
        raise ParseError("提示词泄漏: " + "; ".join(hits))


def substring_leak_warnings(
    text: str, spans: list[dict[str, Any]]
) -> list[str]:
    """只提示 PERSON/ORG/LOCATION 的前缀在其他未覆盖位置出现，不自动补标。"""
    occupied = [(span["start"], span["end"]) for span in spans]
    found: dict[tuple[str, int], str] = {}
    for span in spans:
        if span["label"] not in {"PERSON", "ORG", "LOCATION"}:
            continue
        surface = text[span["start"]:span["end"]]
        for length in range(2, len(surface)):
            prefix = surface[:length]
            for start, end in find_all(text, prefix, latin_boundary=False):
                if any(not (end <= left or right <= start) for left, right in occupied):
                    continue
                found[(prefix, start)] = (
                    f"⚠ 可能的子串泄漏：『{prefix}』出现在位置 {start}，未被覆盖"
                )
    # 同一位置只保留最长前缀，减少人工清单噪声。
    by_position: dict[int, tuple[str, str]] = {}
    for (prefix, start), warning in found.items():
        if start not in by_position or len(prefix) > len(by_position[start][0]):
            by_position[start] = (prefix, warning)
    return [by_position[start][1] for start in sorted(by_position)]


def classify_tier(lang: str, character_count: int, entity_count: int) -> str:
    """规格「长度分档」表 B：按实测长度与实体数归类或丢弃。"""
    character_maximum = 7500 if lang == "en" else 4000
    if character_count < 40 or character_count > character_maximum:
        raise ValueError(
            f"字符数 {character_count} 超出 {lang} 允许区间 [40, {character_maximum}]"
        )
    short_upper = 450 if lang == "en" else 200
    if character_count <= short_upper:
        tier = "短密" if entity_count >= 4 else "短"
    elif character_count <= 1199:
        tier = "中"
    else:
        tier = "长"
    density = entity_count * 1000 / character_count
    if density > MAX_ENTITY_DENSITY_PER_THOUSAND:
        raise ValueError(
            f"实体密度 {density:.2f} 个/千字符超出上限 "
            f"{MAX_ENTITY_DENSITY_PER_THOUSAND:.0f}"
        )
    if entity_count > MAX_ENTITY_COUNT:
        raise ValueError(f"实体绝对数 {entity_count} 超出上限 {MAX_ENTITY_COUNT}")
    return tier


def sweep_structured_pii(
    text: str,
    spans: list[dict[str, Any]],
    ignored_spans: list[dict[str, Any]] | None = None,
) -> None:
    backend_path = str(FINETUNE_DIR.parent / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from app.recognizers import PATTERNS

    misses: set[tuple[int, int, str, str]] = set()
    for spec in PATTERNS:
        label = spec.entity_type.value
        if label not in STRUCTURED_LABELS:
            continue
        for match in spec.regex.finditer(text):
            # BANK_CARD 的可选分隔符会吞掉号码后的空白；空白不是实体表面串。
            surface = match.group(0).rstrip()
            start = match.start(0)
            end = start + len(surface)
            if spec.validator and not spec.validator(surface):
                continue
            covered = any(
                span["start"] <= start and end <= span["end"] for span in spans
            )
            ignored = any(
                span["start"] <= start and end <= span["end"]
                for span in (ignored_spans or [])
            )
            if not covered and not ignored:
                misses.add((start, end, label, surface))
    if misses:
        details = [
            {"start": start, "end": end, "label": label, "text": surface}
            for start, end, label, surface in sorted(misses)
        ]
        raise ParseError(
            "SWEEP 命中未标注的结构化 PII: "
            + json.dumps(details, ensure_ascii=False)
        )


def build_gold(
    lang: str,
    model: str,
    seed: int,
    batch: str,
    kind: str,
    sampled: dict[str, Any],
    sample_id: str,
    classified_tier: str,
    text: str,
    spans: list[dict[str, Any]],
    trap_spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": sample_id,
        "lang": lang,
        "text": text,
        "spans": spans,
        "trap_spans": trap_spans or [],
        "kind": kind,
        "meta": {
            "domain": sampled["domain"],
            "format": sampled["format"],
            "tone": sampled["tone"],
            "tier": classified_tier,
            "label_group": sampled["label_group"],
        },
        "gen": {
            "model": model,
            "batch": batch,
            "seed": seed,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        },
    }


def process_generated_sample(
    raw: str,
    *,
    axes: dict[str, Any],
    requested_tier: str,
    lang: str,
    model: str,
    seed: int,
    batch: str,
    kind: str,
    sampled: dict[str, Any],
    force_checksum_valid: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_id = make_sample_id(seed, lang, requested_tier)
    checksum_valid = force_checksum_valid
    if checksum_valid is None:
        checksum_valid = (
            slot_rng(seed, requested_id, "checksum_validity").random()
            < float(axes["checksum_validity"]["valid_ratio"])
        )
    selected_traps = select_traps(axes, seed) if kind == "hard_negative" else []
    replaced = replace_structured_placeholders(
        raw, seed, requested_id, checksum_valid, lang
    )
    allowed = tuple(sampled["label_group"]) if kind != "true_negative" else ()
    text, tagged_spans = parse_inline_tagged(replaced, allowed=allowed)
    spans = expand_spans(text, tagged_spans)
    reject_prompt_leakage(text, spans)
    if kind == "true_negative" and spans:
        raise ParseError("true_negative 的 spans 必须为空")
    trap_spans = build_trap_spans(text, selected_traps, spans)
    sweep_structured_pii(text, spans, ignored_spans=trap_spans)
    classified_tier = classify_tier(lang, len(text), len(spans))
    sample_id = make_sample_id(seed, lang, classified_tier)
    gold = build_gold(
        lang,
        model,
        seed,
        batch,
        kind,
        sampled,
        sample_id,
        classified_tier,
        text,
        spans,
        trap_spans,
    )
    return gold, {
        "requested_tier": requested_tier,
        "classified_tier": classified_tier,
        "tagged_span_count": len(tagged_spans),
        "expanded_span_count": len(spans),
        "checksum_valid": checksum_valid,
    }


def quota_key(lang: str, tier: str, kind: str) -> str:
    return f"{lang}|{TIER_CODES[tier]}|{kind}"


def build_quotas(total: int) -> dict[str, int]:
    def apportioned(weights: dict[str, float]) -> list[str]:
        rows: list[tuple[str, float, int]] = []
        assigned = 0
        for name, weight in weights.items():
            exact = total * weight
            base = int(exact)
            rows.append((name, exact - base, base))
            assigned += base
        counts = {name: base for name, _, base in rows}
        for name, _, _ in sorted(rows, key=lambda row: (-row[1], row[0]))[
            : total - assigned
        ]:
            counts[name] += 1
        values = [name for name in weights for _ in range(counts[name])]
        return values

    langs = apportioned(LANG_WEIGHTS)
    tiers = apportioned(TIER_WEIGHTS)
    kinds = apportioned(KIND_WEIGHTS)
    random.Random(71001).shuffle(langs)
    random.Random(71002).shuffle(tiers)
    random.Random(71003).shuffle(kinds)
    quotas = {
        quota_key(lang, tier, kind): 0
        for lang in LANG_WEIGHTS
        for tier in TIER_WEIGHTS
        for kind in KIND_WEIGHTS
    }
    assignments = list(zip(langs, tiers, kinds, strict=True))
    # true_negative 必须为 0 实体，按表 B 不可能归类为短密；在同语种内交换
    # kind，保持语种、档位、kind 三个边际配额完全不变。
    for bad_index, (lang, tier, kind) in enumerate(assignments):
        if tier != "短密" or kind != "true_negative":
            continue
        replacement = next(
            index
            for index, (other_lang, other_tier, other_kind) in enumerate(assignments)
            if other_lang == lang
            and other_tier != "短密"
            and other_kind != "true_negative"
        )
        other_lang, other_tier, other_kind = assignments[replacement]
        assignments[bad_index] = (lang, tier, other_kind)
        assignments[replacement] = (other_lang, other_tier, kind)
    for lang, tier, kind in assignments:
        quotas[quota_key(lang, tier, kind)] += 1
    return quotas


def decode_quota_key(key: str) -> tuple[str, str, str]:
    lang, tier_code, kind = key.split("|", 2)
    tier = next(name for name, code in TIER_CODES.items() if code == tier_code)
    return lang, tier, kind


def model_quota_key(model: str, cell_key: str) -> str:
    return f"{model}||{cell_key}"


def decode_model_quota_key(key: str) -> tuple[str, str, str, str]:
    model, cell_key = key.split("||", 1)
    lang, tier, kind = decode_quota_key(cell_key)
    return model, lang, tier, kind


def build_model_quotas(
    quotas: dict[str, int], allocations: dict[str, int], seed: int
) -> dict[str, int]:
    cells = [key for key, count in quotas.items() for _ in range(count)]
    if len(cells) != sum(allocations.values()):
        raise ValueError("模型条数与全局配额总数不一致")
    model_names = list(allocations)
    rng = random.Random(seed)
    for _ in range(100_000):
        shuffled = cells.copy()
        rng.shuffle(shuffled)
        chunks: dict[str, list[str]] = {}
        offset = 0
        for model in model_names:
            count = allocations[model]
            chunks[model] = shuffled[offset : offset + count]
            offset += count
        complete = True
        for model_cells in chunks.values():
            decoded = [decode_quota_key(key) for key in model_cells]
            if (
                {lang for lang, _, _ in decoded} != set(LANG_WEIGHTS)
                or {tier for _, tier, _ in decoded} != set(TIER_WEIGHTS)
                or {kind for _, _, kind in decoded} != set(KIND_WEIGHTS)
            ):
                complete = False
                break
        if complete:
            result = {
                model_quota_key(model, cell): 0
                for model in model_names
                for cell in quotas
            }
            for model, model_cells in chunks.items():
                for cell in model_cells:
                    result[model_quota_key(model, cell)] += 1
            return result
    raise RuntimeError("无法在 100000 次确定性轮转内满足三模型覆盖约束")


def choose_model_deficit(
    model_quotas: dict[str, int], completed: dict[str, int]
) -> tuple[str, str, str, str] | None:
    deficits = [
        (target - completed.get(key, 0), key)
        for key, target in model_quotas.items()
        if target > completed.get(key, 0)
    ]
    if not deficits:
        return None
    _, key = max(deficits, key=lambda item: (item[0], item[1]))
    return decode_model_quota_key(key)


def char_ngrams(text: str, n: int = 4) -> set[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if len(normalized) < n:
        return {normalized} if normalized else set()
    return {normalized[index : index + n] for index in range(len(normalized) - n + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    existing = path.read_bytes() if path.exists() else b""
    payload = b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for record in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(existing + payload)


def write_json(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path} 第 {line_number} 行不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path} 第 {line_number} 行不是对象")
        records.append(value)
    return records


def error_signature(exc: BaseException) -> str:
    normalized = re.sub(r"\d+", "#", str(exc))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return f"{type(exc).__name__}: {normalized}"[:500]


def initial_checkpoint(
    args: argparse.Namespace,
    quotas: dict[str, int],
    model_quotas: dict[str, int],
) -> dict[str, Any]:
    return {
        "version": 3,
        "batch_id": args.batch_id,
        "models": args.models,
        "base_seed": args.seed,
        "target_total": args.pilot,
        "quotas": quotas,
        "base_model_quotas": model_quotas,
        "model_quotas": model_quotas.copy(),
        "completed": {key: 0 for key in model_quotas},
        "call_count": 0,
        "next_seed": args.seed,
        "elapsed_seconds": 0.0,
        "cell_failures": {},
        "cell_schedule_misses": {},
        "exhausted_cells": [],
        "unavailable_models": {},
        "model_network_failures": {model: 0 for model in args.models},
        "api_attempt_count": 0,
        "rejections": {},
    }


def load_checkpoint(
    path: Path,
    args: argparse.Namespace,
    quotas: dict[str, int],
    model_quotas: dict[str, int],
) -> dict[str, Any]:
    if not path.exists():
        return initial_checkpoint(args, quotas, model_quotas)
    checkpoint = json.loads(path.read_bytes())
    if checkpoint.get("version") == 2:
        # v2 把网络错误和“合格但落入满桶”都算质量失败；这些计数不可沿用。
        checkpoint["version"] = 3
        checkpoint["base_model_quotas"] = checkpoint["model_quotas"].copy()
        checkpoint["cell_failures"] = {}
        checkpoint["cell_schedule_misses"] = {}
        checkpoint["exhausted_cells"] = []
        checkpoint["unavailable_models"] = {}
        checkpoint["model_network_failures"] = {
            model: 0 for model in checkpoint["models"]
        }
        checkpoint["api_attempt_count"] = int(checkpoint.get("call_count", 0))
        checkpoint["rejections"] = {}
        checkpoint.pop("consecutive_error_signature", None)
        checkpoint.pop("consecutive_error_count", None)
    checkpoint.setdefault("cell_schedule_misses", {})
    checkpoint.setdefault("exhausted_cells", [])
    checkpoint.setdefault("unavailable_models", {})
    checkpoint.setdefault("model_network_failures", {model: 0 for model in args.models})
    checkpoint.setdefault("api_attempt_count", int(checkpoint.get("call_count", 0)))
    checkpoint.setdefault("rejections", {})
    expected = {
        "batch_id": args.batch_id,
        "models": args.models,
        "base_seed": args.seed,
        "target_total": args.pilot,
        "quotas": quotas,
        "base_model_quotas": model_quotas,
    }
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(
                f"checkpoint 的 {key}={checkpoint.get(key)!r} 与本次 {value!r} 不一致"
            )
    return checkpoint


def reconcile_checkpoint_with_gold(
    checkpoint: dict[str, Any], gold_records: list[dict[str, Any]]
) -> tuple[dict[str, int], set[str], set[str]]:
    completed = {key: 0 for key in checkpoint["model_quotas"]}
    texts: set[str] = set()
    ids: set[str] = set()
    for record in gold_records:
        cell_key = quota_key(record["lang"], record["meta"]["tier"], record["kind"])
        key = model_quota_key(record["gen"]["model"], cell_key)
        if key not in completed:
            raise ValueError(f"pilot gold 含不属于本 checkpoint 的配额键: {key}")
        completed[key] += 1
        soft_cap = math.ceil(checkpoint["model_quotas"][key] * SOFT_QUOTA_FACTOR)
        if completed[key] > soft_cap:
            raise ValueError(f"pilot gold 的配额 {key} 已超出 125% 软上限")
        if record["id"] in ids:
            raise ValueError(f"pilot gold 含重复 id: {record['id']}")
        ids.add(record["id"])
        texts.add(record["text"])
    checkpoint["completed"] = completed
    return completed, texts, ids


def collect_existing_batch_identity() -> tuple[set[str], set[str]]:
    texts: set[str] = set()
    ids: set[str] = set()
    directories = (INTERIM_DIR, FINETUNE_DIR / "data" / "processed")
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            if path == CALLS_PATH or path.stem.endswith("_dryrun"):
                continue
            for record in read_jsonl(path):
                text = record.get("text")
                if isinstance(text, str):
                    texts.add(text)
                sample_id = record.get("id")
                if isinstance(sample_id, str):
                    ids.add(sample_id)
    return texts, ids


REVIEW_HEADER = """# pilot 60 条人工审阅

## 看什么（按重要性）
1. **文中出现了人名/机构名/地名，却没有被【】标出** ← 最重要。
   SWEEP 对这三类的召回只有 3.4%，程序抓不到，只能靠人眼。
2. 标了但不该标：部门名（产品部/技术部）、公众人物、店名中的姓氏被另标 PERSON。
3. 文本读起来假不假：模板感、逻辑矛盾、为凑实体而堆砌。

## 怎么记
在每条下面的「问题」行直接写。最后填汇总表。
"""


def review_annotations(record: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for span in record["spans"]:
        grouped[(span["start"], span["end"])] = {
            "start": span["start"], "end": span["end"],
            "open": "【", "suffix": f"|{span['label']}】",
        }
    for trap in record["trap_spans"]:
        key = (trap["start"], trap["end"])
        if key in grouped:
            annotation = grouped[key]
            if annotation["suffix"] != "|ORG】" or trap["trap"] != "店名中的姓氏不是 PERSON":
                raise ValueError(f"review 非法重合注解: {key}")
            annotation["open"] = "【⟪"
            annotation["suffix"] = f"|{trap['trap']}⟫|ORG】"
        else:
            grouped[key] = {
                "start": trap["start"], "end": trap["end"],
                "open": "⟪", "suffix": f"|{trap['trap']}⟫",
            }
    annotations = list(grouped.values())
    annotations.sort(key=lambda item: (item["start"], item["end"]))
    for left, right in zip(annotations, annotations[1:]):
        if left["end"] > right["start"]:
            raise ValueError(
                f"review 注解重叠: [{left['start']}:{left['end']}] / "
                f"[{right['start']}:{right['end']}]"
            )
    return annotations


def render_annotated_range(
    text: str,
    annotations: list[dict[str, Any]],
    start: int,
    end: int,
) -> str:
    parts: list[str] = []
    cursor = start
    for item in annotations:
        if item["end"] <= start or item["start"] >= end:
            continue
        if item["start"] < start or item["end"] > end:
            raise ValueError("review 截断边界切开了 span 或 trap")
        parts.append(text[cursor : item["start"]])
        parts.append(item["open"])
        parts.append(text[item["start"] : item["end"]])
        parts.append(item["suffix"])
        cursor = item["end"]
    parts.append(text[cursor:end])
    return "".join(parts)


def render_review_text(record: dict[str, Any]) -> str:
    text = record["text"]
    annotations = review_annotations(record)
    if record["meta"]["tier"] != "长" or len(text) <= 1200:
        return render_annotated_range(text, annotations, 0, len(text))

    head_end = 800
    tail_start = len(text) - 400
    for item in annotations:
        if item["start"] < head_end < item["end"]:
            head_end = item["end"]
        if item["start"] < tail_start < item["end"]:
            tail_start = item["start"]
    if head_end >= tail_start:
        return render_annotated_range(text, annotations, 0, len(text))
    omitted_entities = sum(
        head_end <= span["start"] and span["end"] <= tail_start
        for span in record["spans"]
    )
    omitted_chars = tail_start - head_end
    head = render_annotated_range(text, annotations, 0, head_end)
    tail = render_annotated_range(text, annotations, tail_start, len(text))
    marker = f"〔…省略 {omitted_chars} 字符，其中含 {omitted_entities} 个已标实体…〕"
    return f"{head}\n\n{marker}\n\n{tail}"


def write_review_markdown(path: Path, records: list[dict[str, Any]]) -> None:
    sections = [
        REVIEW_HEADER,
        "\n> 长档仅展示前 800 + 后 400 字符；中段漏标不会被本清单覆盖，"
        "这是为控制人工审阅时间所作的取舍。\n",
    ]
    for index, record in enumerate(records, start=1):
        text = record["text"]
        entity_count = len(record["spans"])
        density = entity_count * 1000 / len(text)
        warnings = substring_leak_warnings(text, record["spans"])
        warning_block = "\n".join(warnings) + "\n\n" if warnings else ""
        sections.append(
            f"\n### {index} · {record['id']} · {record['gen']['model']} · "
            f"{record['lang']}/{record['meta']['tier']} · {record['kind']}\n"
            f"{len(text)} 字 / {entity_count} 实体 / {density:.2f} 千分比\n\n"
            f"{render_review_text(record)}\n\n"
            f"{warning_block}问题：\n\n---\n"
        )
    sections.append(
        "\n## 汇总（人工填写）\n"
        "| 模型 | 漏标次数 | 误标次数 | 自然度问题 |\n"
        "|---|---|---|---|\n"
        "| deepseek-v4-pro | | | |\n"
        "| glm-5.2 | | | |\n"
        "| qwen3.7-plus | | | |\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(sections).encode("utf-8"))


def soft_quota_cap(target: int) -> int:
    return math.ceil(target * SOFT_QUOTA_FACTOR) if target else 0


def is_quality_error(exc: BaseException) -> bool:
    message = str(exc)
    return isinstance(exc, ParseError) or any(
        marker in message
        for marker in ("实体密度", "实体绝对数", "API 未返回非空文本 content")
    )


def classify_api_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text.lower()
        if status in (401, 403):
            return "auth"
        if status == 402 or any(word in body for word in ("balance", "quota exhausted", "余额不足")):
            return "balance"
        if status == 429 or status >= 500:
            return "network"
        return "fatal"
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return "network"
    if isinstance(exc, ValueError) and "API 未返回非空文本 content" in str(exc):
        return "quality"
    return "fatal"


def request_batch(
    *,
    model: str,
    prompt: str,
    dry_run: bool,
    requested_tier: str,
    lang: str,
    sampled: dict[str, Any],
    kind: str,
    sample_count: int,
    seed: int,
    trap_sets: list[list[tuple[str, str]]],
    stop_event: threading.Event,
) -> dict[str, Any]:
    started = time.perf_counter()
    if dry_run:
        return {
            "raw": fake_batch_response(
                requested_tier, lang, sampled, kind, sample_count, seed, trap_sets
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "network_attempts": 0,
            "error": None,
            "error_class": None,
            "latency": time.perf_counter() - started,
        }
    network_attempts = 0
    while True:
        if stop_event.is_set():
            return {"raw": "", "usage": {"input_tokens": 0, "output_tokens": 0}, "network_attempts": network_attempts, "error": "全局已停止，取消网络重试", "error_class": "cancelled", "latency": time.perf_counter() - started}
        try:
            raw, usage = call_dashscope_with_usage(model, prompt)
            return {
                "raw": raw,
                "usage": usage,
                "network_attempts": network_attempts + 1,
                "error": None,
                "error_class": None,
                "latency": time.perf_counter() - started,
            }
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            category = classify_api_error(exc)
            network_attempts += 1
            if category != "network" or network_attempts > len(NETWORK_BACKOFF_SECONDS):
                return {
                    "raw": "",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "network_attempts": network_attempts,
                    "error": f"{type(exc).__name__}: {exc}",
                    "error_class": category,
                    "latency": time.perf_counter() - started,
                }
            delay = NETWORK_BACKOFF_SECONDS[network_attempts - 1]
            print(f"{model} 网络错误，第 {network_attempts} 次失败，{delay} 秒后重试：{type(exc).__name__}: {exc}")
            time.sleep(delay)


def choose_model_target(
    model: str,
    model_quotas: dict[str, int],
    completed: dict[str, int],
    exhausted: set[str],
) -> tuple[str, str, str, str] | None:
    choices = []
    prefix = f"{model}||"
    covered = [
        decode_model_quota_key(key)
        for key, count in completed.items()
        if key.startswith(prefix) and count > 0
    ]
    covered_langs = {lang for _, lang, _, _ in covered}
    covered_tiers = {tier for _, _, tier, _ in covered}
    covered_kinds = {kind for _, _, _, kind in covered}
    for key, target in model_quotas.items():
        if not key.startswith(prefix) or key in exhausted:
            continue
        deficit = target - completed.get(key, 0)
        if deficit > 0:
            _, lang, tier, kind = decode_model_quota_key(key)
            coverage_priority = sum(
                (lang not in covered_langs, tier not in covered_tiers, kind not in covered_kinds)
            )
            choices.append((coverage_priority, deficit, key))
    if not choices:
        return None
    return decode_model_quota_key(max(choices, key=lambda item: (item[0], item[1], item[2]))[2])


def redistribute_model_quotas(
    failed_model: str,
    model_quotas: dict[str, int],
    completed: dict[str, int],
    allocations: dict[str, int],
    unavailable: set[str],
) -> None:
    active = [model for model in allocations if model not in unavailable]
    for cell in sorted({key.split("||", 1)[1] for key in model_quotas}):
        failed_key = model_quota_key(failed_model, cell)
        remaining = max(0, model_quotas[failed_key] - completed.get(failed_key, 0))
        model_quotas[failed_key] = completed.get(failed_key, 0)
        if not active or not remaining:
            continue
        total_weight = sum(allocations[model] for model in active)
        exact = {model: remaining * allocations[model] / total_weight for model in active}
        assigned = {model: int(exact[model]) for model in active}
        for model in sorted(active, key=lambda name: (-(exact[name] - assigned[name]), name))[
            : remaining - sum(assigned.values())
        ]:
            assigned[model] += 1
        for model, count in assigned.items():
            model_quotas[model_quota_key(model, cell)] += count


def format_elapsed(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def print_record_full(record: dict[str, Any], requested_tier: str) -> None:
    print(f"=== 样本 {record['id']} · 请求档 {requested_tier} → 归类档 {record['meta']['tier']} ===")
    print(record["text"])
    print("start\tend\tlabel\ttext[start:end]")
    for span in record["spans"]:
        print(f"{span['start']}\t{span['end']}\t{span['label']}\t{record['text'][span['start']:span['end']]}")


def distribution_tables(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model_x_lang": {f"{model}|{lang}": count for (model, lang), count in sorted(Counter((r["gen"]["model"], r["lang"]) for r in records).items())},
        "model_x_tier": {f"{model}|{tier}": count for (model, tier), count in sorted(Counter((r["gen"]["model"], r["meta"]["tier"]) for r in records).items())},
        "model_x_kind": {f"{model}|{kind}": count for (model, kind), count in sorted(Counter((r["gen"]["model"], r["kind"]) for r in records).items())},
        "lang": dict(sorted(Counter(r["lang"] for r in records).items())),
        "tier": dict(sorted(Counter(r["meta"]["tier"] for r in records).items())),
        "kind": dict(sorted(Counter(r["kind"] for r in records).items())),
    }


def persist_checkpoint(path: Path, checkpoint: dict[str, Any], lock: threading.Lock) -> None:
    with lock:
        write_json(path, checkpoint)


def run_status(args: argparse.Namespace) -> None:
    checkpoint_path = INTERIM_DIR / f"{args.batch_id}.checkpoint.json"
    output_path = INTERIM_DIR / f"{args.batch_id}.jsonl"
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"checkpoint 不存在: {checkpoint_path}")
    checkpoint = json.loads(checkpoint_path.read_bytes())
    records = read_jsonl(output_path)
    completed = Counter()
    for record in records:
        completed[model_quota_key(record["gen"]["model"], quota_key(record["lang"], record["meta"]["tier"], record["kind"]))] += 1
    gaps = {
        key: max(0, target - completed[key])
        for key, target in checkpoint["model_quotas"].items()
        if target > completed[key]
    }
    print(json.dumps({"batch": args.batch_id, "total": len(records), "target": checkpoint["target_total"], "gaps": gaps, "unavailable_models": checkpoint.get("unavailable_models", {}), "distributions": distribution_tables(records)}, ensure_ascii=False, indent=2))


def run_pilot(args: argparse.Namespace) -> None:
    axes = load_axes()
    quotas = build_quotas(args.pilot)
    base_model_quotas = build_model_quotas(quotas, args.models, args.seed)
    output_path = INTERIM_DIR / f"{args.batch_id}.jsonl"
    checkpoint_path = INTERIM_DIR / f"{args.batch_id}.checkpoint.json"
    review_path = INTERIM_DIR / f"{args.batch_id}_review.md"
    checkpoint = load_checkpoint(checkpoint_path, args, quotas, base_model_quotas)
    model_quotas = checkpoint["model_quotas"]
    records = read_jsonl(output_path)
    completed, existing_texts, existing_ids = reconcile_checkpoint_with_gold(checkpoint, records)
    all_texts, all_ids = collect_existing_batch_identity()
    existing_texts |= all_texts
    existing_ids |= all_ids
    exhausted = set(checkpoint.get("exhausted_cells", []))
    unavailable = set(checkpoint.get("unavailable_models", {}))
    lock = threading.Lock()
    elapsed_before = float(checkpoint.get("elapsed_seconds", 0.0))
    started = time.perf_counter()
    stop_reason: str | None = None
    stop_event = threading.Event()

    persist_checkpoint(checkpoint_path, checkpoint, lock)
    for record in records[:3]:
        print_record_full(record, "续跑前已保存")

    def elapsed() -> float:
        return elapsed_before + time.perf_counter() - started

    futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
    active_models: set[str] = set()

    try:
        with ThreadPoolExecutor(max_workers=min(args.concurrency, len(args.models))) as executor:
            while True:
                total = sum(completed.values())
                all_reached = all(completed.get(key, 0) >= target for key, target in model_quotas.items())
                if total >= args.pilot or all_reached:
                    stop_reason = "总数达标" if total >= args.pilot else "全部单元达标"
                    break
                if int(checkpoint.get("api_attempt_count", 0)) >= MAX_API_CALLS:
                    stop_reason = f"达到全局调用上限 {MAX_API_CALLS}"
                    break
                if elapsed() >= MAX_WALL_SECONDS:
                    stop_reason = f"达到全局墙钟上限 {MAX_WALL_SECONDS:.0f} 秒"
                    break

                remaining_total = args.pilot - total
                tail_threshold = max(BATCH_SIZE_BY_TIER.values()) * (args.concurrency + 1)
                desired_concurrency = 1 if remaining_total <= tail_threshold else args.concurrency
                for model in args.models:
                    if len(futures) >= desired_concurrency:
                        break
                    if model in unavailable or model in active_models:
                        continue
                    target = choose_model_target(model, model_quotas, completed, exhausted)
                    if target is None:
                        continue
                    _, lang, requested_tier, kind = target
                    cell_key = model_quota_key(model, quota_key(lang, requested_tier, kind))
                    call_index = int(checkpoint["call_count"])
                    call_seed = int(checkpoint["next_seed"])
                    sample_count = BATCH_SIZE_BY_TIER[requested_tier]
                    checkpoint["call_count"] = call_index + 1
                    checkpoint["next_seed"] = call_seed + sample_count
                    sampled = sample_axes(axes, requested_tier, lang, call_seed)
                    trap_sets = [
                        select_traps(axes, call_seed + index)
                        if kind == "hard_negative" else []
                        for index in range(sample_count)
                    ]
                    prompt = build_batch_prompt(
                        requested_tier, lang, sampled, kind, sample_count,
                        call_seed, trap_sets,
                    )
                    future = executor.submit(
                        request_batch,
                        model=model,
                        prompt=prompt,
                        dry_run=args.dry_run,
                        requested_tier=requested_tier,
                        lang=lang,
                        sampled=sampled,
                        kind=kind,
                        sample_count=sample_count,
                        seed=call_seed,
                        trap_sets=trap_sets,
                        stop_event=stop_event,
                    )
                    futures[future] = {"model": model, "lang": lang, "tier": requested_tier, "kind": kind, "cell_key": cell_key, "call_index": call_index, "seed": call_seed, "sample_count": sample_count, "sampled": sampled, "trap_sets": trap_sets}
                    active_models.add(model)

                if not futures:
                    stop_reason = "所有剩余单元均已耗尽或模型均不可用"
                    break
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    context = futures.pop(future)
                    model = context["model"]
                    active_models.remove(model)
                    outcome = future.result()
                    checkpoint["api_attempt_count"] = int(checkpoint.get("api_attempt_count", 0)) + int(outcome["network_attempts"])
                    accepted: list[dict[str, Any]] = []
                    quality_errors: list[str] = []
                    other_errors: list[str] = []
                    overflow_count = 0

                    if outcome["error"]:
                        signature = outcome["error"]
                        category = outcome["error_class"]
                        if category == "quality":
                            quality_errors.append(signature)
                        elif category in ("auth", "network"):
                            checkpoint["model_network_failures"][model] = int(checkpoint["model_network_failures"].get(model, 0)) + 1
                            if category == "auth" or checkpoint["model_network_failures"][model] >= MAX_NETWORK_FAILURES_PER_MODEL:
                                message = (
                                    "DashScope 鉴权失败(401/403)。请检查环境变量 DASHSCOPE_API_KEY，"
                                    "或到百炼控制台确认余额。"
                                    f"已完成的 {sum(completed.values())} 条已保存在 "
                                    f"finetune/data/interim/{args.batch_id}.jsonl，修好后重跑同一条命令即可续跑。"
                                    if category == "auth"
                                    else f"连续 {checkpoint['model_network_failures'][model]} 个批次在重试耗尽后仍为网络失败"
                                )
                                checkpoint["unavailable_models"][model] = message
                                unavailable.add(model)
                                redistribute_model_quotas(model, model_quotas, completed, args.models, unavailable)
                                print(f"模型 {model} 已标记为不可用：{message}；剩余配额已分给其他模型。")
                            other_errors.append(signature)
                        elif category == "balance":
                            stop_reason = (
                                f"DashScope 余额不足。已完成的 {sum(completed.values())} 条已保存在 "
                                f"finetune/data/interim/{args.batch_id}.jsonl，充值后重跑同一条命令即可续跑。"
                            )
                            other_errors.append(signature)
                        else:
                            stop_reason = f"致命 API 错误：{signature}"
                            other_errors.append(signature)
                    else:
                        checkpoint["model_network_failures"][model] = 0
                        try:
                            raw_samples = parse_batch_response(outcome["raw"], context["sample_count"])
                            batch_ngrams: list[set[str]] = []
                            for index, raw_sample in enumerate(raw_samples):
                                try:
                                    gold, _ = process_generated_sample(raw_sample, axes=axes, requested_tier=context["tier"], lang=context["lang"], model=model, seed=context["seed"] + index, batch=args.batch_id, kind=context["kind"], sampled=context["sampled"], force_checksum_valid=True if args.dry_run else None)
                                    text_value = gold["text"]
                                    grams = char_ngrams(text_value)
                                    if any(jaccard(grams, prior) >= 0.35 for prior in batch_ngrams):
                                        raise ValueError("批内字符 4-gram Jaccard >= 0.35")
                                    batch_ngrams.append(grams)
                                    if text_value in existing_texts:
                                        raise ValueError("与已有批次文本完全一致")
                                    if gold["id"] in existing_ids:
                                        raise ValueError(f"与已有批次 id 冲突: {gold['id']}")
                                    actual_key = model_quota_key(model, quota_key(context["lang"], gold["meta"]["tier"], context["kind"]))
                                    if completed.get(actual_key, 0) >= soft_quota_cap(model_quotas.get(actual_key, 0)):
                                        overflow_count += 1
                                        continue
                                    accepted.append(gold)
                                    completed[actual_key] = completed.get(actual_key, 0) + 1
                                    existing_texts.add(text_value)
                                    existing_ids.add(gold["id"])
                                except (ParseError, ValueError) as exc:
                                    signature = error_signature(exc)
                                    (quality_errors if is_quality_error(exc) else other_errors).append(signature)
                        except (ParseError, ValueError) as exc:
                            signature = error_signature(exc)
                            (quality_errors if is_quality_error(exc) else other_errors).append(signature)

                    append_jsonl(output_path, accepted)
                    records.extend(accepted)
                    all_errors = quality_errors + other_errors
                    signature = " | ".join(sorted(set(all_errors))) if all_errors else None
                    append_jsonl(CALLS_PATH, [{
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                        "batch": args.batch_id,
                        "model": model,
                        "attempt": int(checkpoint["cell_failures"].get(context["cell_key"], 0)) + 1,
                        "call_index": context["call_index"],
                        "requested": {"lang": context["lang"], "tier": context["tier"], "kind": context["kind"]},
                        "latency": round(float(outcome["latency"]), 6),
                        "input_tokens": outcome["usage"]["input_tokens"],
                        "output_tokens": outcome["usage"]["output_tokens"],
                        "network_attempts": outcome["network_attempts"],
                        "error_signature": signature,
                        "error_counts": dict(sorted(Counter(all_errors).items())),
                        "overflow_rejections": overflow_count,
                        "success": bool(accepted),
                        "accepted": len(accepted),
                        "dry_run": bool(args.dry_run),
                    }])
                    for signature_value in all_errors:
                        checkpoint["rejections"][signature_value] = int(checkpoint["rejections"].get(signature_value, 0)) + 1
                    if overflow_count:
                        checkpoint["rejections"]["合格但实测归入已满单元"] = int(checkpoint["rejections"].get("合格但实测归入已满单元", 0)) + overflow_count

                    if accepted:
                        checkpoint["cell_failures"][context["cell_key"]] = 0
                        checkpoint["cell_schedule_misses"][context["cell_key"]] = 0
                    elif quality_errors:
                        failures = int(checkpoint["cell_failures"].get(context["cell_key"], 0)) + 1
                        checkpoint["cell_failures"][context["cell_key"]] = failures
                        if failures >= MAX_SAMPLE_ATTEMPTS:
                            exhausted.add(context["cell_key"])
                            print(f"质量失败连续 {failures} 次，停止单元 {context['cell_key']}，继续其他单元。")
                    elif overflow_count or other_errors:
                        misses = int(checkpoint["cell_schedule_misses"].get(context["cell_key"], 0)) + 1
                        checkpoint["cell_schedule_misses"][context["cell_key"]] = misses
                        if misses >= MAX_SCHEDULING_MISSES:
                            exhausted.add(context["cell_key"])
                            print(f"单元连续 {misses} 批没有样本落入可接收软桶，停止调度 {context['cell_key']}；这不计入质量失败。")
                    checkpoint["completed"] = completed
                    checkpoint["exhausted_cells"] = sorted(exhausted)
                    checkpoint["elapsed_seconds"] = round(elapsed(), 6)
                    persist_checkpoint(checkpoint_path, checkpoint, lock)
                    if sum(completed.values()) >= args.pilot:
                        stop_event.set()

                    for record in accepted:
                        if len(records) - len(accepted) + accepted.index(record) < 3:
                            print_record_full(record, context["tier"])
                    total_now = sum(completed.values())
                    entity_count = sum(len(record["spans"]) for record in accepted)
                    char_count = sum(len(record["text"]) for record in accepted)
                    print(f"[{total_now:4d}/{args.pilot}] {model:<14} {context['lang']}/{context['tier']}/{context['kind']:<15} {char_count}字 {entity_count}实体  调用{checkpoint['api_attempt_count']}  用时 {format_elapsed(elapsed())}  拒绝{len(all_errors) + overflow_count}")
                    if total_now and total_now % 200 < len(accepted):
                        print(json.dumps(distribution_tables(records) | {"milestone": total_now}, ensure_ascii=False, indent=2))
                if stop_reason:
                    break
    except KeyboardInterrupt:
        checkpoint["completed"] = completed
        checkpoint["elapsed_seconds"] = round(elapsed(), 6)
        persist_checkpoint(checkpoint_path, checkpoint, lock)
        print(f"已保存 {sum(completed.values())} 条，重跑同一条命令即可续跑")
        return

    checkpoint["completed"] = completed
    checkpoint["elapsed_seconds"] = round(elapsed(), 6)
    checkpoint["stop_reason"] = stop_reason
    persist_checkpoint(checkpoint_path, checkpoint, lock)
    if len(records) >= args.pilot and args.pilot == 60 and not args.dry_run:
        write_review_markdown(review_path, records[: args.pilot])
    shortages = {}
    for key, target in model_quotas.items():
        done = completed.get(key, 0)
        if done < target:
            shortages[key] = {"done": done, "target": target, "ratio": round(done / target, 4) if target else 1.0, "below_80_percent": bool(target and done / target < 0.8)}
    print(json.dumps({"stop_reason": stop_reason, "total": sum(completed.values()), "target": args.pilot, "logical_calls": checkpoint["call_count"], "api_attempts": checkpoint["api_attempt_count"], "estimated_api_calls": checkpoint["call_count"] if args.dry_run else checkpoint["api_attempt_count"], "elapsed_seconds": checkpoint["elapsed_seconds"], "shortages": shortages, "unavailable_models": checkpoint["unavailable_models"], "rejections": checkpoint["rejections"], "distributions": distribution_tables(records)}, ensure_ascii=False, indent=2))


def run_single(args: argparse.Namespace) -> None:
    axes = load_axes()
    sampled = sample_axes(axes, args.tier, args.lang, args.seed)
    rng_sample_id = make_sample_id(args.seed, args.lang, args.tier)
    valid_ratio = float(axes["checksum_validity"]["valid_ratio"])
    checksum_valid = (
        slot_rng(args.seed, rng_sample_id, "checksum_validity").random() < valid_ratio
    )
    seeds = seed_entities(args.seed, args.lang, args.tier, sampled["label_group"])
    prompt = build_prompt(args.tier, args.lang, sampled, entity_seeds=seeds)

    print("=== 抽样轴 ===")
    print(json.dumps(sampled, ensure_ascii=False, indent=2))
    print("=== Prompt ===")
    print(prompt)

    raw = fake_response(args.lang, sampled, args.tier, args.seed) if args.dry_run else call_dashscope(args.model, prompt)
    print("=== 原始返回 ===")
    print(raw)

    replaced = replace_structured_placeholders(
        raw, args.seed, rng_sample_id, checksum_valid, args.lang
    )
    print("=== 替换后正文 ===")
    print(replaced)

    text, tagged_spans = parse_inline_tagged(
        replaced, allowed=tuple(sampled["label_group"])
    )
    spans = expand_spans(text, tagged_spans)
    reject_prompt_leakage(text, spans)
    sweep_structured_pii(text, spans)
    print("=== 标签扩展 ===")
    print(f"{len(tagged_spans)} → {len(spans)}")
    print("=== SWEEP ===")
    print("PASS")
    classified_tier = classify_tier(args.lang, len(text), len(spans))
    sample_id = make_sample_id(args.seed, args.lang, classified_tier)
    print("=== 请求档 → 归类档 ===")
    print(f"{args.tier} → {classified_tier}")
    print("=== 纯文本 ===")
    print(text)
    print("=== spans ===")
    print(json.dumps(spans, ensure_ascii=False, indent=2))
    print("=== span 对照 ===")
    for span in spans:
        surface = text[span["start"] : span["end"]]
        print(
            f"{span['label']} [{span['start']}:{span['end']}] "
            f"text[start:end]={surface!r}"
        )
    print("=== checksum valid ===")
    print(str(checksum_valid).lower())

    gold = build_gold(
        args.lang,
        args.model,
        args.seed,
        "stage1-single",
        "positive",
        sampled,
        sample_id,
        classified_tier,
        text,
        spans,
    )
    print("=== gold ===")
    print(json.dumps(gold, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    if args.status:
        run_status(args)
    elif args.pilot is not None:
        run_pilot(args)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
