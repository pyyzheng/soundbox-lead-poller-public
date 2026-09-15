#!/usr/bin/env python3
"""
cloud-messenger-dedup.py — 删除 Facebook-Messenger 与 Lead Ad 表单重复线索

根因：facebook-lead-webhook 的 writeMessengerLead 与 writeLead 对同一客户各写一条
（Channels=Facebook-Messenger vs Facebook），间隔通常 < 1 分钟。

策略：若同邮箱在时间窗内已有 Channels=Facebook 的表单线索，则删除 Messenger 副本。
仅 Messenger-only（无表单兄弟）的线索保留。
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("messenger-dedup")

RECENT_HOURS = int(os.environ.get("MESSENGER_DEDUP_RECENT_HOURS", "48"))
PAIR_WINDOW_MINUTES = int(os.environ.get("MESSENGER_DEDUP_PAIR_MINUTES", "60"))
MAX_RECORDS = int(os.environ.get("MESSENGER_DEDUP_MAX_RECORDS", "500"))
DRY_RUN = os.environ.get("MESSENGER_DEDUP_DRY_RUN", "false").lower() == "true"

FIELD_ENTRY_TIME = "Entry Time（录入时间）"
FIELD_CHANNELS = "Channels（渠道）"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_CUSTOMER = "Customer Name（客户名称）"

CHANNEL_LEAD_AD = "Facebook"
CHANNEL_MESSENGER = "Facebook-Messenger"
CHANNEL_INSTAGRAM = "Instagram"
MESSENGER_LIKE_CHANNELS = {CHANNEL_MESSENGER, CHANNEL_INSTAGRAM}


def _normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value or value in {"n/a", "na", "none", "-"}:
        return ""
    # Messenger 行常见 "a@x.com,a@x.com"，取首个有效邮箱做配对
    if "," in value:
        for part in value.split(","):
            part = part.strip()
            if part and part not in {"n/a", "na", "none", "-"}:
                return part
        return ""
    return value


def _search_records(token: str, body: dict) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/search?page_size=100"
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
    return items


def _delete_records(token: str, record_ids: list[str]) -> int:
    deleted = 0
    for record_id in record_ids:
        resp = feishu_api(
            "DELETE",
            (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
                f"/tables/{FEISHU_TABLE_ID}/records/{record_id}"
            ),
            token=token,
            max_retries=3,
        )
        data = resp.json()
        if data.get("code") == 0:
            deleted += 1
            continue
        if data.get("code") == 1254043:
            log.info("记录已不存在，跳过: %s", record_id)
            continue
        log.error("删除失败 %s: %s", record_id, data)
    return deleted


def _find_messenger_duplicates(records: list[dict], cutoff_ms: int, pair_window_ms: int) -> list[dict]:
    facebook_by_email: dict[str, list[dict]] = {}
    messenger_rows: list[dict] = []

    for item in records:
        fields = item.get("fields", {})
        entry_ms = fields.get(FIELD_ENTRY_TIME, 0) or 0
        if entry_ms and entry_ms < cutoff_ms:
            continue

        email = _normalize_email(extract_text(fields.get(FIELD_EMAIL, "")))
        if not email:
            continue

        channel = extract_text(fields.get(FIELD_CHANNELS, ""))
        row = {
            "record_id": item.get("record_id", ""),
            "lead_id": extract_text(fields.get(FIELD_LEAD_ID, "")),
            "email": email,
            "name": extract_text(fields.get(FIELD_CUSTOMER, "")),
            "entry_ms": entry_ms,
            "channel": channel,
        }

        if channel == CHANNEL_LEAD_AD:
            facebook_by_email.setdefault(email, []).append(row)
        elif channel in MESSENGER_LIKE_CHANNELS:
            messenger_rows.append(row)

    duplicates: list[dict] = []
    for messenger in messenger_rows:
        siblings = facebook_by_email.get(messenger["email"], [])
        for facebook in siblings:
            if facebook["entry_ms"] > messenger["entry_ms"]:
                continue
            if messenger["entry_ms"] - facebook["entry_ms"] <= pair_window_ms:
                duplicates.append({
                    **messenger,
                    "facebook_lead_id": facebook["lead_id"],
                    "facebook_record_id": facebook["record_id"],
                })
                break
    return duplicates


def run() -> int:
    token = get_feishu_token()
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).timestamp() * 1000)
    pair_window_ms = PAIR_WINDOW_MINUTES * 60 * 1000

    records = _search_records(
        token,
        {
            "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
            "field_names": [
                FIELD_ENTRY_TIME,
                FIELD_LEAD_ID,
                FIELD_CHANNELS,
                FIELD_EMAIL,
                FIELD_CUSTOMER,
            ],
        },
    )[:MAX_RECORDS]

    duplicates = _find_messenger_duplicates(records, cutoff_ms, pair_window_ms)
    if not duplicates:
        log.info("无 Messenger 重复线索 (recent_hours=%s pair_minutes=%s)", RECENT_HOURS, PAIR_WINDOW_MINUTES)
        return 0

    for dup in duplicates:
        log.info(
            "Messenger 重复 %s record=%s email=%s | 保留表单 %s",
            dup["lead_id"] or dup["record_id"],
            dup["record_id"],
            dup["email"],
            dup["facebook_lead_id"] or dup["facebook_record_id"],
        )

    if DRY_RUN:
        log.info("dry_run: 将删除 %d 条 Messenger 重复", len(duplicates))
        return 0

    deleted = _delete_records(token, [d["record_id"] for d in duplicates if d["record_id"]])
    log.info("完成: 删除 Messenger 重复 %d/%d", deleted, len(duplicates))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
