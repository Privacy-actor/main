"""NER 模型层：加载失败时退回轻量识别器；装了 Transformers 时用一个现造的小模型跑通真实的加载与推理路径。"""
import asyncio
import json

import pytest

from app import ner_adapter as ner_module
from app.config import settings
from app.ner_adapter import NerAdapter, model_display_name, resolve_model_path
from app.pipeline import run_pipeline
from app.schemas import DetectRequest, Strategy


def test_model_names_and_relative_paths(tmp_path, monkeypatch):
    assert model_display_name("Davlan/xlm-roberta-base-ner-hrl") == "xlm-roberta-base-ner-hrl"
    assert model_display_name("D:\\models\\xlm-roberta-base-ner-hrl\\") == "xlm-roberta-base-ner-hrl"
    (tmp_path / "models" / "demo").mkdir(parents=True)
    monkeypatch.setattr(ner_module, "BASE_DIR", tmp_path)
    assert resolve_model_path("models/demo") == str(tmp_path / "models" / "demo")
    assert resolve_model_path("Davlan/xlm-roberta-base-ner-hrl") == "Davlan/xlm-roberta-base-ner-hrl"


def test_a_broken_model_falls_back_to_the_lite_recognizer(monkeypatch):
    monkeypatch.setattr(settings, "ner_enabled", True)
    monkeypatch.setattr(settings, "ner_model", "models/does-not-exist")
    adapter = NerAdapter()
    monkeypatch.setattr(ner_module, "ner_adapter", adapter)
    import app.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "ner_adapter", adapter)

    spans, trace = asyncio.run(adapter.detect("联系人：张伟，电话13800138000。", Strategy.MASK))
    assert spans == [] and trace.status == "degraded" and "轻量识别器" in trace.detail
    status = adapter.status()
    assert status["state"] == "failed" and status["detail"]
    # 失败后一段时间内不再重试，识别不会每次都卡在加载上
    attempts = []
    monkeypatch.setattr(adapter, "_pipeline", None)
    original = asyncio.to_thread
    monkeypatch.setattr(ner_module.asyncio, "to_thread", lambda *args, **kwargs: attempts.append(1) or original(*args, **kwargs))
    asyncio.run(adapter.detect("张伟", Strategy.MASK))
    assert attempts == []

    spans, _, traces, _, _ = asyncio.run(run_pipeline(DetectRequest(text="联系人：张伟，电话13800138000。", use_llm=False)))
    ner_trace = next(step for step in traces if step.key == "ner_model")
    assert ner_trace.status == "degraded"
    assert {span.text for span in spans} >= {"张伟", "13800138000"}


@pytest.fixture()
def tiny_model(tmp_path):
    """现造一个 XLM-R 式标签体系的小模型：没有编码层，字向量直接决定标签（张伟 → PER，北京 → LOC，星河 → ORG）。"""
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from transformers import BertConfig, BertForTokenClassification, BertTokenizerFast

    labels = ["O", "B-DATE", "I-DATE", "B-PER", "I-PER", "B-ORG", "I-ORG", "B-LOC", "I-LOC"]
    targets = {"张": "B-PER", "伟": "I-PER", "北": "B-LOC", "京": "I-LOC", "星": "B-ORG", "河": "I-ORG"}
    chars = sorted(set("联系人张伟在北京星河大学工作电话和见面上周的。，：" + "abcdefghijklmnopqrstuvwxyz0123456789"))
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", *chars]
    (tmp_path / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    tokenizer = BertTokenizerFast(vocab_file=str(tmp_path / "vocab.txt"), do_lower_case=True)
    tokenizer.save_pretrained(str(tmp_path))
    config = BertConfig(vocab_size=len(vocab), hidden_size=len(labels) + 1, num_hidden_layers=0, num_attention_heads=1, intermediate_size=8,
                        max_position_embeddings=512, id2label=dict(enumerate(labels)), label2id={label: index for index, label in enumerate(labels)})
    model = BertForTokenClassification(config).eval()
    with torch.no_grad():
        embeddings = model.bert.embeddings
        embeddings.position_embeddings.weight.zero_()
        embeddings.token_type_embeddings.weight.zero_()
        embeddings.word_embeddings.weight.zero_()
        for token, index in tokenizer.get_vocab().items():
            label = targets.get(token, "O")
            embeddings.word_embeddings.weight[index, labels.index(label)] = 10.0
        model.classifier.weight.zero_()
        model.classifier.bias.zero_()
        for index in range(len(labels)):
            model.classifier.weight[index, index] = 5.0
    model.save_pretrained(str(tmp_path))
    assert json.loads((tmp_path / "config.json").read_text())["num_hidden_layers"] == 0
    return tmp_path


def test_real_transformers_pipeline_with_a_tiny_model(tiny_model, monkeypatch):
    monkeypatch.setattr(settings, "ner_enabled", True)
    monkeypatch.setattr(settings, "ner_model", str(tiny_model))
    adapter = NerAdapter()
    import app.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "ner_adapter", adapter)

    text = "联系人张伟在北京星河大学工作，电话13800138000。"
    asyncio.run(adapter.warmup())
    assert adapter.status()["state"] == "ready"
    spans, trace = asyncio.run(adapter.detect(text, Strategy.MASK))
    found = {(span.text, span.entity_type.value) for span in spans}
    assert {("张伟", "PERSON"), ("北京", "LOCATION"), ("星河", "ORG")} <= found
    assert all(text[span.start:span.end] == span.text for span in spans)
    assert trace.status == "done" and trace.detail == tiny_model.name

    merged, _, traces, _, _ = asyncio.run(run_pipeline(DetectRequest(text=text, use_llm=False)))
    ner_trace = next(step for step in traces if step.key == "ner_model")
    assert ner_trace.status == "done"
    person = next(span for span in merged if span.text == "张伟")
    assert "NER" in person.sources
