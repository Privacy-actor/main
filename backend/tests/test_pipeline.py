import asyncio

from app import anonymizer, llm_adapter
from app.anonymizer import SEMANTIC_REPLACEMENTS, pseudonymization_metadata, redact_text
from app.recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
from app.schemas import Strategy
from app.schemas import EntityType, Span


def test_rule_offsets_and_redaction():
    text = "我叫王洋，电话是13800138000，邮箱是wang@example.com。"
    rules, _ = detect_rule_spans(text, Strategy.MASK)
    ners, _ = detect_lite_ner_spans(text, Strategy.MASK)
    spans = merge_spans(text, rules + ners)
    assert all(text[span.start:span.end] == span.text for span in spans)
    assert {span.entity_type.value for span in spans} >= {"PERSON", "PHONE", "EMAIL"}
    output = redact_text(text, spans, Strategy.MASK)
    assert "13800138000" not in output
    assert "wang@example.com" not in output


def test_same_entity_uses_consistent_mask():
    text = "我叫王洋，王洋的邮箱是wang@example.com。"
    rules, _ = detect_rule_spans(text, Strategy.MASK)
    ners, _ = detect_lite_ner_spans(text, Strategy.MASK)
    output = redact_text(text, merge_spans(text, rules + ners), Strategy.MASK)
    assert output.count("【PERSON-001】") >= 1


def test_rejected_overlap_cannot_evict_active_span():
    text = "北京市海淀区"
    active = Span(id="active", start=0, end=len(text), text=text, entity_type=EntityType.ADDRESS,
                  score=.8, sources=["NER"], status="accepted", strategy=Strategy.MASK)
    rejected = Span(id="rejected", start=0, end=3, text="北京市", entity_type=EntityType.LOCATION,
                    score=.99, sources=["LLM"], status="rejected", strategy=Strategy.MASK)
    merged = merge_spans(text, [active, rejected])
    assert len(merged) == 1
    assert merged[0].id == "active"
    assert merged[0].status == "pending"


def test_local_knowledge_hierarchy_preserves_semantic_level():
    text = "中国人民大学"
    span = Span(id="org", start=0, end=len(text), text=text, entity_type=EntityType.ORG,
                score=1.0, sources=["TEST"], status="accepted", strategy=Strategy.GENERALIZE)
    assert redact_text(text, [span], Strategy.GENERALIZE, 1) == "北京高校"
    assert redact_text(text, [span], Strategy.GENERALIZE, 3) == "教育机构"

def test_llm_retries_structurally_invalid_offsets(monkeypatch):
    text = "王洋"
    span = Span(id="candidate", start=0, end=2, text=text, entity_type=EntityType.PERSON,
                score=.7, sources=["NER"], status="pending", strategy=Strategy.MASK)
    responses = [
        '{"decisions": [], "additions": [{"text": "王洋", "label": "PERSON", "start": 1, "end": 2}]}',
        '{"decisions": [{"id": "candidate", "keep": true, "label": "PERSON", "certainty": "high"}], "additions": []}',
    ]
    calls = []

    async def fake_completion(body):
        calls.append(body)
        return responses[len(calls) - 1], 1

    monkeypatch.setattr(llm_adapter.settings, "llm_enabled", True)
    monkeypatch.setattr(llm_adapter.settings, "llm_max_retries", 1)
    monkeypatch.setattr(llm_adapter, "_request_completion", fake_completion)
    result, trace = asyncio.run(llm_adapter.verify_with_llm(text, [span], Strategy.MASK))

    assert len(calls) == 2
    assert result[0].status == "accepted"
    assert "LLM" in result[0].sources
    assert "2 次调用" in trace.detail



def test_exponential_pseudonymization_is_randomized_but_document_consistent(monkeypatch):
    text = "\u738b\u6d0b\u548c\u738b\u6d0b"
    spans = [
        Span(id="person-1", start=0, end=2, text="\u738b\u6d0b", entity_type=EntityType.PERSON, score=1, sources=["TEST"], status="accepted", strategy=Strategy.PSEUDONYMIZE),
        Span(id="person-2", start=3, end=5, text="\u738b\u6d0b", entity_type=EntityType.PERSON, score=1, sources=["TEST"], status="accepted", strategy=Strategy.PSEUDONYMIZE),
    ]
    calls = []

    def fake_scores(source, candidates):
        calls.append((source, candidates))
        return [1.0] + [0.0] * (len(candidates) - 1)

    class FakeRandom:
        @staticmethod
        def choices(pool, weights, k):
            assert k == 1
            assert weights[0] > weights[1]
            return [pool[0]]

    monkeypatch.setattr(anonymizer.semantic_encoder, "cosine_scores", fake_scores)
    monkeypatch.setattr(anonymizer, "_SYSTEM_RANDOM", FakeRandom())
    output = redact_text(text, spans, Strategy.PSEUDONYMIZE, 3)
    left, right = output.split("\u548c")
    assert left == right == SEMANTIC_REPLACEMENTS[EntityType.PERSON][0]
    assert len(calls) == 1
    assert calls[0][0] == "\u738b\u6d0b"

    metadata = pseudonymization_metadata(2)
    assert metadata["mechanism"] == "exponential"
    assert metadata["epsilon"] == 1.0
    assert metadata["utility_sensitivity"] == 1.0
    assert metadata["random_source"] == "system-cryptographic-rng"
    assert metadata["semantic_encoder"].endswith("paraphrase-multilingual-MiniLM-L12-v2")
    assert pseudonymization_metadata(1)["epsilon"] == 0.25
    assert pseudonymization_metadata(3)["epsilon"] == 4.0




def test_risk_level_controls_pending_span_redaction():
    text = "王洋"
    span = Span(id="pending-person", start=0, end=2, text=text, entity_type=EntityType.PERSON,
                score=.6, sources=["TEST"], status="pending", strategy=Strategy.MASK)
    assert redact_text(text, [span], Strategy.MASK, include_pending=True) != text
    assert redact_text(text, [span], Strategy.MASK, include_pending=False) == text


def test_language_scope_changes_lite_semantic_recognition():
    text = "姓名：王洋。Alice Morgan."
    zh_spans, zh_trace = detect_lite_ner_spans(text, Strategy.MASK, language="zh")
    en_spans, en_trace = detect_lite_ner_spans(text, Strategy.MASK, language="en")
    assert any(span.text == "王洋" for span in zh_spans)
    assert not any(span.text == "Alice Morgan" for span in zh_spans)
    assert any(span.text == "Alice Morgan" for span in en_spans)
    assert not any(span.text == "王洋" for span in en_spans)
    assert "仅中文" in zh_trace.detail
    assert "仅英文" in en_trace.detail


def test_lite_ner_keeps_person_and_organization_boundaries_precise():
    text = "采访对象姓名：王洋，现就读于中国人民大学。His supervisor is Dr. Alice Morgan from Northbridge Institute."
    spans, _ = detect_lite_ner_spans(text, Strategy.MASK, language="auto")
    detected = {(span.text, span.entity_type) for span in spans}
    assert ("王洋", EntityType.PERSON) in detected
    assert ("中国人民大学", EntityType.ORG) in detected
    assert ("Alice Morgan", EntityType.PERSON) in detected
    assert ("Northbridge Institute", EntityType.ORG) in detected
    assert not any(span.text in {"姓名", "现就读于中国人民大学"} for span in spans)
