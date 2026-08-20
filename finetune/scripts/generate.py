"""阶段一：单次生成一条 gold 记录。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
from typing import Any

import httpx
import yaml

from primitives import (
    ParseError,
    expand_spans,
    make_bank_card,
    make_email,
    make_id_card,
    make_passport,
    make_phone,
    parse_inline_tagged,
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
CLASSIFIED_ENTITY_MAX = {"短密": 8, "短": 3, "中": 8, "长": 5}
STRUCTURED_LABELS = ("PHONE", "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT")
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
STRUCTURED_TAG_RE = re.compile(
    r"<(PHONE|EMAIL|ID_CARD|BANK_CARD|PASSPORT)>(.*?)</\1>", re.S
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成一条 PrivShield gold 记录")
    parser.add_argument("--tier", required=True, choices=TIER_TARGETS)
    parser.add_argument("--lang", required=True, choices=("zh", "en", "mixed"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


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


def build_prompt(tier: str, lang: str, sampled: dict[str, Any]) -> str:
    target = TIER_TARGETS[tier]
    length = target["en"] if lang == "en" else target["zh"]
    minimum, maximum = target["entities"]
    locale = {
        "zh": "使用自然中文；姓名、地址和地名采用中文语境。",
        "en": "Write natural English using entities appropriate to an English-language locale.",
        "mixed": "使用自然的中英混合文本，两种语言必须都出现在正文中。",
    }[lang]
    labels = ", ".join(sampled["label_group"])
    placeholders = " ".join(f"{{{{{label}}}}}" for label in STRUCTURED_LABELS)
    sparse = (
        "\n这是长文本：个人信息必须极其稀疏，大部分篇幅必须是正常叙述。"
        if tier == "长"
        else ""
    )
    return f"""生成一条用于隐私实体识别的合成文本，只输出正文，不要解释，不要 Markdown 代码块。

场景：{sampled['domain']}
载体：{sampled['format']}
语气：{sampled['tone']}
语言约束：{locale}
长度档：{tier}（{length} 字符）
实体数量：{minimum}–{maximum} 个
本条可使用的标签：{labels}
label_group 只是允许标签池，不要求每个标签都出现。

ORG 只标注可独立识别的公司、学校、医院、政府机关或社会组织。公司内部的产品部、销售部、人事部、财务部、技术部等部门名不得标成 ORG。

必须用成对的内联标签标注实体，例如 <PERSON>张伟</PERSON>。
PHONE、EMAIL、ID_CARD、BANK_CARD、PASSPORT 的标签内容只能分别写成以下占位符，禁止自行编写号码或邮箱：
{placeholders}
示例：<PHONE>{{{{PHONE}}}}</PHONE>、<BANK_CARD>{{{{BANK_CARD}}}}</BANK_CARD>。
不要输出未闭合标签、嵌套标签、列表、标题或 JSON。{sparse}"""


def fake_response(lang: str, sampled: dict[str, Any], tier: str) -> str:
    values = {
        "zh": {
            "PERSON": "张伟",
            "ORG": "明远科技有限公司",
            "LOCATION": "杭州",
            "ADDRESS": "杭州市西湖区文三路88号",
        },
        "en": {
            "PERSON": "Alex Chen",
            "ORG": "Northwind Analytics",
            "LOCATION": "Seattle",
            "ADDRESS": "120 Pine Street, Seattle",
        },
        "mixed": {
            "PERSON": "张伟",
            "ORG": "Northwind 杭州团队",
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


def call_dashscope(model: str, prompt: str) -> str:
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
    return content


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
        if label == "PHONE":
            return make_phone(rng)
        if label == "EMAIL":
            return make_email(rng)
        if label == "ID_CARD":
            return make_id_card(rng, valid=checksum_valid)
        if label == "BANK_CARD":
            return make_bank_card(rng, valid=checksum_valid)
        if label == "PASSPORT":
            if lang == "mixed":
                locale = "zh" if rng.random() < 0.70 else "en"
            else:
                locale = "en" if lang == "en" else "zh"
            return make_passport(rng, locale)
        raise ParseError(f"未知占位符: {label}")

    replaced = PLACEHOLDER_RE.sub(replacement, raw)
    if PLACEHOLDER_RE.search(replaced):
        raise ParseError("替换后仍残留占位符")
    return replaced


def classify_tier(lang: str, character_count: int, entity_count: int) -> str:
    """规格「长度分档」表 B：按实测长度与实体数归类或丢弃。"""
    if character_count < 40 or character_count > 4000:
        raise ValueError(f"字符数 {character_count} 超出允许区间 [40, 4000]")
    short_upper = 450 if lang == "en" else 200
    if character_count <= short_upper:
        tier = "短密" if entity_count >= 4 else "短"
    elif character_count <= 1199:
        tier = "中"
    else:
        tier = "长"
    maximum = CLASSIFIED_ENTITY_MAX[tier]
    if entity_count > maximum:
        raise ValueError(f"归类档 {tier} 的实体数 {entity_count} 超出上限 {maximum}")
    return tier


def sweep_structured_pii(text: str, spans: list[dict[str, Any]]) -> None:
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
            surface = match.group(0)
            if spec.validator and not spec.validator(surface):
                continue
            start, end = match.span(0)
            covered = any(
                span["start"] <= start and end <= span["end"] for span in spans
            )
            if not covered:
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
    args: argparse.Namespace,
    sampled: dict[str, Any],
    sample_id: str,
    classified_tier: str,
    text: str,
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": sample_id,
        "lang": args.lang,
        "text": text,
        "spans": spans,
        "trap_spans": [],
        "kind": "positive",
        "meta": {
            "domain": sampled["domain"],
            "format": sampled["format"],
            "tone": sampled["tone"],
            "tier": classified_tier,
            "label_group": sampled["label_group"],
        },
        "gen": {
            "model": args.model,
            "batch": "stage1-single",
            "seed": args.seed,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        },
    }


def main() -> None:
    args = parse_args()
    axes = load_axes()
    sampled = sample_axes(axes, args.tier, args.lang, args.seed)
    rng_sample_id = make_sample_id(args.seed, args.lang, args.tier)
    valid_ratio = float(axes["checksum_validity"]["valid_ratio"])
    checksum_valid = (
        slot_rng(args.seed, rng_sample_id, "checksum_validity").random() < valid_ratio
    )
    prompt = build_prompt(args.tier, args.lang, sampled)

    print("=== 抽样轴 ===")
    print(json.dumps(sampled, ensure_ascii=False, indent=2))
    print("=== Prompt ===")
    print(prompt)

    raw = fake_response(args.lang, sampled, args.tier) if args.dry_run else call_dashscope(args.model, prompt)
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

    gold = build_gold(args, sampled, sample_id, classified_tier, text, spans)
    print("=== gold ===")
    print(json.dumps(gold, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
