import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .anonymizer import infer_replacements, memory_from_replacements, redact_with_map
from .config import settings
from .schemas import EntityType, FinalTextUpdate, ReviewRequest, Span, Strategy, migrate_scope


class RevisionConflictError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_preview(payload: dict[str, Any], fallback: str = "") -> str:
    final_text = payload.get("final_text")
    if final_text is None:
        final_text = payload.get("redacted_text", fallback)
    return final_text[:80]


def _changed_character_count(before: str, after: str) -> int:
    prefix = 0
    limit = min(len(before), len(after))
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    remaining = min(len(before) - prefix, len(after) - prefix)
    while suffix < remaining and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    return max(len(before) - prefix - suffix, len(after) - prefix - suffix)


def _pending_count(payload: dict[str, Any]) -> int:
    return sum(
        span.get("status") == "pending" or bool(span.get("conflict"))
        for span in payload.get("spans", []) if span.get("status") != "rejected"
    )


def public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """返回给界面的任务内容：去掉只在服务端使用的替换记忆。"""
    return {key: value for key, value in payload.items() if key != "redaction_memory"}


class Storage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init()
        secret = self.get_setting("audit_secret")
        if not isinstance(secret, str) or len(secret) < 32:
            secret = secrets.token_hex(32)
            self.set_setting("audit_secret", secret)
        self._audit_key = secret.encode("utf-8")

    def text_hash(self, value: str) -> str:
        """操作记录里的文本摘要：带本机密钥的 HMAC-SHA256。

        不加密钥的 SHA-256 可以对手机号、证件号这类取值空间有限的信息逐个穷举还原。"""
        return hmac.new(self._audit_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=settings.database_timeout_seconds)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(f"PRAGMA busy_timeout = {int(settings.database_timeout_seconds * 1000)}")
        return db

    def _init(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, preview TEXT NOT NULL,
                    entity_count INTEGER NOT NULL, risk TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    config TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS custom_rules (
                    id TEXT PRIMARY KEY, project_id TEXT, created_at TEXT NOT NULL,
                    name TEXT NOT NULL, kind TEXT NOT NULL, pattern TEXT NOT NULL,
                    entity_type TEXT NOT NULL, enabled INTEGER NOT NULL, case_sensitive INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL, total INTEGER NOT NULL, processed INTEGER NOT NULL,
                    failed INTEGER NOT NULL, payload TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(projects)").fetchall()}
            if "config" not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
            task_columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            if "pending" not in task_columns or "project_id" not in task_columns:
                # 待确认数和所属项目单独成列，复核队列和任务记录不用逐个解析全部任务
                if "pending" not in task_columns:
                    db.execute("ALTER TABLE tasks ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
                if "project_id" not in task_columns:
                    db.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT")
                for row in db.execute("SELECT id, payload FROM tasks").fetchall():
                    try:
                        payload = json.loads(row["payload"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    db.execute("UPDATE tasks SET pending=?, project_id=? WHERE id=?", (_pending_count(payload), payload.get("project_id"), row["id"]))
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_pending ON tasks(pending, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)")
            self._sanitize_legacy_job_payloads(db)

    @staticmethod
    def _sanitize_legacy_job_payloads(db: sqlite3.Connection) -> None:
        """Remove duplicated source text from jobs created by older releases."""
        rows = db.execute("SELECT id, payload FROM jobs").fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            changed = False
            for result in payload.get("results", []):
                if "text" in result:
                    result.pop("text", None)
                    changed = True
            for failure in payload.get("failures", []):
                raw_text = failure.pop("text", None)
                if raw_text is not None:
                    raw_text = str(raw_text)
                    failure.setdefault("text_length", len(raw_text))
                    failure.setdefault("text_hash", hashlib.sha256(raw_text.encode("utf-8")).hexdigest())
                    changed = True
            if changed:
                db.execute(
                    "UPDATE jobs SET payload=?, updated_at=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), _now(), row["id"]),
                )

    def ping(self):
        with self.connect() as db:
            return db.execute("SELECT 1").fetchone()[0] == 1

    def get_task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        # 改版前保存的任务没有替换映射，读取时按结果文本对齐补出，供界面联动高亮
        if "replacements" not in payload and payload.get("spans") and isinstance(payload.get("redacted_text"), str):
            payload["replacements"] = infer_replacements(payload.get("text", ""), payload["spans"], payload["redacted_text"])
        return public_payload(payload)

    def task_exists(self, task_id) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is not None

    def save_task(self, task_id, preview, entity_count, risk, payload):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO tasks(id,created_at,preview,entity_count,risk,payload,pending,project_id) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, payload.get("created_at", _now()), preview, entity_count, risk, json.dumps(payload, ensure_ascii=False),
                 _pending_count(payload), payload.get("project_id")),
            )

    def set_task_memory(self, task_ids: list[str], memory: dict) -> None:
        """批处理完成后把整个文件的替换记忆写回该文件的每个任务，之后复核新增的实体不会和其他段撞号。"""
        with self.connect() as db:
            for task_id in task_ids:
                row = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row is None:
                    continue
                payload = json.loads(row["payload"])
                payload["redaction_memory"] = memory
                db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), task_id))

    def delete_task(self, task_id):
        with self.connect() as db:
            db.execute("DELETE FROM audits WHERE task_id=?", (task_id,))
            cursor = db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return cursor.rowcount > 0

    def purge_tasks(self, older_than_days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM tasks WHERE created_at < ?", (cutoff,)).fetchall()]
            for task_id in ids:
                db.execute("DELETE FROM audits WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return len(ids)

    def apply_review(self, request: ReviewRequest):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (request.task_id,)).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload"])
            spans = [Span.model_validate(item) for item in payload.get("spans", [])]
            target = next((item for item in spans if item.id == request.span_id), None)
            touched: list[Span] = []
            skipped = 0
            if request.operation == "add_many":
                if not request.spans:
                    raise ValueError("批量补充需要提交 spans")
                text = payload.get("text", "")
                known_ids = {item.id for item in spans}
                active_ranges = [(item.start, item.end) for item in spans if item.status != "rejected"]
                for candidate in request.spans:
                    if candidate.id in known_ids:
                        raise ValueError("Span id 已存在")
                    if candidate.end > len(text) or text[candidate.start:candidate.end] != candidate.text:
                        raise ValueError("补充的实体与任务原文偏移不一致")
                    if any(candidate.start < end and candidate.end > start for start, end in active_ranges):
                        skipped += 1
                        continue
                    candidate.status, candidate.conflict = "accepted", False
                    spans.append(candidate)
                    touched.append(candidate)
                    known_ids.add(candidate.id)
                    active_ranges.append((candidate.start, candidate.end))
                if not touched:
                    raise ValueError("这些位置已被其他实体覆盖，无需重复补充")
                target = None
            elif request.operation in {"accept_many", "reject_many"}:
                wanted = set(request.span_ids or [])
                if not wanted:
                    raise ValueError("批量操作需要提交 span_ids")
                status = "accepted" if request.operation == "accept_many" else "rejected"
                for item in spans:
                    if item.id in wanted:
                        item.status, item.conflict = status, False
                        touched.append(item)
                if not touched:
                    raise LookupError("任务中不存在这些 Span")
                target = None
            elif request.operation == "add":
                if request.span is None or request.span.id != request.span_id:
                    raise ValueError("新增实体必须提交完整 span")
                if any(item.id == request.span_id for item in spans):
                    raise ValueError("Span id 已存在")
                target = request.span
                spans.append(target)
            elif request.operation == "set_strategy":
                try:
                    strategy = Strategy(request.after)
                except ValueError as exc:
                    raise ValueError("脱敏策略无效") from exc
                for item in spans:
                    item.strategy = strategy
                # 统一换成一种方式后，不再按实体类型分别设置
                payload.setdefault("applied_config", {}).update(strategy=strategy.value, use_policies=False)
                target = None
            elif request.operation == "set_strength":
                try:
                    strength = int(request.after or "")
                except ValueError as exc:
                    raise ValueError("保护强度需为 1、2 或 3") from exc
                if strength not in {1, 2, 3}:
                    raise ValueError("保护强度需为 1、2 或 3")
                payload.setdefault("applied_config", {})["privacy_strength"] = strength
                target = None
            elif target is None:
                raise LookupError("任务中不存在该 Span")
            elif request.operation == "accept":
                target.status, target.conflict = "accepted", False
            elif request.operation == "reject":
                target.status, target.conflict = "rejected", False
            elif request.operation == "change_type":
                target.entity_type, target.status, target.conflict = EntityType(request.after), "accepted", False
                if request.strategy is not None:
                    target.strategy = request.strategy
            elif request.operation == "set_span_strategy":
                target.strategy, target.status = Strategy(request.after), "accepted"
            elif request.operation == "set_replacement":
                replacement = request.after or ""
                if len(replacement) > 500:
                    raise ValueError("custom replacement must not exceed 500 characters")
                if replacement:
                    target.metadata["custom_replacement"] = replacement
                else:
                    target.metadata.pop("custom_replacement", None)
                target.status = "accepted"
            elif request.operation == "adjust_boundary":
                try:
                    start, end = (int(value) for value in (request.after or "").split(":"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("调整边界需使用 start:end 格式") from exc
                text = payload.get("text", "")
                if not 0 <= start < end <= len(text):
                    raise ValueError("调整后的边界超出原文")
                target.start, target.end, target.text = start, end, text[start:end]
                target.status, target.conflict = "accepted", False

            text = payload.get("text", "")
            spans.sort(key=lambda item: (item.start, item.end))
            if any(item.end > len(text) or text[item.start:item.end] != item.text for item in spans):
                raise ValueError("复核 Span 与任务原文偏移不一致")
            for item in ([target] if target is not None else []) + touched:
                item.sources = sorted(set(item.sources + ["HUMAN"]))
                item.metadata["last_review_operation"] = request.operation
            active = [item for item in spans if item.status != "rejected"]
            if any(current.start < previous.end for previous, current in zip(active, active[1:])):
                raise ValueError("复核结果包含重叠的有效 Span")

            payload["spans"] = [item.model_dump(mode="json") for item in spans]
            applied_config = payload.get("applied_config", {})
            strength = int(applied_config.get("privacy_strength", 2))
            include_pending = applied_config.get("risk_level", "strict") == "strict"
            memory = payload.get("redaction_memory")
            if not isinstance(memory, dict):
                # 旧任务没有替换记忆：从已有替换映射推出，复核后编号和替换词不变
                memory = memory_from_replacements(payload.get("replacements") or [], payload["spans"], strength)
            generated, replacements = redact_with_map(text, spans, None, strength, include_pending, memory)
            payload["redaction_memory"] = memory
            payload["redacted_text"] = generated
            payload["replacements"] = replacements
            if not payload.get("has_manual_edits", False):
                payload["final_text"] = generated
            counts: dict[str, int] = {}
            for item in active:
                counts[item.entity_type.value] = counts.get(item.entity_type.value, 0) + 1
            risk_score = min(100, sum(count * (18 if entity_type in {"ID_CARD", "PASSPORT", "BANK_CARD"} else 10) for entity_type, count in counts.items()))
            payload.setdefault("summary", {}).update(total=len(active), pending=sum(item.status == "pending" or item.conflict for item in active), by_type=counts, risk_score=risk_score)
            risk = "high" if risk_score >= 60 else "medium" if risk_score >= 25 else "low"
            db.execute("UPDATE tasks SET entity_count=?, risk=?, payload=?, pending=? WHERE id=?", (len(active), risk, json.dumps(payload, ensure_ascii=False), _pending_count(payload), request.task_id))
            audit_payload = request.model_dump(mode="json")
            if request.operation == "set_replacement":
                for field in ("before", "after"):
                    raw_value = audit_payload.get(field) or ""
                    audit_payload[field] = {
                        "text_length": len(raw_value),
                        "text_hash": self.text_hash(raw_value),
                    }
            if request.span is not None:
                raw = request.span.text
                audit_payload["span"] = {
                    "id": request.span.id,
                    "start": request.span.start,
                    "end": request.span.end,
                    "entity_type": request.span.entity_type.value,
                    "text_length": len(raw),
                    "text_hash": self.text_hash(raw),
                }
            if request.spans is not None:
                audit_payload["spans"] = [{
                    "id": item.id, "start": item.start, "end": item.end, "entity_type": item.entity_type.value,
                    "text_length": len(item.text), "text_hash": self.text_hash(item.text),
                } for item in touched]
                audit_payload["skipped"] = skipped
            if request.operation in {"accept_many", "reject_many"}:
                audit_payload["span_ids"] = [item.id for item in touched]
            db.execute(
                "INSERT INTO audits(task_id,created_at,operation,payload) VALUES (?,?,?,?)",
                (request.task_id, _now(), request.operation, json.dumps(audit_payload, ensure_ascii=False)),
            )
            if skipped:
                payload = {**payload, "review_notice": f"已跳过 {skipped} 处已被其他实体覆盖的位置"}
            return public_payload(payload)

    def save_final_text(self, task_id, request: FinalTextUpdate):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload"])
            revision = int(payload.get("final_revision", 0))
            if request.expected_revision != revision:
                raise RevisionConflictError(f"最终稿已被其他页面更新（服务器 v{revision}，当前页面 v{request.expected_revision}）")
            automatic_text = payload.get("redacted_text", "")
            if request.automatic_text != automatic_text:
                raise RevisionConflictError("自动稿已因实体或策略复核发生变化，请先复制保留当前人工稿，再重新载入任务")
            before = payload.get("final_text", payload.get("redacted_text", ""))
            if before == request.text:
                return {
                    "final_text": before, "final_revision": revision,
                    "has_manual_edits": before != automatic_text,
                    "saved_at": payload.get("final_text_updated_at"), "changed": False,
                }
            new_revision = revision + 1
            saved_at = _now()
            before_hash = self.text_hash(before)
            after_hash = self.text_hash(request.text)
            audit = {
                "revision": new_revision, "before_length": len(before), "after_length": len(request.text),
                "changed_characters": _changed_character_count(before, request.text),
                "before": before_hash, "after": after_hash,
                "before_hash": before_hash, "after_hash": after_hash, "note": request.note,
            }
            payload.update(
                final_text=request.text,
                final_revision=new_revision, final_text_updated_at=saved_at,
                has_manual_edits=request.text != automatic_text,
            )
            db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), task_id))
            db.execute("INSERT INTO audits(task_id,created_at,operation,payload) VALUES (?,?,?,?)", (task_id, saved_at, "edit_text", json.dumps(audit, ensure_ascii=False)))
            return {
                "final_text": request.text, "final_revision": new_revision,
                "has_manual_edits": request.text != automatic_text,
                "saved_at": saved_at, "changed": True, "audit": audit,
            }

    def history(self, limit=30, offset=0, query: str = "", pending_only: bool = False, project_id: str | None = None):
        """任务记录：按时间倒序分页，可按关键字（原文、结果、任务号）、待确认、项目筛选。返回 (条目, 总数)。"""
        where, params = [], []
        if query:
            where.append("(payload LIKE ? OR id LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if pending_only:
            where.append("pending > 0")
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM tasks {clause}", params).fetchone()[0]
            rows = db.execute(f"SELECT id,created_at,preview,entity_count,risk,payload FROM tasks {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            payload = json.loads(item.pop("payload"))
            item["preview"] = _task_preview(payload, item["preview"])
            spans = [span for span in payload.get("spans", []) if span.get("status") != "rejected"]
            config = payload.get("applied_config") or {}
            item.update({
                "entity_count": len(spans),
                "pending": sum(span.get("status") == "pending" or bool(span.get("conflict")) for span in spans),
                "project_id": payload.get("project_id"),
                "strategy": config.get("strategy"),
                "use_policies": bool(config.get("use_policies")),
                "has_manual_edits": bool(payload.get("has_manual_edits")),
                "final_revision": int(payload.get("final_revision") or 0),
                "text_length": len(payload.get("text", "")),
            })
            items.append(item)
        return items, total

    def task_detail(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT id,created_at,preview,entity_count,risk,payload FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        detail = dict(row)
        payload = json.loads(detail.pop("payload"))
        detail["preview"] = _task_preview(payload, detail["preview"])
        return {**public_payload(payload), **detail}

    def audits(self, limit=50):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audits ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def task_audits(self, task_id: str, limit: int = 200):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM audits WHERE task_id=? ORDER BY created_at DESC, id DESC LIMIT ?", (task_id, limit),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def stats(self):
        with self.connect() as db:
            task_rows = db.execute("SELECT payload FROM tasks").fetchall()
            job_count = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            audit_count = db.execute("SELECT COUNT(*) FROM audits").fetchone()[0]
        by_type: dict[str, int] = {}
        entities = pending = edited = 0
        for row in task_rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            edited += int(bool(payload.get("has_manual_edits")))
            for span in payload.get("spans", []):
                if span.get("status") == "rejected":
                    continue
                entities += 1
                pending += int(span.get("status") == "pending" or bool(span.get("conflict")))
                key = str(span.get("entity_type", "CUSTOM"))
                by_type[key] = by_type.get(key, 0) + 1
        return {
            "tasks": len(task_rows), "entities": entities, "pending": pending, "edited_tasks": edited,
            "jobs": job_count, "audits": audit_count, "by_type": by_type,
        }

    def review_queue(self, limit=500):
        """所有任务中待确认或有冲突的实体，按任务时间倒序。返回 (条目, 总数)。"""
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM tasks WHERE pending > 0 ORDER BY created_at DESC").fetchall()
        items = []
        total = 0
        for row in rows:
            payload = json.loads(row["payload"])
            text = payload.get("text", "")
            for span in payload.get("spans", []):
                if span.get("status") == "rejected" or (span.get("status") != "pending" and not span.get("conflict")):
                    continue
                total += 1
                if len(items) >= limit:
                    continue
                context_start = max(0, span["start"] - 60)
                items.append({
                    "task_id": row["id"],
                    "context": text[context_start:span["end"] + 60],
                    "context_offset": context_start,
                    "text_length": len(text),
                    "created_at": payload.get("created_at"),
                    "project_id": payload.get("project_id"),
                    "span": span,
                    "reason": "识别器冲突" if span.get("conflict") else "低置信度候选",
                })
        return items, total

    def get_setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?,?)", (key, json.dumps(value, ensure_ascii=False), _now()))

    @staticmethod
    def _project_row(row):
        if row is None:
            return None
        item = dict(row)
        item["config"] = migrate_scope(json.loads(item.get("config") or "{}"))
        return item

    def list_projects(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["config"] = migrate_scope(json.loads(item.get("config") or "{}"))
            except json.JSONDecodeError:
                item["config"] = {}
            items.append(item)
        return items

    def get_project(self, project_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["config"] = migrate_scope(json.loads(item.get("config") or "{}"))
        return item

    def create_project(self, project_id, request):
        now = _now()
        payload = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(request)
        with self.connect() as db:
            db.execute(
                "INSERT INTO projects(id,name,description,config,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (project_id, payload["name"], payload.get("description", ""), json.dumps(payload.get("config", {}), ensure_ascii=False), now, now),
            )
        return self.get_project(project_id)

    def update_project(self, project_id, request):
        current = self.get_project(project_id)
        if current is None:
            return None
        changes = request.model_dump(mode="json", exclude_none=True) if hasattr(request, "model_dump") else dict(request)
        name = changes.get("name", current["name"])
        description = changes.get("description", current["description"])
        config = changes.get("config", current["config"])
        with self.connect() as db:
            db.execute(
                "UPDATE projects SET name=?,description=?,config=?,updated_at=? WHERE id=?",
                (name, description, json.dumps(config, ensure_ascii=False), _now(), project_id),
            )
        return self.get_project(project_id)

    def delete_project(self, project_id):
        with self.connect() as db:
            db.execute("DELETE FROM custom_rules WHERE project_id=?", (project_id,))
            cursor = db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return cursor.rowcount > 0

    def list_rules(self, project_id=None):
        with self.connect() as db:
            if project_id:
                rows = db.execute("SELECT * FROM custom_rules WHERE project_id IS NULL OR project_id=? ORDER BY created_at", (project_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM custom_rules WHERE project_id IS NULL ORDER BY created_at").fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"]), "case_sensitive": bool(row["case_sensitive"])} for row in rows]

    def get_rule(self, rule_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM custom_rules WHERE id=?", (rule_id,)).fetchone()
        return {**dict(row), "enabled": bool(row["enabled"]), "case_sensitive": bool(row["case_sensitive"])} if row else None

    def save_rule(self, rule_id, data):
        now = _now()
        payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else dict(data)
        entity_type = payload["entity_type"].value if hasattr(payload["entity_type"], "value") else payload["entity_type"]
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO custom_rules(id,project_id,created_at,name,kind,pattern,entity_type,enabled,case_sensitive) VALUES (?,?,?,?,?,?,?,?,?)",
                (rule_id, payload.get("project_id"), now, payload["name"], payload["kind"], payload["pattern"], entity_type, int(payload.get("enabled", True)), int(payload.get("case_sensitive", False))),
            )
        return self.get_rule(rule_id)

    def update_rule(self, rule_id, data):
        current = self.get_rule(rule_id)
        if current is None:
            return None
        changes = data.model_dump(mode="json", exclude_none=True) if hasattr(data, "model_dump") else dict(data)
        merged = {**current, **changes}
        with self.connect() as db:
            db.execute(
                "UPDATE custom_rules SET name=?,kind=?,pattern=?,entity_type=?,enabled=?,case_sensitive=? WHERE id=?",
                (merged["name"], merged["kind"], merged["pattern"], merged["entity_type"], int(merged["enabled"]), int(merged["case_sensitive"]), rule_id),
            )
        return self.get_rule(rule_id)

    def delete_rule(self, rule_id):
        with self.connect() as db:
            cursor = db.execute("DELETE FROM custom_rules WHERE id=?", (rule_id,))
        return cursor.rowcount > 0

    def create_job(self, job_id, project_id, total, payload):
        now = _now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, project_id, now, now, "queued", total, 0, 0, json.dumps(payload, ensure_ascii=False)),
            )
        return self.get_job(job_id)

    def update_job(self, job_id, *, status=None, processed=None, failed=None, payload=None):
        current = self.get_job(job_id)
        if current is None:
            return None
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET updated_at=?,status=?,processed=?,failed=?,payload=? WHERE id=?",
                (_now(), status or current["status"], processed if processed is not None else current["processed"], failed if failed is not None else current["failed"], json.dumps(payload if payload is not None else current["payload"], ensure_ascii=False), job_id),
            )
        return self.get_job(job_id)

    def get_job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["progress"] = 100 if item["total"] == 0 else round(item["processed"] / item["total"] * 100)
        return item

    def list_jobs(self, limit=30, project_id=None):
        with self.connect() as db:
            if project_id:
                rows = db.execute("SELECT id FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.get_job(row["id"]) for row in rows]

    def delete_job(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            return False
        task_ids = {
            str(item.get("task_id"))
            for item in job.get("payload", {}).get("results", [])
            if item.get("task_id")
        }
        with self.connect() as db:
            for task_id in task_ids:
                db.execute("DELETE FROM audits WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            cursor = db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cursor.rowcount > 0
