#!/usr/bin/env python3
"""
email_template.py — 建联邮件模板引擎（纯模板模式）

根据产品品类 × 客户类型选择模板，填充占位符后返回邮件内容。
无匹配模板时返回 None，管线跳过自动回复。

模板来源：建联模板.xlsx（业务侧审核通过）
"""

import logging
from pathlib import Path

log = logging.getLogger("email-template")

# ── 画册文件路径 ──────────────────────────────────────────────────────────
ATTACHMENTS_DIR = Path(__file__).resolve().parent.parent / "attachments"
BOOTH_BROCHURES = {
    "ART": ATTACHMENTS_DIR / "ART-POD-NEW.pdf",
    "VRT": ATTACHMENTS_DIR / "VRT+pod-NEW.pdf",
}

# ── 统一签名（与 email_generator.py 共用同一份） ──────────────────────────
from email_generator import UNIFIED_SIGNATURE as SIGNATURE

# ── 产品品类 → 模板键映射 ──────────────────────────────────────────────────
# 管线中的 product_category 值 → 模板内部键名
PRODUCT_MAP = {
    "静音舱": "booth",
    "吸音板": "panel",
    "隔音门": "door",
    "声学材料": "material",
}

# ── 中文→英文映射（country/channel 用于英文邮件模板） ────────────────────────
COUNTRY_EN = {
    "中国": "China", "美国": "the USA", "英国": "the UK", "德国": "Germany",
    "法国": "France", "日本": "Japan", "韩国": "South Korea", "印度": "India",
    "澳大利亚": "Australia", "加拿大": "Canada", "巴西": "Brazil",
    "西班牙": "Spain", "意大利": "Italy", "荷兰": "the Netherlands",
    "瑞士": "Switzerland", "葡萄牙": "Portugal", "新西兰": "New Zealand",
    "新加坡": "Singapore", "马来西亚": "Malaysia", "泰国": "Thailand",
    "印度尼西亚": "Indonesia", "印尼": "Indonesia",
    "菲律宾": "the Philippines", "越南": "Vietnam",
    "墨西哥": "Mexico", "沙特": "Saudi Arabia", "阿联酋": "the UAE",
    "土耳其": "Turkey", "以色列": "Israel", "南非": "South Africa",
    "智利": "Chile", "哥伦比亚": "Colombia", "阿根廷": "Argentina",
    "波兰": "Poland", "瑞典": "Sweden", "挪威": "Norway", "丹麦": "Denmark",
    "芬兰": "Finland", "爱尔兰": "Ireland", "奥地利": "Austria",
    "比利时": "Belgium", "捷克": "Czech Republic", "匈牙利": "Hungary",
    "罗马尼亚": "Romania", "希腊": "Greece", "埃及": "Egypt",
    "尼日利亚": "Nigeria", "肯尼亚": "Kenya", "巴基斯坦": "Pakistan",
    "孟加拉": "Bangladesh", "斯里兰卡": "Sri Lanka", "缅甸": "Myanmar",
    "柬埔寨": "Cambodia", "老挝": "Laos", "台湾": "Taiwan", "香港": "Hong Kong",
    "澳门": "Macao", "俄罗斯": "Russia", "乌克兰": "Ukraine",
    "卡塔尔": "Qatar", "阿曼": "Oman",
    "保加利亚": "Bulgaria", "克罗地亚": "Croatia", "塞尔维亚": "Serbia",
    "摩洛哥": "Morocco", "斯洛伐克": "Slovakia", "斯洛文尼亚": "Slovenia",
    "爱沙尼亚": "Estonia", "拉脱维亚": "Latvia", "立陶宛": "Lithuania",
    "白俄罗斯": "Belarus", "秘鲁": "Peru",
    "哈萨克斯坦": "Kazakhstan", "乌兹别克斯坦": "Uzbekistan",
    "格鲁吉亚": "Georgia", "阿塞拜疆": "Azerbaijan",
    "蒙古": "Mongolia", "尼泊尔": "Nepal",
    "科威特": "Kuwait", "巴林": "Bahrain",
    "约旦": "Jordan", "黎巴嫩": "Lebanon", "伊拉克": "Iraq",
    "冰岛": "Iceland", "卢森堡": "Luxembourg", "马耳他": "Malta",
    "塞浦路斯": "Cyprus", "摩尔多瓦": "Moldova",
    "阿尔巴尼亚": "Albania", "北马其顿": "North Macedonia",
    "黑山": "Montenegro", "波黑": "Bosnia and Herzegovina",
    "巴拿马": "Panama", "哥斯达黎加": "Costa Rica",
    "多米尼加": "Dominican Republic", "厄瓜多尔": "Ecuador",
    "乌拉圭": "Uruguay", "委内瑞拉": "Venezuela",
    "玻利维亚": "Bolivia", "巴拉圭": "Paraguay",
    "加纳": "Ghana", "坦桑尼亚": "Tanzania", "埃塞俄比亚": "Ethiopia",
    "喀麦隆": "Cameroon", "塞内加尔": "Senegal",
}

