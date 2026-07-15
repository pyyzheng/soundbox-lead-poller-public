#!/usr/bin/env python3
"""
lead_grader.py — Google 渠道线索分级模块（V4.3.1 模块化版）

设计原则：
  - 纯代码规则，无 LLM 调用
  - LLM 语义槽位提取由 slot_extractor.py 负责
  - 本模块负责：数量/容量/身份/信号/Level 判级/格式化

依赖：
  - slot_extractor.py（同目录，LLM 语义槽位提取）
  - 若 semantic_slots 预先提供，则无需 ZHIPU_API_KEY（支持纯代码测试）
"""

import re
import json
import logging
from typing import Optional

log = logging.getLogger("lead-grader")


# ═══════════════════════════════════════════════════════════════════════════════
# 常量 & 正则模式
# ═══════════════════════════════════════════════════════════════════════════════

# ── A. 个人邮箱域名白名单 ──────────────────────────────────────────────────────
PERSONAL_DOMAINS = frozenset({
    "gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "aol.com",
    "icloud.com", "protonmail.com", "mail.com", "gmx.com", "yandex.com",
    "qq.com", "163.com", "126.com", "sina.com", "sohu.com", "yeah.net",
    "hotmail.co.uk", "live.com", "zoho.com", "21cn.com", "foxmail.com",
    "tom.com", "189.cn", "139.com", "me.com", "msn.com",
})

# ── B. 数量单位白名单（Evidence Gate）─────────────────────────────────────────
# 数字 + 这些单位 → 数量证据
QUANTITY_UNITS = (
    r"pcs|units|sets|nos|booths?|pods?|cabins?|cabines?|cabinas?"
    r"|台|套|个"
)
QUANTITY_UNIT_RE = re.compile(
    rf"\b({QUANTITY_UNITS})\b", re.IGNORECASE
)

# "N of them/these/those"
OF_THEM_RE = re.compile(
    r"\d+\s+of\s+(?:them|these|those)\b", re.IGNORECASE
)

# ── C. 规格排除模式（Spec Exclusion）───────────────────────────────────────────
# 数字附近出现这些 → 该数字不是数量
_SPEC_UNITS_WORD = (
    r"mm|cm|meter|metre|inch|ft|dB|Rw|Hz|W|V"
    r"|sqm|m2|㎡|平方"
)
SPEC_UNIT_RE = re.compile(rf"\b({_SPEC_UNITS_WORD})\b", re.IGNORECASE)

SPEC_CONTEXT_WORDS = (
    r"width|depth|height|dimension"
    r"|external\s+dimensions|internal\s+dimensions"
    r"|NAICS|HS\s+code|SKU|Item\s+No|Item\s+number|Item\s*#|Model\s+No"
)
SPEC_CONTEXT_RE = re.compile(rf"\b({SPEC_CONTEXT_WORDS})", re.IGNORECASE)

# ── D. 舱体单位片段（用于分项求和 Line-item Sum）───────────────────────────────
_BOOTH_UNITS = (
    r"booths?|pods?|cabins?|cabines?|cabinas?"
    r"|sound\s*box(?:es)?|silence\s*box(?:es)?"
    r"|phone\s*booths?"
)
# 数字(数字或英文词) + 0-5 个形容词/修饰词 + 舱体单位
LINE_ITEM_RE = re.compile(
    rf"\b(\d{{1,5}}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|fifty|couple)"
    rf"\s+(?:of\s+)?((?:[\w-]+\s+){{0,5}})({_BOOTH_UNITS})\b",
    re.IGNORECASE,
)

# ── D2. 产品型号（用于 "two SRP-M + two SRP-L" 这类数量表达）────────────────────
_PRODUCT_MODEL = (
    r"S(?:RP?)?[-\s]?(?:S|M|L|XL|1|2|SM|ML)?"
    r"|VR[-\s]?(?:S|M|L|XL|SM|ML)?"
    r"|VRT?[-\s]?(?:S|M|L|XL|SM|ML)?"
    r"|ART[-\s]?(?:S|M|L|XL|SM|ML)?"
    r"|EQ|DQ|AQ"
)
_PRODUCT_MODEL_RE = re.compile(rf"\b({_PRODUCT_MODEL})\b", re.IGNORECASE)

# 英文数字词 → 数字映射
_WORD_NUM_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "fifty": 50, "hundred": 100,
    "couple": 2,
}

# 数字(数字或英文词) + 产品型号: "two SRP-M", "2 VR-S", "three ART pods"
# 注意: 型号后必须 \b，防止 "2 side" 中 "s" 被误匹配为 bare "S"
LINE_ITEM_MODEL_RE = re.compile(
    rf"\b(\d{{1,5}}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    rf"\s+({_PRODUCT_MODEL})\b"
    rf"(?:\s*\([^)]*\))?"  # 可选括号说明，如 (F4)
    rf"(?:\s+(?:and|&|,)\s+)?"  # 可选 and/,
    , re.IGNORECASE,
)

# "2x pods" / "2x Model S booths" 格式（Nx 乘数表达）
LINE_ITEM_NX_RE = re.compile(
    rf"\b(\d{{1,5}})x\s+((?:[\w-]+\s+){{0,5}})({_BOOTH_UNITS})\b",
    re.IGNORECASE,
)

# ── E. 总量声明模式（Total Declaration）─────────────────────────────────────────
_TOTAL_DECL_RE = re.compile(
    r"(?:looking\s+for|need|needs|require|requires|order|purchase|get|want"
    r"|interested\s+in|would\s+like|'d\s+like|like\s+to\s+(?:have|get)|looking\s+to"
    r"|after|requesting|inquiring)"
    r"[^.]{0,40}?"
    r"(\d{1,5})\s+"
    rf"(?:[\w-]+\s*){{0,3}}({_BOOTH_UNITS})",
    re.IGNORECASE,
)

# "interested in two SRP-M" 这类"动词 + 数字 + 型号"的总量声明
_TOTAL_DECL_MODEL_RE = re.compile(
    r"(?:looking\s+for|need|needs|require|requires|order|purchase|get|want|interested\s+in)"
    r"[^.]{0,30}?"
    rf"(?:\d{{1,5}}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    rf"\s+({_PRODUCT_MODEL})\b",
    re.IGNORECASE,
)

# ── F. 容量模式（Capacity — 几人舱）────────────────────────────────────────────
# 数字 + person/people/persons/personas/personnes/personen/pax/pessoa/pessoas
# 支持: "4 person", "4-person", "4person" (连字符或空格)
_CAPACITY_NUM_RE = re.compile(
    r"\b(\d)\s*[-]?\s*(?:person|people|persons|personas|personnes|personen|pax|pessoa|pessoas)\b",
    re.IGNORECASE,
)
# 文字表达
_CAPACITY_TEXT_MAP = {
    "single": "1", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8",
}
_CAPACITY_TEXT_RE = re.compile(
    r"\b(single|one|two|three|four|five|six|seven|eight)"
    r"[\s-]*(?:person|people|persons)\b",
    re.IGNORECASE,
)
# 范围表达: 2-4 persons
_CAPACITY_RANGE_RE = re.compile(
    r"\b(\d)\s*[-~–]\s*(\d)\s*"
    r"(?:person|people|persons|personas|personnes|personen|pax|pessoa|pessoas)\b",
    re.IGNORECASE,
)

