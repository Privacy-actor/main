"""最后一轮独立测试发现的问题（2026-10-03 晚）：自然语言要求误缩小范围、座机、常见姓名、地址边界、英文署名等。"""
import asyncio

from fastapi.testclient import TestClient

from app import instruction_parser
from app.llm_adapter import InstructionPlan
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

PII_TEXT = (
    "患者张建国，男，56岁，身份证号110105198506120034，住址北京市朝阳区建国路88号院3号楼502室，"
    "联系电话13812345678，邮箱zhangjg@163.com，银行卡号6222021234567890128。"
)
PII_VALUES = ("张建国", "110105198506120034", "13812345678", "zhangjg@163.com", "6222021234567890128", "建国路88号院")


def _detect(**payload):
    response = client.post("/api/v1/detect", json={"use_llm": False, "persist": False, **payload})
    assert response.status_code == 200, response.text
    return response.json()


def _found(result):
    return {(span["text"], span["entity_type"]) for span in result["spans"] if span["status"] != "rejected"}


# A1 自然语言要求里的“只……”

def test_partial_keep_requests_never_switch_redaction_off():
    for instruction in ("电话号码只保留后四位", "手机号只显示后四位", "只保留姓氏", "身份证中间八位打码", "keep only the last four digits of phone numbers"):
        result = _detect(text=PII_TEXT, instruction=instruction)
        for value in PII_VALUES:
            assert value not in result["redacted_text"], (instruction, value)
        plan = result["applied_config"]["instruction_plan"]
        assert plan["ignored_clauses"] and not plan["enabled_entity_types"], (instruction, plan)
        step = next(item for item in result["trace"] if item["key"] == "instruction")
        assert "暂不支持" in step["detail"]


def test_only_scope_needs_a_clear_object():
    parse = instruction_parser.parse_instruction_locally
    assert parse("只处理姓名")["enabled_entity_types"] == ["PERSON"]
    assert parse("仅针对电话和邮箱")["enabled_entity_types"] == ["PHONE", "EMAIL"]
    assert parse("只保留北京")["preserve_terms"] == ["北京"] and not parse("只保留北京")["enabled_entity_types"]
    assert parse("只对姓名做差分隐私替换")["type_strategies"] == {"PERSON": "pseudonymize"}
    assert not parse("只对姓名做差分隐私替换")["enabled_entity_types"]
    for vague in ("只要后面的", "只隐去患者信息", "把文中涉及患者的部分都隐去"):
        plan = parse(vague)
        assert not plan["enabled_entity_types"] and not plan["force_terms"] and plan["ignored_clauses"], (vague, plan)


def test_model_output_cannot_quietly_narrow_the_scope(monkeypatch):
    async def fake_llm(instruction, deployment_mode="local"):
        return InstructionPlan(enabled_entity_types=["PHONE"], disabled_entity_types=["PHONE", "EMAIL"], preserve_terms=["后四位", "张建国"])

    monkeypatch.setattr(instruction_parser, "parse_instruction_with_llm", fake_llm)
    plan = asyncio.run(instruction_parser.parse_instruction("电话号码只保留后四位", use_llm=True))
    assert plan["enabled_entity_types"] == [] and plan["disabled_entity_types"] == [] and plan["preserve_terms"] == []
    assert plan["ignored_clauses"] == ["电话号码只保留后四位"]
    kept = asyncio.run(instruction_parser.parse_instruction("邮箱不要脱敏", use_llm=True))
    assert kept["disabled_entity_types"] == ["EMAIL"] and kept["enabled_entity_types"] == []


# A2 座机、校验位不对的卡号和证件号

def test_landlines_are_phone_numbers():
    cases = {
        "办公电话010-62751234，传真021-64381234。": ["010-62751234", "021-64381234"],
        "请致电021-54321234联系前台。": ["021-54321234"],
        "科室电话：(010)69156114": ["(010)69156114"],
        "总机 0571-87654321 转 8001": ["0571-87654321 转 8001"],
        "深圳办公室 0755-86001234，另一个号码 0755 8600 1234。": ["0755-86001234", "0755 8600 1234"],
        "科室电话：（０１０）６９１５６１１４": ["（０１０）６９１５６１１４"],
    }
    for text, values in cases.items():
        result = _detect(text=text)
        assert {value for value, label in _found(result) if label == "PHONE"} == set(values), (text, result["spans"])
        assert not any(label == "BANK_CARD" for _, label in _found(result)), text
        for value in values:
            assert value not in result["redacted_text"]


