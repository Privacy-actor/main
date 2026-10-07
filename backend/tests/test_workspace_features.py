import io
import json

from docx import Document
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE = "联系人：李明，电话13800138000，邮箱 liming@example.com。项目代号星舟，星舟计划下周启动，星舟由李明牵头。"


def _detect(**overrides):
    payload = {"text": SAMPLE, "use_llm": False, **overrides}
    response = client.post("/api/v1/detect", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_detect_returns_replacement_map_aligned_with_output():
    task = _detect()
    assert task["persisted"] is True
    assert task["replacements"]
    characters = list(task["text"])
    output = list(task["redacted_text"])
    for item in task["replacements"]:
        assert "".join(characters[item["start"]:item["end"]]) == next(span["text"] for span in task["spans"] if span["id"] == item["span_id"])
        assert "".join(output[item["out_start"]:item["out_end"]]) == item["replacement"]


def test_preview_detection_is_not_saved_to_history():
    preview = _detect(persist=False)
    assert preview["task_id"].startswith("preview_")
    assert preview["persisted"] is False
    assert client.get(f"/api/v1/tasks/{preview['task_id']}").status_code == 404
    assert "persist" not in preview["applied_config"]


def test_add_many_marks_every_occurrence_and_audits_without_plaintext():
    task = _detect()
    text = task["text"]
    starts = [index for index in range(len(text)) if text.startswith("星舟", index)]
    spans = [{"id": f"human_{index}", "start": start, "end": start + 2, "text": "星舟", "entity_type": "CUSTOM",
              "score": 1, "sources": ["HUMAN"], "status": "accepted", "strategy": "mask", "metadata": {}}
             for index, start in enumerate(starts)]
    response = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": "bulk", "operation": "add_many", "spans": spans})
    assert response.status_code == 200, response.text
    snapshot = response.json()["snapshot"]
    assert "星舟" not in snapshot["redacted_text"]
    assert sum(span["text"] == "星舟" for span in snapshot["spans"]) == len(starts) == 3
    audits = client.get(f"/api/v1/tasks/{task['task_id']}/audits").json()["items"]
    assert audits[0]["operation"] == "add_many"
    assert "星舟" not in json.dumps(audits[0], ensure_ascii=False)


def test_add_many_skips_positions_already_covered():
    task = _detect()
    phone = next(span for span in task["spans"] if span["entity_type"] == "PHONE")
    inner = {"id": "human_inner", "start": phone["start"] + 1, "end": phone["start"] + 4, "text": phone["text"][1:4],
             "entity_type": "CUSTOM", "score": 1, "sources": ["HUMAN"], "status": "accepted", "strategy": "mask", "metadata": {}}
    response = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": "bulk", "operation": "add_many", "spans": [inner]})
    assert response.status_code == 422


def test_bulk_accept_and_reject_update_snapshot():
    task = _detect()
    ids = [span["id"] for span in task["spans"]]
    rejected = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": "bulk", "operation": "reject_many", "span_ids": ids}).json()["snapshot"]
    assert rejected["redacted_text"] == task["text"]
    assert all("HUMAN" in span["sources"] for span in rejected["spans"])
    accepted = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": "bulk", "operation": "accept_many", "span_ids": ids[:1]}).json()["snapshot"]
    assert accepted["summary"]["total"] == 1


def test_recheck_flags_reintroduced_phone_and_residual_entity_but_not_replacements():
    task = _detect(strategy="pseudonymize")
    clean = client.post(f"/api/v1/tasks/{task['task_id']}/recheck", json={}).json()
    assert clean["passed"] is True, clean
    edited = task["redacted_text"] + " 备用电话13900139000，李明。"
    dirty = client.post(f"/api/v1/tasks/{task['task_id']}/recheck", json={"text": edited}).json()
    assert dirty["passed"] is False
    found = {(item["text"], item["severity"]) for item in dirty["findings"]}
    assert ("13900139000", "high") in found
    assert ("李明", "high") in found


def test_recheck_respects_restored_entities():
    task = _detect()
    person = next(span for span in task["spans"] if span["text"] == "李明")
    snapshot = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": person["id"], "operation": "reject"}).json()["snapshot"]
    result = client.post(f"/api/v1/tasks/{task['task_id']}/recheck", json={}).json()
    assert not any(item["text"] == "李明" for item in result["findings"]), result
    assert snapshot["redacted_text"].count("李明") >= 1


def test_export_formats():
    task = _detect()
    txt = client.get(f"/api/v1/tasks/{task['task_id']}/export?format=txt")
    assert txt.status_code == 200 and txt.text == task["redacted_text"]
    assert "attachment" in txt.headers["content-disposition"]
    docx = client.get(f"/api/v1/tasks/{task['task_id']}/export?format=docx")
    assert docx.status_code == 200
    paragraphs = [paragraph.text for paragraph in Document(io.BytesIO(docx.content)).paragraphs]
    assert "\n".join(paragraphs) == task["redacted_text"]
    audit = client.get(f"/api/v1/tasks/{task['task_id']}/export?format=json").json()
    assert audit["text"] == SAMPLE and "audits" in audit
    assert client.get(f"/api/v1/tasks/{task['task_id']}/export?format=pdf").status_code == 422


def test_models_and_stats_describe_the_running_system():
    models = client.get("/api/v1/models").json()
    assert models["rules"]["builtin_patterns"] >= 6
    assert models["pseudonym_epsilon"] == {"1": 4.0, "2": 1.0, "3": 0.25}
    assert set(models["endpoints"]) == {"local", "cloud"}
    _detect()
    stats = client.get("/api/v1/stats").json()
    assert stats["tasks"] >= 1 and stats["entities"] >= 1
    assert "PHONE" in stats["by_type"]


def test_redact_endpoint_returns_replacements_for_strategy_comparison():
    task = _detect()
    for strategy in ("mask", "pseudonymize", "generalize"):
        response = client.post("/api/v1/redact", json={"text": task["text"], "spans": task["spans"], "strategy": strategy, "privacy_strength": 2})
        assert response.status_code == 200
        data = response.json()
        assert len(data["replacements"]) == len([span for span in task["spans"] if span["status"] != "rejected"])


def test_same_entity_is_handled_at_every_occurrence():
    # 第二处“李明”前后没有任何线索，只能靠同名补全
    task = _detect(text="联系人：李明，电话13800138000。星舟计划下周启动，会议记录人李明。")
    occurrences = [span for span in task["spans"] if span["text"] == "李明"]
    assert len(occurrences) == 2
    assert any(span["metadata"].get("propagated_from") for span in occurrences)
    assert "李明" not in task["redacted_text"]
    assert task["redacted_text"].count("【PERSON-001】") == 2


def test_rule_tester_uses_backend_regex_engine():
    response = client.post("/api/v1/rules/test", json={"kind": "regex", "pattern": r"ACCT-\d{8}", "text": "ACCT-12345678 与 acct-87654321", "case_sensitive": False})
    assert response.status_code == 200
    data = response.json()
    assert [item["text"] for item in data["matches"]] == ["ACCT-12345678", "acct-87654321"]
    bad = client.post("/api/v1/rules/test", json={"kind": "regex", "pattern": "(", "text": "x"})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False and "正则表达式无效" in bad.json()["error"]
    assert client.post("/api/v1/rules", json={"name": "坏规则", "kind": "regex", "pattern": "(", "entity_type": "CUSTOM"}).status_code == 422
    keyword = client.post("/api/v1/rules/test", json={"kind": "keyword", "pattern": "a.b", "text": "a.b axb", "case_sensitive": True}).json()
    assert [item["start"] for item in keyword["matches"]] == [0]


def test_lite_recognizers_keep_clean_boundaries():
    from app.recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
    from app.schemas import Strategy

    text = "客户反馈：上周在上海市浦东新区世纪大道88号的门店办理业务，银行卡 6222021001116248 被重复扣款。导师是陈晓峰教授，关于王经理的车间主任。"
    spans = merge_spans(text, detect_rule_spans(text, Strategy.MASK)[0] + detect_lite_ner_spans(text, Strategy.MASK)[0])
    found = {(span.entity_type.value, span.text): span for span in spans}
    assert ("ADDRESS", "上海市浦东新区世纪大道88号") in found
    assert ("BANK_CARD", "6222021001116248") in found
    assert ("PERSON", "陈晓峰") in found
    # 城市名嵌在地址里是正常嵌套，不应把地址标成冲突
    assert not found[("ADDRESS", "上海市浦东新区世纪大道88号")].conflict
    assert not any(span.text in {"关于王", "于王", "车间"} for span in spans)


def test_review_queue_context_offset_locates_span():
    task = _detect(text="Please copy Michael Chen on the reply. 联系人：王芳。")
    items = [item for item in client.get("/api/v1/reviews").json()["items"] if item["task_id"] == task["task_id"]]
    assert items, "低置信度姓名应进入复核队列"
    for item in items:
        span = item["span"]
        context = list(item["context"])
        offset = item["context_offset"]
        assert "".join(context[span["start"] - offset:span["end"] - offset]) == span["text"]


def test_batch_rows_report_entity_types():
    files = {"files": ("demo.txt", "联系人：李明，电话13800138000。".encode("utf-8"), "text/plain")}
    created = client.post("/api/v1/jobs", files=files, data={"config_json": json.dumps({"use_llm": False})})
    assert created.status_code == 200, created.text
    job = client.get(f"/api/v1/jobs/{created.json()['id']}").json()
    row = job["payload"]["results"][0]
    assert row["by_type"]["PHONE"] == 1
    assert row["by_type"]["PERSON"] == 1


def test_pseudonyms_stay_within_the_same_kind_of_entity():
    from app.anonymizer import candidate_pool
    from app.schemas import EntityType, Span, Strategy

    def pool(text, entity_type):
        span = Span(id="x", start=0, end=len(text), text=text, entity_type=entity_type, score=1, sources=["TEST"], strategy=Strategy.PSEUDONYMIZE)
        return candidate_pool(span)

    assert all(name.endswith(("大学", "学院")) for name in pool("星河大学", EntityType.ORG))
    assert all(name.endswith("医院") for name in pool("北京协和医院", EntityType.ORG))
    assert all("Institute" in name for name in pool("Northbridge Institute", EntityType.ORG))
    assert all(name.endswith("市") for name in pool("上海", EntityType.LOCATION))


def test_extract_keeps_every_column_of_csv_and_json():
    csv_bytes = "工单号,姓名,内容\n1,李明,电话13800138000\n2,王芳,来电反馈\n".encode("utf-8")
    response = client.post("/api/v1/extract", files={"files": ("tickets.csv", csv_bytes, "text/csv")})
    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert [record["text"] for record in records] == ["工单号：1\n姓名：李明\n内容：电话13800138000", "工单号：2\n姓名：王芳\n内容：来电反馈"]
    assert response.json()["text"] == "工单号：1\n姓名：李明\n内容：电话13800138000\n\n工单号：2\n姓名：王芳\n内容：来电反馈"
    json_bytes = json.dumps([{"id": 7, "note": "联系人：李明，电话13800138000"}], ensure_ascii=False).encode("utf-8")
    response = client.post("/api/v1/extract", files={"files": ("notes.json", json_bytes, "application/json")})
    assert response.json()["records"][0]["text"] == "id：7\nnote：联系人：李明，电话13800138000"


def test_department_and_title_is_flagged_as_implicit_identifier():
    task = _detect(text="据心内科主任介绍，财务处处长也参加了会议。")
    roles = [span for span in task["spans"] if span["entity_type"] == "ROLE"]
    assert {span["text"] for span in roles} == {"心内科主任", "财务处处长"}
    # 隐性隐私置信度低于 0.90，交给人工确认
    assert all(span["status"] == "pending" and span["sources"] == ["IMPLICIT"] for span in roles)


def test_implicit_recognizer_is_separate_from_the_finetune_upstream():
    """第三层微调与评测的上游（规则 + 轻量 NER）保持九类标签；职务身份由单独的识别器给出。"""
    from app.recognizers import detect_implicit_spans, detect_lite_ner_spans, detect_rule_spans
    from app.schemas import EntityType, Strategy

    text = "待会儿把技术部负责人叫来，后来车间主任也到了，心内科主任王芳最后发言。"
    rule, _ = detect_rule_spans(text, Strategy.MASK)
    lite, _ = detect_lite_ner_spans(text, Strategy.MASK)
    assert all(span.entity_type != EntityType.ROLE for span in rule + lite)
    implicit, trace = detect_implicit_spans(text, Strategy.MASK)
    assert [span.text for span in implicit] == ["技术部负责人", "心内科主任"]
    assert trace.key == "implicit"


def test_llm_prompt_keeps_the_finetuned_contract():
    """发给大模型的提示词与微调样本同构：不含 ROLE，职务身份不作为候选。"""
    import asyncio
    import json as _json
    from unittest.mock import patch
    from app import llm_adapter
    from app.recognizers import detect_implicit_spans, detect_lite_ner_spans, merge_spans
    from app.schemas import Strategy

    text = "据心内科主任介绍，Kevin Zhang 也参加了会议。"
    lite, _ = detect_lite_ner_spans(text, Strategy.MASK)
    implicit, _ = detect_implicit_spans(text, Strategy.MASK)
    spans = merge_spans(text, lite + implicit)
    captured = {}

    async def fake_structured(body, parser, hint):
        captured["prompt"] = _json.loads(body["messages"][1]["content"])
        return parser('{"decisions": [], "additions": []}'), 1

    with patch.object(llm_adapter, "resolve_endpoint", return_value=(llm_adapter.LlmEndpoint("http://x/v1", "k", "m", "local"), "本地模型")), \
         patch.object(llm_adapter, "_request_structured", fake_structured):
        asyncio.run(llm_adapter.verify_with_llm(text, spans, Strategy.MASK))
    prompt = captured["prompt"]
    assert list(prompt) == ["task", "entity_types", "context", "candidates", "user_requirement", "requirement_rule", "output_schema"]
    assert "ROLE" not in prompt["entity_types"]
    assert all(item["label"] != "ROLE" for item in prompt["candidates"])


def test_generalization_follows_the_language_of_the_sentence():
    text = "导师在星河大学任教。\nShe presented the survey in Shanghai with Dr. Alice Morgan."
    task = _detect(text=text, strategy="generalize", privacy_strength=1)
    output = task["redacted_text"]
    assert "某高校" in output
    assert "a city in East China" in output
    assert "Dr. someone" in output
    assert "某某" not in output.split("\n")[1]


def test_address_generalizes_up_the_administrative_hierarchy():
    from app.knowledge_base import local_levels
    from app.schemas import EntityType

    levels, _, _ = local_levels("浙江省杭州市西湖区文三路90号", EntityType.ADDRESS)
    assert levels == ("杭州市西湖区", "杭州市", "浙江省")
    levels, _, _ = local_levels("London", EntityType.LOCATION, "en")
    assert levels[0] == "a city in the UK"


def test_models_expose_confidence_threshold_and_policies_have_sensible_defaults():
    info = client.get("/api/v1/models").json()
    assert info["confidence_threshold"] == 0.9
    from app.main import DEFAULT_POLICIES
    assert DEFAULT_POLICIES["PERSON"] == "pseudonymize"
    assert DEFAULT_POLICIES["ORG"] == "generalize"
    assert DEFAULT_POLICIES["PHONE"] == "mask" and DEFAULT_POLICIES["ROLE"] == "generalize"


def test_names_next_to_titles_and_verbs_keep_correct_length():
    from app.recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
    from app.schemas import Strategy

    text = "心内科主任王芳会参加。客户李明反馈银行卡 6222021001116248 被重复扣款。王老师和李经理都在。"
    spans = merge_spans(text, detect_rule_spans(text, Strategy.MASK)[0] + detect_lite_ner_spans(text, Strategy.MASK)[0])
    people = {span.text for span in spans if span.entity_type.value == "PERSON"}
    assert people == {"王芳", "李明"}
    # “银行卡”不是一家叫“反馈银行”的机构
    assert not any(span.entity_type.value == "ORG" for span in spans)


def test_instruction_parser_handles_both_word_orders():
    from app.instruction_parser import parse_instruction_locally

    plan = parse_instruction_locally("保留所有北京的地名，但上海的相关地名隐去")
    assert plan["preserve_terms"] == ["北京"] and plan["force_terms"] == ["上海"]
    assert parse_instruction_locally("隐去项目代号“星舟”")["force_terms"] == ["星舟"]
    assert parse_instruction_locally("把项目代号星舟都隐藏")["force_terms"] == ["星舟"]


def test_legacy_tasks_get_an_inferred_replacement_map():
    """改版前保存的任务没有 replacements，读取时按结果文本对齐补出，三种方式都与实际映射一致。"""
    from app.anonymizer import infer_replacements, redact_with_map
    from app.recognizers import detect_lite_ner_spans, detect_rule_spans, merge_spans
    from app.schemas import Strategy

    text = "我叫林若宁，在星河大学读研究生，电话 13912345678。林若宁的导师是陈晓峰教授。"
    rule, _ = detect_rule_spans(text, Strategy.MASK)
    lite, _ = detect_lite_ner_spans(text, Strategy.MASK)
    spans = merge_spans(text, rule + lite)
    for strategy in Strategy:
        for span in spans:
            span.strategy = strategy
        redacted, segments = redact_with_map(text, spans, strategy, 2, include_pending=True)
        inferred = infer_replacements(text, [span.model_dump(mode="json") for span in spans], redacted)
        expected = [(item["span_id"], item["out_start"], item["out_end"]) for item in segments if item["replacement"] != text[item["start"]:item["end"]]]
        assert [(item["span_id"], item["out_start"], item["out_end"]) for item in inferred] == expected
    assert infer_replacements(text, [span.model_dump(mode="json") for span in spans], "完全不同的文本") == []


def test_legacy_scope_gains_role_but_new_choices_are_kept():
    """改版前保存的方案勾选了当时的全部十类，读取时补上职务身份；新方案里主动取消职务身份则保持取消。"""
    from app.schemas import DetectRequest, EntityType, migrate_scope

    legacy = ["PERSON", "ORG", "LOCATION", "ADDRESS", "PHONE", "EMAIL", "ID_CARD", "BANK_CARD", "PASSPORT", "CUSTOM"]
    request = DetectRequest.model_validate({"text": "心内科主任来了", "enabled_entity_types": legacy})
    assert EntityType.ROLE in request.enabled_entity_types and request.scope_version == 2
    chosen = DetectRequest.model_validate({"text": "心内科主任来了", "enabled_entity_types": legacy, "scope_version": 2})
    assert EntityType.ROLE not in chosen.enabled_entity_types
    partial = migrate_scope({"enabled_entity_types": ["PERSON", "PHONE"]})
    assert partial["enabled_entity_types"] == ["PERSON", "PHONE"] and partial["scope_version"] == 2

    created = client.post("/api/v1/projects", json={"name": "范围迁移", "config": {"enabled_entity_types": legacy, "scope_version": 2}}).json()
    assert "ROLE" not in created["config"]["enabled_entity_types"]
    policies = client.get("/api/v1/policies").json()["policies"]
    assert policies["ROLE"] in {"mask", "pseudonymize", "generalize"}
