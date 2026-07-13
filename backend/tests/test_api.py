import io
import json
import uuid
import zipfile

from fastapi.testclient import TestClient

from app.main import app, storage


client = TestClient(app)


def test_detect_api_full_flow():
    text = "我叫王洋，电话是13800138000，邮箱是wang@example.com，住在北京市海淀区中关村大街59号。"
    response = client.post("/api/v1/detect", json={"text": text, "strategy": "mask", "use_llm": True, "language": "auto", "risk_level": "strict"})
    assert response.status_code == 200
    result = response.json()
    assert {span["entity_type"] for span in result["spans"]} >= {"PERSON", "PHONE", "EMAIL", "ADDRESS"}
    assert all(text[span["start"]:span["end"]] == span["text"] for span in result["spans"])
    assert "wang@example.com" not in result["redacted_text"]
    email = next(span for span in result["spans"] if span["entity_type"] == "EMAIL")
    assert email["text"] == "wang@example.com"
    assert result["final_text"] == result["redacted_text"]
    assert result["final_revision"] == 0
    assert result["has_manual_edits"] is False


def test_health_and_evaluation_api():
    assert client.get("/api/v1/health").status_code == 200
    response = client.get("/api/v1/evaluations")
    assert response.status_code == 200
    assert len(response.json()["systems"]) >= 4


def test_review_rejects_unknown_task():
    response = client.post("/api/v1/reviews", json={
        "task_id": "task_missing", "span_id": "span_missing", "operation": "accept"
    })
    assert response.status_code == 404


def test_redact_rejects_overlapping_active_spans():
    text = "王洋电话"
    span = lambda span_id, start, end: {
        "id": span_id, "start": start, "end": end, "text": text[start:end],
        "entity_type": "PERSON", "sources": ["MANUAL"], "status": "accepted",
        "strategy": "mask",
    }
    response = client.post("/api/v1/redact", json={
        "text": text, "strategy": "mask", "spans": [span("one", 0, 2), span("two", 1, 3)]
    })
    assert response.status_code == 422


def test_batch_reports_malformed_json_as_client_error():
    response = client.post(
        "/api/v1/jobs", files={"file": ("records.json", b"[{broken", "application/json")}
    )
    assert response.status_code == 400
    assert "JSON" in response.json()["detail"]


def test_review_updates_persisted_snapshot():
    detected = client.post("/api/v1/detect", json={"text": "电话13800138000", "use_llm": False}).json()
    phone = next(span for span in detected["spans"] if span["entity_type"] == "PHONE")
    response = client.post("/api/v1/reviews", json={
        "task_id": detected["task_id"], "span_id": phone["id"],
        "operation": "reject", "before": "accepted", "after": "rejected",
    })
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    persisted = next(span for span in snapshot["spans"] if span["id"] == phone["id"])
    assert persisted["status"] == "rejected"
    assert "13800138000" in snapshot["redacted_text"]


def test_entity_policies_can_drive_pipeline_redaction():
    saved = client.put("/api/v1/policies", json={"policies": {"PHONE": "generalize"}})
    assert saved.status_code == 200
    response = client.post("/api/v1/detect", json={
        "text": "电话13800138000", "strategy": "mask", "use_policies": True, "use_llm": False,
    })
    assert response.status_code == 200
    assert "某联系电话" in response.json()["redacted_text"]


def test_evaluation_response_is_explicitly_marked_demo():
    result = client.get("/api/v1/evaluations").json()
    assert result["is_demo"] is True
    assert result["metadata"]["verified"] is False


