from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate import (
    BATCH_SIZE_BY_TIER,
    DEFAULT_MODEL_WEIGHTS,
    TIER_REQUEST_LIMITS,
    TIER_WEIGHTS,
    build_prompt,
    classify_tier,
    prompt_leakage_hits,
    substring_leak_warnings,
)


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


def test_prompt_uses_structure_not_character_count() -> None:
    sampled = {
        "domain": "测试场景",
        "format": "内部备忘录",
        "tone": "正式书面",
        "label_group": ["PERSON", "ORG", "PHONE", "EMAIL"],
    }
    middle = build_prompt("中", "en", sampled)
    assert "1-2 个自然段，每段 3-5 句" in middle
    assert "300-540" not in middle and "字符" not in middle
    assert "那个电话一直没打通" in middle
    long = build_prompt("长", "zh", sampled)
    assert "2-3 个自然段，每段 3-4 句" in long
    assert "不要写成长篇独白或连续吐槽" in long


def test_tier_weights_and_request_limits() -> None:
    assert TIER_WEIGHTS == {"短密": 0.05, "短": 0.30, "中": 0.55, "长": 0.10}
    assert BATCH_SIZE_BY_TIER == {"短密": 8, "短": 8, "中": 4, "长": 1}
    assert TIER_REQUEST_LIMITS == {
        "短密": {"timeout": 45.0, "max_tokens": 2000},
        "短": {"timeout": 45.0, "max_tokens": 2000},
        "中": {"timeout": 60.0, "max_tokens": 2500},
        "长": {"timeout": 60.0, "max_tokens": 1500},
    }
    assert DEFAULT_MODEL_WEIGHTS == {
        "glm-5.2": 0.30,
        "deepseek-v4-pro": 0.25,
        "qwen3.7-plus": 0.25,
        "kimi-k2.6": 0.20,
    }


def test_measured_tier_boundary_moves_to_550() -> None:
    assert classify_tier("zh", 549, 0) == "中"
    assert classify_tier("zh", 550, 0) == "长"
    assert classify_tier("en", 549, 0) == "中"
    assert classify_tier("en", 550, 0) == "长"