CHANNEL_EN = {
    "谷歌1": "Google", "谷歌2": "Google", "谷歌": "Google",
    "FB1": "Facebook", "FB2": "Facebook", "FB": "Facebook",
    "阿里国际站": "Alibaba", "阿里巴巴": "Alibaba",
    "展会": "an exhibition", "转介绍": "a referral",
}


def _to_english(raw: str, mapping: dict) -> str:
    """将中文值转为英文，无匹配则原样返回。"""
    if not raw:
        return raw
    return mapping.get(raw.strip(), raw.strip())

# 兜底：按关键词模糊匹配
PRODUCT_KEYWORDS = {
    "booth": ["booth", "pod", "silence", "soundproof booth", "vr-", "vrt", "sr-", "art-"],
    "panel": ["panel", "acoustic panel", "吸音"],
    "door": ["door", "acoustic door", "隔音门"],
    "material": ["damping", "mat", "paint", "sealant", "caster", "caulk", "声学材料"],
}

# ── 客户类型识别 ──────────────────────────────────────────────────────────
# "general" = 一般询盘, "dealer" = 经销商, "big_b" = 大B客户
DEALER_KEYWORDS = ["partnership", "distributor", "dealer", "agent", "resell", "代理", "经销", "合作"]
BIG_B_KEYWORDS = ["project", "procurement", "tender", "hotel", "office building",
                  "government", "hospital", "school"]


