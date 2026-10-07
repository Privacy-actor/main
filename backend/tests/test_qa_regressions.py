"""2026-10 测试代理发现的问题的回归测试：隐私覆盖、稳定性、批处理和接口健壮性。"""
import hashlib
import io
import json
import time
import zipfile

from fastapi.testclient import TestClient

from app.main import app
from app.recognizers import merge_spans
from app.schemas import EntityType, Span, Strategy

client = TestClient(app, raise_server_exceptions=False)


def _detect(**payload):
    response = client.post("/api/v1/detect", json={"use_llm": False, **payload})
    assert response.status_code == 200, response.text
    return response.json()


def _span(start, end, text, entity_type, score, sources=("NER",), status="accepted"):
    return Span(id=f"{entity_type}-{start}", start=start, end=end, text=text, entity_type=entity_type, score=score,
                sources=list(sources), status=status, strategy=Strategy.MASK)


# 保留词与隐去词

def test_preserve_term_only_exempts_itself():
    text = "他在北京协和医院工作，住在北京市朝阳区建国路88号。"
    result = _detect(text=text, preserve_terms=["北京"], persist=False)
    assert "北京协和医院" not in result["redacted_text"]
    assert "建国路88号" not in result["redacted_text"]
    plan = _detect(text="患者住在北京市海淀区中关村大街27号，来北京复诊。", instruction="保留北京的地名", persist=False)
    assert "中关村大街27号" not in plan["redacted_text"]


def test_propagation_respects_preserved_ranges_and_casefold_offsets():
    result = _detect(text="我在北京大学读书，后来去北京工作。", preserve_terms=["北京大学"], persist=False)
    assert result["redacted_text"].startswith("我在北京大学读书")
    shifted = _detect(text="ßßß联系人：王芳 13800138000", preserve_terms=["王芳"], persist=False)
    assert shifted["redacted_text"] == "ßßß联系人：王芳 【PHONE-001】"


def test_hide_term_inside_an_entity_keeps_the_whole_entity_redacted():
    text = "患者王建国，住址：北京市海淀区中关村大街27号，就诊于北京协和医院。"
    result = _detect(text=text, custom_keywords=[{"value": "海淀"}, {"value": "协和"}], persist=False)
    assert "中关村大街" not in result["redacted_text"]
    assert "北京协和医院" not in result["redacted_text"] and "医院" not in result["redacted_text"]


def test_partial_overlap_merges_into_one_covering_span():
    text = "联系地址北京市海淀区27号院"
    merged = merge_spans(text, [_span(4, 13, "北京市海淀区27号", EntityType.ADDRESS, .92), _span(10, 14, "27号院", EntityType.CUSTOM, 1.0, ("CUSTOM_RULE",))])
    assert [(item.start, item.end) for item in merged] == [(4, 14)]
    assert merged[0].conflict


def test_force_terms_survive_a_restricted_scope():
    result = _detect(text="联系人李明负责星舟计划。", instruction="只脱敏人名，另外隐去星舟", persist=False)
    assert "星舟" not in result["redacted_text"]


# 稳定性与健壮性

def test_catastrophic_regex_times_out_instead_of_hanging():
    tested = client.post("/api/v1/rules/test", json={"kind": "regex", "pattern": "(a|aa)+$", "text": "a" * 80 + "b"}).json()
    assert tested["valid"] and tested["timed_out"]
    started = time.perf_counter()
    result = _detect(text="a" * 80 + "b", custom_patterns=[{"name": "慢规则", "pattern": "(a|aa)+$"}], persist=False)
    assert time.perf_counter() - started < 5
    rule_trace = next(step for step in result["trace"] if step["key"] == "rule")
    assert "超时" in rule_trace["detail"]


def test_many_entities_merge_quickly():
    text = "".join(f"1380013{index:04d}，" for index in range(8000))
    started = time.perf_counter()
    result = _detect(text=text, persist=False)
    assert time.perf_counter() - started < 10
    assert result["summary"]["total"] == 8000


