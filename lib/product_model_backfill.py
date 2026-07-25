"""历史线索产品大类/型号回填推断（只覆盖无效值）。"""

from __future__ import annotations

from facebook_product import (
    SR_COUNTRIES,
    VRT_COUNTRIES,
    determine_product,
    looks_like_booth_form,
    refine_facebook_product,
)
from lead_fallback_parser import (
    default_series_for_sub_channel,
    extract_series_model_keyword,
    identify_product_category,
    identify_product_model,
)
from tagline_fields import (
    CATEGORY_TO_FEISHU,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    feishu_product_category,
    infer_product_category,
)

# 可被回填覆盖的无效取值；其它值视为人工/已确认，不改
INVALID_PRODUCT_VALUES = frozenset(
    {
        "",
        "无法识别",
        "无可用选项",
        "No options available",
        "N/A",
        "n/a",
    }
)
_BOOTH_SERIES = frozenset({"SR", "VR", "VRT", "ART"})


def is_invalid_product_value(value: str) -> bool:
    return (value or "").strip() in INVALID_PRODUCT_VALUES


def _cn_category(feishu_or_cn: str) -> str:
    """Silence Booth 静音舱 / 静音舱 → 静音舱。"""
    text = (feishu_or_cn or "").strip()
    if not text or is_invalid_product_value(text):
        return ""
    for cn, feishu in CATEGORY_TO_FEISHU.items():
        if text == cn or text == feishu or cn in text:
            return cn
    return text


def infer_product_updates(
    *,
    enquiry: str,
    country: str = "",
    channels: str = "",
    sub_channel: str = "",
    current_category: str = "",
    current_model: str = "",
    rules: dict | None = None,
) -> dict[str, str]:
    """根据询盘/国家/渠道推断可写回的产品字段。

    仅返回当前值为无效时的更新；已有明确型号/大类不会出现在结果里。
    正文只要出现 VRT/SR/VR/ART（含中文旁）即可回填型号。
    """
    rules = rules or {}
    enquiry = enquiry or ""
    country = (country or "").strip()
    channels = (channels or "").strip()
    sub_channel = (sub_channel or "").strip()
    cur_cat = (current_category or "").strip()
    cur_model = (current_model or "").strip()

    need_cat = is_invalid_product_value(cur_cat)
    need_model = is_invalid_product_value(cur_model)
    if not need_cat and not need_model:
        return {}

    inferred_cat_cn = ""
    inferred_model = ""

    # 最高优先：正文系列裸词（不区分渠道/大类）
    keyword_model = extract_series_model_keyword(enquiry) if need_model else ""

    is_facebook = channels == "Facebook" or sub_channel == "Facebook"
    if is_facebook:
        inferred_cat_cn, inferred_model = refine_facebook_product(
            country,
            {},
            enquiry,
            rules=rules,
        )
    else:
        country_cat, country_model = determine_product(country)
        inferred_cat_cn = identify_product_category(enquiry, rules) or ""
        if not inferred_cat_cn:
            inferred_cat_cn = infer_product_category(enquiry) or ""
        if not inferred_cat_cn and country_cat != "无法识别":
            inferred_cat_cn = country_cat
        if not inferred_cat_cn and looks_like_booth_form({}, enquiry):
            inferred_cat_cn = "静音舱"

        effective_cat = inferred_cat_cn or _cn_category(cur_cat)
        # 当前大类已是家居/声学时，不以推断大类覆盖，避免误套 VRT
        cur_cn = _cn_category(cur_cat)
        if cur_cn in {"家居舱", "声学产品"}:
            effective_cat = cur_cn

        default_series = ""
        if effective_cat in {"", "静音舱"}:
            default_series = (
                default_series_for_sub_channel(sub_channel)
                or (country_model if country_model != "无法识别" else "")
                or ("SR" if country in SR_COUNTRIES else "")
                or ("VRT" if country in VRT_COUNTRIES else "")
            )

        inferred_model = identify_product_model(
            enquiry,
            rules,
            sub_channel=sub_channel,
            default_series=default_series,
        )

        if effective_cat in {"家居舱", "声学产品"}:
            # 家居/声学：无系列裸词时清空国家/渠道默认型号
            if not keyword_model:
                series = (inferred_model or "").split("-", 1)[0]
                if series in _BOOTH_SERIES:
                    inferred_model = ""
            # 大类已明确时，不改大类推断覆盖
            if not need_cat:
                inferred_cat_cn = ""
        elif inferred_model in {"", "无法识别"} and country_model not in {"", "无法识别"}:
            if effective_cat in {"", "静音舱"}:
                inferred_model = country_model
                if not inferred_cat_cn:
                    inferred_cat_cn = "静音舱"

    if keyword_model:
        inferred_model = keyword_model

    updates: dict[str, str] = {}
    if need_cat and inferred_cat_cn and not is_invalid_product_value(inferred_cat_cn):
        updates[FIELD_PRODUCT_CAT] = feishu_product_category(inferred_cat_cn)

    if need_model and inferred_model and not is_invalid_product_value(inferred_model):
        updates[FIELD_PRODUCT_MODEL] = inferred_model
        if need_cat and FIELD_PRODUCT_CAT not in updates:
            if inferred_model.split("-", 1)[0] in _BOOTH_SERIES:
                updates[FIELD_PRODUCT_CAT] = feishu_product_category("静音舱")

    return updates