# ── H. 场景代码兜底（Scenario Code Fallback）────────────────────────────────────
# LLM 返回 Unknown 时，用关键词规则兜底提取 scenario
# 规则来源: 41 条历史数据分析，覆盖 9/13 条 LLM 场景漏提
_SCENARIO_PATTERNS = {
    "Office": [
        # "for our/the/my/a/an [地址] office" — 允许中间隔词（含逗号地名）
        # 排除产品名: "office pod/booth/cabin/cabine" 后面不跟产品单位
        r"\bfor\s+(?:our|the|my|a|an)\b.{0,40}?\boffice\b(?![-\s]*(?:pod|booth|cabin|cabine|phone))\b",
        r"\bin\s+(?:our|the)\b.{0,40}?\boffice\b(?![-\s]*(?:pod|booth|cabin|cabine|phone))\b",
        r"\bfor\s+office\b(?![-\s]*(?:pod|booth|cabin|cabine|phone))\b",
        r"\boffice\s+use\b",
        r"\bworkplace\b",
        r"\bcoworking\b",
        r"\bcorporate\b",
        r"\bopen\s+plan\b",
        r"\bmeeting\s+room\b",
        r"\bbreakout\s+area\b",
        r"\breception\s+area\b",
        r"\bcommunal\s+(?:area|space|workspace)\b",
        r"\bstaff\s+room\b",
        r"\bshared\s+(?:office|workspace|space)\b",
        r"\bfloor\s+plan\b",
        r"\bhot\s*desking\b",
        r"\b(?:adding|add)\s+to\b.{0,20}?\boffice\b(?![-\s]*(?:pod|booth|cabin|cabine|phone))",
        r"\b(?:our|the|my)\s+\w+\s+office\b(?![-\s]*(?:pod|booth|cabin|cabine|phone))",
    ],
    "Studio": [
        r"\brecording\s+(?:booth|studio|room|pod|space)\b",
        r"\brecording\s+studio\b",
        r"\bstudio\b",
        r"\bdubbing\b",
        r"\bvoice\s*over\b",
        r"\bpodcast\b",
        r"\bmusic\s+(?:booth|studio|room|pod)\b",
        r"\brecord\s+(?:audio|sound|music|voice)\b",
    ],
    "Home": [
        r"\bfor\s+home\b",
        r"\bat\s+home\b",
        r"\bhome\s+use\b",
        r"\bhome\s+pod\b",
        r"\bresidential\b",
        r"\bfor\s+(?:my|our|the)\s+(?:home|house|apartment|flat)\b",
        r"\bliving\s+room\b",
        r"\bbedroom\b",
    ],
    "Project": [
        r"\bnew\s+(?:building|office|headquarters|campus|location|site)\b",
        r"\brenovation\b",
        r"\bfit-?out\b",
        r"\b(?:upcoming|new)\s+project\b",
        r"\bexpansion\b",
        r"\bheadquarters\b",
        r"\btender\b",
        r"\bRF[QI]\b",
        r"\bdoubling\b",
        r"\brefurbish(?:ment)?\b",
        r"\brelocat(?:e|ion|ing)\b",
    ],
}
_SCENARIO_COMPILED = {
    name: [re.compile(p, re.IGNORECASE) for p in pats]
    for name, pats in _SCENARIO_PATTERNS.items()
}

# ── G. 交付地点模式（Need_Location 判定）────────────────────────────────────────
# 两步匹配：先匹配 delivery/ship/send 关键词（不区分大小写），
# 再检查后面是否跟首字母大写的城市/国家名
_DELIVERY_KEYWORD_RE = re.compile(
    r"\b(?:deliver(?:y|ing)?|ship(?:ping|ped|s|to)?|send(?:ing|s|to)?|transport|forwarding"
    r"|enviar|enviam|entreg[ae])\b",
    re.IGNORECASE,
)
_PROPER_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{1,})?)\b")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. extract_quantity — 购买数量提取
# ═══════════════════════════════════════════════════════════════════════════════

def _near_spec(text: str, pos: int, number_str: str, window: int = 28) -> bool:
    """检查数字附近 ±window 字符是否有规格/编码排除词。"""
    num_start = pos
    num_end = pos + len(number_str)
    left = text[max(0, num_start - window): num_start]
    right = text[num_end: min(len(text), num_end + window)]
    context = f"{left} {right}"
    if SPEC_UNIT_RE.search(context) or SPEC_CONTEXT_RE.search(context):
        return True
    return False


def _is_large_code_number(text: str, pos: int, number_str: str) -> bool:
    """数字 >= 10000 且前面 25 字符内没有 qty/quantity → 视为编码忽略。"""
    try:
        num = int(number_str)
    except ValueError:
        return False
    if num < 10000:
        return False
    prefix = text[max(0, pos - 25): pos]
    if re.search(r"\b(?:qty|quantity)\b", prefix, re.IGNORECASE):
        return False  # 有 qty/quantity 前缀 → 不是编码
    return True  # 大数字无 qty 前缀 → 视为编码