def test_recheck_flags_a_landline_left_in_the_final_text():
    task = client.post("/api/v1/detect", json={"text": "王小明的办公电话010-62751234。", "use_llm": False}).json()
    checked = client.post(f"/api/v1/tasks/{task['task_id']}/recheck", json={"text": "王小明的办公电话010-62751234。"}).json()
    assert not checked["passed"]
    assert any("010-62751234" in finding["text"] for finding in checked["findings"])


def test_card_and_id_numbers_next_to_their_labels_do_not_need_a_valid_checksum():
    card = _detect(text="银行卡号6222000011112222，持卡人本人。")
    assert ("6222000011112222", "BANK_CARD") in _found(card)
    identity = _detect(text="其身份信息110101198910233128已附在报销封面页。")
    assert ("110101198910233128", "ID_CARD") in _found(identity)
    assert not any(label == "BANK_CARD" for _, label in _found(identity))
    plain = _detect(text="订单编号6222000011112222已发货。")
    assert not any(label == "BANK_CARD" for _, label in _found(plain))


# A3 常见姓名与英文街道地址

def test_common_names_in_lists_family_terms_and_before_contact_words():
    cases = {
        "参会人有王伟、陈静、李强。": {"王伟", "陈静", "李强"},
        "同行的还有刘伟、张敏和李静。": {"刘伟", "张敏", "李静"},
        "其母王秀兰陪同就诊。": {"王秀兰"},
        "其子张小明今年读高二，妻子李秀英也来了。": {"张小明", "李秀英"},
        "王小明的办公电话010-62751234。": {"王小明"},
        "王强负责与李娜对接。": {"王强", "李娜"},
        "家属 | 刘小红": {"刘小红"},
        "紧急联系人：陈晓东": {"陈晓东"},
    }
    for text, names in cases.items():
        result = _detect(text=text)
        found = {value for value, label in _found(result) if label == "PERSON"}
        assert names <= found, (text, result["spans"])
        for name in names:
            assert name not in result["redacted_text"], (text, name)


def test_lists_of_ordinary_words_are_not_names():
    for text in ("会议讨论了安全、质量和成本。", "请准备方案、计划和预算。", "我们去了北京、上海、广州。", "成本、费用、余额都要核对。",
                 "联系人电话：13800138000", "联系人信息有误。", "联系人待定，地址另行通知。", "包装也很严实。"):
        result = _detect(text=text)
        assert not any(label == "PERSON" for _, label in _found(result)), (text, result["spans"])


def test_english_street_addresses():
    cases = {
        "Please send it to 742 Evergreen Terrace, Springfield, IL 62704 in care of Bella.": "742 Evergreen Terrace, Springfield, IL 62704",
        "She lives at 221B Baker Street.": "221B Baker Street",
        "The office is at 18 Keji Road, Nanshan District, Shenzhen.": "18 Keji Road, Nanshan District, Shenzhen",
        "Visit 1600 Amphitheatre Parkway, Mountain View tomorrow.": "1600 Amphitheatre Parkway, Mountain View",
        "Mail it to 22 Broadway, Suite 100, Denver, CO 80203.": "22 Broadway, Suite 100, Denver, CO 80203",
    }
    for text, address in cases.items():
        result = _detect(text=text)
        assert (address, "ADDRESS") in _found(result), (text, result["spans"])
        assert address not in result["redacted_text"]
    assert not any(label == "PERSON" for _, label in _found(_detect(text="Visit 1600 Amphitheatre Parkway, Mountain View tomorrow.")))


# B1 姓名后面紧跟“电话”

def test_names_followed_by_contact_words_keep_their_length():
    cases = {
        "客户赵敏电话13512345678。": ("赵敏", "客户【PERSON-001】电话【PHONE-001】。"),
        "联系人王芳电话13900001111": ("王芳", "联系人【PERSON-001】电话【PHONE-001】"),
        "经理李强电话：13800001111": ("李强", "经理【PERSON-001】电话：【PHONE-001】"),
    }
    for text, (name, redacted) in cases.items():
        result = _detect(text=text)
        assert (name, "PERSON") in _found(result), (text, result["spans"])
        assert result["redacted_text"] == redacted, (text, result["redacted_text"])


# B2 地址的起点与结尾

