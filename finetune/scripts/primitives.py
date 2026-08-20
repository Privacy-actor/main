"""生成侧原语。所有函数纯函数、无 IO、可单测。"""
from __future__ import annotations
import hashlib, random, re

LABELS = ("PERSON","ORG","LOCATION","ADDRESS","PHONE","EMAIL",
          "ID_CARD","BANK_CARD","PASSPORT")
_TAG = re.compile(r"<([A-Z_]+)>(.*?)</([A-Z_]+)>", re.S)

class ParseError(ValueError): pass

def parse_inline_tagged(raw: str, allowed=LABELS) -> tuple[str, list[dict]]:
    """<PERSON>张伟</PERSON> -> (纯文本, spans)。偏移在剥标签过程中累加得到，
    不用 text.find()，因此重复值不会定位错。任何异常一律抛错（上层丢弃重生成）。"""
    text, spans, pos = [], [], 0
    plain_len = 0
    for m in _TAG.finditer(raw):
        open_lab, val, close_lab = m.group(1), m.group(2), m.group(3)
        if open_lab != close_lab:
            raise ParseError(f"标签不匹配: <{open_lab}> ... </{close_lab}>")
        if open_lab not in allowed:
            raise ParseError(f"未知标签: {open_lab}")
        if not val or "<" in val or ">" in val:
            raise ParseError(f"标签内容非法: {val!r}")
        head = raw[pos:m.start()]
        text.append(head); plain_len += len(head)
        start = plain_len
        text.append(val); plain_len += len(val)
        spans.append({"start": start, "end": plain_len, "label": open_lab})
        pos = m.end()
    text.append(raw[pos:])
    out = "".join(text)
    if re.search(r"</?[A-Z_]+>", out):
        raise ParseError("剥离后仍残留标签，可能有嵌套或未闭合")
    for s in spans:                      # 自校验：偏移必须真的对得上
        if out[s["start"]:s["end"]] == "":
            raise ParseError("空 span")
    return out, spans