def extract_quantity(text: str) -> tuple[str, str, bool]:
    """提取购买数量（quantity_min, quantity_max, evidence_flag）。

    算法：
    1. 扫描所有 line-item 模式（数字 + 舱体单位）
    2. 扫描总量声明（need X booths）
    3. 应用证据门槛、规格排除、编码排除
    4. 总量+分项去重

    Returns:
        (quantity_min, quantity_max, evidence_flag)
        quantity 为字符串（"N/A" 或 数字字符串）
    """
    if not text:
        return ("N/A", "N/A", False)

    # ── Step 2: 总量声明（Total Declaration）— 先检测，以便排除重叠 ────────
    total_decl = None
    total_decl_spans = []  # (start, end) of total declaration matches
    for m in _TOTAL_DECL_RE.finditer(text):
        num_str = m.group(1)
        pos = m.start()
        if _near_spec(text, pos, num_str):
            continue
        if _is_large_code_number(text, pos, num_str):
            continue
        try:
            num = int(num_str)
        except ValueError:
            continue
        if num > 0:
            total_decl = num
            total_decl_spans.append((m.start(), m.end()))
            break  # 取第一个有效总量声明

    # ── Step 1: 分项求和（Line-item Sum）───────────────────────────────────
    # 排除与总量声明重叠的匹配
    def _overlaps_total(start: int, end: int) -> bool:
        for ts, te in total_decl_spans:
            if start < te and end > ts:
                return True
        return False

    line_items = []
    line_item_spans = []  # (start, end) 用于与 model items 去重
    for m in LINE_ITEM_RE.finditer(text):
        num_str = m.group(1)
        pos = m.start()
        # 排除与总量声明重叠的匹配
        if _overlaps_total(m.start(), m.end()):
            continue
        if _near_spec(text, pos, num_str):
            continue
        if _is_large_code_number(text, pos, num_str):
            continue
        # 支持英文数字词（two pods）和纯数字（2 pods）
        num = _WORD_NUM_MAP.get(num_str.lower())
        if num is None:
            try:
                num = int(num_str)
            except ValueError:
                continue
        if num > 0:
            line_items.append(num)
            line_item_spans.append((m.start(), m.end()))

    # ── Step 1b: 产品型号分项求和（"two SRP-M and two SRP-L"）────────────
    model_items = []
    for m in LINE_ITEM_MODEL_RE.finditer(text):
        # 跳过与 booth-based line item 重叠的匹配（避免 "2 SR-S booths" 被双算）
        if any(m.start() < te and m.end() > ts for ts, te in line_item_spans):
            continue
        num_str = m.group(1)
        pos = m.start()
        if _overlaps_total(m.start(), m.end()):
            continue
        # 解析数字（可能是英文词）
        num = _WORD_NUM_MAP.get(num_str.lower())
        if num is None:
            try:
                num = int(num_str)
            except ValueError:
                continue
        if num > 0 and num < 1000:  # 合理范围
            model_items.append(num)

    # 合并 booth-based 和 model-based line items
    all_line_items = line_items + model_items

    # ── Step 1c: Nx 格式分项（"2x booths", "2x Model S pods"）─────────────
    for m in LINE_ITEM_NX_RE.finditer(text):
        num_str = m.group(1)
        try:
            num = int(num_str)
        except ValueError:
            continue
        if num > 0:
            all_line_items.append(num)

    # 检查是否存在列表/bullet 结构（≥2 个分项项才可能求和）
    has_list_structure = (
        bool(re.search(r"(?:^|\n)\s*[•\-\*]\s", text))  # bullet: • - *
        or bool(re.search(r"(?:^|\n)\s*\d+[.)]\s", text))  # numbered: 1) 2.
        or len(all_line_items) >= 3
        or (len(model_items) >= 2 and bool(re.search(r"\band\b", text, re.IGNORECASE)))  # "two X and two Y"
    )

    breakdown_sum = None
    if len(all_line_items) >= 2 and has_list_structure:
        breakdown_sum = sum(all_line_items)
    elif len(all_line_items) >= 3:
        # 即使没有明显 bullet，≥3 个分项也求和
        breakdown_sum = sum(all_line_items)

    # ── Step 3: 通用数量证据扫描（单个数字 + 单位 / "N of them"）──────────
    general_numbers = []

    # 数字 + 数量单位
    _NUM_UNIT_RE = re.compile(
        rf"\b(\d{{1,5}})\s+({QUANTITY_UNITS})\b", re.IGNORECASE
    )
    for m in _NUM_UNIT_RE.finditer(text):
        num_str = m.group(1)
        pos = m.start()
        if _near_spec(text, pos, num_str):
            continue
        if _is_large_code_number(text, pos, num_str):
            continue
        try:
            num = int(num_str)
        except ValueError:
            continue
        if num > 0:
            general_numbers.append(num)

    # "N of them/these/those"
    for m in OF_THEM_RE.finditer(text):
        num_str = m.group().split()[0]
        try:
            num = int(num_str)
        except ValueError:
            continue
        if num > 0:
            general_numbers.append(num)

    # ── Step 4: 汇总去重 ───────────────────────────────────────────────────
    evidence_flag = False
    quantity = None

    # 优先级 1: 总量 + 分项去重
    if total_decl is not None and breakdown_sum is not None:
        if abs(breakdown_sum - total_decl) <= 1:
            quantity = total_decl
        else:
            quantity = max(total_decl, breakdown_sum)
        evidence_flag = True
    elif total_decl is not None:
        quantity = total_decl
        evidence_flag = True
    elif breakdown_sum is not None:
        quantity = breakdown_sum
        evidence_flag = True

    # 优先级 2: 通用数量证据
    if quantity is None and general_numbers:
        quantity = max(general_numbers)
        evidence_flag = True

    # 优先级 3: 单个 line-item（仅 1 个匹配，booth 或 model）
    if quantity is None and len(all_line_items) == 1:
        quantity = all_line_items[0]
        evidence_flag = True

    # 前面 3 个优先级都要求"数字+单位/型号"，无法匹配裸型号列举如 "ART+ Medium AND ART+ ML"
    # 此规则在同一句子内检测无数字前缀的多型号提及，首尾型号间距 ≤40 字符才推断
    if quantity is None:
        for segment in re.split(r'[.!?\n]', text):
            seg_matches = list(_PRODUCT_MODEL_RE.finditer(segment))
            if len(seg_matches) >= 2:
                span = seg_matches[-1].end() - seg_matches[0].start()
                if span <= 30:
                    between = segment[seg_matches[0].start():seg_matches[-1].end()]
                    has_conn = bool(re.search(r"\band\b|&", between, re.IGNORECASE))
                    if (len(seg_matches) >= 2 and has_conn) or len(seg_matches) >= 3:
                        quantity = len(seg_matches)
                        evidence_flag = True
                        break

    if quantity is None:
        return ("N/A", "N/A", False)

    return (str(quantity), str(quantity), True)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. extract_capacity — 容量规格提取（几人舱）
# ═══════════════════════════════════════════════════════════════════════════════

def extract_capacity(text: str) -> str:
    """提取容量规格（几人舱），返回如 "1-person", "2-person, 4-person" 或 "N/A"。

    容量只进 capacity_options，不进 quantity。
    """
    if not text:
        return "N/A"

    capacities = set()

    # ── 范围表达（优先匹配）─────────────────────────────────────────────
    for m in _CAPACITY_RANGE_RE.finditer(text):
        low, high = m.group(1), m.group(2)
        capacities.add(f"{low}-person")
        capacities.add(f"{high}-person")

    # ── 数字表达 ────────────────────────────────────────────────────────
    for m in _CAPACITY_NUM_RE.finditer(text):
        n = m.group(1)
        capacities.add(f"{n}-person")

    # ── 文字表达 ────────────────────────────────────────────────────────
    for m in _CAPACITY_TEXT_RE.finditer(text):
        word = m.group(1).lower()
        n = _CAPACITY_TEXT_MAP.get(word)
        if n:
            capacities.add(f"{n}-person")

    if not capacities:
        return "N/A"

    # 排序输出
    return ", ".join(sorted(capacities, key=lambda x: int(x.split("-")[0])))


