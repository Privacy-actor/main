import asyncio
import json
import re
import time
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from .config import settings
from .schemas import EntityType, Span, Strategy, TraceStep


_llm_slots = asyncio.Semaphore(max(1, settings.llm_max_concurrency))


class Decision(BaseModel):
    id: str
    keep: bool
    label: EntityType
    certainty: str = "medium"


class Addition(BaseModel):
    text: str
    label: EntityType
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    certainty: str = "medium"

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.end <= self.start:
            raise ValueError("addition end must be greater than start")
        return self


class LlmOutput(BaseModel):
    decisions: list[Decision] = Field(default_factory=list)
    additions: list[Addition] = Field(default_factory=list)


RISK_PATTERN = re.compile(r"我叫|姓名|联系人|住在|地址|电话|手机|邮箱|身份证|银行卡|微信|就职|work(?:s|ed)? at|contact|address|email|phone|my name", re.I)


def routed_context(text: str) -> str:
    sentences = list(re.finditer(r".*?(?:[。！？!?\n]|$)", text, re.S))
    risky = [m for m in sentences if m.group() and (RISK_PATTERN.search(m.group()) or (re.search(r"[A-Za-z]", m.group()) and re.search(r"[\u4e00-\u9fff]", m.group())))]
    selected = [(item.start(), item.end(), item.group()) for item in risky[:settings.llm_max_routed_sentences]]
    if not selected:
        selected = [(0, min(1200, len(text)), text[:1200])]
    return "\n".join(f"[{start}:{end}] {value}" for start, end, value in selected)


async def _request_completion(body: dict[str, Any]) -> tuple[str, int]:
    attempts = max(1, settings.llm_max_retries + 1)
    async with _llm_slots:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.post(
                        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {settings.llm_api_key}"}, json=body,
                    )
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"]["content"], attempt
                except httpx.HTTPStatusError as exc:
                    retryable = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
                    if attempt == attempts or not retryable:
                        raise
                except httpx.RequestError:
                    if attempt == attempts:
                        raise
                await asyncio.sleep(min(1.0, 0.25 * 2 ** (attempt - 1)))
    raise RuntimeError("unreachable")


async def verify_with_llm(text: str, spans: list[Span], strategy: Strategy) -> tuple[list[Span], TraceStep]:
    if not settings.llm_enabled:
        return spans, TraceStep(key="llm", label="14B 候选核验", duration_ms=0, count=0, status="skipped", detail="LLM 未启用，保留规则与 NER 结果")
    started = time.perf_counter()
    candidates = [{"id": s.id, "text": s.text, "label": s.entity_type.value, "score": s.score, "sources": s.sources} for s in spans if s.status == "pending" or s.conflict]
    context = routed_context(text)
    prompt = {
        "task": "复核候选隐私实体，并补充上下文中遗漏的实体。只返回 JSON，不改写原文。addition 必须给出原文中的精确 start/end Unicode 字符偏移，text 必须与该切片逐字一致。",
        "entity_types": [x.value for x in EntityType],
        "context": context,
        "candidates": candidates,
        "output_schema": {"decisions": [{"id": "candidate id", "keep": True, "label": "PERSON", "certainty": "high|medium|low"}], "additions": [{"text": "exact substring", "start": 0, "end": 2, "label": "PERSON", "certainty": "high|medium|low"}]},
    }
    body = {
        "model": settings.llm_model, "temperature": 0, "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": "你是隐私实体审计器。禁止输出思考过程，禁止虚构原文不存在的字符串。/no_think"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        content, attempts = await _request_completion(body)
        parsed = LlmOutput.model_validate_json(content)
        by_id = {s.id: s for s in spans}
        for decision in parsed.decisions:
            span = by_id.get(decision.id)
            if not span:
                continue
            span.sources = sorted(set(span.sources + ["LLM"]))
            span.metadata["llm_certainty"] = decision.certainty
            span.metadata["llm_model"] = settings.llm_model
            span.metadata["llm_attempts"] = attempts
            span.status = "accepted" if decision.keep else "rejected"
            span.entity_type = decision.label
        additions = 0
        for item in parsed.additions:
            if item.end > len(text) or text[item.start:item.end] != item.text:
                continue
            if any(s.start == item.start and s.end == item.end for s in spans):
                continue
            spans.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=item.start, end=item.end, text=item.text, entity_type=item.label, score=.82 if item.certainty == "high" else .70, sources=["LLM"], status="accepted" if item.certainty == "high" else "pending", strategy=strategy, metadata={"llm_model": settings.llm_model, "certainty": item.certainty, "llm_attempts": attempts}))
            additions += 1
        spans.sort(key=lambda s: s.start)
        elapsed = round((time.perf_counter() - started) * 1000)
        return spans, TraceStep(key="llm", label="14B 核验与补漏", duration_ms=elapsed, count=len(parsed.decisions) + additions, detail=f"复核 {len(parsed.decisions)}，补充 {additions} · {settings.llm_model} · {attempts} 次调用")
    except (httpx.HTTPError, LookupError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        return spans, TraceStep(key="llm", label="14B 核验与补漏", duration_ms=elapsed, count=0, status="degraded", detail=f"模型调用失败，安全降级：{type(exc).__name__}")
