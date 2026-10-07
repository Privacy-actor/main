from pathlib import Path
import json
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from eval_privshield import Scorer, build_ui_metrics, load_prediction_cache, main


def test_ui_metrics_preserves_metric_contract_and_latency_units():
    baseline = Scorer()
    baseline.add({("张三", "PERSON")}, set())
    finetuned = Scorer()
    finetuned.add({("张三", "PERSON")}, {("张三", "PERSON")})
    results = {
        "B0": (baseline, baseline, {}),
        "B2": (finetuned, finetuned, {"n": 796, "latency_avg": 4.33}),
    }
    output = build_ui_metrics(results, Path("test.jsonl"), 800, 0.73, "2026-09-05T00:00:00Z", True)
    assert output["is_demo"] is False
    assert output["run_id"]
    assert output["metadata"]["metric"] == "surface-label micro"
    assert output["metadata"]["system_records"] == {"B0": 800, "B2": 796}
    assert output["systems"][1]["latency"] == 4330.0
    assert output["systems"][1]["f1"] == pytest.approx(1.0)
    assert "未重新调用模型" in output["notice"]
    assert output["metadata"]["verified"] is False
    assert any("评测样本数量不同" in warning for warning in output["metadata"]["warnings"])


def test_b0_ui_metrics_does_not_invent_latency():
    baseline = Scorer()
    baseline.add({("test@example.com", "EMAIL")}, {("test@example.com", "EMAIL")})
    output = build_ui_metrics({"B0": (baseline, baseline, {})}, "test.jsonl", 10, 1.0, "2026-09-05T00:00:00Z", True)
    assert "latency" not in output["systems"][0]
    assert next(item for item in output["categories"] if item["name"] == "EMAIL")["recall"] == 1.0


@pytest.mark.parametrize("predictions", [[], [{"id": "sample", "source_id": "wrong"}], [{"id": "sample", "source_id": "gold"}, {"id": "sample", "source_id": "gold"}]])
def test_cache_rejects_missing_mismatched_and_duplicate_ids(tmp_path, predictions):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("\n".join(json.dumps(item) for item in predictions), encoding="utf-8")
    with pytest.raises(ValueError):
        load_prediction_cache(cache, [{"id": "sample", "source_id": "gold"}])


def test_cached_only_stops_before_network_and_does_not_write_report(tmp_path, monkeypatch):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"id": "gold", "text": "synthetic", "spans": []}), encoding="utf-8")
    sft = tmp_path / "sft.jsonl"
    sft.write_text(json.dumps({"id": "sample", "source_id": "gold"}), encoding="utf-8")
    output = tmp_path / "new-report"
    monkeypatch.setattr(sys, "argv", ["eval_privshield.py", "--gold", str(gold), "--sft", str(sft), "--out-dir", str(output), "--preds-dir", str(tmp_path), "--cached-only"])
    monkeypatch.setattr("eval_privshield.predict_all", lambda *args: pytest.fail("不得调用网络"))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not output.exists()
