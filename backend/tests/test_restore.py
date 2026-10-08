"""还原大模型回答：把回答里的掩码编号、替换词、泛化词按任务的替换映射换回原文。"""
from fastapi.testclient import TestClient

from app.main import app
from app.restore import restore_text

client = TestClient(app, raise_server_exceptions=False)


def _task(task_id: str, text: str, pairs: list[tuple[str, str, str, str]]):
    """pairs：(原词, 替换词, 实体类型, 方式)。按原词在 text 里第一次出现的位置生成替换映射。"""
    replacements = []
    for index, (original, replacement, entity_type, strategy) in enumerate(pairs):
        start = text.index(original)
        replacements.append({
            "span_id": f"s{index}", "start": start, "end": start + len(original), "replacement": replacement,
            "entity_type": entity_type, "strategy": strategy,
        })
    spans = [{"id": item["span_id"], "text": original, "entity_type": item["entity_type"]} for item, (original, *_) in zip(replacements, pairs)]
    return {"task_id": task_id, "text": text, "spans": spans, "replacements": replacements}


MASKED = _task("t1", "张三和李四都在北京大学，身份证110101199001011234。", [
    ("张三", "【PERSON-001】", "PERSON", "mask"),
    ("李四", "【PERSON-002】", "PERSON", "mask"),
    ("北京大学", "【ORG-001】", "ORG", "mask"),
    ("110101199001011234", "【ID_CARD-001】", "ID_CARD", "mask"),
])


def test_mask_numbers_are_recognised_however_the_model_rewrote_them():
    answer = "【PERSON-001】是导师，PERSON-002 和 [PERSON_001] 都在 Org-1 工作；person 2 也提到了。"
    result = restore_text(answer, [MASKED])
    assert result["text"] == "张三是导师，李四 和 张三 都在 北京大学 工作；李四 也提到了。"
    assert result["restored"] == 5 and not result["unresolved"]
    first = result["items"][0]
    assert result["text"][first["start"]:first["end"]] == "张三" and first["replaced"] == "【PERSON-001】"


def test_types_with_underscores_and_lone_brackets():
    answer = "ID_CARD-001、ID-CARD-001、IDCARD001 和 （PERSON-001） 以及 【PERSON-002"
    result = restore_text(answer, [MASKED])
    assert result["text"] == "110101199001011234、110101199001011234、110101199001011234 和 （张三） 以及 【李四"


def test_numbers_missing_from_the_task_stay_and_are_listed():
    result = restore_text("PERSON-009 和 PERSON 2024 年的报告", [MASKED])
    assert result["text"] == "PERSON-009 和 PERSON 2024 年的报告"
    assert result["unresolved"] == [{"text": "PERSON-009", "reason": "unknown", "count": 1}]


def test_pseudonyms_respect_word_boundaries_and_prefer_longer_words():
    task = _task("t2", "Alice Morgan 和陈晓峰、陈晓见面", [
        ("Alice Morgan", "Emily Stone", "PERSON", "pseudonymize"),
        ("陈晓峰", "林清禾", "PERSON", "pseudonymize"),
        ("陈晓", "林清", "PERSON", "pseudonymize"),
    ])
    result = restore_text("Emily Stone met Emily Stoneman；林清禾和林清都来了。", [task])
    assert result["text"] == "Alice Morgan met Emily Stoneman；陈晓峰和陈晓都来了。"
    assert result["restored"] == 3


def test_generalised_words_are_restored_only_when_unambiguous():
    task = _task("t3", "北京协和医院和上海瑞金医院都与北京大学合作", [
        ("北京协和医院", "医疗机构", "ORG", "generalize"),
        ("上海瑞金医院", "医疗机构", "ORG", "generalize"),
        ("北京大学", "高等院校", "ORG", "generalize"),
    ])
    result = restore_text("医疗机构的报告提到了高等院校。", [task])
    assert result["text"] == "医疗机构的报告提到了北京大学。"
    assert [item["check"] for item in result["items"]] == [True]
    assert result["unresolved"] == [{"text": "医疗机构", "reason": "ambiguous", "count": 1, "candidates": ["北京协和医院", "上海瑞金医院"], "entity_type": "ORG"}]


def test_star_masks_must_match_completely():
    task = _task("t4", "证件号110101199001011234", [("110101199001011234", "**************1234", "ID_CARD", "pseudonymize")])
    result = restore_text("证件 **************1234 与 ****************1234", [task])
    assert result["text"] == "证件 110101199001011234 与 ****************1234"


def test_earlier_tasks_win_and_conflicts_are_noted():
    recent = _task("recent", "王五来了", [("王五", "【PERSON-001】", "PERSON", "mask")])
    result = restore_text("PERSON-001 和 PERSON-002", [recent, MASKED])
    assert result["text"] == "王五 和 李四"
    assert result["unresolved"] == [{"text": "PERSON-001", "reason": "conflict", "count": 1, "candidates": ["王五", "张三"], "entity_type": "PERSON", "chosen": "王五"}]
    assert [item["task_id"] for item in result["items"]] == ["recent", "t1"]


SAMPLE = "访谈人：林若宁，电话13912345678，邮箱lin.rn@example.com。导师陈晓峰在星河大学任教。"


def _detect(**payload):
    response = client.post("/api/v1/detect", json={"text": SAMPLE, "use_llm": False, **payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_restoring_the_whole_masked_text_gives_back_the_original():
    task = _detect(strategy="mask")
    assert task["redacted_text"] != SAMPLE
    response = client.post("/api/v1/restore", json={"text": task["redacted_text"], "task_ids": [task["task_id"]]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["text"] == SAMPLE
    assert result["restored"] == len(task["replacements"]) and result["tasks"] == [task["task_id"]]
    audits = client.get(f"/api/v1/tasks/{task['task_id']}/audits").json()["items"]
    assert audits[0]["operation"] == "restore" and audits[0]["payload"]["restored"] == result["restored"]
    assert SAMPLE not in str(audits[0]["payload"]) and task["redacted_text"] not in str(audits[0]["payload"])


def test_restoring_pseudonyms_from_the_api():
    task = _detect(strategy="pseudonymize")
    result = client.post("/api/v1/restore", json={"text": task["redacted_text"], "task_ids": [task["task_id"]]}).json()
    assert result["text"] == SAMPLE


def test_restore_requires_an_existing_task():
    response = client.post("/api/v1/restore", json={"text": "PERSON-001", "task_ids": ["no-such-task"]})
    assert response.status_code == 404 and "任务" in response.json()["detail"]
    assert client.post("/api/v1/restore", json={"text": "", "task_ids": ["x"]}).status_code == 422
    assert client.post("/api/v1/restore", json={"text": "x", "task_ids": []}).status_code == 422
