#!/usr/bin/env python3
"""
cloud-tagline-field-fix.py — 从 Enquiry details 标签行回填结构化字段

飞书多维表格曾依赖 AI 字段捷径，把询盘末尾标签行（如 秘鲁-Facebook-静音舱-VRT）
解析写入 Country / 细分渠道 / 产品字段。捷径停止服务(800004402) 后，这些字段会
一直为空，导致分配公式阻塞在「分配中/阻塞」。

本脚本扫描最近线索，从标签行和正文直接回填缺失字段，不依赖飞书 AI 捷径。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)
from assignment_fields import FIELD_LEAD_ID, get_field  # noqa: E402
from tagline_fields import (  # noqa: E402
    FIELD_CHANNELS,
    FIELD_COUNTRY,
    FIELD_EMAIL,
    FIELD_ENQUIRY,
    FIELD_PHONE,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_SUB_CHANNEL,
    build_feishu_fields_from_content,
    extract_tag_line,
    filter_missing_fields,
    is_valid_tag_line,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tagline-fix")

RECENT_HOURS = int(os.environ.get("TAGLINE_FIX_RECENT_HOURS", "72"))
MAX_RECORDS = int(os.environ.get("TAGLINE_FIX_MAX_RECORDS", "500"))
DRY_RUN = os.environ.get("TAGLINE_FIX_DRY_RUN", "false").lower() == "true"
FIELD_ENTRY_TIME = "Entry Time（录入时间）"

SCAN_FIELDS = [
    FIELD_ENTRY_TIME,
    FIELD_ENQUIRY,
    FIELD_COUNTRY,
    FIELD_SUB_CHANNEL,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_EMAIL,
    FIELD_PHONE,
    FIELD_CHANNELS,
    "Customer Name（客户名称）",
    "Wechat（微信）",
    "阿里ID",
    FIELD_LEAD_ID,
]


def _search_records(token: str, body: dict, page_size: int = 100) -> list[dict]:
    all_items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/search?page_size={page_size}"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json=body, max_retries=3)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书查询失败: {data}")

        body_data = data.get("data", {})
        all_items.extend(body_data.get("items", []))
        if not body_data.get("has_more"):
            break
        page_token = body_data.get("page_token", "")
        if not page_token:
            break
    return all_items


def _update_record(token: str, record_id: str, fields: dict) -> bool:
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records/{record_id}"
    )
    resp = feishu_api("PUT", url, token=token, json={"fields": fields}, max_retries=3)
    data = resp.json()
    if data.get("code") != 0:
        log.error("更新失败 record=%s: %s", record_id, data.get("msg", data))
        return False
    return True


def _copy_from_sibling(records: list[dict]) -> dict[str, dict[str, str]]:
    """For Messenger duplicates without tag line, copy fields from same-email Lead Ad."""
    by_email: dict[str, dict[str, str]] = {}
    for item in records:
        fields = item.get("fields", {})
        content = extract_text(fields.get(FIELD_ENQUIRY, ""))
        email = extract_text(fields.get(FIELD_EMAIL, "")).lower().strip()
        if not email:
            email = build_feishu_fields_from_content(content).get(FIELD_EMAIL, "").lower().strip()
        if not email or not extract_tag_line(content) or not is_valid_tag_line(extract_tag_line(content) or ""):
            continue
        candidate = build_feishu_fields_from_content(content)
        if candidate.get(FIELD_COUNTRY):
            by_email[email] = candidate
    return by_email


def run() -> int:
    token = get_feishu_token()
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).timestamp() * 1000)

    records = _search_records(
        token,
        {
            "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
            "field_names": SCAN_FIELDS,
        },
    )
    records = records[:MAX_RECORDS]
    sibling_map = _copy_from_sibling(records)

    fixed = 0
    skipped = 0
    for item in records:
        record_id = item.get("record_id", "")
        fields = item.get("fields", {})
        entry_ms = fields.get(FIELD_ENTRY_TIME, 0) or 0
        if entry_ms and entry_ms < cutoff_ms:
            continue

        content = extract_text(fields.get(FIELD_ENQUIRY, ""))
        candidate = build_feishu_fields_from_content(content)
        tag = extract_tag_line(content) or ""
        email = extract_text(fields.get(FIELD_EMAIL, "")).lower().strip()
        if not email:
            email = candidate.get(FIELD_EMAIL, "").lower().strip()
        if email and email in sibling_map:
            sibling = sibling_map[email]
            merged = {**sibling, **candidate}
            if not is_valid_tag_line(tag):
                for key in (FIELD_COUNTRY, FIELD_SUB_CHANNEL, FIELD_PRODUCT_CAT, FIELD_PRODUCT_MODEL):
                    if sibling.get(key):
                        merged[key] = sibling[key]
            candidate = merged

        updates = filter_missing_fields(fields, candidate)
        if not updates:
            skipped += 1
            continue

        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        log.info(
            "回填 %s record=%s fields=%s",
            lead_id or record_id,
            record_id,
            list(updates.keys()),
        )
        if DRY_RUN:
            for key, value in updates.items():
                log.info("  %s = %s", key, value)
            fixed += 1
            continue

        if _update_record(token, record_id, updates):
            fixed += 1

    log.info("完成: 回填=%s 跳过=%s dry_run=%s", fixed, skipped, DRY_RUN)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
