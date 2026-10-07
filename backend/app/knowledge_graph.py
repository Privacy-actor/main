from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from .config import settings
from .knowledge_base import EXACT_HIERARCHY, GENERIC_LEVELS, GENERIC_LEVELS_EN, knowledge_entry_count, local_levels  # noqa: F401
from .schemas import EntityType, Span, TraceStep


REMOTE_ENTITY_TYPES = {EntityType.PERSON, EntityType.ORG, EntityType.LOCATION, EntityType.ADDRESS}


@dataclass(slots=True)
class KnowledgeResult:
    term: str
    entity_type: str
    levels: list[str]
    source: str
    status: str
    provider: str
    detail: str
    remote_attempted: bool = False

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def infer_local_levels(term: str, entity_type: EntityType, lang: str = "zh") -> tuple[tuple[str, str, str], str, str]:
    """本地层级：内置知识库命中或按名称特征推断，英文语境给出英文上位概念。"""
    return local_levels(term, entity_type, lang)


def _collect_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            found.append(text)
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_strings(item))
    elif isinstance(value, dict):
        preferred = ("concept", "value", "category", "label", "name", "type")
        for key in preferred:
            if key in value:
                found.extend(_collect_strings(value[key]))
        if not found:
            for item in value.values():
                found.extend(_collect_strings(item))
    return found


def _remote_levels(payload: Any, local_levels: tuple[str, str, str], term: str) -> tuple[str, str, str] | None:
    excluded = {term.casefold(), "concept", "category", "label", "name", "type", "value"}
    candidates: list[str] = []
    for value in _collect_strings(payload):
        normalized = value.strip().strip("[](){}'\"")
        if not normalized or normalized.casefold() in excluded or normalized.startswith("http"):
            continue
        if len(normalized) > 40 or normalized in candidates:
            continue
        candidates.append(normalized)
    if not candidates:
        return None
    chosen = candidates[:3]
    while len(chosen) < 3:
        chosen.append(local_levels[len(chosen)])
    return tuple(chosen[:3])


class KnowledgeGraphService:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[float, KnowledgeResult]] = {}

    def status(self) -> dict[str, Any]:
        return {
            "enabled": settings.knowledge_graph_remote_enabled,
            "state": "configured" if settings.knowledge_graph_remote_enabled else "local-fallback",
            "provider": "CN-Probase / CN-DBpedia",
            "local_entries": knowledge_entry_count(),
            "cache_entries": len(self._cache),
            "timeout_seconds": settings.knowledge_graph_timeout_seconds,
            "detail": "远程查询已启用，失败时自动回退本地层级" if settings.knowledge_graph_remote_enabled else "远程查询未启用，当前使用内置层级与规则推断",
        }

    async def lookup(self, term: str, entity_type: EntityType, allow_remote: bool = True, lang: str = "zh") -> KnowledgeResult:
        cleaned = term.strip()
        levels, local_source, local_detail = infer_local_levels(cleaned, entity_type, lang)
        # 远程知识图谱（CN-Probase / CN-DBpedia）只提供中文概念，英文语境使用本地英文层级
        remote_allowed = lang != "en" and allow_remote and settings.knowledge_graph_remote_enabled and entity_type in REMOTE_ENTITY_TYPES
        if not remote_allowed:
            return KnowledgeResult(cleaned, entity_type.value, list(levels), local_source, "fallback", "local", local_detail, False)

        key = (cleaned.casefold(), entity_type.value)
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]

        result = await self._lookup_remote(cleaned, entity_type, levels, local_source, local_detail)
        self._cache[key] = (now + settings.knowledge_graph_cache_seconds, result)
        return result

    async def _lookup_remote(
        self,
        term: str,
        entity_type: EntityType,
        local_levels: tuple[str, str, str],
        local_source: str,
        local_detail: str,
    ) -> KnowledgeResult:
        providers = (
            ("CN-Probase", settings.knowledge_graph_cnprobase_url, {"entity": term}),
            ("CN-DBpedia", settings.knowledge_graph_cndbpedia_url, {"query": term}),
        )
        errors: list[str] = []
        timeout = httpx.Timeout(settings.knowledge_graph_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for provider, url, params in providers:
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    levels = _remote_levels(payload, local_levels, term)
                    if levels:
                        return KnowledgeResult(term, entity_type.value, list(levels), "remote", "ready", provider, f"{provider} 返回层级并已缓存", True)
                    errors.append(f"{provider}: 无可用层级")
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    errors.append(f"{provider}: {type(exc).__name__}")
        detail = f"远程不可用，{local_detail}（{'；'.join(errors)}）"
        return KnowledgeResult(term, entity_type.value, list(local_levels), local_source, "degraded", "local", detail, True)

    async def enrich_spans(self, spans: list[Span]) -> TraceStep:
        started = time.perf_counter()
        candidates = [span for span in spans if span.entity_type in REMOTE_ENTITY_TYPES and span.status != "rejected"]
        if not candidates:
            return TraceStep(key="knowledge", label="知识图谱分级", duration_ms=0, count=0, status="skipped", detail="本次无可泛化的实体")

        unique: dict[tuple[str, EntityType], asyncio.Task[KnowledgeResult]] = {}
        async with asyncio.TaskGroup() as group:
            for span in candidates:
                key = (span.text, span.entity_type)
                if key not in unique:
                    unique[key] = group.create_task(self.lookup(span.text, span.entity_type))
        results = {key: task.result() for key, task in unique.items()}
        degraded = False
        remote_hits = 0
        for span in candidates:
            result = results[(span.text, span.entity_type)]
            english, _, _ = infer_local_levels(span.text, span.entity_type, "en")
            span.metadata.update(
                knowledge_levels=result.levels,
                knowledge_levels_en=list(english),
                knowledge_source=result.source,
                knowledge_status=result.status,
                knowledge_provider=result.provider,
                knowledge_detail=result.detail,
            )
            degraded = degraded or result.status == "degraded"
            remote_hits += int(result.source == "remote")
        elapsed = max(1, round((time.perf_counter() - started) * 1000))
        status = "degraded" if degraded else "done"
        detail = f"{len(unique)} 个唯一实体；远程命中 {remote_hits}，其余使用本地层级/推断"
        return TraceStep(key="knowledge", label="知识图谱分级", duration_ms=elapsed, count=len(candidates), status=status, detail=detail)


knowledge_graph = KnowledgeGraphService()
