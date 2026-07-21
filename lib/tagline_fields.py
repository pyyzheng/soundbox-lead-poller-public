"""Parse tag-line suffix in Enquiry details and map to Feishu field values."""

from __future__ import annotations

import re

FIELD_COUNTRY = "Country（国家）"
FIELD_SUB_CHANNEL = "Channel segmentation (细分渠道)"
FIELD_PRODUCT_CAT = "Product Categories（产品大类）"
FIELD_PRODUCT_MODEL = "Product model（具体型号）"
FIELD_CUSTOMER_NAME = "Customer Name（客户名称）"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_PHONE = "Phone（客户电话）"
FIELD_WECHAT = "Wechat（微信）"
FIELD_ALI_ID = "阿里ID"
FIELD_ENQUIRY = "Enquiry details（询盘内容）"
FIELD_CHANNELS = "Channels（渠道）"

from assignment_fields import (  # noqa: E402
    INVALID_CHANNEL_VALUES,
    INVALID_SUB_CHANNEL_VALUES,
    SUB_CHANNEL_TO_CHANNEL as _SHARED_SUB_CHANNEL_TO_CHANNEL,
    heal_invalid_sub_channel,
    is_invalid_sub_channel,
)

CATEGORY_TO_FEISHU = {
    "静音舱": "Silence Booth 静音舱",
    "家居舱": "Homepod 家居舱",
    "声学产品": "Acoustic products 声学产品",
}

SUB_CHANNEL_TO_CHANNEL = dict(_SHARED_SUB_CHANNEL_TO_CHANNEL)

PRODUCT_KEYWORDS = {
    "吸音板": "声学产品",
    "隔音": "声学产品",
    "声学": "声学产品",
    "acoustic": "声学产品",
    "soundproof": "声学产品",
    "静音舱": "静音舱",
    "phone booth": "静音舱",
    "office pod": "静音舱",
    "soundbox": "静音舱",
}


def extract_tag_line(content: str) -> str | None:
    if not content:
        return None
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    if not lines:
        return None
    return lines[-1]


def normalize_tag_segments(tag: str) -> list[str]:
    """Split tag line on one or more dashes; handles 巴西--Facebook--家居舱."""
    return [part.strip() for part in re.split(r"-+", tag.strip()) if part.strip()]


def parse_tag_line(tag: str) -> dict[str, str]:
    segments = normalize_tag_segments(tag)
    if len(segments) >= 4:
        country, sub_channel, category, model = segments[:4]
    elif len(segments) == 3:
        country, sub_channel, category = segments
        model = ""
    else:
        return {}
    return {
        "country": country,
        "sub_channel": sub_channel,
        "product_category": category,
        "product_model": model,
    }


def parse_alibaba_tail(line: str) -> dict[str, str]:
    """Parse trailing lines like '阿里1 菲律宾' or '阿里1 加拿大'."""
    match = re.match(r"^(阿里[12]|1688|中文官网)\s+(.+)$", line.strip())
    if not match:
        return {}
    sub_channel = match.group(1)
    country = match.group(2).strip()
    return {"sub_channel": sub_channel, "country": country}


def infer_product_category(content: str) -> str:
    lower = content.lower()
    for keyword, category in PRODUCT_KEYWORDS.items():
        if keyword.lower() in lower:
            return category
    return ""