# ═══════════════════════════════════════════════════════════════════════════════
# 2b. extract_scenario_fallback — 场景代码兜底
# ═══════════════════════════════════════════════════════════════════════════════

def extract_scenario_fallback(text: str) -> str:
    """LLM 返回 Unknown 时，用关键词规则兜底提取 scenario。

    优先级: Office > Studio > Home > Project > Unknown
    （Office 最常见且误判成本最低）

    Returns:
        "Office" / "Home" / "Studio" / "Project" / "Unknown"
    """
    if not text or not text.strip():
        return "Unknown"

    # 按优先级检查（Office > Studio > Project > Home）
    for scenario in ("Office", "Studio", "Project", "Home"):
        for pat in _SCENARIO_COMPILED[scenario]:
            if pat.search(text):
                return scenario

    return "Unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. check_identity — 邮箱身份判定
# ═══════════════════════════════════════════════════════════════════════════════

def check_identity(email: str) -> str:
    """判定邮箱身份强度。

    Returns:
        "Company domain email" / "Personal email only" / "Unknown"
    """
    if not email or not email.strip():
        return "Unknown"

    email = email.strip().lower()
    if "@" not in email:
        return "Unknown"

    domain = email.rsplit("@", 1)[-1]

    if domain in PERSONAL_DOMAINS:
        return "Personal email only"

    return "Company domain email"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. map_scale_tier — 数量映射规模档位
# ═══════════════════════════════════════════════════════════════════════════════

def map_scale_tier(quantity_max: str, evidence_flag: bool) -> str:
    """将 quantity_max 映射为规模档位。

    Returns:
        "1" / "2-3" / "4-9" / "10+" / "Unknown"
    """
    if not evidence_flag:
        return "Unknown"

    try:
        n = int(quantity_max)
    except (ValueError, TypeError):
        return "Unknown"

    if n == 1:
        return "1"
    elif 2 <= n <= 3:
        return "2-3"
    elif 4 <= n <= 9:
        return "4-9"
    else:
        return "10+"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. calc_signals — 信号计算
# ═══════════════════════════════════════════════════════════════════════════════

def calc_signals(slots: dict) -> dict:
    """基于槽位计算 6 个信号。

    Args:
        slots: 包含所有槽位的 dict，必须包含:
            identity_strength, timeline_slot, quantity_max,
            quantity_evidence_flag, scenario_slot, capacity_options, config_slot

    Returns:
        {"S1": bool, "S2": bool, "S3a": bool, "S3b": bool, "S4": bool, "S5": bool, "S6": bool}
    """
    identity = slots.get("identity_strength", "Unknown")
    timeline = slots.get("timeline_slot", "NotMentioned")
    qmax_str = slots.get("quantity_max", "N/A")
    evidence = slots.get("quantity_evidence_flag", False)
    scenario = slots.get("scenario_slot", "Unknown")
    capacity = slots.get("capacity_options", "N/A")
    config = slots.get("config_slot", "NotMentioned")

    # 解析 quantity_max 为 int
    try:
        qmax = int(qmax_str)
    except (ValueError, TypeError):
        qmax = 0

    signals = {
        "S1": identity == "Company domain email",
        "S2": timeline == "TimeSensitive",
        "S3a": evidence and qmax >= 2,
        "S3b": evidence and qmax >= 10,
        "S4": scenario != "Unknown",
        "S5": capacity != "N/A",
        "S6": config == "ConfigMentioned",
    }
    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 6. determine_level — Level 判定
# ═══════════════════════════════════════════════════════════════════════════════

def determine_level(signals: dict, slots: dict) -> str:
    """基于信号和槽位判定 Level 1-4（首匹配优先）。

    Returns:
        "Level 1" / "Level 2" / "Level 3" / "Level 4"
    """
    S1 = signals.get("S1", False)
    S2 = signals.get("S2", False)
    S3a = signals.get("S3a", False)
    S3b = signals.get("S3b", False)
    S4 = signals.get("S4", False)
    S5 = signals.get("S5", False)
    S6 = signals.get("S6", False)

    product = slots.get("product_slot", "Unclear")
    intent = slots.get("intent_slot", "Unclear")
    identity = slots.get("identity_strength", "Unknown")
    evidence = slots.get("quantity_evidence_flag", False)
    qmax_str = slots.get("quantity_max", "N/A")
    try:
        qmax = int(qmax_str)
    except (ValueError, TypeError):
        qmax = 0

    # ── Level 4 ───────────────────────────────────────────────────────────
    if product == "NoMatch" and intent != "Partnership" and identity != "Company domain email":
        return "Level 4"

    # ── Level 1 ───────────────────────────────────────────────────────────
    if S3b:
        return "Level 1"
    if S1 and S2:
        return "Level 1"
    if intent == "Partnership" and (S1 or S4):
        return "Level 1"

    # ── Level 2 ───────────────────────────────────────────────────────────
    if S1 and (S2 or S3a or S4 or S5):
        return "Level 2"
    if S4 and intent == "Quotation":
        return "Level 2"
    if identity == "Personal email only" and (S2 or S3a or S5):
        return "Level 2"
    if evidence and qmax == 1 and (S1 or S2 or S4 or S5):
        return "Level 2"
    if S1 and product == "AcousticPod" and S6:
        return "Level 2"

    # ── Level 3（默认）────────────────────────────────────────────────────
    return "Level 3"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. calc_price_only_flag — 价格猎手标记
# ═══════════════════════════════════════════════════════════════════════════════

def calc_price_only_flag(slots: dict) -> str:
    """判定是否为纯价格猎手（Price Only）。

    规则：intent==Quotation 且至少 2 项缺失：
      - scenario==Unknown
      - quantity_max=="N/A" AND capacity=="N/A"
      - timeline=="NotMentioned"
      - identity!="Company domain email"

    Returns:
        "Yes" / "No"
    """
    if slots.get("intent_slot") != "Quotation":
        return "No"

    missing = 0
    if slots.get("scenario_slot") == "Unknown":
        missing += 1
    if slots.get("quantity_max") == "N/A" and slots.get("capacity_options") == "N/A":
        missing += 1
    if slots.get("timeline_slot") == "NotMentioned":
        missing += 1
    if slots.get("identity_strength") != "Company domain email":
        missing += 1

    return "Yes" if missing >= 2 else "No"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. calc_l2_tag — Level 2 缺失标签
# ═══════════════════════════════════════════════════════════════════════════════

def calc_l2_tag(level: str, slots: dict, raw_text: str) -> str:
    """仅当 Level 2 时，计算缺失标签（优先级排序）。

    Returns:
        "Need_Scenario" / "Need_Quantity" / "Need_Timeline" / "Need_Location" / "N/A"
    """
    if level != "Level 2":
        return "N/A"

    # 优先级 1: 场景缺失
    if slots.get("scenario_slot") == "Unknown":
        return "Need_Scenario"

    # 优先级 2: 数量和容量都缺失
    if slots.get("quantity_max") == "N/A" and slots.get("capacity_options") == "N/A":
        return "Need_Quantity"

    # 优先级 3: 时间线缺失
    if slots.get("timeline_slot") == "NotMentioned":
        return "Need_Timeline"

    # 优先级 4: 无交付地点
    if not _has_delivery_location(raw_text):
        return "Need_Location"

    return "N/A"


