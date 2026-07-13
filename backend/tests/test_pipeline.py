from app.anonymizer import redact_text
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
