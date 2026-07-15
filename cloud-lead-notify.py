#!/usr/bin/env python3
"""
cloud-lead-notify.py — 线索分配 / 询盘更新 IM 通知兜底

工作流为主路径；本脚本每 5 分钟扫描近期记录，补发工作流漏掉的通知。
去重：Follow-up Records 表写入 SYS_NOTIFY:* 标记。
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from feishu_utils import (
    FEISHU_APP_TOKEN,
    extract_text,
    feishu_api,
    feishu_search_url,
    get_feishu_token,
    require_env,
    send_alert_webhook,
)
from lead_notify import (
    ERROR_ASSIGNEES,
    FIELD_ASSIGNEE,
    FIELD_COUNTRY,
    FIELD_CUSTOMER,
    FIELD_ENQUIRY,
    FIELD_ENTRY_TIME,
    FIELD_LEAD_ID,
    FIELD_NOTIFY_USER,
    FOLLOWUP_MARKER_ASSIGN,
    FOLLOWUP_MARKER_ENQUIRY,
    build_assign_card,
    build_enquiry_update_card,
    enquiry_snippet,
    followup_marker_exists,
    is_valid_assignee,
    load_sales_open_id_map,
    record_url,
    resolve_notify_open_id,
    send_im_card,
    write_followup_marker,
)

log = logging.getLogger("lead-notify-worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

FOLLOWUP_TABLE_ID = require_env("FEISHU_FOLLOWUP_TABLE")
DRY_RUN = os.environ.get("LEAD_NOTIFY_DRY_RUN", "false").lower() == "true"
# 分配兜底仅扫最近几小时，避免首次上线对历史线索群发
ASSIGN_LOOKBACK_HOURS = int(os.environ.get("LEAD_NOTIFY_ASSIGN_LOOKBACK_HOURS", "6"))
ENQUIRY_LOOKBACK_HOURS = int(os.environ.get("LEAD_NOTIFY_ENQUIRY_LOOKBACK_HOURS", "24"))
AUDIT_LOOKBACK_HOURS = int(os.environ.get("LEAD_NOTIFY_AUDIT_LOOKBACK_HOURS", "72"))
MAX_RECORDS = int(os.environ.get("LEAD_NOTIFY_MAX_RECORDS", "50"))


def _ms_hours_ago(hours: int) -> int:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return int(dt.timestamp() * 1000)


def _search_assigned_since(token: str, entry_cutoff_ms: int) -> list[dict]:
    conditions = [
        {"field_name": FIELD_NOTIFY_USER, "operator": "isNotEmpty", "value": []},
        {"field_name": FIELD_ASSIGNEE, "operator": "isNotEmpty", "value": []},
        {"field_name": FIELD_ENTRY_TIME, "operator": "isGreater", "value": ["ExactDate", str(entry_cutoff_ms)]},
    ]
    for err in ERROR_ASSIGNEES:
        conditions.append({
            "field_name": FIELD_ASSIGNEE,
            "operator": "isNot",
            "value": [err],
        })

    items: list[dict] = []
    page_token = ""
    while len(items) < MAX_RECORDS:
        body: dict = {
            "filter": {"conjunction": "and", "conditions": conditions},
            "page_size": min(50, MAX_RECORDS - len(items)),
        }
        if page_token:
            body["page_token"] = page_token
        resp = feishu_api("POST", feishu_search_url(), token=token, json=body)
        data = resp.json()
        if data.get("code") != 0:
            log.error("线索查询失败: %s", data)
            break
        batch = data.get("data", {}).get("items", [])
        items.extend(batch)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token", "")
    return items[:MAX_RECORDS]


def _modified_after(item: dict, cutoff_ms: int) -> bool:
    modified = int(item.get("last_modified_time") or 0)
    return modified >= cutoff_ms


def _enquiry_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _latest_enquiry_marker_hash(token: str, lead_record_id: str) -> str | None:
    resp = feishu_api(
        "POST",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FOLLOWUP_TABLE_ID}/records/search",
        token=token,
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": "Related Lead", "operator": "is", "value": [lead_record_id]},
                ],
            },
            "page_size": 20,
        },
    )
    data = resp.json()
    if data.get("code") != 0:
        return None
    latest = None
    for item in data.get("data", {}).get("items", []):
        details = extract_text(item.get("fields", {}).get("Follow-up Details", ""))
        if not details.startswith(FOLLOWUP_MARKER_ENQUIRY):
            continue
        parts = details.split(":")
        if len(parts) >= 4:
            latest = parts[3]
    return latest


def process_assignment_backfill(
    token: str,
    records: list[dict],
    sales_map: dict[str, str],
    modified_cutoff_ms: int,
) -> int:
    sent = 0
    for item in records:
        if not _modified_after(item, modified_cutoff_ms):
            continue
        rid = item.get("record_id", "")
        fields = item.get("fields", {})
        assignee = extract_text(fields.get(FIELD_ASSIGNEE, "")).strip()
        if not is_valid_assignee(assignee):
            continue
        lead_id = extract_text(fields.get(FIELD_LEAD_ID, "")).strip()
        marker = f"{FOLLOWUP_MARKER_ASSIGN}:{lead_id}:{assignee}"
        if followup_marker_exists(token, FOLLOWUP_TABLE_ID, rid, marker):
            continue

        open_id = resolve_notify_open_id(fields, sales_map)
        if not open_id:
            log.warning("无 open_id，跳过分配通知: lead=%s assignee=%s", lead_id, assignee)
            continue

        customer = extract_text(fields.get(FIELD_CUSTOMER, ""))
        country = extract_text(fields.get(FIELD_COUNTRY, ""))
        card = build_assign_card(lead_id, customer, country, assignee, record_url(rid))

        if DRY_RUN:
            log.info("[DRY] 分配通知 → %s lead=%s", assignee, lead_id)
        elif send_im_card(token, open_id, card):
            write_followup_marker(token, FOLLOWUP_TABLE_ID, rid, marker, marker)
            sent += 1
            log.info("补发分配通知: lead=%s → %s", lead_id, assignee)
    return sent


def process_enquiry_backfill(
    token: str,
    records: list[dict],
    sales_map: dict[str, str],
    modified_cutoff_ms: int,
) -> int:
    """仅当询盘 hash 变化且曾有标记时补发（避免与首次分配重复）。"""
    sent = 0
    for item in records:
        if not _modified_after(item, modified_cutoff_ms):
            continue
        rid = item.get("record_id", "")
        fields = item.get("fields", {})
        lead_id = extract_text(fields.get(FIELD_LEAD_ID, "")).strip()
        enquiry = extract_text(fields.get(FIELD_ENQUIRY, ""))
        if not enquiry.strip():
            continue

        current = _enquiry_hash(enquiry)
        prev = _latest_enquiry_marker_hash(token, rid)
        marker = f"{FOLLOWUP_MARKER_ENQUIRY}:{lead_id}:{current}"
        if prev is None or prev == current:
            continue
        if followup_marker_exists(token, FOLLOWUP_TABLE_ID, rid, marker):
            continue

        open_id = resolve_notify_open_id(fields, sales_map)
        if not open_id:
            continue

        customer = extract_text(fields.get(FIELD_CUSTOMER, ""))
        card = build_enquiry_update_card(
            lead_id, customer, enquiry_snippet(enquiry), record_url(rid),
        )
        if DRY_RUN:
            log.info("[DRY] 询盘更新 → lead=%s hash %s→%s", lead_id, prev, current)
        elif send_im_card(token, open_id, card):
            write_followup_marker(token, FOLLOWUP_TABLE_ID, rid, marker, marker)
            sent += 1
            log.info("补发询盘更新: lead=%s", lead_id)
    return sent


def audit_sales_roster(records: list[dict], sales_map: dict[str, str]) -> list[str]:
    missing: set[str] = set()
    for item in records:
        fields = item.get("fields", {})
        assignee = extract_text(fields.get(FIELD_ASSIGNEE, "")).strip()
        if not is_valid_assignee(assignee):
            continue
        if not resolve_notify_open_id(fields, sales_map) and assignee not in sales_map:
            missing.add(assignee)
    return sorted(missing)


def main() -> int:
    token = get_feishu_token()
    sales_map = load_sales_open_id_map(token)
    log.info(
        "业务通知名单 %d 人 | dry_run=%s | assign_h=%s enquiry_h=%s",
        len(sales_map), DRY_RUN, ASSIGN_LOOKBACK_HOURS, ENQUIRY_LOOKBACK_HOURS,
    )

    entry_cutoff = _ms_hours_ago(max(ASSIGN_LOOKBACK_HOURS, ENQUIRY_LOOKBACK_HOURS, AUDIT_LOOKBACK_HOURS))
    records = _search_assigned_since(token, entry_cutoff)
    assign_cutoff = _ms_hours_ago(ASSIGN_LOOKBACK_HOURS)
    enquiry_cutoff = _ms_hours_ago(ENQUIRY_LOOKBACK_HOURS)

    assign_sent = process_assignment_backfill(token, records, sales_map, assign_cutoff)
    enquiry_sent = process_enquiry_backfill(token, records, sales_map, enquiry_cutoff)

    audit_records = [r for r in records if _modified_after(r, _ms_hours_ago(AUDIT_LOOKBACK_HOURS))]
    gaps = audit_sales_roster(audit_records, sales_map)
    if gaps:
        msg = f"业务通知名单缺人（近{AUDIT_LOOKBACK_HOURS}h）: {', '.join(gaps)}"
        log.warning(msg)
        if not DRY_RUN:
            send_alert_webhook(msg)

    log.info("完成: 扫描=%d 分配补发=%d 询盘补发=%d", len(records), assign_sent, enquiry_sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
