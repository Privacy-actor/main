import asyncio
import csv
import hashlib
import io
import json
import re
import time
import uuid
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .anonymizer import PSEUDONYM_EPSILON, redact_with_map
from .config import BASE_DIR, settings
from .documents import DocumentError, column_hint_spans, display_text, parse_document, restore_table
from .evaluation import load_evaluation_report
from .instruction_parser import parse_instruction
from .knowledge_base import knowledge_entry_count
from .knowledge_graph import knowledge_graph
from .llm_adapter import cloud_endpoint, find_all_occurrences, http_client, json_payload, local_enabled, local_endpoint, resolve_endpoint
from .model_settings import model_settings
from .ner_adapter import ner_adapter
from .pipeline import run_pipeline
from .recheck import recheck_text
from .recognizers import PATTERNS, compile_user_pattern, detect_implicit_spans, detect_lite_ner_spans, find_user_matches, user_regex
from .semantic_adapter import semantic_encoder
from .schemas import (
    DetectRequest, DetectResponse, EntityType, FinalTextUpdate, InstructionRequest, KnowledgeLookupRequest, ModelProbeRequest, ModelSettingsUpdate,
    PolicyUpdate, ProcessingConfig, ProjectCreate, ProjectUpdate, RecheckRequest, RedactRequest,
    ReviewRequest, RuleCreate, RuleTestRequest, RuleUpdate, Span, Strategy,
)
from .storage import RevisionConflictError, Storage

@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启用 NER 模型时在后台预加载，第一次识别不用等模型加载
    warmup = asyncio.create_task(ner_adapter.warmup())
    yield
    warmup.cancel()


