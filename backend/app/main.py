import asyncio
import csv
import io
import json
from pathlib import Path
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .anonymizer import redact_text
from .config import BASE_DIR, settings
from .recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
from .pipeline import run_pipeline
from .schemas import DetectRequest, DetectResponse, EntityType, PolicyUpdate, RedactRequest, ReviewRequest, Strategy, TraceStep
from .storage import Storage

app = FastAPI(title=settings.app_name, version="0.1.0", docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
storage = Storage(settings.database_path)

DEFAULT_POLICIES = {key: "mask" for key in ["PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE", "EMAIL", "ID_CARD", "BANK_CARD"]}
DEMO_METRICS = {
    "is_demo": True,
    "notice": "演示数据：接入冻结测试集后由 benchmark 输出自动替换",
    "metadata": {"source": "built-in-placeholder", "verified": False, "metric": "illustrative-only"},
    "systems": [
        {"name": "仅规则", "precision": 0.98, "recall": 0.61, "f1": 0.75, "latency": 18},
        {"name": "轻量 NER", "precision": 0.82, "recall": 0.76, "f1": 0.79, "latency": 142},
        {"name": "规则 + NER", "precision": 0.91, "recall": 0.87, "f1": 0.89, "latency": 168},
        {"name": "级联 + 14B", "precision": 0.93, "recall": 0.94, "f1": 0.935, "latency": 1840},
    ],
    "categories": [
        {"name": "电话", "recall": 0.99}, {"name": "邮箱", "recall": 0.99}, {"name": "姓名", "recall": 0.91},
        {"name": "地址", "recall": 0.89}, {"name": "机构", "recall": 0.92}, {"name": "证件", "recall": 0.98},
    ],
}


@app.get("/api/v1/health")
def health():
    try:
        database = "online" if storage.ping() else "offline"
    except Exception:
        database = "offline"
    return {"status": "ok" if database == "online" else "degraded", "version": app.version, "mode": "llm" if settings.llm_enabled else "lightweight", "database": database}


@app.get("/api/v1/models")
def models():
    return {
        "active": settings.llm_model, "enabled": settings.llm_enabled,
        "mode": "non-thinking", "provider": "OpenAI-compatible vLLM" if settings.llm_enabled else "离线轻量模式",
        "candidates": ["Qwen/Qwen3-14B", "Qwen/Qwen3-14B-AWQ", "Qwen/Qwen3-8B", "Qwen/Qwen2.5-7B-Instruct"],
    }


@app.post("/api/v1/detect", response_model=DetectResponse)
async def detect(request: DetectRequest):
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    policies = None
    if request.use_policies:
        raw_policies = await asyncio.to_thread(storage.get_setting, "policies", DEFAULT_POLICIES)
        policies = {EntityType(key): Strategy(value) for key, value in raw_policies.items()}
    spans, redacted, trace = await run_pipeline(request, policies)
    active_spans = [span for span in spans if span.status != "rejected"]
    counts = Counter(span.entity_type.value for span in active_spans)
    risk_score = min(100, sum(18 if key in {"ID_CARD", "BANK_CARD"} else 10 for key in counts for _ in range(counts[key])))
    created = datetime.now(timezone.utc).isoformat()
    response = DetectResponse(
        task_id=task_id, text=request.text, spans=spans, redacted_text=redacted, trace=trace,
        summary={"total": len(active_spans), "pending": sum(s.status == "pending" for s in active_spans), "risk_score": risk_score, "by_type": counts},
        model={"name": settings.llm_model, "enabled": settings.llm_enabled, "mode": "non-thinking", "runtime": "vLLM" if settings.llm_enabled else "lightweight"},
        created_at=created,
    )
    # History previews must not duplicate raw PII. The full source remains in the
    # protected task payload because human review requires exact offsets.
    await asyncio.to_thread(storage.save_task, task_id, redacted[:80], len(active_spans), "high" if risk_score >= 60 else "medium" if risk_score >= 25 else "low", response.model_dump(mode="json"))
    return response


@app.post("/api/v1/redact")
def redact(request: RedactRequest):
    for span in request.spans:
        if span.end > len(request.text) or request.text[span.start:span.end] != span.text:
            raise HTTPException(422, f"Span {span.id} 与原文偏移不一致")
    active = sorted((span for span in request.spans if span.status != "rejected"), key=lambda span: (span.start, span.end))
    if any(current.start < previous.end for previous, current in zip(active, active[1:])):
        raise HTTPException(422, "有效 Span 之间不能重叠，请先完成冲突消歧")
    return {"redacted_text": redact_text(request.text, request.spans, request.strategy)}


@app.post("/api/v1/reviews")
async def review(request: ReviewRequest):
    try:
        snapshot = await asyncio.to_thread(storage.apply_review, request)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if snapshot is None:
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "recorded_at": datetime.now(timezone.utc).isoformat(), "snapshot": snapshot}


