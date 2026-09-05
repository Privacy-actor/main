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
def make_id_card(rng: random.Random, valid: bool = True) -> str:
    """valid=False 时校验位故意写错：正则形态仍匹配，但后端 _cn_id 会拒绝，
    规则层因此静默丢弃 —— 这正是第三层要补的一类（见规格「结构化 PII 生成」）。"""
    body = rng.choice(_AREA) + f"{rng.randint(1960,2005)}{rng.randint(1,12):02d}{rng.randint(1,28):02d}" + f"{rng.randint(1,999):03d}"
    w = [7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
    c = "10X98765432"
    right = c[sum(int(body[i]) * w[i] for i in range(17)) % 11]
    if valid:
        return body + right
    return body + rng.choice([d for d in "10X98765432" if d != right])

_ZH_BIN = ("622202","621700","622848","622588","622262","621661")
def _luhn_ok(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        n = int(ch)
        if alt:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n; alt = not alt
    return total % 10 == 0

def make_bank_card(
    rng: random.Random, valid: bool = True, locale: str = "zh"
) -> str:
    """valid=False 时 Luhn 故意不过。依据：队友前端演示样例里的
    6222021001116247 就是这一类（Luhn 余数 9），规则层完全漏掉。"""
    if locale not in {"zh", "en"}:
        raise ValueError(f"未知银行卡 locale: {locale}")
    if locale == "zh":
        body = rng.choice(_ZH_BIN) + "".join(str(rng.randint(0,9)) for _ in range(9))
    else:
        body = rng.choice("45") + "".join(str(rng.randint(0,9)) for _ in range(14))
    right = next(d for d in range(10) if _luhn_ok(body + str(d)))
    if valid:
        return body + str(right)
    return body + str(rng.choice([d for d in range(10) if d != right]))

def make_phone(rng: random.Random, locale: str = "zh") -> str:
    if locale == "zh":
        return rng.choice(("138","139","150","151","176","188","199","135")) + "".join(str(rng.randint(0,9)) for _ in range(8))
    if locale == "en":
        area = f"{rng.randint(200, 999):03d}"
        exchange = rng.choice("23456789") + f"{rng.randint(0, 99):02d}"
        return f"+1 {area}-{exchange}-{rng.randint(0, 9999):04d}"
    raise ValueError(f"未知电话号码 locale: {locale}")

_MAIL_DOMAINS = ("example.com", "example.net", "example.org", "mailbridge.cn",
                 "inbox-hub.com", "cloudpost.cn", "letterbox.net", "mail.qingyun.cn",
                 "corp-relay.com", "n7mail.cn", "postbox.example", "hs-mail.edu.cn")
_MAIL_LOCAL = ("{a}.{b}", "{a}{b}", "{i}{b}", "{a}_{b}", "{a}{n}", "{b}{n}", "{a}.{b}{n}")
_PY = ("zhang", "li", "wang", "chen", "liu", "yang", "zhao", "wu", "sun", "zhou",
       "alex", "sarah", "mark", "julia", "kevin", "nina", "peter", "emma")

def make_email(rng: random.Random) -> str:
    """域名与本地部分都做形态变化。全部为虚构域名，不保证未被注册（已知局限）。"""
    a, b = rng.sample(_PY, 2)
    local = rng.choice(_MAIL_LOCAL).format(a=a, b=b, i=a[0], n=rng.randint(1, 9999))
    return f"{local}@{rng.choice(_MAIL_DOMAINS)}"

def make_passport(rng: random.Random, locale: str = "zh") -> str:
    r"""后端 PASSPORT 正则: [EGDSP]\d{8} | [A-Z]{1,2}\d{6,9}"""
    if locale == "zh":
        return rng.choice("EGDSP") + "".join(str(rng.randint(0, 9)) for _ in range(8))
    letters = "".join(rng.choice("ABCDEFGHJKLMNPRSTVWXYZ") for _ in range(rng.choice((1, 2))))
    return letters + "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(6, 9)))

def slot_rng(seed: int, sample_id: str, slot: str) -> random.Random:
    h = hashlib.sha256(f"{seed}:{sample_id}:{slot}".encode()).digest()
    return random.Random(int.from_bytes(h, "big"))

# ---- 姓名与机构种子：由程序给模型指定，压低模型先验导致的重复 ----
CN_SURNAMES = tuple("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢滑裴陆荣翁荀羊甄曲封芮羿储靳汲邴糜隗侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白蒲邰鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公")
_CN_GIVEN_A = tuple("书立雅思雨泽宇嘉明若安子云清昕景亦知承以浩宁语文远俊欣")
_CN_GIVEN_B = tuple("瑶恒琴文琳涵辰轩然怡彤哲宁悦航川清博嘉妍琪睿晨峰")
CN_GIVEN_NAMES = tuple(a + b for a in _CN_GIVEN_A for b in _CN_GIVEN_B)
EN_FIRST_NAMES = tuple("Aiden Amelia Andrew Audrey Benjamin Brooke Caleb Cameron Charlotte Chloe Claire Daniel David Dylan Eleanor Elijah Ella Emily Emma Ethan Evelyn Fiona Gabriel Grace Hannah Harper Hazel Henry Isaac Isabella Jack Jackson Jacob James Jasmine Jordan Joseph Julia Julian Katherine Leah Leo Liam Lily Lucas Lucy Luke Madison Maya Mia Nathan Natalie Noah Nora Oliver Olivia Owen Penelope Rachel Ryan Samuel Sarah Scarlett Sebastian Sophia Stella Theodore Thomas Victoria Violet William Wyatt Zoe Adrian Alice Arthur Bella Colin Diana Eric Felicity George Helen Ian Jennifer Kevin Laura Marcus Nina Oscar Peter Quinn Rebecca Steven Teresa Ursula Vincent Wendy Xavier Yvonne Zachary".split())
EN_LAST_NAMES = tuple("Adams Allen Anderson Baker Bell Bennett Brooks Brown Campbell Carter Clark Collins Cook Cooper Cox Davis Edwards Evans Fisher Flores Foster Garcia Gray Green Hall Harris Hayes Henderson Hill Howard Hughes Jackson James Jenkins Johnson Kelly King Lee Lewis Long Martin Martinez Miller Mitchell Moore Morgan Morris Murphy Nelson Parker Perry Peterson Phillips Powell Price Reed Richardson Rivera Roberts Robinson Rogers Ross Russell Sanchez Scott Smith Stewart Taylor Thomas Thompson Turner Walker Ward Watson White Williams Wilson Wood Wright Young Bailey Barnes Butler Coleman Diaz Griffin Hamilton Kennedy Marshall Mason Murray Ortiz Palmer Patterson Porter Ramirez Reynolds Sanders Simmons Stone Sullivan Wallace Warren Webb Wells Wheeler".split())
CN_ORG_REGIONS = tuple("北京 上海 天津 重庆 广州 深圳 杭州 南京 苏州 成都 武汉 西安 长沙 郑州 青岛 厦门 宁波 无锡 合肥 福州 济南 大连 昆明 南宁 贵阳 海口 石家庄 太原 沈阳 长春 哈尔滨 兰州 西宁 银川 乌鲁木齐 珠海 佛山 东莞 泉州 温州 嘉兴 绍兴 南通 常州 洛阳 桂林 黔南".split())
CN_ORG_INDUSTRIES = tuple("云影 星桥 远航 明川 清源 智联 数科 信息 数据 网络 软件 能源 生物 医药 健康 医疗 教育 文化 传媒 物流 交通 建筑 设计 材料 环保 农业 食品 商贸 金融 咨询 检测 仪器 光电 通信 智能 机器人 航空 海洋 安全".split())
CN_ORG_SUFFIXES = ("科技有限公司", "信息有限公司", "研究院", "医院", "大学", "集团", "实验室", "基金会", "协会", "中心", "设计院", "合作社")
EN_ORG_REGIONS = tuple("Aurora Beacon Cedar Delta Ember Falcon Granite Harbor Indigo Juniper Keystone Linden Meridian Northstar Oak Pacific Quartz River Summit Timber Union Valley Willow Zenith Alpine Brighton Cascade Dover Franklin Hudson Irvine Kingston Liberty Madison Newport Oxford Portland Quincy Richmond Salem Trenton Urbana Ventura Weston Yorktown Ashland Bristol Clayton Dayton Edison Fairfax Georgetown Hamilton".split())
EN_ORG_INDUSTRIES = tuple("Analytics BioHealth Cloud Data Digital Energy Engineering Finance Foods Health Learning Logistics Materials Media Medical Networks Robotics Security Software Systems Telecom Transport Ventures Aerospace Agriculture Architecture Automation Consulting Design Diagnostics Education Environmental Imaging Infrastructure Insurance Research".split())
EN_ORG_SUFFIXES = ("Corporation", "Group", "Institute", "University", "Hospital", "Laboratory", "Foundation", "Association", "Center", "Partners", "Holdings", "Limited")

def make_person_name(rng: random.Random, locale: str = "zh") -> str:
    if locale == "zh":
        return rng.choice(CN_SURNAMES) + rng.choice(CN_GIVEN_NAMES)
    if locale == "en":
        return f"{rng.choice(EN_FIRST_NAMES)} {rng.choice(EN_LAST_NAMES)}"
    raise ValueError(f"未知姓名 locale: {locale}")

def make_org_name(rng: random.Random, locale: str = "zh") -> str:
    if locale == "zh":
        return rng.choice(CN_ORG_REGIONS) + rng.choice(CN_ORG_INDUSTRIES) + rng.choice(CN_ORG_SUFFIXES)
    if locale == "en":
        return f"{rng.choice(EN_ORG_REGIONS)} {rng.choice(EN_ORG_INDUSTRIES)} {rng.choice(EN_ORG_SUFFIXES)}"
    raise ValueError(f"未知机构 locale: {locale}")

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

def expand_spans(text: str, tagged: list[dict]) -> list[dict]:
    """把每个已标 surface 扩展到全部出现位置，并丢弃被更长 span 包含的子串。"""
    candidates: dict[tuple[int, int, str], dict] = {}
    surfaces: set[tuple[str, str]] = set()
    for span in tagged:
        start, end, label = span["start"], span["end"], span["label"]
        if not (0 <= start < end <= len(text)) or text[start:end] == "":
            raise ParseError(f"非法 tagged span: {span}")
        surface = text[start:end]
        surfaces.add((surface, label))
        candidates[(start, end, label)] = {"start": start, "end": end, "label": label}

    for surface, label in surfaces:
        for start, end in find_all(text, surface):
            candidates[(start, end, label)] = {"start": start, "end": end, "label": label}

    all_candidates = list(candidates.values())
    kept = []
    for candidate in all_candidates:
        length = candidate["end"] - candidate["start"]
        contained = any(
            (other["end"] - other["start"] > length)
            and other["start"] <= candidate["start"]
            and candidate["end"] <= other["end"]
            for other in all_candidates
        )
        if not contained:
            kept.append(candidate)

    kept.sort(key=lambda span: (span["start"], span["end"], span["label"]))
    for index, span in enumerate(kept):
        if text[span["start"]:span["end"]] == "":
            raise ParseError(f"扩展后空 span: {span}")
        if index and span["start"] < kept[index - 1]["end"]:
            raise ParseError(f"扩展后 span 重叠: {kept[index - 1]} / {span}")
    return kept


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

    print("[7] 校验位故意错误 · 必须被校验器拒绝")
    r = random.Random(11)
    for _ in range(150):
        assert not _cn_id(make_id_card(r, False))
        assert not _luhn(make_bank_card(r, False))
    ok(True, "150 组非法身份证/银行卡全部被拒")

    print("[8] 校验位错误 → 规则层静默丢弃(第三层要补的一类)")
    r, silent, mistyped = random.Random(11), 0, 0
    for _ in range(200):
        num = make_id_card(r, False)
        got = {s.entity_type.value for s in detect_rule_spans(f"身份证{num}。", Strategy.MASK)[0]}
        if not got:
            silent += 1
        elif "BANK_CARD" in got:
            mistyped += 1          # 18 位数字碰巧 Luhn 通过 → 撞上 BANK_CARD 正则
    assert silent + mistyped == 200, (silent, mistyped)
    ok(True, f"静默丢弃 {silent}/200 · 被误判成 BANK_CARD {mistyped}/200(后端已知跨类型混淆)")

    print("[9] EMAIL / PASSPORT · 形态多样且被规则层检出")
    from app.recognizers import PATTERNS
    r = random.Random(21)
    mails = [make_email(r) for _ in range(300)]
    doms = {m.split("@")[1] for m in mails}
    locals_ = {m.split("@")[0] for m in mails}
    assert len(doms) >= 10 and len(locals_) >= 250, (len(doms), len(locals_))
    miss = 0
    for m in mails[:60]:
        got = {s.entity_type.value for s in detect_rule_spans(f"邮箱{m}。", Strategy.MASK)[0]}
        if "EMAIL" not in got:
            miss += 1
    assert miss == 0, miss
    ps = [make_passport(r, "zh") for _ in range(60)] + [make_passport(r, "en") for _ in range(60)]
    pmiss = sum(1 for x in ps
                if "PASSPORT" not in {s.entity_type.value
                                      for s in detect_rule_spans(f"护照号{x}。", Strategy.MASK)[0]})
    assert pmiss == 0, pmiss
    ok(True, f"EMAIL 域名 {len(doms)} 种/本地部分 {len(locals_)} 种，检出 60/60 · PASSPORT 检出 120/120")

    print("[10] slot_rng 可复现")
    a = make_phone(slot_rng(42, "syn_zh_mid_000123", "phone"))
    b = make_phone(slot_rng(42, "syn_zh_mid_000123", "phone"))
    c = make_phone(slot_rng(42, "syn_zh_mid_000124", "phone"))
    assert a == b and a != c
    ok(True, f"同 id 一致 {a} · 不同 id 不同 {c}")

    print("[11] 标签扩展 · 重复 surface 找全且包含守卫生效")
    text = "张伟联系张伟，电话13800138000；地址上海市浦东新区世纪大道88号，张伟再拨13800138000后到上海。"
    tagged = []
    for surface, label, start in [
        ("张伟", "PERSON", text.index("张伟")),
        ("13800138000", "PHONE", text.index("13800138000")),
        ("上海市浦东新区世纪大道88号", "ADDRESS", text.index("上海市浦东新区世纪大道88号")),
        ("上海", "LOCATION", text.rindex("上海")),
    ]:
        tagged.append({"start": start, "end": start + len(surface), "label": label})
    expanded = expand_spans(text, tagged)
    assert len(expanded) == 7, expanded
    assert all(text[s["start"]:s["end"]] for s in expanded)
    assert all(expanded[i]["end"] <= expanded[i + 1]["start"] for i in range(len(expanded) - 1))
    address_start = text.index("上海市浦东新区世纪大道88号")
    assert not any(s["label"] == "LOCATION" and s["start"] == address_start for s in expanded)
    ok(True, "扩展 7 个 span · 原文一致 · 无重叠 · ADDRESS 内 LOCATION 已丢弃")

    print("[12] 姓名与机构种子 · 可复现且 500 条重复率低于 5%")
    pools = {
        "中文人名": [make_person_name(slot_rng(i, f"seed-{i}", "person"), "zh") for i in range(500)],
        "英文人名": [make_person_name(slot_rng(i, f"seed-{i}", "person"), "en") for i in range(500)],
        "中文机构": [make_org_name(slot_rng(i, f"seed-{i}", "org"), "zh") for i in range(500)],
        "英文机构": [make_org_name(slot_rng(i, f"seed-{i}", "org"), "en") for i in range(500)],
    }
    rates = {name: 1 - len(set(values)) / 500 for name, values in pools.items()}
    assert all(rate < 0.05 for rate in rates.values()), rates
    assert make_person_name(slot_rng(7, "same", "person"), "zh") == make_person_name(slot_rng(7, "same", "person"), "zh")
    ok(True, " · ".join(f"{name}重复率 {rate:.1%}" for name, rate in rates.items()))

    print("[13] 英文 PHONE / BANK_CARD · 格式正确且被规则层检出")
    r, misses = random.Random(37), 0
    for _ in range(100):
        phone = make_phone(r, "en")
        card = make_bank_card(r, locale="en")
        assert re.fullmatch(r"\+1 \d{3}-[2-9]\d{2}-\d{4}", phone), phone
        assert card[0] in "45" and len(card) == 16 and _luhn(card), card
        got = {(s.entity_type.value, s.text) for s in detect_rule_spans(
            f"Phone {phone}; card {card}.", Strategy.MASK
        )[0]}
        if not {("PHONE", phone), ("BANK_CARD", card)} <= got:
            misses += 1
    assert misses == 0, f"{misses}/100 未被规则层精确检出"
    invalid = [make_bank_card(r, False, "en") for _ in range(100)]
    assert all(not _luhn(card) for card in invalid)
    ok(True, "100 组北美电话与 Visa/Mastercard 全部被规则层检出，100 张故意错误卡均未过 Luhn")

    print("\n全部通过。")


if __name__ == "__main__":
    _self_test()