def test_final_text_editor_persists_revision_and_audit():
    detected = client.post(
        "/api/v1/detect", json={"text": "电话13800138000", "use_llm": False}
    ).json()
    final_text = f"{detected['redacted_text']}（已人工复核）"
    response = client.put(
        f"/api/v1/tasks/{detected['task_id']}/final-text",
        json={
            "text": final_text,
            "automatic_text": detected["redacted_text"],
            "expected_revision": 0,
            "note": "人工补充说明",
        },
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["final_text"] == final_text
    assert saved["final_revision"] == 1
    assert saved["has_manual_edits"] is True
    assert saved["audit"]["changed_characters"] > 0
    assert "13800138000" not in saved["audit"]["before"]
    assert "13800138000" not in saved["audit"]["after"]

    audits = client.get("/api/v1/history").json()["audits"]
    audit = next(item for item in audits if item["task_id"] == detected["task_id"])
    assert audit["operation"] == "edit_text"
    assert audit["payload"]["after_hash"]


def test_final_text_editor_rejects_stale_revision():
    detected = client.post(
        "/api/v1/detect", json={"text": "邮箱a@example.com", "use_llm": False}
    ).json()
    url = f"/api/v1/tasks/{detected['task_id']}/final-text"
    payload = {
        "text": "第一次修改",
        "automatic_text": detected["redacted_text"],
        "expected_revision": 0,
    }
    assert client.put(url, json=payload).status_code == 200
    stale = client.put(url, json={**payload, "text": "过期修改"})
    assert stale.status_code == 409


def test_strategy_change_is_persisted_and_audited():
    detected = client.post(
        "/api/v1/detect", json={"text": "电话13800138000", "use_llm": False}
    ).json()
    response = client.post("/api/v1/reviews", json={
        "task_id": detected["task_id"],
        "span_id": "all",
        "operation": "set_strategy",
        "before": "mask",
        "after": "generalize",
    })
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert all(span["strategy"] == "generalize" for span in snapshot["spans"])
    assert "某联系电话" in snapshot["redacted_text"]


def test_project_rule_and_project_config_form_a_reusable_pipeline():
    project_response = client.post("/api/v1/projects", json={
        "name": "访谈材料项目",
        "description": "项目级配置与规则测试",
        "config": {
            "strategy": "generalize",
            "privacy_strength": 3,
            "use_llm": False,
            "enabled_entity_types": ["PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE", "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT", "CUSTOM"],
        },
    })
    assert project_response.status_code == 200
    project = project_response.json()
    rule_response = client.post("/api/v1/rules", json={
        "project_id": project["id"], "name": "内部项目代号", "kind": "keyword",
        "pattern": "内部代号Z", "entity_type": "CUSTOM", "enabled": True,
    })
    assert rule_response.status_code == 200

    detected = client.post("/api/v1/detect", json={"text": "材料含内部代号Z", "project_id": project["id"]})
    assert detected.status_code == 200
    payload = detected.json()
    assert any(span["entity_type"] == "CUSTOM" and span["text"] == "内部代号Z" for span in payload["spans"])
    assert "受保护信息" in payload["redacted_text"]
    assert payload["applied_config"]["privacy_strength"] == 3

    updated = client.put(f"/api/v1/projects/{project['id']}", json={"description": "已更新"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "已更新"

    regex_rule = client.post("/api/v1/rules", json={
        "project_id": project["id"], "name": "企业账号", "kind": "regex",
        "pattern": r"ACCT-\d{8}", "entity_type": "CUSTOM", "enabled": True,
    }).json()
    invalid_update = client.put(f"/api/v1/rules/{regex_rule['id']}", json={"pattern": "[broken"})
    assert invalid_update.status_code == 422

    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 200


def test_instruction_parser_applies_preserve_and_force_terms():
    parsed = client.post("/api/v1/instructions/parse", json={
        "instruction": "保留北京地名，隐去上海地名，使用最高强度泛化", "use_llm": False,
    })
    assert parsed.status_code == 200
    plan = parsed.json()
    assert "北京" in plan["preserve_terms"]
    assert "上海" in plan["force_terms"]
    assert plan["strategy"] == "generalize"
    assert plan["privacy_strength"] == 3

    detected = client.post("/api/v1/detect", json={
        "text": "北京和上海", "instruction": "保留北京地名，隐去上海地名，使用最高强度泛化",
        "use_llm": False,
    })
    assert detected.status_code == 200
    result = detected.json()
    assert "北京" in result["redacted_text"]
    assert "上海" not in result["redacted_text"]


def test_batch_exports_current_manual_final_text_without_duplicating_source_text():
    raw_text = "电话13800138000\n邮箱a@example.com"
    response = client.post(
        "/api/v1/jobs",
        files=[("files", ("folder/a.txt", raw_text.encode(), "text/plain"))],
        data={"config_json": '{"use_llm": false, "strategy": "mask"}'},
    )
    assert response.status_code == 200
    job = client.get(f"/api/v1/jobs/{response.json()['id']}").json()
    assert job["status"] in {"completed", "completed_with_errors"}
    assert len(job["payload"]["results"]) == 2
    assert all("text" not in row for row in job["payload"]["results"])

    first = job["payload"]["results"][0]
    task = client.get(f"/api/v1/tasks/{first['task_id']}").json()
    revised = "【人工确认后的最终稿】"
    saved = client.put(
        f"/api/v1/tasks/{first['task_id']}/final-text",
        json={
            "text": revised,
            "automatic_text": task["redacted_text"],
            "expected_revision": task["final_revision"],
        },
    )
    assert saved.status_code == 200

    refreshed = client.get(f"/api/v1/jobs/{job['id']}").json()
    refreshed_first = next(row for row in refreshed["payload"]["results"] if row["task_id"] == first["task_id"])
    assert refreshed_first["final_text"] == revised
    assert refreshed_first["has_manual_edits"] is True

    archive = client.get(f"/api/v1/jobs/{job['id']}/download")
    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        text_exports = [name for name in bundle.namelist() if name.endswith(".redacted.txt")]
        assert any(revised in bundle.read(name).decode("utf-8") for name in text_exports)
        manifest = bundle.read("manifest.json").decode("utf-8")
        assert revised in manifest
        assert raw_text not in manifest

    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    assert client.get(f"/api/v1/tasks/{first['task_id']}").status_code == 404


def test_legacy_batch_payload_is_sanitized_before_api_exposure():
    job_id = f"job_legacy_{uuid.uuid4().hex[:10]}"
    source = "旧版本敏感原文13800138000"
    storage.create_job(job_id, None, 1, {
        "results": [{"file": "legacy.txt", "row": 1, "text": source}],
        "failures": [{"file": "bad.txt", "row": 1, "text": source, "error": "legacy"}],
    })
    storage.update_job(job_id, status="completed", processed=1)
    try:
        public = client.get(f"/api/v1/jobs/{job_id}")
        assert public.status_code == 200
        payload = public.json()["payload"]
        assert "text" not in payload["results"][0]
        assert "text" not in payload["failures"][0]
        assert payload["failures"][0]["text_length"] == len(source)
        assert payload["failures"][0]["text_hash"]
        assert source not in public.text
    finally:
        storage.delete_job(job_id)

def test_task_can_be_deleted_after_export():
    detected = client.post("/api/v1/detect", json={"text": "邮箱delete@example.com", "use_llm": False}).json()
    task_id = detected["task_id"]
    assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404


def test_manual_add_audit_does_not_repeat_sensitive_text():
    text = "ordinary secret-token-42 tail"
    detected = client.post("/api/v1/detect", json={"text": text, "use_llm": False}).json()
    start = text.index("secret-token-42")
    response = client.post("/api/v1/reviews", json={
        "task_id": detected["task_id"],
        "span_id": "manual-audit-span",
        "operation": "add",
        "span": {
            "id": "manual-audit-span", "start": start, "end": start + len("secret-token-42"),
            "text": "secret-token-42", "entity_type": "CUSTOM", "sources": ["MANUAL"],
            "status": "accepted", "strategy": "mask",
        },
    })
    assert response.status_code == 200
    audits = client.get("/api/v1/history").json()["audits"]
    audit = next(item for item in audits if item["task_id"] == detected["task_id"] and item["operation"] == "add")
    assert "secret-token-42" not in json.dumps(audit["payload"], ensure_ascii=False)
    assert audit["payload"]["span"]["text_hash"]


def test_extract_docx_and_text_files():
    from docx import Document

    document = Document()
    document.add_paragraph("\u738b\u6d0b\u7684\u7535\u8bdd\u662f13800138000")
    buffer = io.BytesIO()
    document.save(buffer)

    response = client.post(
        "/api/v1/extract",
        files=[
            ("files", ("notes.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("memo.txt", "\u90ae\u7bb1a@example.com".encode("utf-8"), "text/plain")),
        ],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["files"] == 2
    assert "13800138000" in payload["text"]
    assert "a@example.com" in payload["text"]
    assert {record["file"] for record in payload["records"]} == {"notes.docx", "memo.txt"}


def test_browser_extension_origin_is_allowed_by_cors():
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "chrome-extension://privacy-redactor-test",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "chrome-extension://privacy-redactor-test"
    assert "GET" in response.headers["access-control-allow-methods"]


def test_selected_entity_custom_replacement_is_applied_and_audited_without_plaintext():
    detected = client.post("/api/v1/detect", json={"text": "Email owner@example.com", "use_llm": False}).json()
    email = next(span for span in detected["spans"] if span["entity_type"] == "EMAIL")
    response = client.post("/api/v1/reviews", json={
        "task_id": detected["task_id"],
        "span_id": email["id"],
        "operation": "set_replacement",
        "before": "",
        "after": "contact-hidden",
    })
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert "contact-hidden" in snapshot["redacted_text"]
    updated = next(span for span in snapshot["spans"] if span["id"] == email["id"])
    assert updated["metadata"]["custom_replacement"] == "contact-hidden"

    audits = client.get("/api/v1/history").json()["audits"]
    audit = next(item for item in audits if item["task_id"] == detected["task_id"] and item["operation"] == "set_replacement")
    serialized = json.dumps(audit["payload"], ensure_ascii=False)
    assert "contact-hidden" not in serialized
    assert audit["payload"]["after"]["text_hash"]

    cleared = client.post("/api/v1/reviews", json={
        "task_id": detected["task_id"],
        "span_id": email["id"],
        "operation": "set_replacement",
        "before": "contact-hidden",
        "after": "",
    })
    assert cleared.status_code == 200
    assert "contact-hidden" not in cleared.json()["snapshot"]["redacted_text"]



def test_knowledge_lookup_endpoint_returns_three_strength_levels():
    status = client.get("/api/v1/knowledge/status")
    assert status.status_code == 200
    assert status.json()["state"] in {"configured", "local-fallback"}

    response = client.post("/api/v1/knowledge/lookup", json={
        "term": "中国人民大学", "entity_type": "ORG", "allow_remote": False,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["levels"] == ["北京高校", "高等院校", "教育机构"]
    assert payload["source"] == "local_exact"


def test_redact_endpoint_applies_strict_and_standard_risk_modes():
    text = "王洋"
    span = {
        "id": "pending-person", "start": 0, "end": 2, "text": text,
        "entity_type": "PERSON", "sources": ["TEST"], "status": "pending",
        "strategy": "mask",
    }
    strict = client.post("/api/v1/redact", json={"text": text, "spans": [span], "risk_level": "strict"})
    standard = client.post("/api/v1/redact", json={"text": text, "spans": [span], "risk_level": "standard"})
    assert strict.status_code == standard.status_code == 200
    assert strict.json()["redacted_text"] != text
    assert standard.json()["redacted_text"] == text


def test_generalization_pipeline_exposes_knowledge_metadata_and_trace():
    response = client.post("/api/v1/detect", json={
        "text": "中国人民大学", "strategy": "generalize", "privacy_strength": 2,
        "use_llm": False, "enabled_entity_types": ["ORG"],
    })
    assert response.status_code == 200
    payload = response.json()
    organization = next(span for span in payload["spans"] if span["entity_type"] == "ORG")
    assert organization["metadata"]["knowledge_levels"] == ["北京高校", "高等院校", "教育机构"]
    assert payload["redacted_text"] == "高等院校"
    assert any(step["key"] == "knowledge" and step["status"] == "done" for step in payload["trace"])
