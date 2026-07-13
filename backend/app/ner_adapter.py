import asyncio
import time
import uuid
from typing import Any

from .config import settings
from .schemas import EntityType, Span, Strategy, TraceStep


LABEL_MAP = {
    "PER": EntityType.PERSON, "PERSON": EntityType.PERSON, "NAME": EntityType.PERSON,
    "ORG": EntityType.ORG, "ORGANIZATION": EntityType.ORG, "COMPANY": EntityType.ORG,
    "LOC": EntityType.LOCATION, "LOCATION": EntityType.LOCATION, "GPE": EntityType.LOCATION,
    "ADDRESS": EntityType.ADDRESS,
}


class NerAdapter:
    """Lazy Transformers adapter. No model is imported or downloaded unless enabled."""

    def __init__(self):
        self._pipeline: Any = None
        self._lock = asyncio.Lock()
        # Transformers pipelines and GPU kernels should not be entered concurrently
        # by independent requests unless the serving stack explicitly supports it.
        self._inference_lock = asyncio.Lock()

    async def _load(self):
        if self._pipeline is not None:
            return
        async with self._lock:
            if self._pipeline is not None:
                return
            def load():
                from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
                tokenizer = AutoTokenizer.from_pretrained(settings.ner_model, use_fast=True)
                model = AutoModelForTokenClassification.from_pretrained(settings.ner_model)
                return pipeline("token-classification", model=model, tokenizer=tokenizer, aggregation_strategy="simple", device=settings.ner_device)
            self._pipeline = await asyncio.to_thread(load)

    @staticmethod
    def _chunks(text: str, size: int = 420, overlap: int = 40):
        if len(text) <= size:
            return [(0, text)]
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                boundary = max(text.rfind(mark, start + size // 2, end) for mark in "。！？!?\n")
                if boundary > start:
                    end = boundary + 1
            chunks.append((start, text[start:end]))
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
        return chunks

    async def detect(self, text: str, strategy: Strategy) -> tuple[list[Span], TraceStep]:
        if not settings.ner_enabled:
            return [], TraceStep(key="ner_model", label="NER 模型", duration_ms=0, count=0, status="skipped", detail="服务器配置中未启用 Transformers NER")
        started = time.perf_counter()
        try:
            await self._load()
            spans: list[Span] = []
            for base, chunk in self._chunks(text):
                async with self._inference_lock:
                    results = await asyncio.to_thread(self._pipeline, chunk)
                for item in results:
                    raw_label = str(item.get("entity_group", item.get("entity", ""))).upper().removeprefix("B-").removeprefix("I-")
                    entity_type = LABEL_MAP.get(raw_label)
                    score = float(item.get("score", 0))
                    if not entity_type or score < settings.ner_threshold:
                        continue
                    start, end = base + int(item["start"]), base + int(item["end"])
                    value = text[start:end]
                    if not value.strip():
                        continue
                    spans.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=value, entity_type=entity_type, score=score, sources=["NER"], status="pending" if score < .82 else "accepted", strategy=strategy, metadata={"model": settings.ner_model}))
            elapsed = round((time.perf_counter() - started) * 1000)
            return spans, TraceStep(key="ner_model", label="NER 模型", duration_ms=elapsed, count=len(spans), detail=settings.ner_model)
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000)
            return [], TraceStep(key="ner_model", label="NER 模型", duration_ms=elapsed, count=0, status="degraded", detail=f"加载或推理失败：{type(exc).__name__}")


ner_adapter = NerAdapter()
