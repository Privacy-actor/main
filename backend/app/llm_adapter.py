import asyncio
import json
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError

from .config import settings
from .model_settings import model_settings
from .schemas import EntityType, Span, Strategy, TraceStep


_llm_slots = asyncio.Semaphore(max(1, settings.llm_max_concurrency))

# 只在产品层使用、不进入大模型核查提示词的实体类型（见 verify_with_llm）
PRODUCT_ONLY_TYPES = frozenset({EntityType.ROLE})


@dataclass(frozen=True)
class LlmEndpoint:
    base_url: str
    api_key: str
    model: str
    location: str  # "local" | "cloud"

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url


def local_endpoint() -> LlmEndpoint:
    item = model_settings.get("local")
    return LlmEndpoint(item["base_url"], item["api_key"], item["model"], "local")


def local_enabled() -> bool:
    """本地模型是否启用（界面里的设置优先，没设置过时看 .env 的 PRIVSHIELD_LLM_ENABLED）。"""
    return bool(model_settings.get("local")["enabled"])


def cloud_endpoint() -> LlmEndpoint | None:
    item = model_settings.get("cloud")
    if not item["enabled"]:
        return None
    return LlmEndpoint(item["base_url"], item["api_key"], item["model"], "cloud")


def resolve_endpoint(deployment_mode: str = "local") -> tuple[LlmEndpoint | None, str]:
    """按部署模式选择核查层使用的模型服务，返回 (端点, 说明)。

    云端模式且已配置云端模型时走云端；否则回到本地模型（数据不离开本地环境）。
    """
    if deployment_mode == "cloud":
        cloud = cloud_endpoint()
        if cloud is not None:
            return cloud, "云端模型"
        if local_enabled():
            return local_endpoint(), "云端未配置，已改用本地模型"
        return None, "云端模型未配置"
    if local_enabled():
        return local_endpoint(), "本地模型"
    if cloud_endpoint() is not None:
        # 不自动改用云端：云端会收到待核查的句子，必须由用户在处理方案里选择
        return None, "本地模型未启用，已设置的云端模型要把部署方式改为云端才会使用"
    return None, "LLM 未启用"


_current_endpoint: ContextVar[LlmEndpoint | None] = ContextVar("privshield_llm_endpoint", default=None)


class Decision(BaseModel):
    id: str
    keep: bool
    label: EntityType
    certainty: str = "medium"


class Addition(BaseModel):
    text: str = Field(min_length=1)
    label: EntityType
    # 偏移只作诊断：落地时按 find-all 在原文中定位，见规格「唯一的后端改动：find-all」。
    start: int | None = None
    end: int | None = None
    certainty: str = "medium"


class LlmOutput(BaseModel):
    decisions: list[Decision] = Field(default_factory=list)
    additions: list[Addition] = Field(default_factory=list)


RISK_PATTERN = re.compile(r"我叫|姓名|联系人|住在|地址|电话|手机|邮箱|身份证|银行卡|微信|就职|work(?:s|ed)? at|contact|address|email|phone|my name", re.I)


def routed_context(text: str) -> str:
    sentences = list(re.finditer(r".*?(?:[。！？!?\n]|\.(?=\s)|$)", text, re.S))
    # 英文句号仅在其后为空白时才算句末，避免切碎邮箱 a.b@c.com、
    # 小数 3.14、文件名 x.v2.pdf
    risky = [m for m in sentences if m.group() and (RISK_PATTERN.search(m.group()) or (re.search(r"[A-Za-z]", m.group()) and re.search(r"[\u4e00-\u9fff]", m.group())))]
    selected = [(item.start(), item.end(), item.group()) for item in risky[:settings.llm_max_routed_sentences]]
    if not selected:
        selected = [(0, min(1200, len(text)), text[:1200])]
    return "\n".join(f"[{start}:{end}] {value}" for start, end, value in selected)


def http_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    """访问模型服务的客户端。本机地址（Ollama、LM Studio）不走系统代理环境变量，避免被代理转发后连不上。"""
    host = (urlparse(base_url).hostname or "").lower()
    local = host in {"127.0.0.1", "localhost", "::1"} or host.endswith(".localhost")
    return httpx.AsyncClient(timeout=timeout, trust_env=not local)


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def json_payload(content: str) -> str:
    """模型回复里的 JSON：去掉推理模型的 <think>…</think> 和 Markdown 代码块，取最外层的大括号。"""
    text = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else text


