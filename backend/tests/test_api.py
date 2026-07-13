from fastapi.testclient import TestClient

from app.main import app


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