def _has_delivery_location(text: str) -> bool:
    """检查原文是否包含交付城市/国家信息。

    两步匹配：先找 delivery/ship/send 关键词，再检查其后 40 字符内
    是否有首字母大写的地名（排除常见非地名如 Info, Please 等）。
    """
    if not text:
        return False
    # 常见非地名排除
    _NON_LOCATION = frozenset({
        "info", "information", "please", "thank", "thanks",
        "best", "regards", "hello", "dear", "sincerely",
    })
    for m in _DELIVERY_KEYWORD_RE.finditer(text):
        # 检查关键词后 40 字符内是否有首字母大写的地名
        after = text[m.end(): min(len(text), m.end() + 40)]
        for nm in _PROPER_NAME_RE.finditer(after):
            name = nm.group(1).lower()
            if name not in _NON_LOCATION:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 9. grade_lead — 主分级入口
# ═══════════════════════════════════════════════════════════════════════════════

def grade_lead(
    raw_body: str,
    email: str = "",
    semantic_slots: Optional[dict] = None,
) -> dict:
    """主分级入口：整合代码规则 + LLM 语义槽位 → 完整分级结果。

    Args:
        raw_body: 询盘原文
        email: 客户邮箱
        semantic_slots: 预提取的语义槽位（None 则调用 slot_extractor）

    Returns:
        包含 level, l2_tag, price_only_flag, slots, signals, conclusion 的 dict
    """
    # ── Step 1: 获取语义槽位 ─────────────────────────────────────────────
    if semantic_slots is None:
        try:
            from slot_extractor import extract_semantic_slots
            semantic_slots = extract_semantic_slots(raw_body)
        except ImportError:
            log.warning("slot_extractor 不可用，使用默认槽位")
            semantic_slots = None
        except Exception as e:
            log.error("语义槽位提取失败: %s", e)
            semantic_slots = None

    # 默认语义槽位
    sem = semantic_slots or {}
    slots = {
        "intent_slot": sem.get("intent", "Unclear"),
        "product_slot": sem.get("product", "Unclear"),
        "scenario_slot": sem.get("scenario", "Unknown"),
        "timeline_slot": sem.get("timeline", "NotMentioned"),
        "config_slot": sem.get("config", "NotMentioned"),
        "language_hint": sem.get("language", "Other"),
    }

    # ── Step 1b: scenario 代码兜底 + Office 降级 ──────────────────────────
    # LLM 返回 Unknown 时，用关键词规则兜底
    if slots["scenario_slot"] == "Unknown":
        fallback = extract_scenario_fallback(raw_body)
        if fallback != "Unknown":
            slots["scenario_slot"] = fallback
            log.info("scenario 兜底命中: %s", fallback)

    # Office 降级：LLM 返回 Office 时，需代码关键词确认
    # glm-4-flash 常把产品名中的 "office" 误判为场景，用代码反向校验
    elif slots["scenario_slot"] == "Office":
        confirmed = extract_scenario_fallback(raw_body)
        if confirmed != "Office":
            # 降级后重新检查其他场景（如 LLM 返回 Office 但实际是 Studio）
            fallback = extract_scenario_fallback(raw_body)
            slots["scenario_slot"] = fallback  # 可能是 Studio/Home/Project/Unknown
            log.info("scenario Office 降级: %s → %s", "Office", fallback)

    # ── Step 2: 代码规则提取 ──────────────────────────────────────────────
    qmin, qmax, evidence = extract_quantity(raw_body)
    capacity = extract_capacity(raw_body)
    identity = check_identity(email)

    slots["quantity_min"] = qmin
    slots["quantity_max"] = qmax
    slots["quantity_evidence_flag"] = evidence
    slots["capacity_options"] = capacity
    slots["identity_strength"] = identity
    slots["scale_tier"] = map_scale_tier(qmax, evidence)
    slots["extracted_email"] = email if email else "N/A"

    # ── Step 3: 信号 → Level → 标签 ──────────────────────────────────────
    signals = calc_signals(slots)
    level = determine_level(signals, slots)
    price_only = calc_price_only_flag(slots)
    l2_tag = calc_l2_tag(level, slots, raw_body)
    conclusion = generate_conclusion({
        "level": level,
        "l2_tag": l2_tag,
        "price_only_flag": price_only,
        **slots,
        **signals,
    })

    result = {
        "level": level,
        "l2_tag": l2_tag,
        "price_only_flag": price_only,
        "conclusion": conclusion,
        "slots": slots,
        "signals": signals,
        # 向下兼容字段（供 test_grading.py 等旧消费者使用）
        "product_intent": slots["product_slot"],
        "basis_1_intent_hit": f"intent={slots['intent_slot']} product={slots['product_slot']}",
        "basis_2_structure_hit": _format_signal_summary(signals),
        "basis_3_risk_or_note": "N/A",
    }

    log.info(
        "分级完成: level=%s | tag=%s | intent=%s | product=%s | identity=%s",
        level, l2_tag, slots["intent_slot"], slots["product_slot"], identity,
    )
    return result


def _format_signal_summary(signals: dict) -> str:
    """格式化信号摘要。"""
    hits = []
    for k, v in signals.items():
        if v:
            hits.append(k)
    return f"Signals: {', '.join(hits)}" if hits else "Signals: none"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. format_grading_section — 飞书格式化输出
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_evidence(text: str, patterns: list[str], fallback: str = "") -> str:
    """从原文中提取简短证据片段。"""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return fallback


