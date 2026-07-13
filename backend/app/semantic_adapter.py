import threading
from typing import Any

from .config import settings


class SemanticEncoder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"
        self._state = "not_loaded"
        self._detail = ""

    def _ensure_loaded(self) -> bool:
        if not settings.semantic_model_enabled:
            self._state = "disabled"
            self._detail = "semantic model disabled; deterministic feature-vector fallback is active"
            return False
        if self._model is not None:
            return True
        if self._state == "failed":
            return False
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer

                requested = settings.semantic_model_device
                self._device = f"cuda:{requested}" if requested >= 0 and torch.cuda.is_available() else "cpu"
                self._tokenizer = AutoTokenizer.from_pretrained(settings.semantic_model)
                self._model = AutoModel.from_pretrained(settings.semantic_model).to(self._device)
                self._model.eval()
                self._torch = torch
                self._state = "ready"
                self._detail = settings.semantic_model
                return True
            except Exception as error:  # optional model must never break the redaction pipeline
                self._state = "failed"
                self._detail = f"{type(error).__name__}: {error}"[:300]
                return False

    def cosine_scores(self, source: str, candidates: list[str]) -> list[float] | None:
        if not candidates or not self._ensure_loaded():
            return None
        assert self._tokenizer is not None and self._model is not None and self._torch is not None
        torch = self._torch
        with self._lock, torch.no_grad():
            encoded = self._tokenizer([source, *candidates], padding=True, truncation=True, max_length=64, return_tensors="pt")
            encoded = {key: value.to(self._device) for key, value in encoded.items()}
            output = self._model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).expand(output.size()).float()
            pooled = (output * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
            scores = normalized[1:] @ normalized[0]
            return [max(0.0, min(1.0, float(value))) for value in scores.detach().cpu().tolist()]

    def status(self) -> dict[str, str]:
        return {"state": self._state, "detail": self._detail, "model": settings.semantic_model}


semantic_encoder = SemanticEncoder()
