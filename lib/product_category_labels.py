"""Product Categories（产品大类）双语选项：内部短名 ↔ 主表写入名。"""

from __future__ import annotations

import re

PRODUCT_CATEGORY_SHORT_TO_WRITE: dict[str, str] = {
    "静音舱": "Silence Booth 静音舱",
    "家居舱": "Homepod 家居舱",
    "声学产品": "Acoustic products 声学产品",
    "无法识别": "Unrecognized（无法识别）",
}

_WRITE_TO_SHORT: dict[str, str] = {v: k for k, v in PRODUCT_CATEGORY_SHORT_TO_WRITE.items()}
_PAREN_RE = re.compile(r"（([^）]+)）")


def normalize_product_category_short(category: str) -> str:
    c = (category or "").strip()
    if not c:
        return ""
    if c in PRODUCT_CATEGORY_SHORT_TO_WRITE:
        return c
    if c in _WRITE_TO_SHORT:
        return _WRITE_TO_SHORT[c]
    m = _PAREN_RE.search(c)
    if m:
        inner = m.group(1).strip()
        if inner in PRODUCT_CATEGORY_SHORT_TO_WRITE:
            return inner
    return c


def to_write_product_category(category: str) -> str:
    c = (category or "").strip()
    if not c:
        return ""
    if c in _WRITE_TO_SHORT or c in PRODUCT_CATEGORY_SHORT_TO_WRITE.values():
        return c if c in PRODUCT_CATEGORY_SHORT_TO_WRITE.values() else PRODUCT_CATEGORY_SHORT_TO_WRITE.get(
            _WRITE_TO_SHORT.get(c, ""), c
        )
    short = normalize_product_category_short(c)
    return PRODUCT_CATEGORY_SHORT_TO_WRITE.get(short, c)


INVALID_PRODUCT_CATEGORY_VALUES = frozenset({
    "",
    "无法识别",
    "Unrecognized",
    "Unrecognized（无法识别）",
    "无可用选项",
    "No options available",
    "No options available（无可用选项）",
    "N/A",
})