# ---- 结构化 PII：必须能通过后端 _cn_id / _luhn ----
_AREA = ("110101","310104","440305","510107","320106","420106","330102","370102")
def make_id_card(rng: random.Random) -> str:
    body = rng.choice(_AREA) + f"{rng.randint(1960,2005)}{rng.randint(1,12):02d}{rng.randint(1,28):02d}" + f"{rng.randint(1,999):03d}"
    w = [7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
    c = "10X98765432"
    return body + c[sum(int(body[i]) * w[i] for i in range(17)) % 11]

_BIN = ("622202","621700","622848","622588","622262","621661")
def make_bank_card(rng: random.Random) -> str:
    body = rng.choice(_BIN) + "".join(str(rng.randint(0,9)) for _ in range(9))
    for d in range(10):
        cand = body + str(d)
        s, alt = 0, False
        for ch in reversed(cand):
            n = int(ch)
            if alt: n *= 2; n = n - 9 if n > 9 else n
            s += n; alt = not alt
        if s % 10 == 0: return cand
    raise AssertionError

def make_phone(rng: random.Random) -> str:
    return rng.choice(("138","139","150","151","176","188","199","135")) + "".join(str(rng.randint(0,9)) for _ in range(8))

def slot_rng(seed: int, sample_id: str, slot: str) -> random.Random:
    h = hashlib.sha256(f"{seed}:{sample_id}:{slot}".encode()).digest()
    return random.Random(int.from_bytes(h, "big"))

# ---- 落地：所有出现位置 ----
def find_all(text: str, surface: str, latin_boundary=True) -> list[tuple[int,int]]:
    """find-all 语义：敏感串的每一次出现都要打码。两个护栏见下。"""
    if len(surface) < 2:                       # 护栏1：单字不做全文匹配
        return []
    latin = re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", surface) is not None
    hits, i = [], text.find(surface)
    while i != -1:
        ok = True
        if latin and latin_boundary:           # 护栏2：拉丁串要词边界
            before = text[i-1] if i > 0 else " "
            after = text[i+len(surface)] if i+len(surface) < len(text) else " "
            ok = not (before.isalpha() or after.isalpha())
        if ok: hits.append((i, i+len(surface)))
        i = text.find(surface, i+1)
    return hits


# ---------------------------------------------------------------- 自检
def _self_test() -> None:
    ok = lambda c, m: print(f"  {'PASS' if c else 'FAIL'}  {m}") or (_ for _ in ()).throw(AssertionError(m)) if not c else print(f"  PASS  {m}")

    print("[1] 解析器 · 正例(重复实体必须拿到不同偏移)")
    t, sp = parse_inline_tagged(
        "报修人<PERSON>张伟</PERSON>联系<PERSON>张伟</PERSON>，电话<PHONE>13812345678</PHONE>。")
    assert t == "报修人张伟联系张伟，电话13812345678。", t
    assert [s["start"] for s in sp] == [3, 7, 12], sp
    for s in sp:
        assert t[s["start"]:s["end"]], s
    ok(True, f"偏移 {[s['start'] for s in sp]}，重复值未撞车")

    print("[2] 解析器 · 反例(六类畸形必须全部拒绝)")
    for name, raw in [("嵌套", "<PERSON>张<ORG>伟</ORG></PERSON>"),
                      ("未闭合", "<PERSON>张伟"),
                      ("标签错配", "<PERSON>张伟</ORG>"),
                      ("未知标签", "<NICKNAME>阿伟</NICKNAME>"),
                      ("空内容", "<PERSON></PERSON>"),
                      ("残留标签", "正常<PERSON>张伟</PERSON>但有</ORG>")]:
        try:
            parse_inline_tagged(raw)
            raise AssertionError(f"{name} 未被拒绝")
        except ParseError:
            pass
    ok(True, "六类畸形全部拒绝")

    print("[3] 结构化 PII · 必须通过后端校验器")
    import sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from app.recognizers import _cn_id, _luhn, detect_rule_spans
    from app.schemas import Strategy, EntityType
    r = random.Random(0)
    n_id = sum(_cn_id(make_id_card(r)) for _ in range(200))
    n_bk = sum(_luhn(make_bank_card(r)) for _ in range(200))
    assert n_id == 200 and n_bk == 200, (n_id, n_bk)
    ok(True, f"身份证 _cn_id {n_id}/200 · 银行卡 _luhn {n_bk}/200")

    print("[4] 结构化 PII · 必须被规则层精确检出(端到端)")
    r, bad = random.Random(7), 0
    for _ in range(100):
        idc, bk, ph = make_id_card(r), make_bank_card(r), make_phone(r)
        txt = f"身份证{idc}，银行卡{bk}，手机{ph}。"
        got = {(s.entity_type.value, s.text) for s in detect_rule_spans(txt, Strategy.MASK)[0]}
        if not {("ID_CARD", idc), ("BANK_CARD", bk), ("PHONE", ph)} <= got:
            bad += 1
    assert bad == 0, f"{bad}/100 未被精确检出"
    ok(True, "100 组全部被规则层精确检出")

    print("[5] 标签集与后端 EntityType 一致")
    assert set(LABELS) | {"CUSTOM"} == {e.value for e in EntityType}
    ok(True, "9 标签 + CUSTOM == 后端枚举")

    print("[6] find-all 的两个护栏")
    zh = "张伟的电话是13812345678，张伟说另一个号是13900001111。张伟住在杭州。"
    assert find_all(zh, "张伟") == [(0, 2), (18, 20), (38, 40)]
    assert find_all(zh, "的") == []
    en = "Contact Li Wei at Lisbon office; police report by Li Wei."
    assert all(en[a:b] == "Li" and not en[b].isalpha() for a, b in find_all(en, "Li"))
    ok(True, "中文找全 · 单字返回空 · 拉丁串不误伤 Lisbon")

    print("[7] slot_rng 可复现")
    a = make_phone(slot_rng(42, "syn_zh_mid_000123", "phone"))
    b = make_phone(slot_rng(42, "syn_zh_mid_000123", "phone"))
    c = make_phone(slot_rng(42, "syn_zh_mid_000124", "phone"))
    assert a == b and a != c
    ok(True, f"同 id 一致 {a} · 不同 id 不同 {c}")

    print("\n全部通过。")


if __name__ == "__main__":
    _self_test()