def test_invalid_requests_get_422_without_echoing_input():
    response = client.post("/api/v1/detect", content='{"text":"abc\\ud800 13800138000","use_llm":false}', headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert "13800138000" not in response.text
    assert client.post("/api/v1/detect", json={"text": "abc", "scope_version": [1]}).status_code == 422
    deep = client.post("/api/v1/extract", files={"files": ("deep.json", ("[" * 50000 + "]" * 50000).encode(), "application/json")})
    assert deep.status_code == 400


def test_docx_export_tolerates_control_characters():
    task = _detect(text="联系人：王芳，电话13800138000\u000b换行\u000c结束\u0001")
    response = client.get(f"/api/v1/tasks/{task['task_id']}/export?format=docx")
    assert response.status_code == 200


# 差分隐私替换

def test_pseudonyms_stay_stable_across_review_actions():
    task = _detect(text="联系人：王芳，电话13800138000。张伟教授在北京协和医院工作。", strategy="pseudonymize", privacy_strength=3)
    first = task["redacted_text"]
    phone = next(span for span in task["spans"] if span["entity_type"] == "PHONE")
    for _ in range(3):
        snapshot = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": phone["id"], "operation": "accept"}).json()["snapshot"]
        assert snapshot["redacted_text"] == first
        assert "redaction_memory" not in snapshot


def test_pseudonyms_are_unique_and_never_reuse_names_from_the_document():
    from app.anonymizer import redact_with_map
    names = ["王伟", "李娜", "张敏", "罗刚", "陈丽", "梁辉", "刘洋", "陈静", "赵敏", "孙强", "周杰", "吴磊", "郑爽", "冯涛", "褚明", "卫东", "蒋欣", "沈畅", "韩梅", "杨帆"]
    text = "参会人员：" + "、".join(f"{name}教授" for name in names) + "，以及林清禾。"
    spans, position = [], 5
    for name in names:
        spans.append(Span(id=name, start=position, end=position + len(name), text=name, entity_type=EntityType.PERSON, score=1,
                          sources=["TEST"], strategy=Strategy.PSEUDONYMIZE))
        position += len(name) + 3
    for _ in range(20):
        _, replacements = redact_with_map(text, spans, None, 3)
        values = [item["replacement"] for item in replacements]
        assert len(set(values)) == len(values)
        assert "林清禾" not in values


def test_switching_to_one_method_clears_per_type_methods():
    task = _detect(text="联系人：王芳，电话13800138000。", use_policies=True)
    snapshot = client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": "all", "operation": "set_strategy", "after": "mask"}).json()["snapshot"]
    assert snapshot["applied_config"]["use_policies"] is False


def test_generalization_does_not_reexpose_a_hidden_place():
    result = _detect(text="王芳来北京开会，住在北京市海淀区中关村大街59号。", strategy="generalize", privacy_strength=1, persist=False)
    assert "北京" not in result["redacted_text"]


# 批处理

def _run_job(files, **config):
    response = client.post("/api/v1/jobs", files=files, data={"config_json": json.dumps({"use_llm": False, **config})})
    assert response.status_code == 200, response.text
    job = client.get(f"/api/v1/jobs/{response.json()['id']}").json()
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/jobs/{job['id']}/download").content))
    return job, {name: archive.read(name).decode("utf-8-sig") for name in archive.namelist()}


def test_batch_csv_keeps_every_column_and_numbers_entities_across_rows():
    rows = "姓名,手机,地址,备注\n张伟,13800138000,北京市海淀区中关村大街27号,老客户\n李娜,13900139000,上海市浦东新区世纪大道88号,张伟介绍\n张伟,13800138000,北京市海淀区中关村大街27号,复购\n"
    job, files = _run_job([("files", ("客户.csv", rows.encode("utf-8"), "text/csv"))])
    assert len(job["payload"]["results"]) == 3
    output = files["客户.redacted.csv"].splitlines()
    assert output[0] == "姓名,手机,地址,备注"
    assert output[1] == "【PERSON-001】,【PHONE-001】,【ADDRESS-001】,老客户"
    assert output[2] == "【PERSON-002】,【PHONE-002】,【ADDRESS-002】,【PERSON-001】介绍"
    assert output[3] == output[1].replace("老客户", "复购")


def test_batch_json_round_trip_keeps_types_and_nulls():
    items = [{"name": "王芳", "age": 30, "vip": True, "note": "电话13700137000"}, {"name": "赵敏", "age": 41, "note": None}]
    _, files = _run_job([("files", ("记录.json", json.dumps(items, ensure_ascii=False).encode("utf-8"), "application/json"))])
    restored = json.loads(files["记录.redacted.json"])
    assert restored[0] == {"name": "【PERSON-001】", "age": 30, "vip": True, "note": "电话【PHONE-001】"}
    assert restored[1] == {"name": "【PERSON-002】", "age": 41, "note": None}


def test_batch_text_files_keep_layout_names_and_encoding():
    text = "第一段：联系人张伟，电话13800138000。\n\n\n第二段：张伟又来电。\n"
    job, files = _run_job([
        ("files", ("a/notes.txt", text.encode("utf-8"), "text/plain")),
        ("files", ("b/notes.txt", "联系人：李娜，电话13900139000".encode("utf-8"), "text/plain")),
        ("files", ("gbk.txt", "联系人：王芳，电话13700137000。".encode("gb18030"), "text/plain")),
    ])
    assert files["a/notes.redacted.txt"] == "第一段：联系人【PERSON-001】，电话【PHONE-001】。\n\n\n第二段：【PERSON-001】又来电。\n"
    assert "李娜" not in files["b/notes.redacted.txt"]
    assert files["gbk.redacted.txt"] == "联系人：【PERSON-001】，电话【PHONE-001】。"
    assert len(job["payload"]["results"]) == 3


def test_batch_rows_whose_task_was_deleted_are_marked():
    job, _ = _run_job([("files", ("one.txt", "联系人：王芳，电话13700137000".encode("utf-8"), "text/plain"))])
    task_id = job["payload"]["results"][0]["task_id"]
    assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 200
    refreshed = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert refreshed["payload"]["results"][0]["status"] == "deleted"


# 复核队列、任务记录、复检、审计

def test_review_queue_and_history_cover_all_tasks():
    for index in range(105):
        _detect(text=f"第{index}号：请联系心内科主任。")
    queue = client.get("/api/v1/reviews").json()
    assert queue["total"] >= 105
    history = client.get("/api/v1/history?limit=10&pending=true").json()
    assert history["total"] >= 105 and len(history["items"]) == 10
    found = client.get("/api/v1/history?q=第104号").json()
    assert found["total"] == 1


def test_recheck_still_reports_a_leaked_address_that_contains_a_kept_word():
    task = _detect(text="住在北京市朝阳区建国路88号。", preserve_terms=["北京"])
    leaked = client.post(f"/api/v1/tasks/{task['task_id']}/recheck", json={"text": "住在北京市朝阳区建国路88号。"}).json()
    assert not leaked["passed"]


def test_audit_hashes_are_keyed():
    task = _detect(text="联系人：王芳，电话13800138000。")
    phone = next(span for span in task["spans"] if span["entity_type"] == "PHONE")
    client.post("/api/v1/reviews", json={"task_id": task["task_id"], "span_id": "manual", "operation": "add",
                                         "span": {**phone, "id": "manual"}})
    audits = client.get(f"/api/v1/tasks/{task['task_id']}/audits").json()["items"]
    recorded = json.dumps(audits)
    assert hashlib.sha256(b"13800138000").hexdigest() not in recorded


# 自然语言要求

def test_instruction_parser_understands_negation_scope_and_per_type_methods():
    from app.instruction_parser import parse_instruction_locally as parse

    def plan(text):
        return {key: value for key, value in parse(text).items() if value and key != "parser"}

    assert plan("不要隐去李明") == {"preserve_terms": ["李明"]}
    assert plan("Do not redact emails") == {"disabled_entity_types": ["EMAIL"]}
    assert plan("邮箱不要脱敏") == {"disabled_entity_types": ["EMAIL"]}
    assert plan("不要处理邮箱") == {"disabled_entity_types": ["EMAIL"]}
    assert plan("手机号不用脱敏") == {"disabled_entity_types": ["PHONE"]}
    assert plan("姓名用差分隐私替换") == {"type_strategies": {"PERSON": "pseudonymize"}}
    assert plan("保留人名，只脱敏电话") == {"enabled_entity_types": ["PHONE"], "disabled_entity_types": ["PERSON"]}
    assert plan("只脱敏人名，另外隐去星舟") == {"enabled_entity_types": ["PERSON"], "force_terms": ["星舟"]}
    assert plan("不要泛化，用掩码") == {"strategy": "mask"}
    assert plan("Only redact phone numbers and replace them with placeholders") == {"enabled_entity_types": ["PHONE"], "strategy": "mask"}
    assert plan("只脱敏邮箱地址") == {"enabled_entity_types": ["EMAIL"]}
    assert plan("Show me the redacted text") == {}
    assert plan("use the strictest protection") == {"privacy_strength": 3}
    assert plan("保留张伟和李娜") == {"preserve_terms": ["张伟", "李娜"]}
    assert plan("保留北京协和医院") == {"preserve_terms": ["北京协和医院"]}
    assert plan("pseudonymize names and mask phone numbers") == {"type_strategies": {"PERSON": "pseudonymize", "PHONE": "mask"}}


def test_per_type_method_from_instruction_is_applied():
    result = _detect(text="联系人：王芳，电话13800138000。", instruction="姓名用差分隐私替换", persist=False)
    assert "【PHONE-001】" in result["redacted_text"]
    assert "王芳" not in result["redacted_text"] and "【PERSON" not in result["redacted_text"]
    kept = _detect(text="联系人：王芳，邮箱 wang@example.com。", instruction="邮箱不要脱敏", persist=False)
    assert "wang@example.com" in kept["redacted_text"] and "王芳" not in kept["redacted_text"]


# 识别误报与漏报（测试代理给出的样例）

def test_common_words_are_not_entities():
    for text in ("患者高血压病史十年。", "学生时代的回忆很美好。", "客户高度评价我们的服务。", "员工应当遵守规定。",
                 "这些任务完成都很顺利。", "This is a comparison of results.", "这所学校的老师很好。", "去年学校组织了春游。",
                 "王老师和李经理都在。", "大家隆重庆祝了这一天。"):
        result = _detect(text=text, persist=False)
        assert result["spans"] == [], (text, result["spans"])
    english = _detect(text="Last Friday the Sales Team met in New Delhi to review Quarterly Results.", persist=False)
    assert [(span["text"], span["entity_type"]) for span in english["spans"]] == [("New Delhi", "LOCATION")]


def test_full_width_and_zero_width_numbers_are_detected():
    for text, value in (("电话：１３８００１３８０００", "１３８００１３８０００"), ("电话138​0013​8000", "138​0013​8000"),
                        ("My phone is ＋86 １３８００１３８０００.", "＋86 １３８００１３８０００")):
        result = _detect(text=text, persist=False)
        assert [(span["text"], span["entity_type"]) for span in result["spans"]] == [(value, "PHONE")]
        assert value not in result["redacted_text"]


def test_english_name_after_a_non_name_word_and_role_boundaries():
    result = _detect(text="Contact Wang Fang at wang.fang@example.com or 415-555-0123.", persist=False)
    assert ("Wang Fang", "PERSON") in [(span["text"], span["entity_type"]) for span in result["spans"]]
    role = _detect(text="请联系心内科主任。", persist=False)
    assert [(span["text"], span["entity_type"]) for span in role["spans"]] == [("心内科主任", "ROLE")]


def test_rule_templates_match_their_samples():
    """前端规则模板（frontend/src/lib/ruleTemplates.ts）在后端的正则引擎里能用，并能命中示例。"""
    import re as std_re
    from pathlib import Path

    from app.recognizers import compile_user_pattern, find_user_matches

    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "ruleTemplates.ts").read_text(encoding="utf-8")
    templates = std_re.findall(r"key: '([^']+)'.*?pattern: String\.raw`([^`]+)`,\s*sample: '([^']+)'", source, std_re.S)
    assert len(templates) >= 11
    for key, pattern, sample in templates:
        compiled = compile_user_pattern(pattern)
        found, complete = find_user_matches(compiled, sample)
        assert complete and found, key
        matched = [sample[start:end] for start, end in found]
        if key == "uscc":
            assert matched == ["91310115MA1K4ABC2X"], matched
