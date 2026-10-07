import pytest
import hashlib
import json
import sys

from scripts.run_benchmark import main, percentile, score


def test_score_accepts_finetune_label_field():
    predicted = [{"start": 0, "end": 2, "entity_type": "PERSON", "status": "accepted"}]
    gold = [{"start": 0, "end": 2, "label": "PERSON"}]
    assert score(predicted, gold)[:3] == (1, 0, 0)


def test_score_keeps_exact_span_boundaries():
    predicted = [{"start": 0, "end": 2, "entity_type": "PERSON"}]
    gold = [{"start": 3, "end": 5, "label": "PERSON"}]
    assert score(predicted, gold)[:3] == (0, 1, 1)


def test_score_excludes_rejected_predictions():
    predicted = [{"start": 0, "end": 2, "entity_type": "PERSON", "status": "rejected"}]
    assert score(predicted, [])[:3] == (0, 0, 0)


def test_score_rejects_missing_entity_type():
    with pytest.raises(ValueError, match="entity_type 或 label"):
        score([], [{"start": 0, "end": 2}])


def test_percentile_handles_empty_and_sorted_values():
    assert percentile([], 0.95) == 0.0
    assert percentile([40, 10, 20, 30], 0.95) == 40
    assert percentile([10], 0.50) == 10
    assert percentile(list(range(1, 101)), 0.95) == 95


def test_benchmark_records_actual_model_state(tmp_path, monkeypatch):
    dataset = tmp_path / "gold.jsonl"
    dataset.write_text(json.dumps({"text": "synthetic", "spans": []}), encoding="utf-8")
    output = tmp_path / "result.json"
    calls = []

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return Response({"enabled": False, "active": "configured-but-disabled"})

        def post(self, url, **kwargs):
            calls.append(kwargs["json"])
            return Response({"spans": [], "trace": [{"key": "llm", "status": "skipped"}]})

    monkeypatch.setattr("scripts.run_benchmark.httpx.Client", Client)
    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", str(dataset), "--output", str(output), "--no-llm"])
    main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["run_id"] and result["created_at"]
    assert result["metadata"]["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert result["metadata"]["llm_enabled"] is False
    assert result["metadata"]["llm_statuses"] == {"skipped": 1}
    assert "未启用 LLM" in result["systems"][0]["name"]
    assert calls[0]["use_llm"] is False
    assert calls[0]["use_policies"] is False
