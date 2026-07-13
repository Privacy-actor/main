from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORG = "ORG"
    LOCATION = "LOCATION"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    ID_CARD = "ID_CARD"
    BANK_CARD = "BANK_CARD"


class Strategy(StrEnum):
    MASK = "mask"
    PSEUDONYMIZE = "pseudonymize"
    GENERALIZE = "generalize"


class Span(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=100_000)
    entity_type: EntityType
    score: float | None = Field(default=None, ge=0, le=1)
    sources: list[str] = Field(min_length=1, max_length=20)
    status: Literal["accepted", "pending", "rejected"] = "accepted"
    conflict: bool = False
    strategy: Strategy = Strategy.MASK
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        return self


class DetectRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    language: Literal["auto", "zh", "en", "mixed"] = "auto"
    risk_level: Literal["standard", "strict"] = "standard"
    strategy: Strategy = Strategy.MASK
    use_llm: bool = True
    use_policies: bool = False


class TraceStep(BaseModel):
    key: str
    label: str
    duration_ms: int
    count: int
    status: Literal["done", "skipped", "degraded"] = "done"
    detail: str


class DetectResponse(BaseModel):
    task_id: str
    text: str
    spans: list[Span]
    redacted_text: str
    trace: list[TraceStep]
    summary: dict[str, Any]
    model: dict[str, Any]
    created_at: str


class RedactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    spans: list[Span] = Field(max_length=5_000)
    strategy: Strategy = Strategy.MASK


class ReviewRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    span_id: str = Field(min_length=1, max_length=128)
    operation: Literal["accept", "reject", "change_type", "add", "adjust_boundary"]
    before: str | None = Field(default=None, max_length=10_000)
    after: str | None = Field(default=None, max_length=10_000)
    span: Span | None = None


class PolicyUpdate(BaseModel):
    policies: dict[EntityType, Strategy]
