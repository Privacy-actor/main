import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_task_snapshot_preserves_processing_settings_for_reopening():
    config = {
        "use_llm": False, "use_policies": True, "risk_level": "standard", "privacy_strength": 3,
        "custom_keywords": [{"value": "星舟", "entity_type": "CUSTOM", "case_sensitive": False}],
        "custom_patterns": [], "instruction": "隐藏星舟",
    }
    task = client.post("/api/v1/detect", json={"text": "😀项目代号星舟", **config}).json()
    stored = client.get(f"/api/v1/tasks/{task['task_id']}").json()
    for key, value in config.items():
        assert stored["applied_config"][key] == value
    assert "text" not in stored["applied_config"]


@pytest.mark.parametrize("manual_text", ["人工最终稿", ""])
def test_stale_automatic_text_cannot_undo_entity_review(manual_text):
    task = client.post("/api/v1/detect", json={"text": "电话13800138000", "use_llm": False}).json()
    url = f"/api/v1/tasks/{task['task_id']}"
    reviewed = client.post("/api/v1/reviews", json={
        "task_id": task["task_id"], "span_id": "all", "operation": "set_strategy", "after": "generalize",
    }).json()["snapshot"]
    assert reviewed["redacted_text"] != task["redacted_text"]
    response = client.put(url + "/final-text", json={
        "text": manual_text, "automatic_text": task["redacted_text"], "expected_revision": 0,
    })
    assert response.status_code == 409
    current = client.get(url).json()
    assert current["redacted_text"] == reviewed["redacted_text"]
    assert current["final_text"] == reviewed["final_text"]


def test_manual_save_cannot_replace_server_automatic_result():
    task = client.post("/api/v1/detect", json={"text": "邮箱qa@example.com", "use_llm": False}).json()
    url = f"/api/v1/tasks/{task['task_id']}"
    response = client.put(url + "/final-text", json={
        "text": task["final_text"], "automatic_text": "客户端伪造自动稿", "expected_revision": 0,
    })
    assert response.status_code == 409
    assert client.get(url).json()["redacted_text"] == task["redacted_text"]


def test_empty_final_text_survives_review_and_noop_save():
    task = client.post("/api/v1/detect", json={"text": "电话13800138000", "use_llm": False}).json()
    url = f"/api/v1/tasks/{task['task_id']}"
    saved = client.put(url + "/final-text", json={
        "text": "", "automatic_text": task["redacted_text"], "expected_revision": 0,
    }).json()
    reviewed = client.post("/api/v1/reviews", json={
        "task_id": task["task_id"], "span_id": "all", "operation": "set_strategy", "after": "generalize",
    }).json()["snapshot"]
    assert reviewed["final_text"] == ""
    response = client.put(url + "/final-text", json={
        "text": "", "automatic_text": reviewed["redacted_text"], "expected_revision": saved["final_revision"],
    })
    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["has_manual_edits"] is True
    assert client.get(url).json()["final_text"] == ""
