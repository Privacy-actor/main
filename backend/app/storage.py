import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .anonymizer import redact_text
from .schemas import EntityType, ReviewRequest, Span


class Storage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=settings.database_timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(settings.database_timeout_seconds * 1000)}")
        return connection

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
            """)

    def ping(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1").fetchone()[0] == 1

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_task(self, task_id: str, preview: str, entity_count: int, risk: str, payload: dict[str, Any]):
        created = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?, ?, ?, ?, ?)", (task_id, created, preview, entity_count, risk, json.dumps(payload, ensure_ascii=False)))

    def add_audit(self, task_id: str, operation: str, payload: dict[str, Any]):
        with self.connect() as db:
            db.execute("INSERT INTO audits(task_id, created_at, operation, payload) VALUES (?, ?, ?, ?)", (task_id, datetime.now(timezone.utc).isoformat(), operation, json.dumps(payload, ensure_ascii=False)))

    def apply_review(self, request: ReviewRequest) -> dict[str, Any] | None:
        """Atomically update the canonical task snapshot and append its audit row."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM tasks WHERE id = ?", (request.task_id,)).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload"])
            spans = [Span.model_validate(item) for item in payload.get("spans", [])]
            target = next((span for span in spans if span.id == request.span_id), None)

            if request.operation == "add":
                if request.span is None or request.span.id != request.span_id:
                    raise ValueError("新增实体必须提交完整 span")
                if any(span.id == request.span_id for span in spans):
                    raise ValueError("Span id 已存在")
                target = request.span
                spans.append(target)
            elif target is None:
                raise LookupError("任务中不存在该 Span")
            elif request.operation == "accept":
                target.status = "accepted"
                target.conflict = False
            elif request.operation == "reject":
                target.status = "rejected"
                target.conflict = False
            elif request.operation == "change_type":
                target.entity_type = EntityType(request.after)
                target.status = "accepted"
                target.conflict = False
            elif request.operation == "adjust_boundary":
                try:
                    start, end = (int(value) for value in (request.after or "").split(":"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("调整边界需使用 start:end 格式") from exc
                text = payload.get("text", "")
                if not 0 <= start < end <= len(text):
                    raise ValueError("调整后的边界超出原文")
                target.start, target.end, target.text = start, end, text[start:end]
                target.status = "accepted"
                target.conflict = False

            spans.sort(key=lambda span: (span.start, span.end))
            text = payload.get("text", "")
            if any(span.end > len(text) or text[span.start:span.end] != span.text for span in spans):
                raise ValueError("复核 Span 与任务原文偏移不一致")
            if target is not None:
                target.sources = sorted(set(target.sources + ["HUMAN"]))
                target.metadata["last_review_operation"] = request.operation
            active = [span for span in spans if span.status != "rejected"]
            if any(current.start < previous.end for previous, current in zip(active, active[1:])):
                raise ValueError("复核结果包含重叠的有效 Span")
            payload["spans"] = [span.model_dump(mode="json") for span in spans]
            payload["redacted_text"] = redact_text(text, spans, None)
            counts: dict[str, int] = {}
            for span in active:
                counts[span.entity_type.value] = counts.get(span.entity_type.value, 0) + 1
            risk_score = min(100, sum(
                count * (18 if entity_type in {"ID_CARD", "BANK_CARD"} else 10)
                for entity_type, count in counts.items()
            ))
            payload.setdefault("summary", {}).update(
                total=len(active), pending=sum(span.status == "pending" for span in active),
                by_type=counts, risk_score=risk_score,
            )
            now = datetime.now(timezone.utc).isoformat()
            encoded = json.dumps(payload, ensure_ascii=False)
            risk = "high" if risk_score >= 60 else "medium" if risk_score >= 25 else "low"
            db.execute("UPDATE tasks SET entity_count = ?, risk = ?, payload = ? WHERE id = ?", (len(active), risk, encoded, request.task_id))
            db.execute(
                "INSERT INTO audits(task_id, created_at, operation, payload) VALUES (?, ?, ?, ?)",
                (request.task_id, now, request.operation, json.dumps(request.model_dump(mode="json"), ensure_ascii=False)),
            )
            return payload

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id, created_at, preview, entity_count, risk FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def audits(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audits ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def review_queue(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id, payload FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            audits = db.execute("SELECT task_id, payload FROM audits WHERE operation IN ('accept','reject')").fetchall()
        resolved = {(row["task_id"], json.loads(row["payload"]).get("span_id")) for row in audits}
        items = []
        for row in rows:
            payload = json.loads(row["payload"])
            text = payload.get("text", "")
            for span in payload.get("spans", []):
                if (row["id"], span.get("id")) in resolved or (span.get("status") != "pending" and not span.get("conflict")):
                    continue
                items.append({"task_id": row["id"], "context": text[max(0, span["start"]-35):span["end"]+35], "span": span, "reason": "识别器冲突" if span.get("conflict") else "低置信度候选"})
        return items[:limit]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)", (key, json.dumps(value, ensure_ascii=False), now))