def parse_inquiry_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    patterns = {
        "email": r"(?:Email|email|邮箱)[：:]\s*(.+?)(?:\n|$)",
        "phone": r"(?:Telephone Number|Phone number|Phone|Whatsapp|WhatsApp|电话)[：:]\s*(.+?)(?:\n|$)",
        "company": r"(?:Company|公司)[：:]\s*(.+?)(?:\n|$)",
        "ali_id": r"(?:ID|阿里ID)[：:]\s*(.+?)(?:\n|$)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                fields[key] = value

    for pattern in (
        r"(?:Full name|name|姓名|Name)[：:]\s*(.+?)(?:\n|$)",
        r"Name:\s*(.+?)(?:\n|$)",
    ):
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            continue
        name = match.group(1).strip()
        if name and not name.lower().startswith("email"):
            fields["name"] = name
            break
    return fields


VALID_TAG_CATEGORIES = frozenset({"静音舱", "家居舱", "声学产品"})


def is_valid_tag_line(tag: str) -> bool:
    """真标签行形如 美国-谷歌1-静音舱-VRT；表单留言含 '-' 不能误判。"""
    segments = normalize_tag_segments(tag)
    if len(segments) < 3:
        return False
    country = segments[0]
    sub_channel = segments[1] if len(segments) > 1 else ""
    category = segments[2] if len(segments) > 2 else ""
    if not country or country in {"Unknown", "无法识别"}:
        return False
    # 表单末行如 "Message: Hi - I am interested ... (2-3)." 会被 '-' 拆成伪标签
    lowered = tag.lower()
    if ":" in country or country.lower().startswith(("message", "name", "email", "phone", "inquiry")):
        return False
    if any(marker in lowered for marker in ("message:", "e-mail address:", "telephone number:")):
        return False
    if sub_channel not in SUB_CHANNEL_TO_CHANNEL:
        return False
    if sub_channel in {"Messenger", "Facebook-Messenger"}:
        return False
    if category not in VALID_TAG_CATEGORIES and category != "无法识别":
        return False
    if category in {"Messenger", "acoustic pod"}:
        return False
    return True


def feishu_product_category(category: str) -> str:
    return CATEGORY_TO_FEISHU.get(category, category)


def build_feishu_fields_from_content(
    content: str,
    *,
    channels: str = "",
    gmail_msg_id: str = "",
    fb_leadgen: str = "",
    email_from: str = "",
    email_subject: str = "",
    rules: dict | None = None,
) -> dict[str, str]:
    """Build structured Feishu fields from enquiry body + trailing tag line."""
    updates: dict[str, str] = {}
    tag = extract_tag_line(content)
    if tag and is_valid_tag_line(tag):
        parsed = parse_tag_line(tag)
        if parsed.get("country"):
            updates[FIELD_COUNTRY] = parsed["country"]
        if parsed.get("sub_channel"):
            updates[FIELD_SUB_CHANNEL] = parsed["sub_channel"]
        if parsed.get("product_category"):
            updates[FIELD_PRODUCT_CAT] = feishu_product_category(parsed["product_category"])
        if parsed.get("product_model"):
            updates[FIELD_PRODUCT_MODEL] = parsed["product_model"]
    elif tag:
        alibaba = parse_alibaba_tail(tag)
        if alibaba.get("country"):
            updates[FIELD_COUNTRY] = alibaba["country"]
        if alibaba.get("sub_channel"):
            updates[FIELD_SUB_CHANNEL] = alibaba["sub_channel"]
            updates[FIELD_CHANNELS] = SUB_CHANNEL_TO_CHANNEL.get(alibaba["sub_channel"], "阿里国际站")
        else:
            # 标签行存在但无效：仍尝试抽取细分渠道（如 美国-谷歌1-静音舱-无法识别）
            parsed = parse_tag_line(tag)
            sub = (parsed.get("sub_channel") or "").strip()
            if sub and not is_invalid_sub_channel(sub) and sub in SUB_CHANNEL_TO_CHANNEL:
                updates[FIELD_SUB_CHANNEL] = sub
                if parsed.get("country") and parsed["country"] not in {"Unknown", "无法识别"}:
                    updates[FIELD_COUNTRY] = parsed["country"]
                if parsed.get("product_category"):
                    updates[FIELD_PRODUCT_CAT] = feishu_product_category(parsed["product_category"])
                if parsed.get("product_model") and parsed["product_model"] != "无法识别":
                    updates[FIELD_PRODUCT_MODEL] = parsed["product_model"]

    inquiry = parse_inquiry_fields(content)
    if inquiry.get("name"):
        updates[FIELD_CUSTOMER_NAME] = inquiry["name"]
    if inquiry.get("email"):
        updates[FIELD_EMAIL] = inquiry["email"]
    if inquiry.get("phone"):
        updates[FIELD_PHONE] = inquiry["phone"]
    if inquiry.get("ali_id"):
        updates[FIELD_ALI_ID] = inquiry["ali_id"]

    if not updates.get(FIELD_PRODUCT_CAT):
        inferred = infer_product_category(content)
        if inferred:
            updates[FIELD_PRODUCT_CAT] = feishu_product_category(inferred)

    sub_channel = updates.get(FIELD_SUB_CHANNEL, "")
    if sub_channel and not updates.get(FIELD_CHANNELS):
        updates[FIELD_CHANNELS] = SUB_CHANNEL_TO_CHANNEL.get(sub_channel, sub_channel)

    if is_invalid_sub_channel(updates.get(FIELD_SUB_CHANNEL, "")):
        healed_sub = heal_invalid_sub_channel(
            updates.get(FIELD_SUB_CHANNEL, ""),
            enquiry=content,
            channels=channels,
            gmail_msg_id=gmail_msg_id,
            fb_leadgen=fb_leadgen,
            email_from=email_from,
            email_subject=email_subject,
            rules=rules,
        )
        if healed_sub:
            updates[FIELD_SUB_CHANNEL] = healed_sub
            if not updates.get(FIELD_CHANNELS) or updates.get(FIELD_CHANNELS) in INVALID_CHANNEL_VALUES:
                updates[FIELD_CHANNELS] = SUB_CHANNEL_TO_CHANNEL.get(healed_sub, healed_sub)

    # Dup Formula Ready 要求 Email/Phone/Wechat/阿里ID 四字段都非空。
    updates.setdefault(FIELD_WECHAT, "N/A")
    if not updates.get(FIELD_ALI_ID):
        updates[FIELD_ALI_ID] = "N/A"
    if not updates.get(FIELD_PHONE):
        updates[FIELD_PHONE] = "N/A"
    if not updates.get(FIELD_EMAIL):
        updates[FIELD_EMAIL] = "N/A"
    return updates


def filter_missing_fields(existing: dict, candidate: dict) -> dict:
    """Keep only candidate fields that are empty/missing on the record."""
    from assignment_fields import get_field
    from feishu_utils import extract_text

    invalid_contact = {"", "N/A", "无匹配类别"}
    missing: dict[str, str] = {}
    for field_name, value in candidate.items():
        if not value:
            continue
        if field_name == FIELD_SUB_CHANNEL:
            current_text = extract_text(get_field(existing, FIELD_SUB_CHANNEL, "")).strip()
            if current_text in INVALID_SUB_CHANNEL_VALUES or current_text not in SUB_CHANNEL_TO_CHANNEL:
                missing[field_name] = value
                continue
        elif field_name == FIELD_CHANNELS:
            current_text = extract_text(get_field(existing, FIELD_CHANNELS, "")).strip()
            if current_text in INVALID_CHANNEL_VALUES:
                missing[field_name] = value
            continue
        else:
            current = existing.get(field_name, "")
            if isinstance(current, list):
                current = (
                    current[0].get("text", "")
                    if current and isinstance(current[0], dict)
                    else ""
                )
            current_text = str(current or "").strip()

        if field_name in {FIELD_PHONE, FIELD_WECHAT, FIELD_ALI_ID}:
            if current_text in invalid_contact:
                missing[field_name] = value
            continue
        if not current_text:
            missing[field_name] = value
    return missing
