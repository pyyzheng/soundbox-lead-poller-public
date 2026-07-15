#!/usr/bin/env python3
"""
cloud-dedup-conflict-fix.py — 修复查重冲突导致的分配阻塞

处理两类可自动修复的情况：
1. 假冲突：多个 Lookup 字段返回同一业务员但格式不同（如 Stephanie vs Stephanie,Stephanie）
2. 真冲突但可裁决：多个维度命中不同业务员时，按优先级取 Email > Phone > 阿里ID > 微信 > 域名

通过写入「人工改派的业务员」绕过查重冲突，让分配链路继续。
"""

from __future__ import annotations

import logging
import os
import re
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
from assignment_fields import (  # noqa: E402
    FIELD_ASSIGNEE,
    FIELD_ASSIGN_SOURCE,
    FIELD_ENTRY_TIME,
    FIELD_LEAD_ID,
    FIELD_SYSTEM,
    get_field,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dedup-conflict-fix")

RECENT_HOURS = int(os.environ.get("DEDUP_CONFLICT_RECENT_HOURS", "168"))
MAX_RECORDS = int(os.environ.get("DEDUP_CONFLICT_MAX_RECORDS", "200"))
DRY_RUN = os.environ.get("DEDUP_CONFLICT_DRY_RUN", "false").lower() == "true"

FIELD_MANUAL_ASSIGNEE = "人工改派的业务员"
FIELD_DUP_RESULT = "Dup_Match_Result"
FIELD_DUP_OWNER = "Dup_Match_Owner"
FIELD_DUP_CONFLICT = "Dup_Match_Conflict"

CONTACT_FIELDS = (
    "Phone（客户电话）",
    "阿里ID",
    "Wechat（微信）",
)

DUP_OWNER_FIELDS = (
    ("Email（客户邮箱）", "Dup_Email_Owner"),
    ("Phone（客户电话）", "Dup_Phone_Owner"),
    ("阿里ID", "Dup_阿里ID_Owner"),
    ("Wechat（微信）", "Dup_VX_Owner"),
    ("Email（客户邮箱）", "Dup_Email_Domain_Owner"),
)

INVALID_SOURCE_VALUES = {"", "N/A", "无匹配类别"}


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


def _update_record(token: str, record_id: str, fields: dict) -> bool:
    resp = feishu_api(
        "PUT",
        (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/{record_id}"
        ),
        token=token,
        json={"fields": fields},
        max_retries=3,
    )
    return resp.json().get("code") == 0


def _parse_entry_ms(fields: dict) -> int:
    raw = fields.get(FIELD_ENTRY_TIME, 0) or 0
    if isinstance(raw, (int, float)):
        return int(raw)
    return 0


def _source_valid(value: str) -> bool:
    text = (value or "").strip()
    return bool(text) and text not in INVALID_SOURCE_VALUES


def _effective_owner(fields: dict, source_field: str, owner_field: str) -> list[str]:
    if not _source_valid(extract_text(fields.get(source_field, ""))):
        return []
    return _normalize_owner(extract_text(fields.get(owner_field, "")))


def _normalize_owner(value: str) -> list[str]:
    if not value or value in {"匹配错误请检查", "N/A"}:
        return []
    owners: list[str] = []
    for part in re.split(r"[,，;；/|]+", value):
        name = part.strip()
        if name and name not in owners:
            owners.append(name)
    return owners


def _needs_source_normalize(fields: dict) -> dict[str, str]:
    """将「无匹配类别」或空值统一为 N/A，避免 Lookup 误命中。"""
    patch: dict[str, str] = {}
    for field_name in CONTACT_FIELDS:
        value = extract_text(fields.get(field_name, ""))
        if value in INVALID_SOURCE_VALUES:
            patch[field_name] = "N/A"
    return patch


def _is_formula_broken(fields: dict) -> bool:
    system = extract_text(get_field(fields, FIELD_SYSTEM, ""))
    result = extract_text(fields.get(FIELD_DUP_RESULT, ""))
    owner = extract_text(fields.get(FIELD_DUP_OWNER, ""))
    assign_source = extract_text(get_field(fields, FIELD_ASSIGN_SOURCE, ""))
    return any(
        value == "匹配错误请检查"
        for value in (system, result, owner, assign_source)
    ) or bool(_needs_source_normalize(fields))


def _normalize_recent_records(token: str, cutoff_ms: int) -> int:
    """扫描最近线索，把无效联系方式从「无匹配类别」规范为 N/A。"""
    body = {
        "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
        "page_size": min(MAX_RECORDS, 100),
    }
    records = _search_records(token, body)[:MAX_RECORDS]
    normalized = 0
    for item in records:
        fields = item.get("fields", {})
        entry_ms = _parse_entry_ms(fields)
        if entry_ms and entry_ms < cutoff_ms:
            continue
        if not _is_formula_broken(fields):
            continue
        patch = _needs_source_normalize(fields)
        if not patch:
            continue
        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        record_id = item.get("record_id", "")
        log.info("规范化 %s (%s): %s", lead_id, record_id[:8], patch)
        if DRY_RUN:
            normalized += 1
            continue
        if _update_record(token, record_id, patch):
            normalized += 1
    return normalized


def _pick_assignee(fields: dict) -> tuple[str | None, str]:
    """返回 (业务员, 原因)。"""
    manual = extract_text(fields.get(FIELD_MANUAL_ASSIGNEE, ""))
    if manual:
        return None, "already_manual"

    final = extract_text(get_field(fields, FIELD_ASSIGNEE, ""))
    if final and final not in {"", "匹配错误请检查", "未命中规则"}:
        return None, "already_assigned"

    system = extract_text(get_field(fields, FIELD_SYSTEM, ""))
    assign_source = extract_text(get_field(fields, FIELD_ASSIGN_SOURCE, ""))
    if system != "匹配错误请检查" and assign_source != "查重冲突":
        return None, "not_blocked"

    per_field: dict[str, list[str]] = {}
    all_owners: list[str] = []
    for source_field, owner_field in DUP_OWNER_FIELDS:
        owners = _effective_owner(fields, source_field, owner_field)
        if owners:
            per_field[owner_field] = owners
            for owner in owners:
                if owner not in all_owners:
                    all_owners.append(owner)

    if not all_owners:
        return None, "no_dup_owner"

    if len(all_owners) == 1:
        return all_owners[0], "single_owner"

    for source_field, owner_field in DUP_OWNER_FIELDS:
        owners = per_field.get(owner_field, [])
        if owners:
            return owners[0], f"priority:{owner_field}"

    return None, "unresolved_conflict"


def main() -> None:
    log.info("=== Dedup Conflict Fix 启动 (dry_run=%s) ===", DRY_RUN)
    token = get_feishu_token()
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).timestamp() * 1000
    )

    normalized = _normalize_recent_records(token, cutoff_ms)
    log.info("规范化完成: %s 条", normalized)

    body = {
        "filter": {
            "conjunction": "or",
            "conditions": [
                {
                    "field_name": FIELD_ASSIGN_SOURCE,
                    "operator": "is",
                    "value": ["查重冲突"],
                },
                {
                    "field_name": FIELD_SYSTEM,
                    "operator": "is",
                    "value": ["匹配错误请检查"],
                },
            ],
        },
        "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
        "page_size": min(MAX_RECORDS, 100),
    }
    records = _search_records(token, body)[:MAX_RECORDS]
    log.info("候选记录 %s 条 (recent_hours=%s)", len(records), RECENT_HOURS)

    fixed = skipped = 0
    for item in records:
        fields = item.get("fields", {})
        record_id = item.get("record_id", "")
        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        entry_ms = _parse_entry_ms(fields)
        if entry_ms and entry_ms < cutoff_ms:
            continue

        assignee, reason = _pick_assignee(fields)
        if not assignee:
            patch = _needs_source_normalize(fields)
            if patch and _is_formula_broken(fields):
                log.info(
                    "规范化 %s (%s): %s",
                    lead_id,
                    record_id[:8],
                    patch,
                )
                if not DRY_RUN and _update_record(token, record_id, patch):
                    skipped += 1
                    continue
            if reason not in {"already_manual", "already_assigned", "not_blocked"}:
                log.info("跳过 %s (%s): %s", lead_id, record_id[:8], reason)
            skipped += 1
            continue

        log.info(
            "修复 %s (%s): 人工改派 -> %s (%s)",
            lead_id,
            record_id[:8],
            assignee,
            reason,
        )
        if DRY_RUN:
            fixed += 1
            continue
        if _update_record(token, record_id, {FIELD_MANUAL_ASSIGNEE: assignee}):
            fixed += 1
        else:
            log.error("更新失败: %s", lead_id)

    log.info("=== 完成: normalized=%s fixed=%s skipped=%s ===", normalized, fixed, skipped)


if __name__ == "__main__":
    main()