def format_grading_section(result: dict, raw_body: str = "") -> str:
    """将分级结果格式化为追加到 inquiry_content 的文本块。

    只输出等级 + 判级依据（分点 + 原文证据）+ 追问建议。
    """
    if not result:
        return ""

    level = result.get("level", "Unknown")
    clue_level = level.replace("Level ", "L") if level else ""
    l2_tag = result.get("l2_tag", "N/A")
    slots = result.get("slots", {})
    signals = result.get("signals", {})
    price_only = result.get("price_only_flag", "")
    qmax = slots.get("quantity_max", "N/A")
    capacity = slots.get("capacity_options", "N/A")
    scenario = slots.get("scenario_slot", "Unknown")
    timeline = slots.get("timeline_slot", "NotMentioned")
    email = slots.get("extracted_email", "")

    parts = [
        "--- 线索分级 ---",
        f"等级: {clue_level}",
        "依据:",
    ]

    # ── 判级依据（分点 + 原文证据）──
    if level == "Level 1":
        if signals.get("S3b"):
            ev = _extract_evidence(raw_body,
                [r"\d+\s*(?:pcs|units|booths?|pods?|cabins?|台|套)\b",
                 r"(?:need|require|order|purchase|looking for|want)\D{0,30}\d+"])
            parts.append(f"• 大单({qmax}台): {ev or f'数量={qmax}'}")
        if signals.get("S1") and signals.get("S2"):
            domain = email.rsplit("@", 1)[-1] if "@" in email else ""
            parts.append(f"• 公司邮箱: {domain}")
            ev = _extract_evidence(raw_body,
                [r"(?:ASAP|urgent|尽快|立即|\d+\s*(?:weeks?|days?|months?)\b|delivery|交期|ship)"],
                "时间敏感")
            parts.append(f"• 紧急时间线: {ev}")
        elif signals.get("S1"):
            domain = email.rsplit("@", 1)[-1] if "@" in email else ""
            parts.append(f"• 公司邮箱: {domain}")
        if slots.get("intent_slot") == "Partnership":
            ev = _extract_evidence(raw_body,
                [r"(?:dealer|distributor|agent|partner|reseller|representative|cooperation|经销|代理)"],
                "Partnership")
            parts.append(f"• Partnership: {ev}")

    elif level == "Level 2":
        if signals.get("S1"):
            domain = email.rsplit("@", 1)[-1] if "@" in email else ""
            parts.append(f"• 公司邮箱: {domain}")
        if signals.get("S3a"):
            ev = _extract_evidence(raw_body,
                [r"\d+\s*(?:pcs|units|booths?|pods?|cabins?|台|套)\b",
                 r"(?:need|require|order|purchase|looking for|want)\D{0,30}\d+"],
                f"数量={qmax}")
            parts.append(f"• 数量({qmax}台): {ev}")
        if signals.get("S5"):
            ev = _extract_evidence(raw_body,
                [r"\d\s*[-]?\s*(?:person|people|pax|personas?)\b",
                 r"(?:single|one|two|three|four)\s*[-]?\s*person\b"],
                capacity)
            parts.append(f"• 容量({capacity}): {ev}")
        if signals.get("S4") and scenario != "Unknown":
            parts.append(f"• 场景: {scenario}")
        if l2_tag and l2_tag != "N/A":
            tag_reason = {
                "Need_Scenario": "场景不明",
                "Need_Quantity": "数量不明",
                "Need_Timeline": "时间线不明",
                "Need_Location": "交付地不明",
            }
            parts.append(f"• 缺失: {tag_reason.get(l2_tag, l2_tag)}")

    elif level == "Level 3":
        weak = []
        if slots.get("identity_strength") == "Personal email only":
            weak.append("个人邮箱")
        if scenario == "Unknown":
            weak.append("无场景")
        if timeline == "NotMentioned":
            weak.append("无时间线")
        if qmax == "N/A" and capacity == "N/A":
            weak.append("无数量")
        if weak:
            parts.append(f"• 信号弱: {'/'.join(weak)}")

    elif level == "Level 4":
        ev = _extract_evidence(raw_body,
            [r"(?:smoking|烟|cigar|shelter|广告|marketing|SEO)"],
            slots.get("product_slot", ""))
        parts.append(f"• 非目标: {ev or '低质量线索'}")

    # 去掉空的依据行
    if len(parts) > 2 and parts[-1] == "依据:":
        parts.pop()

    # ── 追问建议（缺失项）──
    followups = []
    if scenario == "Unknown":
        followups.append("使用场景")
    if qmax == "N/A" and capacity == "N/A":
        followups.append("数量/容量")
    if timeline == "NotMentioned" and level != "Level 1":
        followups.append("交期")
    if followups:
        parts.append("待确认: " + ", ".join(followups[:2]))

    if price_only == "Yes":
        parts.append("标记: 纯价格猎手")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. generate_conclusion — 自动生成结论
# ═══════════════════════════════════════════════════════════════════════════════

def generate_conclusion(result: dict) -> str:
    """基于 Level 和槽位自动生成结论（替代 LLM CONCLUSION 输出）。

    Returns:
        <=80 字的行动结论字符串
    """
    level = result.get("level", "")
    slots = result.get("slots", {})
    signals = result.get("signals", {})
    intent = slots.get("intent_slot", "Unclear")
    product = slots.get("product_slot", "Unclear")
    identity = slots.get("identity_strength", "Unknown")
    qmax = slots.get("quantity_max", "N/A")
    capacity = slots.get("capacity_options", "N/A")
    scenario = slots.get("scenario_slot", "Unknown")
    timeline = slots.get("timeline_slot", "NotMentioned")
    l2_tag = result.get("l2_tag", "N/A")

    # ── Level 4: 低价值/不匹配 ──────────────────────────────────────────
    if level == "Level 4":
        if product == "NoMatch":
            return "Non-target product. Auto-archive unless convertible."
        return "Low-quality lead. No action needed."

    # ── Level 1: 高优先级 ──────────────────────────────────────────────
    if level == "Level 1":
        parts = ["HIGH PRIORITY"]
        if signals.get("S3b"):
            parts.append(f"Large order ({qmax}+ units)")
        elif signals.get("S1") and signals.get("S2"):
            parts.append("Company email + urgent timeline")
        if intent == "Partnership":
            parts.append("Partnership inquiry")
        # 追问方向
        followups = []
        if scenario == "Unknown":
            followups.append("usage scenario")
        if qmax == "N/A" and capacity == "N/A":
            followups.append("quantity/capacity")
        if followups:
            parts.append(f"Ask: {', '.join(followups[:2])}")
        return ". ".join(parts)

    # ── Level 2: 中高优先级 ────────────────────────────────────────────
    if level == "Level 2":
        parts = ["MEDIUM-HIGH"]
        if l2_tag != "N/A":
            tag_map = {
                "Need_Scenario": "confirm usage scenario",
                "Need_Quantity": "confirm quantity/capacity",
                "Need_Timeline": "confirm delivery timeline",
                "Need_Location": "confirm delivery location",
            }
            parts.append(tag_map.get(l2_tag, l2_tag))
        if identity == "Company domain email":
            parts.append("Company domain verified")
        return ". ".join(parts)

    # ── Level 3: 一般 ──────────────────────────────────────────────────
    parts = ["STANDARD"]
    weak_points = []
    if identity != "Company domain email":
        weak_points.append("personal email only")
    if timeline == "NotMentioned":
        weak_points.append("no timeline")
    if scenario == "Unknown":
        weak_points.append("unknown scenario")
    if weak_points:
        parts.append(f"Low signal: {', '.join(weak_points[:3])}")
    # 追问方向
    followups = []
    if scenario == "Unknown":
        followups.append("scenario")
    if qmax == "N/A":
        followups.append("quantity")
    if followups:
        parts.append(f"Ask: {', '.join(followups[:2])}")
    return ". ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# 向下兼容：保留旧函数签名
