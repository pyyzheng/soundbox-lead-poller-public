#!/usr/bin/env python3
"""
Shared filtering functions for the lead pipeline.

Used by:
  - gmail-webhook-router.py  (primary filtering)
  - lead-fallback-parser.py  (fallback filtering when LLM times out)
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, Tuple

SCRIPT_DIR = Path(__file__).parent.resolve()
RULES_FILE = SCRIPT_DIR.parent / "lead-rules.json"


# ─── Rule Loading ────────────────────────────────────────────

def load_lead_rules() -> Dict[str, Any]:
    """加载 lead-rules.json 配置"""
    try:
        with open(str(RULES_FILE), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[lead-filter-common] 加载 lead-rules.json 失败: {e}", file=__import__("sys").stderr)
        return {}


# ─── Email Address Extraction ────────────────────────────────

def extract_email_address(from_field: str) -> str:
    """从 From 字段提取纯邮箱地址

    支持格式:
    - "email@example.com"
    - "Name <email@example.com>"
    - "Name [email@example.com]"
    """
    if not from_field:
        return ""
    from_field = from_field.strip()

    # <email> 格式
    match = re.search(r'<([^>]+)>', from_field)
    if match:
        return match.group(1).lower()

    # [email] 格式
    match = re.search(r'\[([^\]]+)\]', from_field)
    if match:
        return match.group(1).lower()

    # 整个字符串就是邮箱
    return from_field.lower()


# ─── Skip Sender / Subject ───────────────────────────────────

def check_skip_sender_categories(from_addr: str, rules: Dict[str, Any]) -> Tuple[bool, str]:
    """检查发件人域名是否匹配预置类别（域名后缀匹配）"""
    if not from_addr or "@" not in from_addr:
        return False, ""
    domain = from_addr.split("@")[-1].lower()
    categories = rules.get("skip_sender_categories", {})
    for cat_name, domains in categories.items():
        if cat_name.startswith("_"):
            continue
        for d in domains:
            # domain == d: 精确匹配；endswith: 子域名；startswith: 同名不同 TLD（amazon.com → amazon.co.uk 等）
            if d and (domain == d or domain.endswith("." + d) or domain.startswith(d + ".")):
                return True, f"skip_category({cat_name}:{d})"
    return False, ""


def check_skip_sender(from_addr: str, rules: Dict[str, Any]) -> Tuple[bool, str]:
    """检查发件人是否在跳过列表"""
    skip_senders = rules.get("skip_senders", [])
    for entry in skip_senders:
        if isinstance(entry, dict):
            pattern = entry.get("pattern", "")
            if pattern:
                regex = pattern.replace(".", r"\.").replace("*", ".*")
                if re.match(f"^{regex}$", from_addr, re.IGNORECASE):
                    return True, f"skip_sender(pattern:{pattern})"
        elif isinstance(entry, str):
            if entry.lower() == from_addr.lower():
                return True, f"skip_sender({entry})"
    return False, ""


def check_skip_subject(subject: str, rules: Dict[str, Any]) -> Tuple[bool, str]:
    """检查邮件主题是否匹配跳过模式"""
    skip_subjects = rules.get("skip_subject_patterns", [])
    subject_lower = (subject or "").lower()
    for pattern in skip_subjects:
        if pattern and pattern.lower() in subject_lower:
            return True, f"skip_subject({pattern})"
    return False, ""


# ─── Marketplace / Seller Platform Notifications ─────────────

_PLATFORM_SUPPORT_SENDER = re.compile(
    r"(?:sellersupport|sellercareteam|seller-?support|seller-?notifications|"
    r"merch\.service|account-update|customercare|help@walmart|no-?reply@mpsend\.walmart)",
    re.IGNORECASE,
)
_PLATFORM_CASE_SUBJECT = re.compile(
    r"(?:"
    r"case\s*#|case\s*编号|我们收到了您的\s*(?:cn\s*)?case|"
    r"seller\s+support|gtin\s+exemption|delivery\s+status\s+notification|"
    r"resolution\s+for\s+case|seller\s+approval\s+application|"
    r"marketplace\s+(?:support|notification)|walmart\s+seller"
    r")",
    re.IGNORECASE,
)
_PLATFORM_CASE_BODY = re.compile(
    r"(?:"
    r"沃尔玛卖家支持|walmart\s+seller\s+support|seller\s+care\s+team|"
    r"感谢您联系沃尔玛|thank you for contacting walmart|"
    r"original message\s*-{3,}|---------------\s*original message"
    r")",
    re.IGNORECASE,
)


def check_platform_marketplace_notification(from_addr: str, subject: str,
                                          body: str = "") -> Tuple[bool, str]:
    """硬拦截：电商平台卖家后台工单/Case 通知（非客户询盘）。"""
    addr = (from_addr or "").lower()
    subj = subject or ""
    if _PLATFORM_SUPPORT_SENDER.search(addr):
        return True, f"platform_case(sender={from_addr})"
    if _PLATFORM_CASE_SUBJECT.search(subj):
        return True, f"platform_case(subject={subject[:60]})"
    if body and _PLATFORM_CASE_BODY.search(body):
        return True, "platform_case(body=seller_support_thread)"
    return False, ""



_VOWELS = set("aeiouAEIOU")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_MESSAGE_FIELD_LABEL_RE = re.compile(
    r"(?:Message|Inquiry|留言内容|留言)\s*[:：]",
    re.IGNORECASE,
)


def body_has_message_field(body: str) -> bool:
    """询盘原文是否包含 Message / Inquiry / 留言 字段标签（含 HTML 正文）。"""
    if not body:
        return False
    if _MESSAGE_FIELD_LABEL_RE.search(body):
        return True
    plain = re.sub(r"<[^>]+>", " ", body)
    plain = re.sub(r"\s+", " ", plain)
    return bool(_MESSAGE_FIELD_LABEL_RE.search(plain))


def message_inquiry_body(message: str) -> str:
    """留言正文（去掉国家-渠道-产品标签行，保留中英文）。"""
    text = (message or "").strip()
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if kept:
                kept.append(line)
            continue
        if _CJK_RE.search(stripped) and stripped.count("-") >= 2:
            break
        kept.append(line)
    return "\n".join(kept).strip()


def message_latin_core(message: str) -> str:
    """提取 Message 拉丁文核心（去掉标签行/中文尾缀，避免误判放行）。"""
    text = message_inquiry_body(message)
    latin = re.sub(r"[^\x00-\x7F]+", " ", text)
    return re.sub(r"\s+", " ", latin).strip()


def _message_significant_len(text: str) -> int:
    """有效字符数（字母/数字/中文等，不含空白与标点）。"""
    return len(re.findall(r"[\w]", text, flags=re.UNICODE))


def is_pure_numeric_message(message: str) -> bool:
    """纯数字留言（任意长度，如 123、12345678）。"""
    core = message_inquiry_body(message).strip()
    if not core:
        return False
    compact = re.sub(r"[\s.,\-+]", "", core)
    return bool(compact) and compact.isdigit()


def is_trivial_numeric_message(message: str) -> bool:
    """兼容旧名：纯数字留言。"""
    return is_pure_numeric_message(message)


def is_short_inquiry_message(message: str, rules: Dict[str, Any]) -> bool:
    """留言过短（无实质询盘内容）。"""
    cfg = rules.get("short_message", {})
    if cfg.get("enabled", True) is False:
        return False

    core = message_inquiry_body(message).strip()
    if not core:
        return True

    normalized = re.sub(r"\s+", " ", core.lower()).strip()
    allowlist = {str(x).lower() for x in cfg.get("allowlist", [])}
    if normalized in allowlist:
        return False

    min_chars = int(cfg.get("min_significant_chars", 5))
    return _message_significant_len(core) < min_chars


def check_short_message(
    message: str,
    rules: Dict[str, Any],
    *,
    has_message_field: bool = True,
) -> Tuple[bool, str]:
    """硬拦截：纯数字或过短留言。"""
    if not has_message_field:
        return False, ""

    if is_pure_numeric_message(message):
        core = message_inquiry_body(message).strip()
        compact = re.sub(r"\s+", "", core)
        return True, f"short_message(numeric:{compact[:20]})"

    if is_short_inquiry_message(message, rules):
        core = message_inquiry_body(message).strip()
        sig = _message_significant_len(core)
        min_chars = int(rules.get("short_message", {}).get("min_significant_chars", 5))
        snippet = core[:30] or "(empty)"
        return True, f"short_message({sig}<{min_chars}:{snippet})"

    return False, ""


def check_short_inquiry(
    message: str,
    rules: Dict[str, Any],
    *,
    body: str = "",
    fields_pre: dict | None = None,
) -> Tuple[bool, str]:
    """预过滤 / LLM 后统一过短检测：无 Message 字段则跳过。"""
    if not inquiry_has_message_field(body, fields_pre):
        return False, ""
    return check_short_message(message, rules, has_message_field=True)


def inquiry_has_message_field(body: str, fields_pre: dict | None = None) -> bool:
    """是否应对该询盘做 Message 乱码检测。"""
    if fields_pre and fields_pre.get("has_message_field"):
        return True
    return body_has_message_field(body)


def looks_like_random_latin_text(text: str, *, is_name: bool = False) -> bool:
    """检测拉丁字母串是否像随机敲击/机器人填充（非真实单词）。"""
    if not text or len(text) < 5:
        return False
    clean = text.strip()
    alpha_chars = [c.lower() for c in clean if c.isalpha()]
    if not alpha_chars:
        return True

    # 无元音（仅拉丁部分足够长时）
    if len(alpha_chars) > 10:
        latin_alpha = [c for c in alpha_chars if c.isascii()]
        if len(latin_alpha) > 10 and all(c not in _VOWELS for c in latin_alpha):
            return True

    name_threshold = 12 if is_name else 15
    if len(clean) > name_threshold and " " not in clean:
        return True

    unique_threshold = 0.75 if is_name else 0.95
    min_alpha = 10 if is_name else 16
    if len(alpha_chars) > min_alpha:
        if len(set(alpha_chars)) / len(alpha_chars) > unique_threshold:
            return True

    if len(clean) > 10:
        transitions = 0
        for i in range(len(clean) - 1):
            if not (clean[i].isalpha() and clean[i + 1].isalpha()):
                continue
            if clean[i].isupper() != clean[i + 1].isupper():
                transitions += 1
            else:
                transitions = 0
            if transitions >= 4:
                return True
    return False


def is_short_keyboard_mash(message: str, rules: Dict[str, Any]) -> bool:
    """短单词键盘乱敲（如 Hofutbgh）：8-20 字母、无空格、高字符离散度。"""
    cfg = rules.get("gibberish_message", {})
    text = message_latin_core(message).strip()
    if " " in text or len(text) < int(cfg.get("short_mash_min_length", 8)):
        return False
    if len(text) > int(cfg.get("short_mash_max_length", 20)):
        return False
    if not re.fullmatch(r"[A-Za-z]+", text):
        return False
    lower = text.lower()
    if lower in {
        "interested", "inquiry", "pricing", "quotation", "soundbox",
        "acoustic", "soundproof", "phonebooth",
    }:
        return False
    unique_ratio = len(set(lower)) / len(lower)
    threshold = float(cfg.get("short_mash_unique_ratio", 0.875))
    return unique_ratio >= threshold


def is_gibberish_message(message: str, rules: Dict[str, Any], *, has_message_field: bool = True) -> bool:
    """判断 Message 是否为乱码测试内容（无真实单词），用于硬拦截不入库。

    has_message_field=False 时跳过（表单无 Message/Inquiry/留言 字段，直接放行）。
    """
    if not has_message_field:
        return False

    cfg = rules.get("gibberish_message", {})
    if cfg.get("enabled", True) is False:
        return False

    if is_short_keyboard_mash(message, rules):
        return True

    text = message_latin_core(message)
    min_len = int(cfg.get("min_length", 8))
    if len(text) < min_len:
        return False

    lower = text.lower()
    if lower in {
        "test", "testing", "hello", "hi", "hey", "price", "quote", "inquiry",
        "pricing", "thanks", "thank you",
    }:
        return False

    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"\S+@\S+", " ", cleaned)
    words = re.findall(r"[A-Za-z]{3,}", cleaned)
    min_latin = int(cfg.get("min_latin_letters", 10))
    latin_len = sum(len(w) for w in words)
    if latin_len < min_latin:
        if words and len(words) == 1 and latin_len >= min_len:
            return looks_like_random_latin_text(text, is_name=False)
        return False

    vowel_words = [w for w in words if any(c in _VOWELS for c in w)]
    if len(vowel_words) >= 2:
        return False
    if len(words) >= 2 and vowel_words and max(len(w) for w in words) <= 20:
        return False

    return looks_like_random_latin_text(text, is_name=False)


def check_gibberish_message(
    message: str, rules: Dict[str, Any], *, has_message_field: bool = True
) -> Tuple[bool, str]:
    """乱码 Message 硬拦截：单条即可 reject，不写入飞书。"""
    if not is_gibberish_message(message, rules, has_message_field=has_message_field):
        return False, ""
    snippet = message_latin_core(message)[:40] or (message or "").strip()[:40]
    return True, f"gibberish_message({snippet})"


def check_gibberish_inquiry(
    message: str,
    rules: Dict[str, Any],
    *,
    body: str = "",
    fields_pre: dict | None = None,
) -> Tuple[bool, str]:
    """预过滤 / LLM 后统一乱码检测：无 Message 字段则跳过。"""
    if not inquiry_has_message_field(body, fields_pre):
        return False, ""
    return check_gibberish_message(message, rules, has_message_field=True)


def check_spam(name: str, email: str, message: str, rules: Dict[str, Any]) -> Tuple[bool, str]:
    """检查是否为垃圾线索（bot signals），返回 (is_spam, reason)"""
    spam_rules = rules.get("spam_rules", {})
    min_signals = spam_rules.get("min_bot_signals", 2)
    signals_count = 0
    reasons = []

    def has_random_chars(text: str, is_name: bool = False) -> bool:
        return looks_like_random_latin_text(text, is_name=is_name)

    # random_name
    if name:
        if has_random_chars(name, is_name=True):
            signals_count += 1
            reasons.append(f"random_name({name[:20]})")

    # random_email
    if email:
        local = email.split("@")[0] if "@" in email else email
        dots = local.count(".")
        if dots >= 3:
            signals_count += 1
            reasons.append(f"random_email({dots}dots)")
        elif len(local) >= 10 and has_random_chars(local):
            signals_count += 1
            reasons.append("random_email(random_chars)")

    # random_message
    if message:
        if has_random_chars(message):
            signals_count += 1
            reasons.append("random_message")

    # name+msg both random
    if name and message and has_random_chars(name, is_name=True) and has_random_chars(message):
        if "name+msg" not in str(reasons):
            signals_count += 1
            reasons.append("name+msg_random")

    is_spam = signals_count >= min_signals
    return (is_spam, f"spam({signals_count}/{min_signals}): {'+'.join(reasons)}") if is_spam else (False, "")


# ─── Placeholder / Test Submission Detection ─────────────────

def check_placeholder(name: str, email: str, phone: str, company: str) -> Tuple[bool, str]:
    """检测占位符/测试提交（John Smith + john@company.com + 1234567890 等）"""
    signals = 0
    reasons = []

    # Email placeholders
    email_lower = (email or "").lower().strip()
    placeholder_emails = ["test@", "john@company", "example.com", "sample@", "noreply@"]
    for p in placeholder_emails:
        if p in email_lower:
            signals += 1
            reasons.append(f"placeholder_email({email_lower})")
            break

    # Phone placeholders: sequential digits or all same digit
    phone_clean = re.sub(r'[^0-9]', '', phone or "")
    if phone_clean in ("1234567890", "123456789", "12345678", "1111111111", "0000000000", "9876543210"):
        signals += 1
        reasons.append(f"placeholder_phone({phone_clean})")

    # Name + Company combo: classic test patterns
    name_lower = (name or "").lower().strip()
    company_lower = (company or "").lower().strip()
    test_combos = [
        ("john smith", "acme"),
        ("john doe", ""),
        ("jane doe", ""),
        ("test user", ""),
    ]
    for tname, tcompany in test_combos:
        if tname in name_lower:
            if not tcompany or tcompany in company_lower:
                signals += 1
                reasons.append(f"placeholder_name({name_lower})")
                break

    # 2+ signals → placeholder
    if signals >= 2:
        return True, f"placeholder({' + '.join(reasons)})"
    return False, ""


# ─── Supplier / Vendor Outreach (selling TO us) ───────────────

_SUPPLIER_VENDOR_PITCH = re.compile(
    r"(?:"
    r"we\s+(?:produce|manufacture|supply|offer|speciali[sz]e\s+in)|"
    r"(?:our|my)\s+factory|"
    r"\d+\s+years?\s+(?:of\s+)?OEM\s+experience|"
    r"impaling\s+clips?|"
    r"galvanized\s+steel|"
    r"(?:low\s+)?trial\s+MOQ|"
    r"samples?\s+available"
    r")",
    re.IGNORECASE,
)
_SUPPLIER_ASKS_BUYER = re.compile(
    r"(?:"
    r"(?:your|the)\s+(?:purchasing|procurement|buying)\s+department|"
    r"share\s+(?:me\s+)?(?:the\s+)?(?:email\s+)?(?:address\s+)?of\s+your\s+"
    r"(?:purchasing|procurement|buying|sales)"
    r")",
    re.IGNORECASE,
)


def check_supplier_outreach(
    message: str,
    company: str = "",
    subject: str = "",
    raw_body: str = "",
    rules: Dict[str, Any] | None = None,
) -> Tuple[bool, str]:
    """硬拦截：供应商/厂商向我们推销原材料、配件或服务（非采购询盘）。"""
    parts = [message or "", company or "", subject or ""]
    if raw_body:
        parts.append(raw_body)
    text = " ".join(parts)
    if not text.strip():
        return False, ""

    has_pitch = bool(_SUPPLIER_VENDOR_PITCH.search(text))
    asks_buyer = bool(_SUPPLIER_ASKS_BUYER.search(text))
    if has_pitch and asks_buyer:
        return True, "supplier_outreach(vendor_pitch+asks_purchasing)"

    config = (rules or {}).get("supplier_outreach_patterns", {})
    if config.get("enabled", True):
        for pattern in config.get("patterns", []):
            if not pattern or str(pattern).startswith("_"):
                continue
            try:
                if re.search(str(pattern), text, re.IGNORECASE):
                    return True, f"supplier_outreach(pattern:{pattern[:40]})"
            except re.error:
                if str(pattern).lower() in text.lower():
                    return True, f"supplier_outreach(pattern:{pattern[:40]})"

    return False, ""


# ─── Semantic Spam Content Detection ─────────────────────────

_PROMO_ACTION = re.compile(
    r'\b(review|audit|optimi[sz]e|improve|suggest|analysis|launch|show you|rank|boost|grow|scale|increase|drive)\b',
    re.IGNORECASE,
)
_PROMO_TARGET = re.compile(
    r'\b(website|web site|site|webpage|landing page|pages|search|brand|visibility|online|google|traffic|leads|revenue|sales|ROI)\b',
    re.IGNORECASE,
)

def check_promotional_content(name: str, subject: str, message: str, company: str,
                              rules: Dict[str, Any], raw_body: str = "") -> Tuple[bool, str]:
    """检测推销类内容（合并原 spam_content + cold_outreach）

    两种检测机制，命中任一即返回信号：
    1. rules 中的 spam_content_patterns（关键词/正则列表）
    2. action + target 正则组合（cold outreach 模式）
    """
    parts = [name or '', subject or '', message or '', company or '']
    if not (message or '').strip() and raw_body:
        parts.append(raw_body)
    full_text = " ".join(parts).lower()

    if not full_text.strip():
        return False, ""

    # 机制 1: spam_content_patterns（配置驱动）
    config = rules.get("spam_content_patterns", {})
    if config.get("enabled", True):
        min_matches = config.get("min_matches", 1)
        matches = []
        for pattern in config.get("patterns", []):
            if not pattern or pattern.startswith("_"):
                continue
            pat_lower = pattern.lower()
            try:
                if re.search(pat_lower, full_text):
                    matches.append(pattern)
            except re.error:
                if pat_lower in full_text:
                    matches.append(pattern)
        if len(matches) >= min_matches:
            return True, f"promotional(pattern:{'+'.join(matches[:3])})"

    if _PROMO_ACTION.search(full_text) and _PROMO_TARGET.search(full_text):
        return True, "promotional(action+target)"

    return False, ""


# ─── Irrelevant Business Detection ───────────────────────────

def check_irrelevant_business(name: str, company: str, message: str,
                              rules: Dict[str, Any], raw_body: str = "") -> Tuple[bool, str]:
    """检查是否来自无关行业"""
    irrelevant_list = rules.get("irrelevant_business", [])
    parts = [name or '', company or '', message or '']
    if not (message or '').strip() and raw_body:
        parts.append(raw_body)
    full_text = " ".join(parts).lower()
    for keyword in irrelevant_list:
        if keyword and keyword.lower() in full_text:
            return True, f"irrelevant_business({keyword})"
    return False, ""


# ─── Inquiry Keyword Check ───────────────────────────────────

_INQUIRY_SUBJECT_STRONG = re.compile(
    r"\b(enquiry|inquiry|rfq|quote\s+request|quotation\s+request)\b",
    re.IGNORECASE,
)
_INQUIRY_SUBJECT_REQUEST_PRODUCT = re.compile(
    r"\brequest\b.*\b("
    r"furniture|furnishing|pod|pods|booth|booths|soundbox|acoustic|"
    r"quote|price|pricing|catalog|catalogue|brochure|silence|quiet"
    r")\b|\b("
    r"furniture|furnishing|pod|pods|booth|booths|soundbox|acoustic|quiet\s+pods?"
    r")\b.*\brequest\b",
    re.IGNORECASE,
)
_INQUIRY_SUBJECT_PRODUCT_ENQUIRY = re.compile(
    r"\b(quiet\s+pods?|meeting\s+pods?|office\s+pods?|phone\s+booths?|sound\s+booths?|"
    r"soundbox|acoustic\s+pods?)\b.*\b(enquiry|inquiry|request)\b|"
    r"\b(enquiry|inquiry|request)\b.*\b(quiet\s+pods?|meeting\s+pods?|office\s+pods?|"
    r"phone\s+booths?|soundbox|acoustic\s+pods?)\b",
    re.IGNORECASE,
)
_SEO_NON_INQUIRY_BLOCK = re.compile(
    r"\b("
    r"search\s+ranking|top\s+of\s+search|organic\s+traffic|boost.{0,24}traffic|"
    r"keyword\s+options?|seo\s|digital\s+marketing|web\s+design\s+service|"
    r"all-in-one\s+sales\s+platform|schedule\s+(a\s+)?(call|demo|meeting)|"
    r"book\s+(a\s+)?(call|demo|meeting)|free\s+(consultation|audit|strategy)\s+(call|session)|"
    r"put\s+your\s+banner\s+at\s+the\s+top|seen\s+first\s+and\s+chosen\s+first|"
    r"page\s+one\s+of\s+google|rank.*first\s+on.*google|"
    r"search\s+assessment|website.{0,20}review|enhancing.{0,30}search"
    r")\b",
    re.IGNORECASE,
)


def check_inquiry_keywords(name: str, message: str, company: str,
                           rules: Dict[str, Any], subject: str = "") -> Tuple[bool, str]:
    """检查是否包含至少一个询盘关键词，返回 (has_keyword, reason)

    subject 纳入检索：不少真实询盘只在主题行写 enquiry/request，正文尚未展开。
    """
    if rules.get("require_inquiry_keyword", True) is False:
        return True, ""

    keywords = [k.lower() for k in rules.get("inquiry_keywords", [])]
    product_keywords = []
    for kws in rules.get("product_categories", {}).values():
        product_keywords.extend([k.lower() for k in kws])
    all_kw = keywords + product_keywords

    full_text = f"{subject or ''} {name or ''} {message or ''} {company or ''}".lower()
    for kw in all_kw:
        if kw in full_text:
            return True, ""

    return False, "irrelevant: no inquiry/product keyword matched"


def should_force_inquiry_intent(subject: str, message: str, name: str = "",
                                company: str = "", rules: Dict[str, Any] | None = None,
                                body: str = "") -> bool:
    """LLM 判 non_inquiry 时的放行覆写：主题/正文有明确采购语义且无 SEO 硬特征。

    用于纠正「quiet pods enquiry」「Loose Furniture request」等被误判为推销的情况。
    """
    rules = rules or load_lead_rules()
    combined = " ".join([subject or "", message or "", name or "", company or "", body or ""]).strip()
    if not combined:
        return False

    plat, _ = check_platform_marketplace_notification("", subject, body or message)
    if plat:
        return False

    if _SEO_NON_INQUIRY_BLOCK.search(combined):
        return False

    subj = subject or ""
    if _INQUIRY_SUBJECT_STRONG.search(subj):
        return True
    if _INQUIRY_SUBJECT_REQUEST_PRODUCT.search(subj):
        return True
    if _INQUIRY_SUBJECT_PRODUCT_ENQUIRY.search(subj):
        return True

    has_kw, _ = check_inquiry_keywords(name, message, company, rules, subject=subject)
    if not has_kw:
        return False

    product_keywords: list[str] = []
    for kws in rules.get("product_categories", {}).values():
        product_keywords.extend(k.lower() for k in kws)
    text_lower = combined.lower()
    if any(pk in text_lower for pk in product_keywords):
        return True

    procurement_terms = (
        "enquiry", "inquiry", "quote", "quotation", "pricing", "moq",
        "catalog", "catalogue", "brochure", "distributor", "purchase",
        "interested in your", "send me a quote", "send me a catalog",
        "looking for", "rfq",
    )
    if any(term in text_lower for term in procurement_terms):
        return True
    if re.search(r"please\s+provide.{0,40}(quote|quotation|catalog|brochure|price|pricing|moq)", text_lower):
        return True
    return False


# ─── Marketing Email Detection ─────────────────────────────────

UNSUBSCRIBE_FOOTER = re.compile(
    r'(unsubscribe|manage\s+(?:your\s+)?(?:preferences|subscription)|utm_source=)',
    re.IGNORECASE,
)


_TEST_MESSAGE_TOKENS_DEFAULT = {
    "test", "testing", "hello", "hi", "hey", "abc", "asdf", "xxx", "sample",
    "demo", "foo", "bar", "baz", "dummy", "placeholder", "try", "check",
}


def is_test_like_message(message: str, rules: Dict[str, Any]) -> bool:
    """留言仅由测试/占位词组成（如 test test、hello hi）。"""
    cfg = rules.get("test_message", {})
    if cfg.get("enabled", True) is False:
        return False

    core = message_inquiry_body(message).strip().lower()
    if not core:
        return True

    tokens = re.findall(r"[a-z0-9]+", re.sub(r"\s+", " ", core))
    if not tokens:
        return False

    test_tokens = {str(x).lower() for x in cfg.get("tokens", _TEST_MESSAGE_TOKENS_DEFAULT)}
    return all(t in test_tokens for t in tokens)


def check_trivial_content(
    name: str,
    message: str,
    rules: Dict[str, Any] | None = None,
) -> Tuple[bool, str]:
    """检测明显测试/垃圾提交（name=test、纯数字留言、仅测试词留言）"""
    name_lower = (name or "").lower().strip()
    msg = (message or "").strip()

    if name_lower in ("test", "testing"):
        return True, f"trivial_submission(name={name_lower})"

    if is_pure_numeric_message(msg):
        core = message_inquiry_body(msg).strip()
        compact = re.sub(r"\s+", "", core)
        return True, f"trivial_submission(numeric_msg={compact[:20]})"

    if rules and is_test_like_message(msg, rules):
        core = message_inquiry_body(msg).strip()
        return True, f"trivial_submission(test_message={core[:30]})"

    return False, ""


_FORM_SPAM_HINTS_DEFAULT = [
    "quote", "quotation", "pricing", "price", "inquiry", "enquiry",
    "interested", "looking", "catalog", "brochure", "moq", "rfq",
    "need", "want", "help", "office", "booth", "pod", "soundbox",
    "acoustic", "soundproof", "buy", "order", "sample", "demo",
    "contact", "details", "info", "unit", "model", "silent", "cabin",
    "homepod", "vrt", "art", "sr", "vr", "partition", "meeting",
    "cost", "availability", "specification", "catalogue", "distributor",
    "partner", "dealer", "wholesale", "purchase", "modular",
    "采购", "报价", "静音", "舱", "咨询", "价格", "需要",
]

_form_spam_hints_cache: dict[int, re.Pattern[str]] = {}


def _form_spam_hint_tokens(rules: Dict[str, Any]) -> list[str]:
    """合并 form_spam.inquiry_hints 与 inquiry_keywords 中的单词 token。"""
    cfg = rules.get("form_spam", {})
    tokens = list(cfg.get("inquiry_hints") or _FORM_SPAM_HINTS_DEFAULT)
    if cfg.get("merge_inquiry_keywords", True):
        for kw in rules.get("inquiry_keywords", []):
            word = str(kw).strip()
            if not word or " " in word:
                continue
            if re.fullmatch(r"[\w]+", word, flags=re.UNICODE):
                tokens.append(word)
    # 小写去重，保留中文
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        key = t.lower() if t.isascii() else t
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _form_spam_inquiry_pattern(rules: Dict[str, Any]) -> re.Pattern[str]:
    cache_key = id(rules)
    cached = _form_spam_hints_cache.get(cache_key)
    if cached is not None:
        return cached
    parts = [re.escape(t) for t in _form_spam_hint_tokens(rules) if t]
    pattern = re.compile("|".join(parts), re.IGNORECASE) if parts else re.compile(r"$^")
    _form_spam_hints_cache[cache_key] = pattern
    return pattern


def _phone_is_weak(phone: str) -> bool:
    raw = (phone or "").strip()
    if not raw:
        return True
    if raw.upper() in ("N/A", "NA", "NONE", "-", "NULL", "UNKNOWN"):
        return True
    digits = re.sub(r"[^0-9]", "", raw)
    return len(digits) < 8


def is_single_word_low_intent_message(message: str, rules: Dict[str, Any]) -> bool:
    """单个拉丁单词留言且无询盘关键词（如 Majhar、Hofutbgh 类测试填表）。"""
    core = message_inquiry_body(message).strip()
    if not core or " " in core or "\n" in core:
        return False
    if not re.fullmatch(r"[A-Za-z]+", core):
        return False

    cfg = rules.get("form_spam", {})
    n = len(core)
    if n < int(cfg.get("min_word_length", 6)):
        return False
    if n > int(cfg.get("max_word_length", 24)):
        return False

    lower = core.lower()
    allowlist = {str(x).lower() for x in rules.get("short_message", {}).get("allowlist", [])}
    if lower in allowlist:
        return False
    if _form_spam_inquiry_pattern(rules).search(core):
        return False
    return True


def check_form_spam_submission(
    name: str,
    message: str,
    phone: str,
    company: str,
    rules: Dict[str, Any],
    *,
    has_message_field: bool = True,
) -> Tuple[bool, str]:
    """官网/表单垃圾提交：单词留言 + 无电话 + 无公司 → 硬拦截不入库。"""
    cfg = rules.get("form_spam", {})
    if cfg.get("enabled", True) is False:
        return False, ""
    if not has_message_field:
        return False, ""
    if not is_single_word_low_intent_message(message, rules):
        return False, ""
    if not _phone_is_weak(phone):
        return False, ""
    if (company or "").strip():
        return False, ""

    word = message_inquiry_body(message).strip()
    return True, f"form_spam_single_word({word})"


_SYSTEM_AUTO_PATTERNS = re.compile(
    r"系统自动|system.*automat|auto.*generat|automatically.*sent",
    re.IGNORECASE,
)
_DO_NOT_REPLY_PATTERNS = re.compile(
    r"请勿.*回复|请勿直接回复|do not reply|don.?t reply|请不要回复",
    re.IGNORECASE,
)
_SYSTEM_SENDER_PATTERNS = re.compile(
    r"(?:noreply|no-?reply|do-?not-?reply|notifications|automated|system)@",
    re.IGNORECASE,
)


def check_system_notification(from_addr: str, raw_body: str) -> Tuple[bool, str]:
    """硬拦截：系统自动通知邮件

    命中条件（满足任一）：
    1. 发件人含 noreply/no-reply/notifications/automated/system
    2. 正文同时出现「系统自动」类 + 「请勿回复」类文本
    """
    if from_addr and _SYSTEM_SENDER_PATTERNS.search(from_addr):
        return True, f"system_notification(sender={from_addr})"

    if raw_body:
        has_auto = _SYSTEM_AUTO_PATTERNS.search(raw_body)
        has_no_reply = _DO_NOT_REPLY_PATTERNS.search(raw_body)
        if has_auto and has_no_reply:
            return True, "system_notification(auto+no_reply_in_body)"

    return False, ""


def check_marketing_header(has_unsubscribe_header: bool) -> Tuple[bool, str]:
    """硬拦截：List-Unsubscribe header 存在 = 确定性营销邮件"""
    if has_unsubscribe_header:
        return True, "marketing_header(List-Unsubscribe)"
    return False, ""


def check_marketing_footer(raw_body: str) -> Tuple[bool, str]:
    """信号：正文包含 unsubscribe/manage preferences 等退订文本"""
    if raw_body and UNSUBSCRIBE_FOOTER.search(raw_body):
        return True, "marketing_footer(unsubscribe_in_body)"
    return False, ""
