"""Product model（具体型号）双语选项：内部短名 ↔ 主表写入名。

推断 / 询盘标签 / 代理规则表仍用中文短名或 SR/VR 等代号；写入线索总池时用 to_write_product_model。
"""

from __future__ import annotations

import re

# 中文或元数据短名 → 主表双语选项名（代号 SR/VR/VRT-M 等不在此表，写入时原样保留）
PRODUCT_MODEL_SHORT_TO_WRITE: dict[str, str] = {
    "尖顶": "Pointed top（尖顶）",
    "平顶": "Flat top（平顶）",
    "全系列": "Full range（全系列）",
    "无法识别": "Unrecognized（无法识别）",
    "无可用选项": "Unrecognized（无法识别）",
    "No options available": "Unrecognized（无法识别）",
    "No options available（无可用选项）": "Unrecognized（无法识别）",
    "隔音门": "Soundproof door（隔音门）",
    "吸音板": "Acoustic panel（吸音板）",
    "扩散体": "Diffuser（扩散体）",
    "家庭影院": "Home theater（家庭影院）",
    "阻尼地板": "Damping floor（阻尼地板）",
    "减震垫": "Shock pad（减震垫）",
    "静音脚垫": "Silent foot pad（静音脚垫）",
    "家用窗帘": "Home curtain（家用窗帘）",
    "工业窗帘": "Industrial curtain（工业窗帘）",
    "隔音垫": "Soundproof mat（隔音垫）",
    "隔声板": "Sound barrier panel（隔声板）",
    "声学画": "Acoustic painting（声学画）",
    "隔声涂层": "Sound insulation coating（隔声涂层）",
    "隔音毡": "Soundproof felt（隔音毡）",
    "声学屏障": "Acoustic barrier（声学屏障）",
    "声学处理": "Acoustic treatment（声学处理）",
    "隔音处理": "Soundproofing treatment（隔音处理）",
}

_WRITE_TO_SHORT: dict[str, str] = {v: k for k, v in PRODUCT_MODEL_SHORT_TO_WRITE.items()}
# 两个 option id 都映射到同一双语名
_WRITE_TO_SHORT["No options available（无可用选项）"] = "无法识别"

_PAREN_RE = re.compile(r"（([^）]+)）")


def normalize_product_model_short(model: str) -> str:
    """主表双语名 / 历史短名 → 内部短名（规则匹配、标签解析）。"""
    m = (model or "").strip()
    if not m:
        return ""
    if m in PRODUCT_MODEL_SHORT_TO_WRITE:
        return m
    if m in _WRITE_TO_SHORT:
        return _WRITE_TO_SHORT[m]
    match = _PAREN_RE.search(m)
    if match:
        inner = match.group(1).strip()
        if inner in PRODUCT_MODEL_SHORT_TO_WRITE or inner in _WRITE_TO_SHORT.values():
            return inner
    return m


def to_write_product_model(model: str) -> str:
    """任意型号别名 → 主表双语写入名；代号无法映射时原样返回。"""
    m = (model or "").strip()
    if not m:
        return ""
    if m in _WRITE_TO_SHORT or m in PRODUCT_MODEL_SHORT_TO_WRITE.values():
        return m if m in PRODUCT_MODEL_SHORT_TO_WRITE.values() else PRODUCT_MODEL_SHORT_TO_WRITE.get(
            _WRITE_TO_SHORT.get(m, ""), m
        )
    short = normalize_product_model_short(m)
    return PRODUCT_MODEL_SHORT_TO_WRITE.get(short, m)


INVALID_PRODUCT_MODEL_VALUES = frozenset({
    "",
    "无法识别",
    "Unrecognized",
    "Unrecognized（无法识别）",
    "No options available（无可用选项）",
    "N/A",
})
