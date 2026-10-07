from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SystemMetric(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    precision: float = Field(ge=0, le=1, allow_inf_nan=False)
    recall: float = Field(ge=0, le=1, allow_inf_nan=False)
    f1: float = Field(ge=0, le=1, allow_inf_nan=False)
    latency: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    records: int | None = Field(default=None, ge=0)


class CategoryMetric(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    recall: float = Field(ge=0, le=1, allow_inf_nan=False)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    systems: list[SystemMetric] = Field(min_length=1)
    categories: list[CategoryMetric] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_demo: bool = False
    notice: str = ""
    run_id: str | None = None
    dataset: str | None = None
    created_at: str | None = None


def load_evaluation_report(path: Path) -> dict[str, Any]:
    report = EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
    result = report.model_dump(exclude_none=True)
    result["metadata"]["schema_validated"] = True
    result["metadata"].setdefault("verified", False)
    return result
