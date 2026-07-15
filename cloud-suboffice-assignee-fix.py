#!/usr/bin/env python3
"""
cloud-suboffice-assignee-fix.py — 子办国家负责人自动回填

飞书主表里的「子办规则命中负责人」是普通单选字段，不是公式字段。
子办国家线索应走子办规则（非渠道轮转）。若该字段为空，系统匹配业务员会显示「未命中规则」→ 分配异常。

常见原因：飞书工作流在 Country 写入时触发，但「是否是子办国家」公式尚未就绪，Switch 走了「否」分支。
本脚本从「子办分配规则表」读取启用规则，回填负责人并设置「是否成功分配=是」。
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from assignment_fields import (  # noqa: E402
    FIELD_ASSIGN_METHOD,
    FIELD_ASSIGNEE,
    FIELD_COUNTRY,
    FIELD_EMAIL,
    FIELD_ENTRY_TIME,
    FIELD_LEAD_ID,
    FIELD_STATUS,
    FIELD_SUBOFFICE,
    FIELD_SUBOFFICE_OWNER,
    FIELD_SUCCESS,
    WRITE_ASSIGN_AUTO,
    WRITE_SUCCESS_YES,
    get_field,
)
from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    feishu_api,
    get_feishu_token,
    extract_text,
)
from option_field_match import is_assign_auto, is_suboffice_country  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("suboffice-fix")


SUBOFFICE_RULE_TABLE = os.environ.get("FEISHU_SUBOFFICE_RULE_TABLE", "tblYQpLxEBYjFN0T")
RECENT_HOURS = int(os.environ.get("SUBOFFICE_FIX_RECENT_HOURS", "72"))
MAX_RECORDS = int(os.environ.get("SUBOFFICE_FIX_MAX_RECORDS", "500"))
DRY_RUN = os.environ.get("SUBOFFICE_FIX_DRY_RUN", "false").lower() == "true"


def needs_suboffice_backfill(fields: dict, rules: dict[str, str]) -> str | None:
    """子办国家且负责人缺失/错误时，返回应回填的负责人。"""
    if not is_assign_auto(get_field(fields, FIELD_ASSIGN_METHOD, "")):
        return None
    if not is_suboffice_country(get_field(fields, FIELD_SUBOFFICE, "")):
        return None
    country = extract_text(get_field(fields, FIELD_COUNTRY, "")).strip()
    expected = rules.get(country, "")
    if not expected:
        return None
    current = extract_text(get_field(fields, FIELD_SUBOFFICE_OWNER, "")).strip()
    if current == expected:
        return None
    return expected


def _search_records(token: str, table_id: str, body: dict, page_size: int = 100) -> list[dict]:
    """Read records from a table using search API with pagination."""
    all_items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/search?page_size={page_size}"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api(
            "POST",
            url,
            token=token,
            json=body,
            max_retries=3,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书查询失败 table={table_id}: {data}")

        data_body = data.get("data", {})
        items = data_body.get("items", [])
        all_items.extend(items)
        if not data_body.get("has_more"):
            break
        page_token = data_body.get("page_token", "")
        if not page_token:
            break
    return all_items


def load_enabled_suboffice_rules(token: str) -> dict[str, str]:
    """Return {country: assignee} from enabled suboffice rules."""
    records = _search_records(
        token,
        SUBOFFICE_RULE_TABLE,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [{"field_name": "是否启用", "operator": "is", "value": ["启用"]}],
            },
            "field_names": ["国家", "负责人", "是否启用"],
            "page_size": 100,
        },
    )

    rules: dict[str, str] = {}
    for record in records:
        fields = record.get("fields", {})
        country = extract_text(fields.get("国家", "")).strip()
        assignee = extract_text(fields.get("负责人", "")).strip()
        if country and assignee:
            rules[country] = assignee
    return rules


def fetch_recent_main_records(token: str) -> list[dict]:
    """Fetch recent main records, newest first, stopping after RECENT_HOURS or MAX_RECORDS."""
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).timestamp() * 1000)
    records: list[dict] = []
    page_token = ""

    while len(records) < MAX_RECORDS:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/search?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"

        body = {
            "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
            "field_names": [
                FIELD_ENTRY_TIME,
                FIELD_COUNTRY,
                FIELD_SUBOFFICE,
                FIELD_SUBOFFICE_OWNER,
                FIELD_ASSIGN_METHOD,
                FIELD_EMAIL,
                FIELD_LEAD_ID,
                "Channels（渠道）",
                FIELD_STATUS,
                FIELD_ASSIGNEE,
            ],
            "page_size": 100,
        }
        resp = feishu_api("POST", url, token=token, json=body, max_retries=3)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"主表查询失败: {data}")

        data_body = data.get("data", {})
        items = data_body.get("items", [])
        if not items:
            break

        should_stop = False
        for item in items:
            entry_ms = item.get("fields", {}).get(FIELD_ENTRY_TIME, 0) or 0
            if entry_ms < cutoff_ms:
                should_stop = True
                break
            records.append(item)
            if len(records) >= MAX_RECORDS:
                should_stop = True
                break

        if should_stop or not data_body.get("has_more"):
            break
        page_token = data_body.get("page_token", "")
        if not page_token:
            break

    return records


def fetch_suboffice_exception_records(token: str) -> list[dict]:
    """拉取「分配异常」的子办国家线索（不受 RECENT_HOURS 限制）。"""
    return _search_records(
        token,
        FEISHU_TABLE_ID,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FIELD_STATUS, "operator": "is", "value": ["❌ 分配异常"]},
                    {"field_name": FIELD_SUBOFFICE, "operator": "is", "value": ["是"]},
                    {"field_name": FIELD_ASSIGN_METHOD, "operator": "is", "value": [WRITE_ASSIGN_AUTO]},
                ],
            },
            "field_names": [
                FIELD_ENTRY_TIME,
                FIELD_COUNTRY,
                FIELD_SUBOFFICE,
                FIELD_SUBOFFICE_OWNER,
                FIELD_EMAIL,
                FIELD_LEAD_ID,
                FIELD_ASSIGN_METHOD,
            ],
            "page_size": 50,
        },
    )


def _merge_records(primary: list[dict], extra: list[dict]) -> list[dict]:
    seen = {item.get("record_id") for item in primary if item.get("record_id")}
    merged = list(primary)
    for item in extra:
        record_id = item.get("record_id")
        if record_id and record_id not in seen:
            merged.append(item)
            seen.add(record_id)
    return merged


def update_record_owner(token: str, record_id: str, assignee: str) -> bool:
    if DRY_RUN:
        return True
    resp = feishu_api(
        "PUT",
        (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/{record_id}"
        ),
        token=token,
        json={"fields": {FIELD_SUBOFFICE_OWNER: assignee, FIELD_SUCCESS: WRITE_SUCCESS_YES}},
        max_retries=3,
    )
    data = resp.json()
    if data.get("code") != 0:
        log.error("回填失败 record=%s assignee=%s resp=%s", record_id, assignee, data)
        return False
    return True


def main() -> int:
    token = get_feishu_token()
    rules = load_enabled_suboffice_rules(token)
    log.info("已加载子办规则: %d 个国家", len(rules))

    records = _merge_records(
        fetch_recent_main_records(token),
        fetch_suboffice_exception_records(token),
    )
    log.info("扫描记录: %d 条（含分配异常子办国） dry_run=%s", len(records), DRY_RUN)

    fixed = 0
    skipped = 0
    failed = 0
    for record in records:
        fields = record.get("fields", {})
        record_id = record.get("record_id", "")
        country = extract_text(fields.get(FIELD_COUNTRY, "")).strip()
        expected = needs_suboffice_backfill(fields, rules)
        if not expected:
            skipped += 1
            continue

        current = extract_text(get_field(fields, FIELD_SUBOFFICE_OWNER, "")).strip()
        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        email = extract_text(fields.get(FIELD_EMAIL, ""))[:80]
        log.info(
            "%s回填子办负责人: lead=%s record=%s country=%s current=%s expected=%s email=%s",
            "[DRY-RUN] " if DRY_RUN else "",
            lead_id or "-",
            record_id,
            country,
            current or "(空)",
            expected,
            email,
        )
        if update_record_owner(token, record_id, expected):
            fixed += 1
        else:
            failed += 1

    log.info("完成: fixed=%d skipped=%d failed=%d", fixed, skipped, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
