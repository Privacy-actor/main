import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def report_file(monkeypatch, tmp_path):
    path = tmp_path / "latest.json"
    monkeypatch.setattr("app.main.EVALUATION_RESULTS", path)
    return path


def test_evaluation_serves_replay_without_promoting_verification(report_file):
    report = {
        "run_id": "cache-replay-test",
        "dataset": "test.jsonl",
        "is_demo": False,
        "systems": [{"name": "B2", "precision": 0.8, "recall": 0.7, "f1": 0.75}],
        "metadata": {"metric": "surface-label micro", "verified": False, "evaluation_scope": "offline-cache-replay"},
    }
    report_file.write_text(json.dumps(report), encoding="utf-8")
    response = TestClient(app).get("/api/v1/evaluations")
    assert response.status_code == 200
    assert response.json()["metadata"]["verified"] is False
    assert response.json()["metadata"]["schema_validated"] is True
    assert response.json()["run_id"] == "cache-replay-test"


@pytest.mark.parametrize("invalid", ["{broken", "[]", '{"systems": []}', '{"systems": [{"name": "bad", "precision": 2, "recall": 0.1, "f1": 0.1}]}', '{"systems": [{"name": "bad", "precision": NaN, "recall": 0.1, "f1": 0.1}]}'])
def test_invalid_results_do_not_fall_back_to_demo(report_file, invalid):
    report_file.write_text(invalid, encoding="utf-8")
    response = TestClient(app).get("/api/v1/evaluations")
    assert response.status_code == 503
    assert "未切换为演示指标" in response.json()["detail"]


def test_existing_demo_report_keeps_demo_flag(report_file):
    report_file.write_text(json.dumps({"is_demo": True, "systems": [{"name": "demo", "precision": 0.8, "recall": 0.7, "f1": 0.75}]}), encoding="utf-8")
    response = TestClient(app).get("/api/v1/evaluations")
    assert response.json()["is_demo"] is True
    assert response.json()["metadata"]["verified"] is False


def test_export_uses_same_validated_report_and_download_headers(report_file):
    report_file.write_text(json.dumps({"run_id": "download-test", "systems": [{"name": "B2", "precision": 0.8, "recall": 0.7, "f1": 0.75}]}), encoding="utf-8")
    client = TestClient(app)
    exported = client.get("/api/v1/evaluations/export")
    assert exported.status_code == 200
    assert exported.json() == client.get("/api/v1/evaluations").json()
    assert 'attachment; filename="privshield-evaluation.json"' == exported.headers["content-disposition"]