app = FastAPI(title=settings.app_name, version="0.3.0", docs_url="/api/docs", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$", allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
storage = Storage(settings.database_path)
model_settings.attach(storage)


_VALIDATION_MESSAGES = {
    "missing": "缺少必填内容",
    "string_too_long": "过长（最多 {max_length} 个字符）",
    "string_too_short": "不能为空",
    "too_long": "数量过多（最多 {max_length} 项）",
    "string_unicode": "包含无法识别的字符，请删掉乱码后重试",
    "string_type": "需要文本",
    "int_parsing": "需要整数",
    "int_type": "需要整数",
    "float_parsing": "需要数字",
    "bool_parsing": "需要是或否",
    "enum": "取值无效",
    "literal_error": "取值无效",
    "less_than_equal": "数值过大（最大 {le}）",
    "greater_than_equal": "数值过小（最小 {ge}）",
    "json_invalid": "请求不是有效的 JSON",
    "model_attributes_type": "格式不对",
    "dict_type": "格式不对",
    "list_type": "需要列表",
}


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    """参数校验失败统一返回 422，只给出位置和原因，不把用户提交的内容回显到响应或日志里。"""
    errors = []
    for error in exc.errors():
        kind = str(error.get("type", ""))
        context = {key: value for key, value in (error.get("ctx") or {}).items() if isinstance(value, (int, float, str))}
        template = _VALIDATION_MESSAGES.get(kind)
        try:
            message = template.format(**context) if template else str(error.get("msg", "参数无效"))
        except (KeyError, IndexError):
            message = template or "参数无效"
        location = [str(part) for part in error.get("loc", []) if part not in ("body", "query", "path")]
        field = next((_FIELD_NAMES[part] for part in reversed(location) if part in _FIELD_NAMES), "")
        errors.append({"loc": location, "msg": f"{field}{message}" if field else message, "type": kind})
    return JSONResponse(status_code=422, content={"detail": errors})


_FIELD_NAMES = {
    "text": "文本", "instruction": "自然语言要求", "pattern": "规则内容", "name": "名称", "value": "词语",
    "preserve_terms": "保留词", "custom_keywords": "额外隐去的词", "custom_patterns": "自定义正则", "enabled_entity_types": "识别范围",
    "strategy": "脱敏方式", "privacy_strength": "保护力度", "spans": "实体", "language": "语言", "after": "修改内容",
    "description": "说明", "config": "方案", "scope_version": "方案版本", "expected_revision": "版本号",
}

# 按类型设置方式的默认值：人名用差分隐私替换保持可读，机构和地点用知识图谱泛化保留语境，
# 号码类标识用掩码保证不可逆。用户可在规则库中修改。
DEFAULT_POLICIES = {
    **{key.value: "mask" for key in EntityType},
    EntityType.PERSON.value: "pseudonymize",
    EntityType.ORG.value: "generalize",
    EntityType.LOCATION.value: "generalize",
    EntityType.ADDRESS.value: "generalize",
    # 职务身份泛化为“某部门负责人”，保留语义又切断与具体个人的关联
    EntityType.ROLE.value: "generalize",
}


async def _load_policies() -> dict[str, str]:
    """已保存的按类型方式，加上默认值补齐后来新增的类型（例如职务身份）。"""
    stored = await asyncio.to_thread(storage.get_setting, "policies", {})
    return {**DEFAULT_POLICIES, **(stored if isinstance(stored, dict) else {})}


EVALUATION_RESULTS = BASE_DIR / "reports" / "experiment_results" / "latest.json"
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


def _model_summary(deployment_mode: str = "local") -> dict[str, Any]:
    endpoint, _ = resolve_endpoint(deployment_mode)
    shown = endpoint or local_endpoint()
    return {"name": shown.model, "enabled": endpoint is not None, "mode": "non-thinking", "location": shown.location,
            "runtime": ("cloud-api" if shown.location == "cloud" else "openai-compatible") if endpoint else "lightweight"}


def _risk(spans) -> tuple[int, str]:
    active = [span for span in spans if span.status != "rejected"]
    score = min(100, sum(18 if span.entity_type in {EntityType.ID_CARD, EntityType.BANK_CARD, EntityType.PASSPORT} else 10 for span in active))
    return score, "high" if score >= 60 else "medium" if score >= 25 else "low"


def _parse_upload(filename: str, content: bytes, file_index: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        return parse_document(filename, content, file_index)
    except DocumentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


# 正在处理、已被删除的批处理：后台循环看到后立即停止，并删掉已经生成的任务
_CANCELLED_JOBS: set[str] = set()


async def _process_batch(job_id: str, records: list[dict[str, Any]], config: ProcessingConfig, project_id: str | None = None):
    existing = await asyncio.to_thread(storage.get_job, job_id) or {}
    payload = {**existing.get("payload", {}), "results": [], "failures": [], "config": config.model_dump(mode="json")}
    persistent_rules = await asyncio.to_thread(storage.list_rules, project_id)
    policies = None
    if config.use_policies:
        raw_policies = await _load_policies()
        policies = {EntityType(key): Strategy(value) for key, value in raw_policies.items()}
    await asyncio.to_thread(storage.update_job, job_id, status="running", payload=payload)
    known = await asyncio.to_thread(_file_entities, records, config)
    failed = 0
    created: list[str] = []
    file_tasks: dict[int, list[str]] = {}
    memories: dict[int, dict] = {}
    last_saved = time.monotonic()
    for index, record in enumerate(records):
        if job_id in _CANCELLED_JOBS:
            break
        file_index = int(record.get("file_index", 0))
        try:
            request = DetectRequest(text=record["text"], **config.model_dump(mode="json"))
            memory = memories.setdefault(file_index, {})
            hints = column_hint_spans(record, request.strategy) if record.get("kind") == "table" else []
            hints += _known_entity_hints(record, known.get(file_index, {}), request.strategy)
            spans, redacted, trace, applied_config, replacements = await run_pipeline(request, policies, persistent_rules, memory, hints)
            if record.get("kind") == "table":
                spans, redacted, replacements = await asyncio.to_thread(_keep_inside_fields, record, spans, redacted, replacements, request, policies, memory)
            task_id = f"task_{uuid.uuid4().hex[:12]}"
            active_spans = [span for span in spans if span.status != "rejected"]
            counts = Counter(span.entity_type.value for span in active_spans)
            _, risk = _risk(spans)
            snapshot = DetectResponse(
                task_id=task_id, text=record["text"], spans=spans, redacted_text=redacted, trace=trace,
                summary={"total": len(active_spans), "pending": sum(span.status == "pending" for span in active_spans), "risk_score": _risk(spans)[0], "by_type": counts},
                model=_model_summary(config.deployment_mode),
                created_at=datetime.now(timezone.utc).isoformat(), final_text=redacted, final_revision=0, has_manual_edits=False,
                applied_config=applied_config, project_id=project_id, replacements=replacements,
            ).model_dump(mode="json")
            snapshot["redaction_memory"] = memory
            snapshot["batch"] = {"job_id": job_id, "file": record["file"], "file_index": file_index, "row": record["row"], "kind": record.get("kind", "text")}
            await asyncio.to_thread(storage.save_task, task_id, redacted[:80], len(active_spans), risk, snapshot)
            created.append(task_id)
            file_tasks.setdefault(file_index, []).append(task_id)
            if job_id in _CANCELLED_JOBS:
                break
            payload["results"].append({
                "file": record["file"], "file_index": file_index, "row": record["row"], "kind": record.get("kind", "text"), "task_id": task_id,
                **({"keys": record["keys"], "empty_values": record.get("empty_values", {})} if record.get("keys") else {}),
                "redacted_text": redacted, "final_text": redacted, "final_revision": 0,
                "entity_count": len(active_spans),
                "by_type": dict(counts),
                "pending_count": sum(span.status == "pending" or span.conflict for span in active_spans),
                "status": "needs_review" if any(span.status == "pending" or span.conflict for span in spans) else "completed",
                "applied_config": applied_config,
            })
        except Exception as exc:
            failed += 1
            raw_text = record["text"]
            payload["failures"].append({
                "file": record["file"], "file_index": file_index, "row": record["row"],
                "text_length": len(raw_text),
                "text_hash": storage.text_hash(raw_text),
                "error": f"{type(exc).__name__}: {exc}",
            })
        # 写库节流：大批量时不必每段都整体重写一次任务进度
        if time.monotonic() - last_saved > 0.5 or index == len(records) - 1:
            await asyncio.to_thread(storage.update_job, job_id, status="running", processed=index + 1, failed=failed, payload=payload)
            last_saved = time.monotonic()
    if job_id in _CANCELLED_JOBS:
        for task_id in created:
            await asyncio.to_thread(storage.delete_task, task_id)
        _CANCELLED_JOBS.discard(job_id)
        return
    for file_index, task_ids in file_tasks.items():
        await asyncio.to_thread(storage.set_task_memory, task_ids, memories.get(file_index, {}))
    await asyncio.to_thread(storage.update_job, job_id, status="completed_with_errors" if failed else "completed", processed=len(records), failed=failed, payload=payload)


_FILE_PROPAGATED_TYPES = {EntityType.PERSON, EntityType.ORG, EntityType.LOCATION, EntityType.ADDRESS, EntityType.ROLE}


def _file_entities(records: list[dict[str, Any]], config: ProcessingConfig) -> dict[int, dict[str, EntityType]]:
    """同一文件里已能确定的实体（列名提示、规则与轻量识别的高置信度结果）。

    批处理按段处理，某段里识别出的姓名在同一文件的其他段里出现时也要处理（例如 CSV“姓名”列的人
    出现在另一行的“备注”里）。这里先快速扫一遍全部段落，处理时再作为提示加入。"""
    enabled = set(config.enabled_entity_types) & _FILE_PROPAGATED_TYPES
    known: dict[int, dict[str, EntityType]] = {}
    for record in records:
        found = known.setdefault(int(record.get("file_index", 0)), {})
        candidates = column_hint_spans(record, config.strategy) if record.get("kind") == "table" else []
        candidates += detect_lite_ner_spans(record["text"], config.strategy, enabled, config.language)[0]
        candidates += detect_implicit_spans(record["text"], config.strategy, enabled, config.language)[0]
        for span in candidates:
            if span.entity_type in enabled and span.status == "accepted" and len(span.text.strip()) >= 2:
                found.setdefault(span.text.strip(), span.entity_type)
    return known


def _known_entity_hints(record: dict[str, Any], known: dict[str, EntityType], strategy: Strategy) -> list[Span]:
    text = record["text"]
    hints: list[Span] = []
    for value, entity_type in known.items():
        for start, end in find_all_occurrences(text, value):
            hints.append(Span(id=f"file_{uuid.uuid4().hex[:10]}", start=start, end=end, text=value, entity_type=entity_type,
                              score=0.91, sources=["NER-LITE"], status="accepted", strategy=strategy, metadata={"file_propagation": True}))
    return hints


def _keep_inside_fields(record, spans, redacted, replacements, request, policies, memory):
    """表格记录里，实体只能落在某一格的内容里；跨格或落在列名上的识别结果去掉，再重新生成结果。"""
    fields = [(int(field["start"]), int(field["end"])) for field in record.get("fields") or []]
    kept = [span for span in spans if any(start <= span.start and span.end <= end for start, end in fields)]
    if len(kept) == len(spans):
        return spans, redacted, replacements
    strategy = None if policies else request.strategy
    redacted, replacements = redact_with_map(record["text"], kept, strategy, request.privacy_strength, request.risk_level == "strict", memory)
    return kept, redacted, replacements


def _safe_export_name(filename: str) -> str:
    normalized = re.sub(r"^[A-Za-z]:", "", filename.replace("\\", "/"))
    parts = []
    for part in normalized.split("/"):
        cleaned = re.sub(r'[<>:"|?*\x00-\x1f]', "_", part).rstrip(" .")
        if cleaned and cleaned not in {".", ".."}:
            parts.append(cleaned)
    safe = PurePosixPath(*parts) if parts else PurePosixPath("result.txt")
    stem = safe.stem or "result"
    return str(safe.with_name(f"{stem}.redacted.txt"))


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    """Return a UI/export-safe job view and hydrate each row from its latest task revision."""
    public = {**job, "payload": {**job.get("payload", {})}}
    results = []
    for item in job.get("payload", {}).get("results", []):
        clean = {key: value for key, value in item.items() if key != "text"}
        task = storage.get_task(str(item.get("task_id", ""))) if item.get("task_id") else None
        if task:
            clean["redacted_text"] = task.get("redacted_text", clean.get("redacted_text", ""))
            clean["final_text"] = task.get("final_text", clean["redacted_text"])
            clean["final_revision"] = int(task.get("final_revision", 0))
            clean["has_manual_edits"] = bool(task.get("has_manual_edits", False))
            active_spans = [span for span in task.get("spans", []) if span.get("status") != "rejected"]
            by_type: dict[str, int] = {}
            for span in active_spans:
                key = str(span.get("entity_type", "CUSTOM"))
                by_type[key] = by_type.get(key, 0) + 1
            clean["by_type"] = by_type
            clean["entity_count"] = len(active_spans)
            clean["pending_count"] = sum(span.get("status") == "pending" or bool(span.get("conflict")) for span in active_spans)
            clean["status"] = "needs_review" if any(span.get("status") == "pending" or span.get("conflict") for span in active_spans) else "completed"
        else:
            clean["final_text"] = clean.get("final_text", clean.get("redacted_text", ""))
            clean["final_revision"] = int(clean.get("final_revision", 0))
            clean["has_manual_edits"] = clean["final_text"] != clean.get("redacted_text", "")
            if item.get("task_id"):
                # 任务已在任务记录里删除或被清理：保留这一行，但不再提供打开和复核
                clean["status"] = "deleted"
                clean["pending_count"] = 0
        results.append(clean)
    failures = []
    for item in job.get("payload", {}).get("failures", []):
        clean = {key: value for key, value in item.items() if key != "text"}
        raw_text = item.get("text")
        if raw_text is not None:
            clean.setdefault("text_length", len(raw_text))
            clean.setdefault("text_hash", storage.text_hash(raw_text))
        failures.append(clean)
    public["payload"]["results"] = results
    public["payload"]["failures"] = failures
    return public


@app.get("/api/v1/health")
def health():
    try:
        database = "online" if storage.ping() else "offline"
    except Exception:
        database = "offline"
    return {
        "status": "ok" if database == "online" else "degraded", "version": app.version,
        "mode": "llm" if local_enabled() else "lightweight", "database": database, "deployment": "self-hosted",
        "cloud_llm": cloud_endpoint() is not None,
    }


@app.get("/api/v1/models")
def models():
    local = local_endpoint()
    cloud = cloud_endpoint()
    local_on = local_enabled()
    return {
        "active": local.model, "enabled": local_on,
        "mode": "non-thinking", "provider": "OpenAI 兼容接口" if local_on else "离线轻量模式",
        "ner": settings.ner_model, "ner_enabled": settings.ner_enabled, "ner_threshold": settings.ner_threshold,
        "ner_status": ner_adapter.status(),
        "multilingual": True,
        "semantic": semantic_encoder.status(),
        "knowledge_graph": knowledge_graph.status(),
        "rules": {"builtin_patterns": len(PATTERNS), "entity_types": len(EntityType)},
        "knowledge_entries": knowledge_entry_count(),
        "confidence_threshold": settings.confidence_threshold,
        "pseudonym_epsilon": {str(key): value for key, value in PSEUDONYM_EPSILON.items()},
        "endpoints": {
            "local": {"enabled": local_on, "model": local.model, "host": local.host},
            "cloud": {"enabled": cloud is not None, "model": cloud.model if cloud else "", "host": cloud.host if cloud else ""},
        },
    }


def _settings_view() -> dict[str, Any]:
    return {"local": model_settings.public("local"), "cloud": model_settings.public("cloud"), "editable": settings.model_settings_editable}


def _require_editable() -> None:
    if not settings.model_settings_editable:
        raise HTTPException(403, "服务器管理员关闭了在网页里修改模型设置，请在服务器的 .env 中配置")


@app.get("/api/v1/llm/settings")
def llm_settings():
    """本地、云端大模型的当前设置。密钥只返回是否已保存和尾号。"""
    return _settings_view()


@app.put("/api/v1/llm/settings")
async def update_llm_settings(request: ModelSettingsUpdate):
    _require_editable()
    for target in request.reset:
        await asyncio.to_thread(model_settings.reset, target)
    for target, patch in (("local", request.local), ("cloud", request.cloud)):
        if patch is None:
            continue
        if patch.enabled and not (patch.base_url and patch.model.strip()):
            raise HTTPException(422, "启用前请填写服务地址和模型名称")
        await asyncio.to_thread(model_settings.update, target, patch.model_dump())
    return _settings_view()


def _probe_key(request: ModelProbeRequest) -> str:
    if request.api_key:
        return request.api_key.strip()
    return model_settings.saved_key_for(request.target, request.base_url)


def _probe_error(exc: Exception, base_url: str) -> str:
    host = urlparse(base_url).netloc or base_url
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        try:
            detail = exc.response.json()
            message = (detail.get("error") or {}).get("message") if isinstance(detail.get("error"), dict) else detail.get("error") or detail.get("message") or detail.get("detail")
        except ValueError:
            message = ""
        message = str(message or "")[:160]
        if code in {401, 403}:
            return f"{host} 拒绝了请求（{code}），请检查密钥是否正确、是否有这个模型的权限"
        if code == 404:
            return f"{host} 上找不到这个接口或模型（404），请检查服务地址（一般以 /v1 结尾）和模型名称"
        if code == 429:
            return f"{host} 提示请求过多或额度不足（429）{('：' + message) if message else ''}"
        return f"{host} 返回错误（{code}）{('：' + message) if message else ''}"
    if isinstance(exc, httpx.TimeoutException):
        return f"{host} 在规定时间内没有响应，请确认服务已启动、网络可以访问"
    if isinstance(exc, httpx.RequestError):
        return f"连接不上 {host}，请确认服务已启动、地址和端口正确"
    return f"{type(exc).__name__}：{str(exc)[:160]}"


@app.post("/api/v1/llm/models")
async def list_llm_models(request: ModelProbeRequest):
    """读取服务提供的模型列表（OpenAI 兼容的 GET /models；Ollama 本机模型也走这里）。"""
    _require_editable()
    headers = {"Authorization": f"Bearer {key}"} if (key := _probe_key(request)) else {}
    try:
        async with http_client(request.base_url, 10) as client:
            response = await client.get(f"{request.base_url.rstrip('/')}/models", headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
            raise HTTPException(400, "这个服务没有提供模型列表，请直接填写模型名称") from exc
        raise HTTPException(400, _probe_error(exc, request.base_url)) from exc
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict) and items is None:
        items = data.get("models")
    names = sorted({str(item.get("id") or item.get("name") or item.get("model")) for item in items or [] if isinstance(item, dict) and (item.get("id") or item.get("name") or item.get("model"))})
    if not names:
        raise HTTPException(400, "服务没有返回可用的模型。本地服务请先下载模型（如 ollama pull qwen3:8b），云端服务请直接填写模型名称")
    return {"models": names[:300], "count": len(names)}


@app.post("/api/v1/llm/test")
async def test_llm(request: ModelProbeRequest):
    """用一条很短的请求试一次：能连上、密钥有效、模型能回复。"""
    _require_editable()
    if not request.model.strip():
        raise HTTPException(422, "请先填写模型名称")
    headers = {"Authorization": f"Bearer {key}"} if (key := _probe_key(request)) else {}
    body = {"model": request.model.strip(), "temperature": 0, "max_tokens": 32,
            "messages": [{"role": "system", "content": "只用 JSON 回复。/no_think"}, {"role": "user", "content": '回复 {"ok": true}'}]}
    started = time.perf_counter()
    try:
        async with http_client(request.base_url, min(60.0, settings.llm_timeout_seconds)) as client:
            response = await client.post(f"{request.base_url.rstrip('/')}/chat/completions", headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        message = data["choices"][0]["message"]
        reply = (message.get("content") or "").strip()
    except (httpx.HTTPError, ValueError, LookupError, TypeError) as exc:
        return {"ok": False, "message": _probe_error(exc, request.base_url)}
    elapsed = round((time.perf_counter() - started) * 1000)
    shown = json_payload(reply)[:80] if reply else "（模型只返回了思考过程，也能使用）"
    return {"ok": True, "latency_ms": elapsed, "reply": shown, "message": f"连接成功，{request.model.strip()} 用时 {elapsed} 毫秒"}


@app.get("/api/v1/stats")
async def stats():
    return await asyncio.to_thread(storage.stats)


@app.get("/api/v1/knowledge/status")
def knowledge_status():
    return knowledge_graph.status()


@app.post("/api/v1/knowledge/lookup")
async def knowledge_lookup(request: KnowledgeLookupRequest):
    return (await knowledge_graph.lookup(request.term, request.entity_type, request.allow_remote, request.lang)).model_dump()


@app.post("/api/v1/instructions/parse")
async def parse_requirement(request: InstructionRequest):
    return await parse_instruction(request.instruction, request.use_llm, request.deployment_mode)


@app.post("/api/v1/detect", response_model=DetectResponse)
async def detect(request: DetectRequest):
    if request.project_id:
        project = await asyncio.to_thread(storage.get_project, request.project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        explicit = request.model_dump(include=request.model_fields_set, mode="json")
        request = DetectRequest.model_validate({**project["config"], **explicit})
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    policies = None
    if request.use_policies:
        raw_policies = await _load_policies()
        policies = {EntityType(key): Strategy(value) for key, value in raw_policies.items()}
    persistent_rules = await asyncio.to_thread(storage.list_rules, request.project_id)
    memory: dict = {}
    spans, redacted, trace, applied_config, replacements = await run_pipeline(request, policies, persistent_rules, memory)
    active_spans = [span for span in spans if span.status != "rejected"]
    counts = Counter(span.entity_type.value for span in active_spans)
    risk_score, risk = _risk(spans)
    created = datetime.now(timezone.utc).isoformat()
    if not request.persist:
        task_id = f"preview_{uuid.uuid4().hex[:12]}"
    response = DetectResponse(
        task_id=task_id, text=request.text, spans=spans, redacted_text=redacted, trace=trace,
        summary={"total": len(active_spans), "pending": sum(span.status == "pending" for span in active_spans), "risk_score": risk_score, "by_type": counts},
        model=_model_summary(request.deployment_mode),
        created_at=created, final_text=redacted, final_revision=0, has_manual_edits=False, applied_config=applied_config,
        project_id=request.project_id, replacements=replacements, persisted=request.persist,
    )
    if request.persist:
        await asyncio.to_thread(storage.save_task, task_id, redacted[:80], len(active_spans), risk, {**response.model_dump(mode="json"), "redaction_memory": memory})
    return response


@app.post("/api/v1/redact")
def redact(request: RedactRequest):
    for span in request.spans:
        if span.end > len(request.text) or request.text[span.start:span.end] != span.text:
            raise HTTPException(422, f"Span {span.id} 与原文偏移不一致")
    allowed_statuses = {"accepted", "pending"} if request.risk_level == "strict" else {"accepted"}
    active = sorted((span for span in request.spans if span.status in allowed_statuses), key=lambda span: (span.start, span.end))
    if any(current.start < previous.end for previous, current in zip(active, active[1:])):
        raise HTTPException(422, "有效 Span 之间不能重叠，请先完成冲突消歧")
    redacted, replacements = redact_with_map(request.text, request.spans, request.strategy, request.privacy_strength, include_pending=request.risk_level == "strict")
    return {"redacted_text": redacted, "replacements": replacements}


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
    items, total = await asyncio.to_thread(storage.review_queue)
    return {"items": items, "total": total}


@app.put("/api/v1/tasks/{task_id}/final-text")
async def save_final_text(task_id: str, request: FinalTextUpdate):
    try:
        result = await asyncio.to_thread(storage.save_final_text, task_id, request)
    except RevisionConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "任务不存在")
    return result


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str):
    task = await asyncio.to_thread(storage.get_task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@app.get("/api/v1/tasks/{task_id}/audits")
async def task_audits(task_id: str):
    if await asyncio.to_thread(storage.get_task, task_id) is None:
        raise HTTPException(404, "任务不存在")
    return {"items": await asyncio.to_thread(storage.task_audits, task_id)}


@app.post("/api/v1/tasks/{task_id}/recheck")
async def recheck_task(task_id: str, request: RecheckRequest | None = None):
    task = await asyncio.to_thread(storage.get_task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    text = request.text if request and request.text is not None else task.get("final_text", task.get("redacted_text", ""))
    rules = await asyncio.to_thread(storage.list_rules, task.get("project_id"))
    result = await asyncio.to_thread(recheck_text, text, task, rules)
    return {**result, "task_id": task_id, "checked_at": datetime.now(timezone.utc).isoformat()}


def _docx_bytes(text: str) -> bytes:
    try:
        from docx import Document
    except ImportError as exc:
        raise HTTPException(503, "服务器尚未安装 python-docx，无法导出 DOCX") from exc
    document = Document()
    # Word 的换行符（\v）和分页符（\f）按换行处理，其他 XML 不允许的控制字符去掉，否则无法写入 DOCX
    cleaned = re.sub(r"[\x00-\x08\x0e-\x1f\ufffe\uffff]", "", re.sub(r"[\v\f\r]", "\n", text.replace("\r\n", "\n")))
    cleaned = re.sub(r"[\ud800-\udfff]", "\ufffd", cleaned)
    for line in cleaned.split("\n"):
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@app.get("/api/v1/tasks/{task_id}/export")
async def export_task(task_id: str, format: str = "txt"):
    task = await asyncio.to_thread(storage.get_task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    final_text = task.get("final_text")
    if final_text is None:
        final_text = task.get("redacted_text", "")
    if format == "txt":
        content, media, name = final_text.encode("utf-8"), "text/plain; charset=utf-8", f"{task_id}-final.txt"
    elif format == "md":
        content, media, name = final_text.encode("utf-8"), "text/markdown; charset=utf-8", f"{task_id}-final.md"
    elif format == "docx":
        content = await asyncio.to_thread(_docx_bytes, final_text)
        media, name = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"{task_id}-final.docx"
    elif format == "json":
        audits = await asyncio.to_thread(storage.task_audits, task_id)
        content = json.dumps({**task, "audits": audits}, ensure_ascii=False, indent=2).encode("utf-8")
        media, name = "application/json", f"{task_id}-audit.json"
    else:
        raise HTTPException(422, "导出格式仅支持 txt、md、docx、json")
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.delete("/api/v1/tasks/{task_id}")
async def delete_task(task_id: str):
    if not await asyncio.to_thread(storage.delete_task, task_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@app.delete("/api/v1/tasks")
async def purge_tasks(older_than_days: int = 30):
    if not 1 <= older_than_days <= 3650:
        raise HTTPException(422, "保留天数需在 1 到 3650 之间")
    return {"deleted": await asyncio.to_thread(storage.purge_tasks, older_than_days)}


@app.get("/api/v1/history")
async def history(limit: int = 30, audit_limit: int = 50, offset: int = 0, q: str = "", pending: bool = False, project_id: str | None = None):
    limit = max(1, min(limit, 500))
    audit_limit = max(1, min(audit_limit, 500))
    offset = max(0, offset)
    (items, total), audits = await asyncio.gather(
        asyncio.to_thread(storage.history, limit, offset, q.strip()[:200], pending, project_id),
        asyncio.to_thread(storage.audits, audit_limit),
    )
    return {"items": items, "total": total, "audits": audits}


@app.get("/api/v1/history/{task_id}")
async def history_detail(task_id: str):
    detail = await asyncio.to_thread(storage.task_detail, task_id)
    if detail is None:
        raise HTTPException(404, "历史任务不存在")
    return detail


@app.get("/api/v1/evaluations")
def evaluations():
    if EVALUATION_RESULTS.exists():
        try:
            return load_evaluation_report(EVALUATION_RESULTS)
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "评估结果文件无效，请重新生成；未切换为演示指标。") from exc
    return DEMO_METRICS


@app.get("/api/v1/evaluations/export")
def export_evaluation():
    payload = json.dumps(evaluations(), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    return StreamingResponse(io.BytesIO(payload), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="privshield-evaluation.json"'})


@app.get("/api/v1/policies")
async def get_policies():
    return {"policies": await _load_policies(), "version": "policy-2026.07"}


@app.put("/api/v1/policies")
async def update_policies(request: PolicyUpdate):
    policies = await _load_policies()
    policies.update({key.value: value.value for key, value in request.policies.items()})
    await asyncio.to_thread(storage.set_setting, "policies", policies)
    return {"policies": policies, "version": "policy-2026.07-custom"}


@app.get("/api/v1/projects")
async def list_projects():
    return {"items": await asyncio.to_thread(storage.list_projects)}


@app.post("/api/v1/projects")
async def create_project(request: ProjectCreate):
    project_id = f"project_{uuid.uuid4().hex[:10]}"
    return await asyncio.to_thread(storage.create_project, project_id, request)


@app.put("/api/v1/projects/{project_id}")
async def update_project(project_id: str, request: ProjectUpdate):
    result = await asyncio.to_thread(storage.update_project, project_id, request)
    if result is None:
        raise HTTPException(404, "项目不存在")
    return result


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        deleted = await asyncio.to_thread(storage.delete_project, project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


@app.post("/api/v1/extract")
async def extract_files(files: list[UploadFile] = File(...)):
    records: list[dict[str, Any]] = []
    for index, upload in enumerate(files):
        content = await upload.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, f"{upload.filename} 超过 {settings.max_upload_bytes // 1_000_000} MB")
        parsed, _ = await asyncio.to_thread(_parse_upload, upload.filename or "upload.txt", content, index)
        records.extend(parsed)
    public = [{key: record[key] for key in ("file", "row", "text", "kind")} for record in records]
    return {"records": public, "text": display_text(records), "files": len(files)}


@app.get("/api/v1/rules")
async def list_rules(project_id: str | None = None):
    return {"items": await asyncio.to_thread(storage.list_rules, project_id)}


@app.post("/api/v1/rules")
async def create_rule(request: RuleCreate):
    if request.project_id and await asyncio.to_thread(storage.get_project, request.project_id) is None:
        raise HTTPException(404, "项目不存在")
    if request.kind == "regex":
        _validate_user_regex(request.pattern)
    rule_id = f"rule_{uuid.uuid4().hex[:10]}"
    return await asyncio.to_thread(storage.save_rule, rule_id, request)

@app.post("/api/v1/rules/test")
def test_rule(request: RuleTestRequest):
    # 试匹配是边输入边调用的，输入到一半的正则（比如只写了左括号）很常见，
    # 这里把“无效”作为结果返回，而不是当作请求错误。保存规则时仍然严格校验。
    try:
        compiled = compile_user_pattern(request.pattern, request.kind == "keyword", request.case_sensitive)
    except user_regex.error as exc:
        return {"valid": False, "error": f"正则表达式无效：{exc}", "matches": [], "count": 0, "timed_out": False}
    found, complete = find_user_matches(compiled, request.text, 0.5)
    matches = [{"start": start, "end": end, "text": request.text[start:end]} for start, end in found[:200]]
    return {
        "valid": True, "error": None if complete else "匹配超时：这个正则回溯太多，请简化写法（例如避免 (a+)+ 这类嵌套重复）",
        "matches": matches, "count": len(found), "timed_out": not complete,
    }


def _validate_user_regex(pattern: str) -> None:
    try:
        compile_user_pattern(pattern)
    except user_regex.error as exc:
        raise HTTPException(422, f"正则表达式无效：{exc}") from exc


@app.put("/api/v1/rules/{rule_id}")
async def update_rule(rule_id: str, request: RuleUpdate):
    existing = await asyncio.to_thread(storage.get_rule, rule_id)
    if existing is None:
        raise HTTPException(404, "规则不存在")
    next_kind = request.kind or existing["kind"]
    next_pattern = request.pattern or existing["pattern"]
    if next_kind == "regex":
        _validate_user_regex(next_pattern)
    return await asyncio.to_thread(storage.update_rule, rule_id, request)


@app.delete("/api/v1/rules/{rule_id}")
async def delete_rule(rule_id: str):
    if not await asyncio.to_thread(storage.delete_rule, rule_id):
        raise HTTPException(404, "规则不存在")
    return {"ok": True}


@app.post("/api/v1/jobs")
async def create_batch_job(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    config_json: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    strategy: Strategy = Strategy.MASK,
):
    uploads = list(files or []) + ([file] if file else [])
    if not uploads:
        raise HTTPException(422, "请至少上传一个文件")
    project = None
    if project_id:
        project = await asyncio.to_thread(storage.get_project, project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
    records: list[dict[str, Any]] = []
    file_meta: list[dict[str, Any]] = []
    for index, upload in enumerate(uploads):
        content = await upload.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, f"{upload.filename} 超过 {settings.max_upload_bytes // 1_000_000} MB")
        parsed, meta = await asyncio.to_thread(_parse_upload, upload.filename or "upload.txt", content, index)
        records.extend(parsed)
        file_meta.append(meta)
    if not records:
        raise HTTPException(400, "文件中没有可处理文本")
    if len(records) > settings.max_batch_records:
        raise HTTPException(413, f"单次最多处理 {settings.max_batch_records} 段，请分批处理")
    try:
        explicit_config = json.loads(config_json) if config_json else {"strategy": strategy.value}
        if not isinstance(explicit_config, dict):
            raise ValueError("处理配置必须是 JSON 对象")
        config_data = {**(project["config"] if project else {}), **explicit_config, "project_id": project_id}
        config = ProcessingConfig.model_validate(config_data)
    except (json.JSONDecodeError, ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, f"处理配置无效：{exc}") from exc
    for record in records:
        if len(record["text"]) > 100_000:
            raise HTTPException(422, f"{record['file']} 第 {record['row']} 段超过 100000 字符")
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    await asyncio.to_thread(storage.create_job, job_id, project_id, len(records), {"results": [], "failures": [], "files": [upload.filename for upload in uploads], "file_meta": file_meta, "config": config.model_dump(mode="json")})
    background_tasks.add_task(_process_batch, job_id, records, config, project_id)
    return await asyncio.to_thread(storage.get_job, job_id)


@app.get("/api/v1/jobs")
async def list_jobs(project_id: str | None = None):
    jobs = await asyncio.to_thread(storage.list_jobs, project_id=project_id)
    return {"items": await asyncio.gather(*(asyncio.to_thread(_public_job, job) for job in jobs))}


@app.get("/api/v1/jobs/{job_id}/download")
async def download_job(job_id: str):
    stored_job = await asyncio.to_thread(storage.get_job, job_id)
    if stored_job is None:
        raise HTTPException(404, "批处理任务不存在")
    job = await asyncio.to_thread(_public_job, stored_job)
    if job["status"] in {"queued", "running"}:
        raise HTTPException(409, "批处理尚未完成")
    buffer = io.BytesIO()
    file_meta = {int(meta.get("index", position)): meta for position, meta in enumerate(job["payload"].get("file_meta") or [])}
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for item in job["payload"].get("results", []):
        if item.get("status") == "deleted":
            continue
        # 新任务按上传顺序分组，同名文件不会被合并；旧任务没有序号时按文件名分组
        key = ("index", int(item["file_index"])) if "file_index" in item else ("name", item.get("file") or "result.txt")
        grouped.setdefault(key, []).append(item)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for key, items in grouped.items():
            ordered = sorted(items, key=lambda item: int(item.get("row", 0)))
            filename = ordered[0].get("file") or "result.txt"
            meta = file_meta.get(key[1]) if key[0] == "index" else None
            export_name = _safe_export_name(filename)
            if meta and meta.get("kind") == "table":
                rows = []
                for item in ordered:
                    task = await asyncio.to_thread(storage.get_task, str(item.get("task_id", ""))) if item.get("task_id") else None
                    rows.append(((task or {}).get("text", ""), str(item.get("final_text", item.get("redacted_text", "")))))
                suffix, content = restore_table(meta, rows, [{"keys": item.get("keys"), "empty_values": item.get("empty_values")} for item in ordered])
                export_name = str(PurePosixPath(export_name).with_suffix(suffix))
            elif meta:
                content = "".join(str(item.get("final_text", item.get("redacted_text", ""))) for item in ordered)
            else:
                content = "\n".join(str(item.get("final_text", item.get("redacted_text", ""))) for item in ordered)
            candidate = export_name
            suffix_number = 2
            while candidate in used_names:
                path = PurePosixPath(export_name)
                candidate = str(path.with_name(f"{path.stem}-{suffix_number}{path.suffix}"))
                suffix_number += 1
            used_names.add(candidate)
            archive.writestr(candidate, content)
        archive.writestr("manifest.json", json.dumps(job, ensure_ascii=False, indent=2))
        failures = job["payload"].get("failures", [])
        if failures:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["file", "row", "error", "text_length", "text_hash"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(failures)
            archive.writestr("failures.csv", "\ufeff" + output.getvalue())
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}-redacted.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str):
    result = await asyncio.to_thread(storage.get_job, job_id)
    if result is None:
        raise HTTPException(404, "批处理任务不存在")
    return await asyncio.to_thread(_public_job, result)


@app.delete("/api/v1/jobs/{job_id}")
async def delete_job(job_id: str):
    job = await asyncio.to_thread(storage.get_job, job_id)
    if job is None:
        raise HTTPException(404, "批处理任务不存在")
    if job["status"] in {"queued", "running"}:
        # 正在处理的批处理：通知后台循环停止，并由它删掉之后生成的任务
        _CANCELLED_JOBS.add(job_id)
    await asyncio.to_thread(storage.delete_job, job_id)
    return {"ok": True}