# ═══════════════════════════════════════════════════════════════════════════════

def call_llm_grade(raw_body: str, parsed_fields: Optional[dict] = None) -> Optional[dict]:
    """向下兼容旧接口 — 内部调用 grade_lead()。

    注意：此函数名有误导性，新代码应直接调用 grade_lead()。
    当 ZHIPU_API_KEY 不可用时，仍可用语义槽位默认值进行代码分级。
    """
    email = ""
    if parsed_fields:
        email = parsed_fields.get("email", "")
    try:
        return grade_lead(raw_body, email=email)
    except Exception as e:
        log.error("分级异常: %s", e, exc_info=True)
        return None


# 旧接口保留
def parse_grading_output(raw_text: str) -> Optional[dict]:
    """旧接口保留（解析 LLM 文本输出）。

    注意：新版 lead_grader 不再产生 LLM 文本输出，此函数仅用于
    兼容可能传入旧格式数据的场景。
    """
    if not raw_text:
        return None

    result = {}
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("RAW_LEAD"):
            continue

        m = re.match(r'^([A-Z_]+)="?([^"]*)"?$', line)
        if not m:
            m = re.match(r'^([A-Z_]+)=(.*)$', line)
        if not m:
            continue

        key, value = m.group(1), m.group(2).strip()
        _KEY_MAP = {
            "LEVEL": "level", "L2_TAG": "l2_tag", "PRICE_ONLY_FLAG": "price_only_flag",
            "PRODUCT_INTENT": "product_intent", "CONCLUSION": "conclusion",
            "BASIS_1_INTENT_HIT": "basis_1_intent_hit",
            "BASIS_2_STRUCTURE_HIT": "basis_2_structure_hit",
            "BASIS_3_RISK_OR_NOTE": "basis_3_risk_or_note",
        }
        if key in _KEY_MAP:
            result[_KEY_MAP[key]] = value

    level = result.get("level", "")
    if not level:
        m = re.search(r'Level\s+([1-4])', raw_text)
        if m:
            result["level"] = f"Level {m.group(1)}"
        else:
            return None

    # 标准化
    lv = result["level"]
    if lv in ("1", "2", "3", "4"):
        result["level"] = f"Level {lv}"
    elif lv.upper() in ("L1", "L2", "L3", "L4"):
        result["level"] = f"Level {lv[1]}"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 内置测试用例
# ═══════════════════════════════════════════════════════════════════════════════

