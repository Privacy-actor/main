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

def test_llm_addition_offsets_are_diagnostic_and_find_all_covers_every_occurrence(monkeypatch):
    # 规格「唯一的后端改动：find-all」：偏移错误不再作废整次响应，补漏串在原文的每次出现都要处理
    text = "王洋说，王洋的同事 Li 来自 Lisbon。"
    span = Span(id="candidate", start=0, end=2, text="王洋", entity_type=EntityType.PERSON,
                score=.7, sources=["NER"], status="pending", strategy=Strategy.MASK)
    responses = [
        '{"decisions": [{"id": "candidate", "keep": true, "label": "PERSON", "certainty": "high"}],'
        ' "additions": [{"text": "王洋", "label": "PERSON", "start": 1, "end": 2, "certainty": "high"},'
        ' {"text": "张三", "label": "PERSON", "start": 0, "end": 2, "certainty": "high"}]}',
    ]
    calls = []

    async def fake_completion(body):
        calls.append(body)
        return responses[len(calls) - 1], 1

    monkeypatch.setattr(llm_adapter.settings, "llm_enabled", True)
    monkeypatch.setattr(llm_adapter.settings, "llm_max_retries", 1)
    monkeypatch.setattr(llm_adapter, "_request_completion", fake_completion)
    result, trace = asyncio.run(llm_adapter.verify_with_llm(text, [span], Strategy.MASK))

    assert len(calls) == 1
    assert trace.status == "done"
    persons = sorted((item.start, item.end) for item in result if item.text == "王洋")
    assert persons == [(0, 2), (4, 6)]
    assert next(item for item in result if item.id == "candidate").status == "accepted"
    assert not any(item.text == "张三" for item in result)  # 原文中不存在的补漏不采纳
    assert "原文中不存在" in trace.detail


def test_find_all_respects_latin_word_boundaries_but_treats_cjk_as_boundary():
    assert llm_adapter.find_all_occurrences("Li visited Lisbon", "Li") == [(0, 2)]
    assert llm_adapter.find_all_occurrences("让我联系Quincy Transport Group", "Quincy Transport Group") == [(4, 26)]
    assert llm_adapter.find_all_occurrences("王", "王") == []


def test_llm_retries_structurally_invalid_json(monkeypatch):
    text = "王洋"
    span = Span(id="candidate", start=0, end=2, text=text, entity_type=EntityType.PERSON,
                score=.7, sources=["NER"], status="pending", strategy=Strategy.MASK)
    responses = [
        '{"decisions": [{"id": "candidate", "keep": true, "label": "NOT_A_TYPE"}], "additions": []}',
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


def test_cloud_mode_uses_cloud_endpoint_only_when_configured(monkeypatch):
    monkeypatch.setattr(llm_adapter.settings, "llm_enabled", False)
    monkeypatch.setattr(llm_adapter.settings, "llm_cloud_base_url", "")
    monkeypatch.setattr(llm_adapter.settings, "llm_cloud_model", "")
    assert llm_adapter.resolve_endpoint("cloud")[0] is None
    monkeypatch.setattr(llm_adapter.settings, "llm_cloud_base_url", "https://cloud.example/v1")
    monkeypatch.setattr(llm_adapter.settings, "llm_cloud_model", "cloud-model")
    endpoint, note = llm_adapter.resolve_endpoint("cloud")
    assert endpoint.location == "cloud" and endpoint.model == "cloud-model" and endpoint.host == "cloud.example"
    assert llm_adapter.resolve_endpoint("local")[0] is None  # 本地模式不会把文本送往云端

    seen = []

    async def fake_completion(body):
        seen.append((llm_adapter._current_endpoint.get().location, body["model"]))
        return '{"decisions": [], "additions": []}', 1

    monkeypatch.setattr(llm_adapter, "_request_completion", fake_completion)
    _, trace = asyncio.run(llm_adapter.verify_with_llm("王洋", [], Strategy.MASK, deployment_mode="cloud"))
    assert seen == [("cloud", "cloud-model")]
    assert "云端" in trace.detail


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
    # 附件5 §4.2.3：力度越高，ε 越小，替换越随机
    assert pseudonymization_metadata(1)["epsilon"] == 4.0
    assert pseudonymization_metadata(3)["epsilon"] == 0.25




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


def test_pseudonyms_keep_script_and_do_not_collide_within_a_document():
    text = "林若宁和Alice Morgan、陈晓都来了"
    spans = [
        Span(id="a", start=0, end=3, text="林若宁", entity_type=EntityType.PERSON, score=1, sources=["TEST"], status="accepted", strategy=Strategy.PSEUDONYMIZE),
        Span(id="b", start=4, end=16, text="Alice Morgan", entity_type=EntityType.PERSON, score=1, sources=["TEST"], status="accepted", strategy=Strategy.PSEUDONYMIZE),
        Span(id="c", start=17, end=19, text="陈晓", entity_type=EntityType.PERSON, score=1, sources=["TEST"], status="accepted", strategy=Strategy.PSEUDONYMIZE),
    ]
    for _ in range(30):
        output, segments = anonymizer.redact_with_map(text, spans, Strategy.PSEUDONYMIZE, 3)
        by_id = {item["span_id"]: item["replacement"] for item in segments}
        assert by_id["a"] != by_id["c"]
        assert all("\u4e00" <= char <= "\u9fff" for char in by_id["a"] + by_id["c"])
        assert by_id["b"].isascii()
        for item in segments:
            assert output[item["out_start"]:item["out_end"]] == item["replacement"]


def test_replacement_map_tracks_output_positions_after_emoji():
    text = "😀电话13800138000，邮箱a@b.com"
    spans = [
        Span(id="p", start=3, end=14, text="13800138000", entity_type=EntityType.PHONE, score=1, sources=["RULE"], status="accepted", strategy=Strategy.MASK),
        Span(id="e", start=17, end=24, text="a@b.com", entity_type=EntityType.EMAIL, score=1, sources=["RULE"], status="accepted", strategy=Strategy.MASK),
    ]
    output, segments = anonymizer.redact_with_map(text, spans, Strategy.MASK)
    assert output == "😀电话【PHONE-001】，邮箱【EMAIL-001】"
    assert [(item["out_start"], item["out_end"]) for item in segments] == [(3, 14), (17, 28)]