@app.get("/api/v1/reviews")
async def review_queue():
    return {"items": await asyncio.to_thread(storage.review_queue)}


@app.get("/api/v1/history")
async def history():
    items, audits = await asyncio.gather(asyncio.to_thread(storage.history), asyncio.to_thread(storage.audits))
    return {"items": items, "audits": audits}


@app.get("/api/v1/evaluations")
def evaluations():
    results_file = BASE_DIR / "reports" / "experiment_results" / "latest.json"
    if results_file.exists():
        result = json.loads(results_file.read_text(encoding="utf-8"))
        result["is_demo"] = False
        result.setdefault("metadata", {}).update(source=str(results_file), verified=True)
        return result
    return DEMO_METRICS


@app.get("/api/v1/policies")
async def get_policies():
    policies = await asyncio.to_thread(storage.get_setting, "policies", DEFAULT_POLICIES)
    return {"policies": policies, "version": "policy-2026.07"}


@app.put("/api/v1/policies")
async def update_policies(request: PolicyUpdate):
    policies = await asyncio.to_thread(storage.get_setting, "policies", DEFAULT_POLICIES.copy())
    policies.update({k.value: v.value for k, v in request.policies.items()})
    await asyncio.to_thread(storage.set_setting, "policies", policies)
    return {"policies": policies, "version": "policy-2026.07-custom"}


@app.post("/api/v1/jobs")
async def batch_job(file: UploadFile = File(...), strategy: Strategy = Strategy.MASK, use_policies: bool = False):
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, f"批处理文件上限为 {settings.max_upload_bytes // 1_000_000} MB")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "文件需使用 UTF-8 编码") from exc
    texts: list[str]
    if file.filename and file.filename.lower().endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(decoded)))
        if not rows:
            raise HTTPException(400, "CSV 为空")
        column = "text" if "text" in rows[0] else next(iter(rows[0]))
        texts = [row.get(column, "") for row in rows if row.get(column)]
    elif file.filename and file.filename.lower().endswith(".json"):
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"JSON 格式错误（第 {exc.lineno} 行）") from exc
        if isinstance(parsed, list):
            texts = [str(item.get("text", item.get("content", ""))) if isinstance(item, dict) else str(item) for item in parsed]
            texts = [item for item in texts if item]
        else:
            raise HTTPException(400, "JSON 顶层需为数组")
    else:
        texts = [line for line in decoded.splitlines() if line.strip()]
    if not texts:
        raise HTTPException(400, "文件中没有可处理文本")
    if len(texts) > settings.max_batch_records:
        raise HTTPException(413, f"单次最多处理 {settings.max_batch_records} 条记录")
    results = []
    policies = None
    if use_policies:
        raw_policies = await asyncio.to_thread(storage.get_setting, "policies", DEFAULT_POLICIES)
        policies = {EntityType(key): Strategy(value) for key, value in raw_policies.items()}
    for index, text in enumerate(texts):
        if len(text) > 100_000:
            raise HTTPException(422, f"第 {index + 1} 条文本超过 100000 字符")
        batch_request = DetectRequest(text=text, strategy=strategy, use_llm=True, language="auto", risk_level="strict")
        spans, redacted, _ = await run_pipeline(batch_request, policies)
        results.append({"row": index + 1, "text": text, "redacted_text": redacted, "entity_count": len(spans), "pending_count": sum(s.status == "pending" for s in spans)})
    return {"job_id": f"job_{uuid.uuid4().hex[:10]}", "status": "completed", "total": len(results), "results": results}
