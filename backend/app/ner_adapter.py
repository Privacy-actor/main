import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .config import BASE_DIR, settings
from .schemas import EntityType, Span, Strategy, TraceStep


def resolve_model_path(name: str) -> str:
    """相对路径（如 models/xlm-roberta-base-ner-hrl）按 backend 目录解析，与从哪里启动无关；其余视为模型 ID。"""
    candidate = Path(name)
    if not candidate.is_absolute() and (BASE_DIR / candidate).exists():
        return str(BASE_DIR / candidate)
    return name


def model_display_name(name: str) -> str:
    """界面上显示的模型名：取路径或模型 ID 的最后一段，兼容 Windows 反斜杠。"""
    return re.split(r"[\\/]", name.rstrip("\\/"))[-1] or name


LABEL_MAP = {
    "PER": EntityType.PERSON, "PERSON": EntityType.PERSON, "NAME": EntityType.PERSON,
    "ORG": EntityType.ORG, "ORGANIZATION": EntityType.ORG, "COMPANY": EntityType.ORG,
    "LOC": EntityType.LOCATION, "LOCATION": EntityType.LOCATION, "GPE": EntityType.LOCATION,
    "ADDRESS": EntityType.ADDRESS,
}


class NerAdapter:
    """Lazy Transformers adapter. No model is imported or downloaded unless enabled."""

    # 加载失败后在这段时间内不再重试，避免每次识别都卡在下载或加载上
    RETRY_AFTER_SECONDS = 300

    def __init__(self):
        self._pipeline: Any = None
        self._state = "idle"  # idle：尚未加载；ready：可用；failed：加载失败，已退回轻量识别器
        self._error = ""
        self._failed_at = 0.0
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
                source = resolve_model_path(settings.ner_model)
                tokenizer = AutoTokenizer.from_pretrained(source, use_fast=True)
                model = AutoModelForTokenClassification.from_pretrained(source)
                return pipeline("token-classification", model=model, tokenizer=tokenizer, aggregation_strategy="simple", device=settings.ner_device)
            if self._state == "failed" and time.monotonic() - self._failed_at < self.RETRY_AFTER_SECONDS:
                raise RuntimeError(self._error)
            self._state = "idle"
            try:
                self._pipeline = await asyncio.to_thread(load)
            except Exception as exc:
                self._state, self._error, self._failed_at = "failed", f"{type(exc).__name__}: {str(exc)[:200]}", time.monotonic()
                raise
            self._state, self._error = "ready", ""

    async def warmup(self) -> None:
        """服务启动时预先加载模型，避免第一次识别等待太久；失败只记录状态。"""
        if not settings.ner_enabled:
            return
        try:
            await self._load()
        except Exception:
            pass

    def status(self) -> dict[str, str]:
        name = model_display_name(settings.ner_model)
        if not settings.ner_enabled:
            return {"state": "disabled", "model": name, "detail": ""}
        return {"state": self._state, "model": name, "detail": self._error}

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
                    # 分词器的偏移有时带上前后空白，收紧到实体本身
                    while start < end and text[start].isspace():
                        start += 1
                    while end > start and text[end - 1].isspace():
                        end -= 1
                    # 单个字（“上周”里的“上”）几乎不构成可识别的实体，模型给出的这类碎片不采纳
                    if end - start < 2:
                        continue
                    value = text[start:end]
                    spans.append(Span(id=f"span_{uuid.uuid4().hex[:10]}", start=start, end=end, text=value, entity_type=entity_type, score=score, sources=["NER"], status="pending" if score < settings.confidence_threshold else "accepted", strategy=strategy, metadata={"model": model_display_name(settings.ner_model)}))
            elapsed = round((time.perf_counter() - started) * 1000)
            return spans, TraceStep(key="ner_model", label="NER 模型", duration_ms=elapsed, count=len(spans), detail=model_display_name(settings.ner_model))
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000)
            reason = "模型未加载" if self._state == "failed" else f"推理失败：{type(exc).__name__}"
            return [], TraceStep(key="ner_model", label="NER 模型", duration_ms=elapsed, count=0, status="degraded", detail=f"{reason}，已由轻量识别器处理")


ner_adapter = NerAdapter()