def _run_tests():
    """运行内置测试用例。"""
    import traceback

    passed = 0
    failed = 0
    errors = []

    def _assert(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS: {name}")
        else:
            failed += 1
            msg = f"  FAIL: {name}"
            if detail:
                msg += f" ({detail})"
            print(msg)
            errors.append(name)

    print("=" * 60)
    print("Lead Grader Unit Tests")
    print("=" * 60)

    # ── extract_quantity 测试 ────────────────────────────────────────────
    print("\n[extract_quantity]")

    qmin, qmax, flag = extract_quantity("I need 5 booths for our office.")
    _assert("simple booths", qmax == "5" and flag is True, f"got ({qmin},{qmax},{flag})")

    qmin, qmax, flag = extract_quantity("The pod dimensions are 1200mm x 1000mm x 2300mm")
    _assert("spec exclusion (mm)", qmax == "N/A" and flag is False, f"got ({qmin},{qmax},{flag})")

    qmin, qmax, flag = extract_quantity("NAICS 334210 is our code")
    _assert("large number exclusion", qmax == "N/A" and flag is False, f"got ({qmin},{qmax},{flag})")

    qmin, qmax, flag = extract_quantity("Need qty 15000 pods")
    _assert("large number with qty prefix", qmax == "15000" and flag is True, f"got ({qmin},{qmax},{flag})")

    qmin, qmax, flag = extract_quantity("3 of them")
    _assert("N of them pattern", qmax == "3" and flag is True, f"got ({qmin},{qmax},{flag})")

    # 英文数字词 + 舱体单位（中间有形容词）
    qmin, qmax, flag = extract_quantity(
        "I'd like to ask for a quote for two Small Meeting Pods SR-S to be delivered in New York city please."
    )
    _assert("word number + booth unit (two pods)", qmax == "2" and flag is True, f"got ({qmin},{qmax},{flag})")

    # 英文数字词 + 舱体单位（简单形式）
    qmin, qmax, flag = extract_quantity("Looking for three acoustic booths for our office")
    _assert("word number + simple booth (three booths)", qmax == "3" and flag is True, f"got ({qmin},{qmax},{flag})")

    # 总量+分项去重
    qmin, qmax, flag = extract_quantity(
        "I need 3 booths for our project.\n- 2 single-person acoustic booths\n- 1 meeting booth"
    )
    _assert("total+breakdown dedup", qmax == "3" and flag is True, f"got ({qmin},{qmax},{flag})")

    # 分项求和
    qmin, qmax, flag = extract_quantity(
        "- 2 single-person acoustic booths\n- 1 small meeting booth\n- 3 four-person pods"
    )
    _assert("line-item sum (2+1+3=6)", qmax == "6" and flag is True, f"got ({qmin},{qmax},{flag})")

    # ── extract_capacity 测试 ────────────────────────────────────────────
    print("\n[extract_capacity]")

    cap = extract_capacity("I need a 4-person meeting pod")
    _assert("4-person", cap == "4-person", f"got '{cap}'")

    cap = extract_capacity("Looking for single-person phone booths")
    _assert("single-person text", cap == "1-person", f"got '{cap}'")

    cap = extract_capacity("Need 2-4 persons office pods")
    _assert("range 2-4 persons", "2-person" in cap and "4-person" in cap, f"got '{cap}'")

    cap = extract_capacity("I want 10 booths for the office")
    _assert("no capacity → N/A", cap == "N/A", f"got '{cap}'")

    cap = extract_capacity("4 pax meeting pod needed")
    _assert("4 pax", cap == "4-person", f"got '{cap}'")

    cap = extract_capacity("2 personen cabine")
    _assert("2 personen (German)", cap == "2-person", f"got '{cap}'")

    # ── check_identity 测试 ──────────────────────────────────────────────
    print("\n[check_identity]")

    _assert("gmail → Personal", check_identity("user@gmail.com") == "Personal email only")
    _assert("qq → Personal", check_identity("user@qq.com") == "Personal email only")
    _assert("company → Company", check_identity("user@soundbox.com") == "Company domain email")
    _assert("empty → Unknown", check_identity("") == "Unknown")
    _assert("no @ → Unknown", check_identity("justtext") == "Unknown")
    _assert("163 → Personal", check_identity("user@163.com") == "Personal email only")

    # ── map_scale_tier 测试 ──────────────────────────────────────────────
    print("\n[map_scale_tier]")

    _assert("no evidence → Unknown", map_scale_tier("5", False) == "Unknown")
    _assert("1 → 1", map_scale_tier("1", True) == "1")
    _assert("2 → 2-3", map_scale_tier("2", True) == "2-3")
    _assert("3 → 2-3", map_scale_tier("3", True) == "2-3")
    _assert("5 → 4-9", map_scale_tier("5", True) == "4-9")
    _assert("10 → 10+", map_scale_tier("10", True) == "10+")
    _assert("50 → 10+", map_scale_tier("50", True) == "10+")

    # ── calc_signals 测试 ────────────────────────────────────────────────
    print("\n[calc_signals]")

    sigs = calc_signals({
        "identity_strength": "Company domain email",
        "timeline_slot": "TimeSensitive",
        "quantity_max": "10",
        "quantity_evidence_flag": True,
        "scenario_slot": "Office",
        "capacity_options": "4-person",
        "config_slot": "ConfigMentioned",
    })
    _assert("all signals true", all(sigs.values()), f"got {sigs}")

    sigs = calc_signals({
        "identity_strength": "Personal email only",
        "timeline_slot": "NotMentioned",
        "quantity_max": "N/A",
        "quantity_evidence_flag": False,
        "scenario_slot": "Unknown",
        "capacity_options": "N/A",
        "config_slot": "NotMentioned",
    })
    _assert("all signals false", not any(sigs.values()), f"got {sigs}")

    # ── determine_level 测试 ─────────────────────────────────────────────
    print("\n[determine_level]")

    _assert("NoMatch → L4", determine_level(
        {"S1": False}, {"product_slot": "NoMatch", "intent_slot": "Quotation"}
    ) == "Level 4")

    _assert("S3b → L1", determine_level(
        {"S1": False, "S2": False, "S3a": True, "S3b": True, "S4": False, "S5": False, "S6": False},
        {"product_slot": "AcousticPod", "intent_slot": "Quotation", "identity_strength": "Unknown",
         "quantity_evidence_flag": True, "quantity_max": "10"}
    ) == "Level 1")

    _assert("S1+S2 → L1", determine_level(
        {"S1": True, "S2": True, "S3a": False, "S3b": False, "S4": False, "S5": False, "S6": False},
        {"product_slot": "AcousticPod", "intent_slot": "Quotation", "identity_strength": "Company domain email",
         "quantity_evidence_flag": False, "quantity_max": "N/A"}
    ) == "Level 1")

    _assert("Partnership+S1 → L1", determine_level(
        {"S1": True, "S2": False, "S3a": False, "S3b": False, "S4": False, "S5": False, "S6": False},
        {"product_slot": "AcousticPod", "intent_slot": "Partnership", "identity_strength": "Company domain email",
         "quantity_evidence_flag": False, "quantity_max": "N/A"}
    ) == "Level 1")

    _assert("default → L3", determine_level(
        {"S1": False, "S2": False, "S3a": False, "S3b": False, "S4": False, "S5": False, "S6": False},
        {"product_slot": "AcousticPod", "intent_slot": "Quotation", "identity_strength": "Unknown",
         "quantity_evidence_flag": False, "quantity_max": "N/A"}
    ) == "Level 3")

    _assert("S1+S5 → L2", determine_level(
        {"S1": True, "S2": False, "S3a": False, "S3b": False, "S4": False, "S5": True, "S6": False},
        {"product_slot": "AcousticPod", "intent_slot": "Quotation", "identity_strength": "Company domain email",
         "quantity_evidence_flag": False, "quantity_max": "N/A"}
    ) == "Level 2")

    # ── calc_price_only_flag 测试 ────────────────────────────────────────
    print("\n[calc_price_only_flag]")

    _assert("quotation + 3 missing → Yes", calc_price_only_flag({
        "intent_slot": "Quotation", "scenario_slot": "Unknown",
        "quantity_max": "N/A", "capacity_options": "N/A",
        "timeline_slot": "NotMentioned", "identity_strength": "Personal email only",
    }) == "Yes")

    _assert("not quotation → No", calc_price_only_flag({
        "intent_slot": "Partnership", "scenario_slot": "Unknown",
        "quantity_max": "N/A", "capacity_options": "N/A",
        "timeline_slot": "NotMentioned", "identity_strength": "Personal email only",
    }) == "No")

    _assert("quotation + company email + scenario → No", calc_price_only_flag({
        "intent_slot": "Quotation", "scenario_slot": "Office",
        "quantity_max": "5", "capacity_options": "4-person",
        "timeline_slot": "TimeSensitive", "identity_strength": "Company domain email",
    }) == "No")

    # ── calc_l2_tag 测试 ─────────────────────────────────────────────────
    print("\n[calc_l2_tag]")

    _assert("L3 → N/A", calc_l2_tag("Level 3", {}, "") == "N/A")
    _assert("L2 + Unknown scenario → Need_Scenario", calc_l2_tag("Level 2", {
        "scenario_slot": "Unknown", "quantity_max": "5", "capacity_options": "4-person",
        "timeline_slot": "TimeSensitive",
    }, "") == "Need_Scenario")
    _assert("L2 + no qty/cap → Need_Quantity", calc_l2_tag("Level 2", {
        "scenario_slot": "Office", "quantity_max": "N/A", "capacity_options": "N/A",
        "timeline_slot": "TimeSensitive",
    }, "") == "Need_Quantity")
    _assert("L2 + no timeline → Need_Timeline", calc_l2_tag("Level 2", {
        "scenario_slot": "Office", "quantity_max": "5", "capacity_options": "4-person",
        "timeline_slot": "NotMentioned",
    }, "") == "Need_Timeline")
    _assert("L2 + no delivery → Need_Location", calc_l2_tag("Level 2", {
        "scenario_slot": "Office", "quantity_max": "5", "capacity_options": "4-person",
        "timeline_slot": "TimeSensitive",
    }, "just some text no delivery info") == "Need_Location")

    # ── 汇总 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print(f"Failed tests: {', '.join(errors)}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════════════════
# 独立运行入口（用于测试单条询盘）
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python lead_grader.py '<email_body_text>' [email]")
        print("       cat email.txt | python lead_grader.py - [email]")
        print()
        print("Options:")
        print("  --test   Run built-in unit tests")
        sys.exit(1)

    # ── 内置测试模式 ─────────────────────────────────────────────────────
    if sys.argv[1] == "--test":
        _run_tests()
        sys.exit(0)

    # ── 读取输入 ─────────────────────────────────────────────────────────
    email = sys.argv[2] if len(sys.argv) > 2 else ""
    if sys.argv[1] == "-":
        body = sys.stdin.read()
    else:
        body = sys.argv[1]

    # 使用代码规则分级（不调用 LLM）
    result = grade_lead(body, email=email)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("\n--- Formatted for Feishu ---")
    print(format_grading_section(result))
