"""Facebook 线索产品大类/型号推断（与 facebook-lead-poller 共用）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lead_fallback_parser import identify_product_model

log = logging.getLogger("facebook-product")

SR_COUNTRIES = {"阿联酋", "香港", "印尼", "日本", "韩国", "马来西亚", "菲律宾", "卡塔尔", "沙特", "越南"}
# 欧美澳加 + 拉美/美洲：默认静音舱 VRT（与美加产品线一致）
VRT_COUNTRIES = {
    "澳大利亚", "新西兰", "德国", "法国", "意大利", "西班牙", "英国",
    "荷兰", "比利时", "瑞士", "奥地利", "瑞典", "挪威", "丹麦", "芬兰",
    "波兰", "捷克", "葡萄牙", "爱尔兰", "希腊",
    "美国", "加拿大",
    # 拉丁美洲 / 中南美洲
    "墨西哥", "秘鲁", "智利", "哥伦比亚", "巴西", "阿根廷", "乌拉圭",
    "哥斯达黎加", "巴拿马", "厄瓜多尔", "玻利维亚", "巴拉圭", "委内瑞拉",
    "危地马拉", "洪都拉斯", "萨尔瓦多", "尼加拉瓜", "古巴", "多米尼加",
    "波多黎各",
    # 东欧等欧洲延伸（Facebook 广告常见，同走 VRT）
    "罗马尼亚", "乌克兰", "克罗地亚", "斯洛文尼亚", "立陶宛", "摩尔多瓦",
    "阿尔巴尼亚", "科索沃", "马耳他", "匈牙利", "塞尔维亚",
}

_BOOTH_FORM_MARKERS = (
    "booth",
    "pod",
    "is_this_booth",
    "how_many_people",
    "para_cuántas",
    "para_cuantas",
    "dónde_planeas",
    "donde_planeas",
    "meeting pod",
    "phone booth",
    "soundbox",
)

_STANDARD_FIELDS = {
    "full_name",
    "email",
    "phone_number",
    "phone_number_verified",
    "country",
    "country_code",
    "company_name",
    "work_email",
    "business_email",
    "message",
    "message(project_type)",
}

_RULES_CACHE: dict | None = None


def determine_product(country: str) -> tuple[str, str]:
    """根据国家确定产品大类和型号。返回 (产品大类, 具体型号)。"""
    if not country:
        return ("无法识别", "无法识别")
    if country in SR_COUNTRIES:
        return ("静音舱", "SR")
    if country in VRT_COUNTRIES:
        return ("静音舱", "VRT")
    return ("无法识别", "无法识别")


def looks_like_booth_form(fields: dict, message: str = "") -> bool:
    """表单题干/答案含 booth/pod 容量问题时，视为静音舱线索。"""
    blob_parts = [message or ""]
    for key, val in (fields or {}).items():
        blob_parts.append(str(key))
        blob_parts.append(str(val))
    blob = " ".join(blob_parts).lower()
    return any(marker in blob for marker in _BOOTH_FORM_MARKERS)


def _custom_answers_blob(fields: dict) -> str:
    parts = []
    for key, val in (fields or {}).items():
        if key.lower() not in _STANDARD_FIELDS and val:
            parts.append(f"{key}: {val}")
    return "; ".join(parts)


def load_lead_rules(rules_path: Path | None = None) -> dict:
    global _RULES_CACHE
    if rules_path is None and _RULES_CACHE is not None:
        return _RULES_CACHE
    path = rules_path or (Path(__file__).resolve().parent.parent / "lead-rules.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("lead-rules.json 读取失败: %s", exc)
        return {}
    if rules_path is None:
        _RULES_CACHE = data
    return data


def refine_facebook_product(
    country: str,
    fields: dict,
    message: str,
    rules: dict | None = None,
) -> tuple[str, str]:
    """国家默认 + booth 表单兜底 + 正文容量/型号关键词。"""
    product_cat, product_model = determine_product(country)
    booth = looks_like_booth_form(fields, message)

    if product_cat == "无法识别" and booth:
        product_cat = "静音舱"
        if product_model == "无法识别":
            product_model = "VRT"

    rules = rules if rules is not None else load_lead_rules()
    if not rules:
        return product_cat, product_model

    extra = _custom_answers_blob(fields)
    blob = "\n".join(p for p in [message, extra] if p)
    if not blob.strip():
        return product_cat, product_model

    if product_model in {"无法识别", ""}:
        if country in SR_COUNTRIES:
            default_series = "SR"
        else:
            default_series = "VRT"
    elif "-" in product_model:
        default_series = product_model.split("-", 1)[0]
    else:
        default_series = product_model

    refined = identify_product_model(
        blob,
        rules,
        sub_channel="Facebook",
        default_series=default_series,
    )
    if refined and refined != "无法识别":
        product_model = refined
        if product_cat == "无法识别":
            product_cat = "静音舱"

    return product_cat, product_model