# ═══════════════════════════════════════════════════════════════════════════════
# 模板库
# ═══════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    # ── 静音舱 ──
    ("booth", "general", "default"): {
        "subject": "Quotation for Silence Booth – Soundbox Acoustic",
        "body": (
            "Dear [Customer Name],\n\n"
            "Thanks for your inquiry about our soundproof booth from [Channel].\n\n"
            "This is Frank from Soundbox Acoustic – we specialize in R&D and manufacturing "
            "of high-performance acoustic booths for offices, home studios, and commercial spaces. "
            "Our booths are trusted by clients in [client's country].\n\n"
            "To give you the most accurate recommendation, could you kindly share:\n\n"
            "1) How many people will use the booth? (1-person / 2-person / 4-person / Customized)\n"
            "2) What is the main purpose? (Office calls / Music practice / Meeting room / Other)\n"
            "3) Which country will it be shipped to and how many units do you need?"
        ),
    },
    ("booth", "big_b", "default"): {
        "subject": "Project Quotation – Soundbox",
        "body": (
            "Dear [Customer Name],\n\n"
            "Thanks for sharing your project details. We truly appreciate the time you took to "
            "describe your needs.\n\n"
            "At Soundbox Acoustic, we've successfully delivered similar solutions for several famous "
            "companies. Our booths are CE-certified and can be customized for size, color, and "
            "power outlets.\n\n"
            "Before I prepare a detailed quotation, may I confirm:\n\n"
            "1) What is your target budget range per unit?\n"
            "2) What is your expected delivery date?\n"
            "3) Can we add you on WhatsApp for faster communication during the project?"
        ),
    },

    # ── 吸音板 ──
    ("panel", "general", "default"): {
        "subject": "Acoustic Panels for Project",
        "body": (
            "Dear [Customer Name],\n\n"
            "Thanks for reaching out about acoustic panels from [Channel].\n\n"
            "We manufacture high-NRC acoustic panels – eco-friendly, fire-retardant, and available "
            "in various colors/thicknesses. Currently we're supporting projects in Saudi Arabia "
            "[22,000m² hotel project reference], Africa, and Southeast Asia.\n\n"
            "To quote you accurately, could you let me know:\n\n"
            "1) What is the application? (Wall / Ceiling / Both)\n"
            "2) Total square meters / quantity needed?\n"
            "3) Do you have any specific thickness or color requirements?"
        ),
    },

    # ── 隔音门 ──
    ("door", "general", "default"): {
        "subject": "Acoustic Door for your space",
        "body": (
            "Dear [Customer Name],\n\n"
            "Thanks for your interest in acoustic soundproof doors.\n\n"
            "We supply high-performance acoustic doors with sound reduction up to STC 38–45, "
            "suitable for music studios, home theaters, and conference rooms.\n\n"
            "Before sending the quote, could you confirm:\n\n"
            "1) What is the wall thickness of your current opening?\n"
            "2) What type of room is it? (Music studio / Home theater / Office / Other)\n"
            "3) How many doors do you need and where will they be shipped?"
        ),
    },

    # ── 声学材料 ──
    ("material", "general", "default"): {
        "subject": "Soundproofing Materials for your project",
        "body": (
            "Dear [Customer Name],\n\n"
            "Thanks for your message about soundproofing materials – I'm glad to help!\n\n"
            "We supply a full range of acoustic solutions: vibration damping mats, sound insulation "
            "paint, silent casters, acoustic caulk, and acoustic panels, suitable for various "
            "applications.\n\n"
            "To narrow down the best product for you, could you tell me:\n\n"
            "1) What specific product are you looking for? "
            "(Damping mat / Soundproof paint / Acoustic sealant / Caster / Acoustic art)\n"
            "2) What is the room/space used for? (Home theater / Music practice / Gym / Office)\n"
            "3) What is the approximate floor area or number of units needed?"
        ),
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# 产品品类识别
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_product(product_category: str, message: str = "") -> str:
    """将管线的 product_category 映射为模板键名，兜底用关键词匹配。"""
    # 精确匹配
    if product_category in PRODUCT_MAP:
        return PRODUCT_MAP[product_category]

    # 中文关键词匹配
    cat_lower = product_category.lower()
    for cn_key, tpl_key in PRODUCT_MAP.items():
        if cn_key in cat_lower or cat_lower in cn_key:
            return tpl_key

    # 英文关键词兜底
    msg_lower = (product_category + " " + message).lower()
    for tpl_key, keywords in PRODUCT_KEYWORDS.items():
        for kw in keywords:
            if kw in msg_lower:
                return tpl_key

    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# 客户类型识别
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_customer_type(grading: dict, message: str = "") -> str:
    """根据分级结果和原文判断客户类型。

    Returns: "big_b" | "dealer" | "general"
    """
    text = message.lower()
    slots = grading.get("slots", {}) if grading else {}
    intent = slots.get("intent_slot", "").lower()

    # 经销商标识
    if intent == "partnership":
        return "dealer"
    for kw in DEALER_KEYWORDS:
        if kw in text:
            return "dealer"

    # 大B客户标识
    if intent == "quotation":
        identity = slots.get("identity_strength", "")
        company_mentioned = bool(slots.get("identity_strength") == "Company domain email")
        if company_mentioned:
            return "big_b"
    for kw in BIG_B_KEYWORDS:
        if kw in text:
            return "big_b"

    # 高级别线索（L1）默认视为大B
    level = grading.get("level", "")
    if level == "Level 1":
        return "big_b"

    return "general"


# ═══════════════════════════════════════════════════════════════════════════════
# 模板填充
# ═══════════════════════════════════════════════════════════════════════════════

def _fill_placeholders(text: str, customer_name: str = "",
                       channel: str = "", country: str = "") -> str:
    """替换模板占位符。"""
    result = text
    result = result.replace("[Customer Name]", customer_name or "there")
    result = result.replace("[Channel, e.g., Facebook/Website]", channel or "our website")
    result = result.replace("[Channel]", channel or "our website")
    result = result.replace("[client's country]", country or "your region")
    result = result.replace("[Application – e.g., Home Theater / Gym / Office]", "your project")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def generate_template_email(
    product_category: str,
    grading: dict,
    customer_name: str = "",
    channel: str = "Google",
    country: str = "",
    message: str = "",
    source: str = "default",
) -> dict | None:
    """基于模板生成建联邮件。

    Args:
        product_category: 管线中的产品品类（静音舱/吸音板/隔音门/声学材料）
        grading: grade_lead() 返回的分级结果
        customer_name: 客户姓名
        channel: 渠道（Google/Facebook）
        country: 客户国家（英文）
        message: 询盘原文（用于客户类型识别兜底）
        source: 来源渠道（default/exhibition/alibaba/referral，预留扩展）

    Returns:
        {"subject": ..., "body": ..., "signature": ..., "email_model": ..., "attachments": ...} 或 None
    """
    # 0. 中文→英文转换（模板是英文的，不能出现中文 country/channel）
    country = _to_english(country, COUNTRY_EN)
    channel = _to_english(channel, CHANNEL_EN)

    # 1. 识别产品
    product_key = _normalize_product(product_category, message)
    if not product_key:
        log.info("无匹配产品模板: category=%s", product_category)
        return None

    # 2. 识别客户类型
    customer_type = _detect_customer_type(grading, message)

    # 3. 三级降级链查找模板
    #    (product, type, source) → (product, type, "default") → (product, "general", "default")
    tpl = TEMPLATES.get((product_key, customer_type, source))
    if not tpl and source != "default":
        tpl = TEMPLATES.get((product_key, customer_type, "default"))
        if tpl:
            log.info("模板降级: %s/%s/%s → %s/%s/default", product_key, customer_type, source, product_key, customer_type)
    if not tpl:
        tpl = TEMPLATES.get((product_key, "general", "default"))
        if not tpl:
            log.info("无匹配模板: product=%s, type=%s, source=%s", product_key, customer_type, source)
            return None
        log.info("模板降级: %s/%s → general", product_key, customer_type)

    # 4. 填充占位符
    subject = _fill_placeholders(tpl["subject"], customer_name, channel, country)
    body = _fill_placeholders(tpl["body"], customer_name, channel, country)

    # 5. 静音舱模板附带画册
    attachments = []
    if product_key == "booth":
        msg_lower = message.lower()
        if "art" in msg_lower and BOOTH_BROCHURES["ART"].exists():
            attachments.append(str(BOOTH_BROCHURES["ART"]))
        elif any(w in msg_lower for w in ("vrt", "vr-", "pod")) and BOOTH_BROCHURES["VRT"].exists():
            attachments.append(str(BOOTH_BROCHURES["VRT"]))
        else:
            # 默认发 VRT 画册
            if BOOTH_BROCHURES["VRT"].exists():
                attachments.append(str(BOOTH_BROCHURES["VRT"]))

    log.info("模板匹配: product=%s, type=%s, subject=%s, attachments=%d",
             product_key, customer_type, subject[:50], len(attachments))

    return {
        "subject": subject,
        "body": body,
        "signature": SIGNATURE,
        "email_model": f"TPL-{product_key}-{customer_type}",
        "attachments": attachments,
    }