def test_addresses_keep_their_labels_and_their_tails():
    cases = {
        "住址北京市朝阳区建国路88号院3号楼502室": "北京市朝阳区建国路88号院3号楼502室",
        "地址北京市海淀区中关村大街27号": "北京市海淀区中关村大街27号",
        "住址成都市武侯区人民南路四段9号": "成都市武侯区人民南路四段9号",
        "地址是 北京市朝阳区建国路88号SOHO现代城A座1205室。": "北京市朝阳区建国路88号SOHO现代城A座1205室",
        "登记通讯地址为海南省海口市美兰区国兴大道5号日月广场双子座D栋701室。": "海南省海口市美兰区国兴大道5号日月广场双子座D栋701室",
        "寄往成都市高新区天府大道北段 1480 号拉德方斯大厦西楼 9 层，电话 19999340408。": "成都市高新区天府大道北段 1480 号拉德方斯大厦西楼 9 层",
        "请将发票寄往新地址，具体武汉市洪山区珞喻路1037号华中科技园A栋2单元305室。": "武汉市洪山区珞喻路1037号华中科技园A栋2单元305室",
        "其位于唐家湾镇创新路18号B栋3层的办事处已投入运营。": "唐家湾镇创新路18号B栋3层",
    }
    for text, address in cases.items():
        result = _detect(text=text)
        assert (address, "ADDRESS") in _found(result), (text, result["spans"])
        assert address not in result["redacted_text"]
    labelled = _detect(text="住址北京市朝阳区建国路88号院3号楼502室")
    assert labelled["redacted_text"].startswith("住址【ADDRESS-001】")


# B3 英文署名

def test_english_signatures_do_not_swallow_the_next_line():
    cases = {
        "Best regards,\nJohn Smith\nCustomer Service": ("John Smith", "Best regards,\n【PERSON-001】\nCustomer Service"),
        "Thanks,\nAnna Lee\nMarketing Team": ("Anna Lee", "Thanks,\n【PERSON-001】\nMarketing Team"),
        "Regards,\nPeter Wang\nHuman Resources": ("Peter Wang", "Regards,\n【PERSON-001】\nHuman Resources"),
    }
    for text, (name, redacted) in cases.items():
        result = _detect(text=text)
        assert {value for value, label in _found(result) if label == "PERSON"} == {name}, (text, result["spans"])
        assert result["redacted_text"] == redacted
    for text in ("Department: Customer Service", "Facilities Coordinator at Northstar"):
        assert not any(label == "PERSON" for _, label in _found(_detect(text=text))), text
    result = _detect(text="Did Julia Harris send the file? When Lucy Murphy visited, Respondent Nina Bennett left.")
    names = {value for value, label in _found(result) if label == "PERSON"}
    assert names == {"Julia Harris", "Lucy Murphy", "Nina Bennett"}, names


# B4 中英混合文本的泛化按句子语言

def test_generalization_language_follows_each_sentence():
    text = ("Sarah will send the contract to the client's office in Shenzhen. "
            "下次会议在上海浦东新区世纪大道100号举行。")
    for strength in (1, 2, 3):
        result = _detect(text=text, strategy="generalize", privacy_strength=strength)
        english, chinese = result["redacted_text"].split("下次会议在", 1)
        assert not any("一" <= char <= "鿿" for char in english), (strength, english)
        assert not any(char.isascii() and char.isalpha() for char in chinese), (strength, chinese)


# B5 病历号等编号不是护照号

def test_record_numbers_are_not_passports():
    for text in ("病历号BL123456", "住院号ZY20231234", "会员号VIP123456", "工单号AB654321"):
        assert not any(label == "PASSPORT" for _, label in _found(_detect(text=text))), text
    for text, value in (("护照号E12345678", "E12345678"), ("证件号G12345678", "G12345678"), ("his passportD8809648 expires soon", "D8809648")):
        assert (value, "PASSPORT") in _found(_detect(text=text)), text


# B6 只设置了云端模型、部署方式仍是本地时，识别轨迹说明原因，不自动把句子发到云端

def test_local_mode_does_not_fall_back_to_the_cloud_model():
    saved = client.put("/api/v1/llm/settings", json={"cloud": {"enabled": True, "provider": "custom", "base_url": "http://127.0.0.1:9/v1", "model": "demo-cloud", "api_key": "sk-test-000000000000"}})
    assert saved.status_code == 200, saved.text
    try:
        result = client.post("/api/v1/detect", json={"text": "联系人：王芳，电话13800138000。", "use_llm": True, "deployment_mode": "local", "persist": False}).json()
        step = next(item for item in result["trace"] if item["key"] == "llm")
        assert step["status"] == "skipped" and "改为云端" in step["detail"], step
    finally:
        client.put("/api/v1/llm/settings", json={"reset": ["local", "cloud"]})


def test_passport_exclusions_only_match_whole_words():
    # “valid”结尾的 id 不是编号说法
    assert ("D432530145", "PASSPORT") in _found(_detect(text="Visitors must present a valid D432530145 at the desk."))
    assert not any(label == "PASSPORT" for _, label in _found(_detect(text="Order no. AB123456 has shipped.")))