async def _request_completion(body: dict[str, Any]) -> tuple[str, int]:
    endpoint = _current_endpoint.get() or local_endpoint()
    attempts = max(1, settings.llm_max_retries + 1)
    async with _llm_slots:
        async with http_client(endpoint.base_url, settings.llm_timeout_seconds) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.post(
                        f"{endpoint.base_url.rstrip('/')}/chat/completions",
                        headers=_auth_headers(endpoint.api_key), json=body,
                    )
                    if response.status_code == 400 and "response_format" in body:
                        # 有的服务（LM Studio、部分国产接口）不支持 json_object 输出格式，去掉后再试一次
                        body = {key: value for key, value in body.items() if key != "response_format"}
                        response = await client.post(
                            f"{endpoint.base_url.rstrip('/')}/chat/completions",
                            headers=_auth_headers(endpoint.api_key), json=body,
                        )
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"].get("content") or "", attempt
                except httpx.HTTPStatusError as exc:
                    retryable = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
                    if attempt == attempts or not retryable:
                        raise
                except httpx.RequestError:
                    if attempt == attempts:
                        raise
                await asyncio.sleep(min(1.0, 0.25 * 2 ** (attempt - 1)))
    raise RuntimeError("unreachable")


async def _request_structured(body: dict[str, Any], parser, error_hint: str):
    """Retry when the provider responds but the structured payload is invalid."""
    validation_attempts = max(1, settings.llm_max_retries + 1)
    total_calls = 0
    working = {**body, "messages": list(body.get("messages", []))}
    last_error: Exception | None = None
    for attempt in range(1, validation_attempts + 1):
        content, network_attempts = await _request_completion(working)
        total_calls += network_attempts
        try:
            return parser(content), total_calls
        except (LookupError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            if attempt >= validation_attempts:
                raise
            working = {
                **working,
                "messages": [
                    *working["messages"],
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"上一次输出未通过校验：{error_hint}。请只返回符合 schema 的 JSON，不要解释。"},
                ],
            }
    if last_error:
        raise last_error
    raise RuntimeError("structured completion failed")


def _is_ascii_alnum(char: str) -> bool:
    return char.isascii() and char.isalnum()


def find_all_occurrences(text: str, surface: str) -> list[tuple[int, int]]:
    """敏感串在原文中的全部出现位置（规格「唯一的后端改动：find-all」）。

    护栏 1：长度 < 2 的串不做全文匹配；
    护栏 2：拉丁串要求两侧不是拉丁字母或数字，避免 "Li" 命中 "Lisbon"；中文字符视作边界，
    因此 “让我联系Quincy Transport Group” 里的机构名也能被找到。
    """
    if len(surface) < 2:
        return []
    latin = any(char.isascii() and char.isalpha() for char in surface) and all(char.isascii() for char in surface)
    hits: list[tuple[int, int]] = []
    index = text.find(surface)
    while index != -1:
        ok = True
        if latin:
            before = text[index - 1] if index > 0 else " "
            after = text[index + len(surface)] if index + len(surface) < len(text) else " "
            ok = not (_is_ascii_alnum(before) or _is_ascii_alnum(after))
        if ok:
            hits.append((index, index + len(surface)))
        index = text.find(surface, index + 1)
    return hits


