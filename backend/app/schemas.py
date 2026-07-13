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
    PASSPORT = "PASSPORT"
    CUSTOM = "CUSTOM"


class Strategy(StrEnum):
    MASK = "mask"
    PSEUDONYMIZE = "pseudonymize"
    GENERALIZE = "generalize"


class CustomKeyword(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    entity_type: EntityType = EntityType.CUSTOM
    case_sensitive: bool = False


class CustomPattern(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    pattern: str = Field(min_length=1, max_length=500)
    entity_type: EntityType = EntityType.CUSTOM
    case_sensitive: bool = False


class ProcessingConfig(BaseModel):
    project_id: str | None = Field(default=None, max_length=128)
    language: Literal["auto", "zh", "en", "mixed", "multilingual"] = "auto"
    risk_level: Literal["standard", "strict"] = "strict"
    strategy: Strategy = Strategy.MASK
    privacy_strength: int = Field(default=2, ge=1, le=3)
    use_llm: bool = True
    use_policies: bool = False
    deployment_mode: Literal["local", "cloud"] = "local"
    enabled_entity_types: list[EntityType] = Field(default_factory=lambda: list(EntityType), min_length=1)
    custom_keywords: list[CustomKeyword] = Field(default_factory=list, max_length=200)
    custom_patterns: list[CustomPattern] = Field(default_factory=list, max_length=100)
    preserve_terms: list[str] = Field(default_factory=list, max_length=200)
    instruction: str | None = Field(default=None, max_length=2_000)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    config: ProcessingConfig = Field(default_factory=ProcessingConfig)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    config: ProcessingConfig | None = None


class RuleCreate(BaseModel):
    project_id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["keyword", "regex"]
    pattern: str = Field(min_length=1, max_length=500)
    entity_type: EntityType = EntityType.CUSTOM
    enabled: bool = True
    case_sensitive: bool = False


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: Literal["keyword", "regex"] | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    entity_type: EntityType | None = None
    enabled: bool | None = None
    case_sensitive: bool | None = None


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


class DetectRequest(ProcessingConfig):
    text: str = Field(min_length=1, max_length=100_000)


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
    final_text: str
    final_revision: int = Field(default=0, ge=0)
    has_manual_edits: bool = False
    applied_config: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None


class RedactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    spans: list[Span] = Field(max_length=5_000)
    strategy: Strategy = Strategy.MASK
    privacy_strength: int = Field(default=2, ge=1, le=3)
    risk_level: Literal["standard", "strict"] = "strict"


class KnowledgeLookupRequest(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    entity_type: EntityType = EntityType.ORG
    allow_remote: bool = True


class ReviewRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    span_id: str = Field(min_length=1, max_length=128)
    operation: Literal["accept", "reject", "change_type", "add", "adjust_boundary", "set_strategy", "set_span_strategy", "set_strength", "set_replacement"]
    before: str | None = Field(default=None, max_length=10_000)
    after: str | None = Field(default=None, max_length=10_000)
    span: Span | None = None


class FinalTextUpdate(BaseModel):
    text: str = Field(max_length=100_000)
    automatic_text: str = Field(max_length=100_000)
    expected_revision: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=500)


class PolicyUpdate(BaseModel):
    policies: dict[EntityType, Strategy]


class InstructionRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2_000)
    use_llm: bool = True
