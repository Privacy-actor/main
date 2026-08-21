from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate import prompt_leakage_hits, substring_leak_warnings


def test_prompt_leakage_gate_real_pilot_examples() -> None:
    positives = (
        "一共是五个实体，刚好符合我们这次标注的要求",
        "你必须在备忘录里用 ADDRESS 标签完整包起来",
        "不得将院系标为独立机构",
    )
    for text in positives:
        assert prompt_leakage_hits(text, []), text

    negatives = (
        "王建国在国家统计局工作，周末去广州出差。",
        "请把地址发给我，那个电话一直没打通，联系人还没定。",
        "实体经济发展平稳，银行本次贷款审核已经完成。",
    )
    for text in negatives:
        assert prompt_leakage_hits(text, []) == [], text

    tagged_label_name = "项目代号是 ADDRESS，已获批准。"
    start = tagged_label_name.index("ADDRESS")
    span = {"start": start, "end": start + len("ADDRESS"), "label": "ORG"}
    assert prompt_leakage_hits(tagged_label_name, [span]) == []


def test_substring_leak_is_review_warning_only() -> None:
    text = "广州市天河区已登记，广州另有一份记录。"
    span = {"start": 0, "end": 6, "label": "LOCATION"}
    assert substring_leak_warnings(text, [span]) == [
        "⚠ 可能的子串泄漏：『广州』出现在位置 10，未被覆盖"
    ]