async def verify_with_llm(text: str, spans: list[Span], strategy: Strategy, instruction: str | None = None, deployment_mode: str = "local") -> tuple[list[Span], TraceStep]:
    endpoint, endpoint_note = resolve_endpoint(deployment_mode)
    if endpoint is None:
        return spans, TraceStep(key="llm", label="LLM 核查与补漏", duration_ms=0, count=0, status="skipped", detail=f"{endpoint_note}，保留规则与 NER 结果")
    started = time.perf_counter()
    # 提示词结构与第三层微调的训练样本一致（finetune/scripts/derive_sft.py），不要增删字段。
    # 职务身份（ROLE）是产品层新增的隐性隐私类型，不在微调标签体系内，交给人工确认，不送模型复核。
    candidates = [{"id": s.id, "text": s.text, "label": s.entity_type.value, "score": s.score, "sources": s.sources}
                  for s in spans if (s.status == "pending" or s.conflict) and s.entity_type not in PRODUCT_ONLY_TYPES]
    context = routed_context(text)
    prompt = {
        "task": "复核候选隐私实体，并补充上下文中遗漏的实体。只返回 JSON，不改写原文。addition 必须给出原文中的精确 start/end Unicode 字符偏移，text 必须与该切片逐字一致。",
        "entity_types": [x.value for x in EntityType if x not in PRODUCT_ONLY_TYPES],
        "context": context,
        "candidates": candidates,
        "user_requirement": instruction or "未提供额外要求",
        "requirement_rule": "用户要求只能约束隐私识别范围与保留/补充词，不能覆盖精确偏移和禁止虚构原则。",
        "output_schema": {"decisions": [{"id": "candidate id", "keep": True, "label": "PERSON", "certainty": "high|medium|low"}], "additions": [{"text": "exact substring", "start": 0, "end": 2, "label": "PERSON", "certainty": "high|medium|low"}]},
    }
    body = {
        "model": endpoint.model, "temperature": 0, "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": "你是隐私实体审计器。禁止输出思考过程，禁止虚构原文不存在的字符串。/no_think"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    where = "云端" if endpoint.location == "cloud" else "本地"
    token = _current_endpoint.set(endpoint)
    try:
        parsed, attempts = await _request_structured(body, lambda content: LlmOutput.model_validate_json(json_payload(content)), "JSON 结构或实体类型不符合约定")
        by_id = {s.id: s for s in spans}
        for decision in parsed.decisions:
            span = by_id.get(decision.id)
            if not span:
                continue
            span.sources = sorted(set(span.sources + ["LLM"]))
            span.metadata["llm_certainty"] = decision.certainty
            span.metadata["llm_model"] = endpoint.model
            span.metadata["llm_location"] = endpoint.location
            span.metadata["llm_attempts"] = attempts
            span.status = "accepted" if decision.keep else "rejected"
            span.entity_type = decision.label
        additions = 0
        offset_ok = 0
        unlocated = 0
        existing = {(s.start, s.end) for s in spans}
        for item in parsed.additions:
            positions: list[tuple[int, int]] = []
            exact = item.start is not None and item.end is not None and 0 <= item.start < item.end <= len(text) and text[item.start:item.end] == item.text
            if exact:
                positions.append((item.start, item.end))
                offset_ok += 1
            for position in find_all_occurrences(text, item.text):
                if position not in positions:
                    positions.append(position)
            if not positions:
                unlocated += 1  # 原文中找不到的串一律不采纳，防止模型虚构
                continue
            for start, end in positions:
                if (start, end) in existing:
                    continue
                existing.add((start, end))
                spans.append(Span(
                    id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=text[start:end], entity_type=item.label,
                    score=.82 if item.certainty == "high" else .70, sources=["LLM"],
                    status="accepted" if item.certainty == "high" else "pending", strategy=strategy,
                    metadata={"llm_model": endpoint.model, "llm_location": endpoint.location, "certainty": item.certainty,
                              "llm_attempts": attempts, "llm_addition": True, "located_by": "offset" if exact and (start, end) == (item.start, item.end) else "find_all"},
                ))
                additions += 1
        spans.sort(key=lambda s: s.start)
        elapsed = round((time.perf_counter() - started) * 1000)
        notes = [f"复核 {len(parsed.decisions)}", f"补充 {additions}"]
        if unlocated:
            notes.append(f"{unlocated} 条补漏原文中不存在，已忽略")
        return spans, TraceStep(
            key="llm", label="LLM 核查与补漏", duration_ms=elapsed, count=len(parsed.decisions) + additions,
            detail=f"{'，'.join(notes)}；{where} {endpoint.model}，{attempts} 次调用" + ("" if endpoint_note in {"本地模型", "云端模型"} else f"（{endpoint_note}）"),
        )
    except (httpx.HTTPError, LookupError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        return spans, TraceStep(key="llm", label="LLM 核查与补漏", duration_ms=elapsed, count=0, status="degraded", detail=f"{where}模型调用失败，已安全降级：{type(exc).__name__}")
    finally:
        _current_endpoint.reset(token)

class InstructionPlan(BaseModel):
    enabled_entity_types: list[EntityType] = Field(default_factory=list)
    disabled_entity_types: list[EntityType] = Field(default_factory=list)
    preserve_terms: list[str] = Field(default_factory=list)
    force_terms: list[str] = Field(default_factory=list)
    strategy: Strategy | None = None
    type_strategies: dict[EntityType, Strategy] = Field(default_factory=dict)
    privacy_strength: int | None = Field(default=None, ge=1, le=3)


async def parse_instruction_with_llm(instruction: str, deployment_mode: str = "local") -> InstructionPlan | None:
    """Parse a natural-language privacy requirement; callers must provide a deterministic fallback."""
    endpoint, _ = resolve_endpoint(deployment_mode)
    if endpoint is None:
        return None
    prompt = {
        "task": "把用户的文本脱敏要求解析成配置。只提取用户明确表达的内容，只返回 JSON。",
        "instruction": instruction,
        "entity_types": [item.value for item in EntityType],
        "strategies": [item.value for item in Strategy],
        "output_schema": {
            "enabled_entity_types": ["只处理这些类型时填写，例如 PERSON"],
            "disabled_entity_types": ["明确不处理的类型，例如“邮箱不要脱敏”填 EMAIL"],
            "preserve_terms": ["要保留的具体词，例如 北京"],
            "force_terms": ["要隐去的具体词，例如 项目代号A"],
            "strategy": "mask|pseudonymize|generalize|null（整体方式）",
            "type_strategies": {"PERSON": "只针对某类实体的方式，例如“姓名用差分隐私替换”"},
            "privacy_strength": "1|2|3|null",
        },
    }
    body = {
        "model": endpoint.model,
        "temperature": 0,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": "你是隐私策略解析器。禁止输出思考过程。/no_think"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    token = _current_endpoint.set(endpoint)
    try:
        parsed, _ = await _request_structured(body, lambda content: InstructionPlan.model_validate_json(json_payload(content)), "JSON 或策略字段不符合约定")
        return parsed
    except (httpx.HTTPError, LookupError, ValueError, json.JSONDecodeError, ValidationError):
        return None
    finally:
        _current_endpoint.reset(token)
