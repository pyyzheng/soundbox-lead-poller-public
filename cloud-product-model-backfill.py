#!/usr/bin/env python3
"""
cloud-product-model-backfill.py — 回填历史「无法识别」产品大类/型号

用与入库相同的规则（国家默认 + 裸词型号 + 容量语义）扫描线索：
- 只改 Product Categories / Product model 当前为 无法识别/空/无可用选项 的记录
- 不覆盖已有明确型号或人工选项
- 默认 dry-run；设 PRODUCT_BACKFILL_DRY_RUN=false 才写入

用法:
  PRODUCT_BACKFILL_DRY_RUN=true python3 cloud-product-model-backfill.py
  PRODUCT_BACKFILL_SINCE=2026-07-01 PRODUCT_BACKFILL_DRY_RUN=false python3 cloud-product-model-backfill.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from assignment_fields import FIELD_LEAD_ID, get_field  # noqa: E402
from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)
from product_model_backfill import infer_product_updates  # noqa: E402
from tagline_fields import (  # noqa: E402
    FIELD_CHANNELS,
    FIELD_COUNTRY,
    FIELD_ENQUIRY,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_SUB_CHANNEL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("product-backfill")

FIELD_ENTRY_TIME = "Entry Time（录入时间）"
RULES_PATH = Path(__file__).parent / "lead-rules.json"
DRY_RUN = os.environ.get("PRODUCT_BACKFILL_DRY_RUN", "true").lower() != "false"
SINCE = os.environ.get("PRODUCT_BACKFILL_SINCE", "2026-07-01")
MAX_RECORDS = int(os.environ.get("PRODUCT_BACKFILL_MAX_RECORDS", "2000"))
SLEEP_MS = int(os.environ.get("PRODUCT_BACKFILL_SLEEP_MS", "200"))

SCAN_FIELDS = [
    FIELD_ENTRY_TIME,
    FIELD_ENQUIRY,
    FIELD_COUNTRY,
    FIELD_CHANNELS,
    FIELD_SUB_CHANNEL,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_LEAD_ID,
]


def _load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _since_ms(since: str) -> int:
    """YYYY-MM-DD → 文档时区(+8)当天 0 点毫秒时间戳。"""
    day = datetime.strptime(since.strip()[:10], "%Y-%m-%d")
    day = day.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(day.timestamp() * 1000)


def _search_records(token: str, body: dict, page_size: int = 100) -> list[dict]:
    items: list[dict] = []
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
        items.extend(body_data.get("items", []))
        if not body_data.get("has_more"):
            break
        page_token = body_data.get("page_token", "")
        if not page_token:
            break
        if len(items) >= MAX_RECORDS:
            break
    return items[:MAX_RECORDS]


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


def _option_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("name") or "").strip()
        return str(first).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    return str(value).strip()


def run() -> int:
    token = get_feishu_token()
    rules = _load_rules()
    cutoff = _since_ms(SINCE)
    log.info(
        "开始回填 since=%s cutoff_ms=%s dry_run=%s max=%s",
        SINCE,
        cutoff,
        DRY_RUN,
        MAX_RECORDS,
    )

    # 型号或大类为无法识别（OR）；录入时间用本地过滤避免 search filter 兼容问题
    records = _search_records(
        token,
        {
            "filter": {
                "conjunction": "or",
                "conditions": [
                    {
                        "field_name": FIELD_PRODUCT_MODEL,
                        "operator": "is",
                        "value": ["无法识别"],
                    },
                    {
                        "field_name": FIELD_PRODUCT_CAT,
                        "operator": "is",
                        "value": ["无法识别"],
                    },
                    {
                        "field_name": FIELD_PRODUCT_MODEL,
                        "operator": "isEmpty",
                        "value": [],
                    },
                ],
            },
            "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
            "field_names": SCAN_FIELDS,
        },
    )
    log.info("候选记录 %s 条（含非本月，稍后按录入时间过滤）", len(records))

    fixed = 0
    skipped = 0
    no_infer = 0
    for item in records:
        record_id = item.get("record_id", "")
        fields = item.get("fields", {}) or {}
        entry_ms = fields.get(FIELD_ENTRY_TIME, 0) or 0
        if isinstance(entry_ms, str) and entry_ms.isdigit():
            entry_ms = int(entry_ms)
        if not isinstance(entry_ms, (int, float)):
            entry_ms = 0
        if entry_ms and entry_ms < cutoff:
            skipped += 1
            continue

        enquiry = extract_text(fields.get(FIELD_ENQUIRY, ""))
        country = _option_text(get_field(fields, FIELD_COUNTRY, ""))
        channels = _option_text(get_field(fields, FIELD_CHANNELS, ""))
        sub = _option_text(get_field(fields, FIELD_SUB_CHANNEL, ""))
        cur_cat = _option_text(get_field(fields, FIELD_PRODUCT_CAT, ""))
        cur_model = _option_text(get_field(fields, FIELD_PRODUCT_MODEL, ""))

        updates = infer_product_updates(
            enquiry=enquiry,
            country=country,
            channels=channels,
            sub_channel=sub,
            current_category=cur_cat,
            current_model=cur_model,
            rules=rules,
        )
        if not updates:
            no_infer += 1
            continue

        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        log.info(
            "回填 %s record=%s country=%s ch=%s/%s %s→%s %s→%s",
            lead_id or record_id,
            record_id,
            country,
            channels,
            sub,
            cur_cat or "(空)",
            updates.get(FIELD_PRODUCT_CAT, cur_cat or "(不变)"),
            cur_model or "(空)",
            updates.get(FIELD_PRODUCT_MODEL, cur_model or "(不变)"),
        )
        if DRY_RUN:
            fixed += 1
            continue

        if _update_record(token, record_id, updates):
            fixed += 1
            if SLEEP_MS > 0:
                time.sleep(SLEEP_MS / 1000.0)

    log.info(
        "完成: 回填=%s 无法推断=%s 早于since跳过=%s dry_run=%s",
        fixed,
        no_infer,
        skipped,
        DRY_RUN,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
