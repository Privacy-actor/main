import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # 职务身份：“心内科主任”“财务处处长”这类职务与部门的组合，单独看不含姓名，
    # 但足以推断出具体的人（立项书所述的隐性隐私）
    ROLE = "ROLE"
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


# 职务身份（ROLE）加入之前的十类实体。旧版保存的方案把这十类全部列出，含义是“全部类型”。
LEGACY_ENTITY_TYPES = frozenset({"PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE", "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT", "CUSTOM"})
SCOPE_VERSION = 2


def migrate_scope(config: Any) -> Any:
    """旧版方案（没有 scope_version）若勾选了当时的全部类型，补上职务身份；新版方案保持用户的选择。"""
    if not isinstance(config, dict):
        return config
    try:
        version = int(config.get("scope_version") or 1)
    except (TypeError, ValueError, OverflowError):
        return config  # 交给字段校验报 422
    if version >= SCOPE_VERSION:
        return config
    types = config.get("enabled_entity_types")
    if isinstance(types, list) and LEGACY_ENTITY_TYPES <= {str(item) for item in types} and EntityType.ROLE.value not in types:
        config = {**config, "enabled_entity_types": [*types, EntityType.ROLE.value]}
    return {**config, "scope_version": SCOPE_VERSION}


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
    scope_version: int = SCOPE_VERSION

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_scope(cls, data: Any) -> Any:
        return migrate_scope(data)


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


class RuleTestRequest(BaseModel):
    kind: Literal["keyword", "regex"]
    pattern: str = Field(min_length=1, max_length=500)
    text: str = Field(max_length=20_000)
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
    # 小样调试时为 False：返回完整结果，但不写入任务记录。
    persist: bool = True


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
    replacements: list[dict[str, Any]] = Field(default_factory=list)
    persisted: bool = True


class RedactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    spans: list[Span] = Field(max_length=5_000)
    strategy: Strategy | None = Strategy.MASK
    privacy_strength: int = Field(default=2, ge=1, le=3)
    risk_level: Literal["standard", "strict"] = "strict"


class KnowledgeLookupRequest(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    entity_type: EntityType = EntityType.ORG
    allow_remote: bool = True
    lang: Literal["zh", "en"] = "zh"


class ReviewRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    span_id: str = Field(min_length=1, max_length=128)
    operation: Literal[
        "accept", "reject", "change_type", "add", "adjust_boundary", "set_strategy", "set_span_strategy",
        "set_strength", "set_replacement", "add_many", "accept_many", "reject_many",
    ]
    before: str | None = Field(default=None, max_length=10_000)
    after: str | None = Field(default=None, max_length=10_000)
    span: Span | None = None
    strategy: Strategy | None = None
    # add_many：一次补充同一文本的多处出现；accept_many / reject_many：批量确认或恢复。
    spans: list[Span] | None = Field(default=None, max_length=500)
    span_ids: list[str] | None = Field(default=None, max_length=2_000)


class RestoreRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    # 按优先顺序排列：工作台传当前任务；插件传最近几次脱敏的任务，最近的在前
    task_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(min_length=1, max_length=20)


class RecheckRequest(BaseModel):
    # 不传 text 时复检已保存的最终稿；传入时复检编辑器里尚未保存的内容。
    text: str | None = Field(default=None, max_length=100_000)


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
    deployment_mode: Literal["local", "cloud"] = "local"


class ModelEndpointUpdate(BaseModel):
    """界面里保存的一侧大模型设置。api_key 为 None 表示沿用已保存的密钥，空字符串表示清除。"""
    enabled: bool = False
    provider: str = Field(default="custom", max_length=40)
    base_url: str = Field(default="", max_length=300)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^https?://[^\s/]+", value):
            raise ValueError("服务地址需以 http:// 或 https:// 开头")
        return value


class ModelSettingsUpdate(BaseModel):
    local: ModelEndpointUpdate | None = None
    cloud: ModelEndpointUpdate | None = None
    reset: list[Literal["local", "cloud"]] = Field(default_factory=list)


class ModelProbeRequest(BaseModel):
    """测试连接或读取模型列表。api_key 为空时，若地址与已保存的一致，使用已保存的密钥。"""
    target: Literal["local", "cloud"] = "local"
    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip()
        if not re.match(r"^https?://[^\s/]+", value):
            raise ValueError("服务地址需以 http:// 或 https:// 开头")
        return value
