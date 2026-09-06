#!/usr/bin/env python3
"""仅修复「分配异常」线索的渠道轮转（轻量查询，避免全表扫描）。"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from assignment_fields import (
    CHANNEL_QUEUE_TABLE,
    FIELD_ASSIGNEE,
    FIELD_AGENT_COUNTRY,
    FIELD_AGENT_PRODUCT,
    FIELD_ASSIGN_METHOD,
    FIELD_ASSIGN_SOURCE,
    FIELD_CHANNELS,
    FIELD_DUP_READY,
    FIELD_ENQUIRY,
    FIELD_FB_LEADGEN,
    FIELD_GMAIL_MSG,
    FIELD_LEAD_ID,
    FIELD_MANUAL_ASSIGNEE,
    FIELD_QUEUE_ASSIGNEE,
    FIELD_QUEUE_KEY,
    FIELD_STATUS,
    FIELD_SUB_CHANNEL,
    FIELD_SUBOFFICE,
    FIELD_SUCCESS,
    FIELD_SYSTEM,
    QUEUE_POINTER_TABLE,
    WRITE_ASSIGN_AUTO,
    WRITE_SUCCESS_YES,
    get_field,
    heal_invalid_channel,
    is_invalid_channel,
)
from channel_queue_assign import (
    eligible_for_channel_queue,
    parse_channel_queue_map,
    parse_queue_pointers,
    pick_queue_assignee,
)
from daily_least_assign import (
    PUBLIC_REGION_ME,
    PUBLIC_REGION_POINTER_KEY,
    bump_count,
    counts_should_include,
    eligible_for_daily_least,
    is_daily_least_queue,
    normalize_public_region,
    pick_daily_least_assignee,
    shanghai_day_bounds,
    to_utc_ms,
    TRACKED_ASSIGNEES,
)
from feishu_utils import (
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("fix-anomaly")
DAILY_LEAST_ENABLED = os.environ.get("DAILY_LEAST_ASSIGN_ENABLED", "true").lower() == "true"
FIELD_ENTRY_TIME = "Entry Time（录入时间）"


def _search(token: str, table_id: str, body: dict) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/search?page_size=50"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        body_data = data.get("data", {})
        items.extend(body_data.get("items", []))
        if not body_data.get("has_more"):
            break
        page_token = body_data.get("page_token", "")
        if not page_token:
            break
    return items


def _load_daily_counts(token: str) -> dict[str, int]:
    counts = {name: 0 for name in TRACKED_ASSIGNEES}
    yesterday_start, _, tomorrow_start = shanghai_day_bounds()
    from_ms = to_utc_ms(yesterday_start)
    try:
        items = _search(
            token,
            FEISHU_TABLE_ID,
            {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {
                            "field_name": FIELD_ENTRY_TIME,
                            "operator": "isGreater",
                            "value": ["ExactDate", str(from_ms - 1)],
                        },
                        {
                            "field_name": FIELD_ENTRY_TIME,
                            "operator": "isLess",
                            "value": ["ExactDate", str(to_utc_ms(tomorrow_start))],
                        },
                    ],
                },
                # 不投影 field_names：人工改派字段名超 OpenAPI 单项 50 字符上限
                "page_size": 100,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("加载按天累计失败: %s", exc)
        return counts
    for item in items:
        fields = item.get("fields", {}) or {}
        final = extract_text(get_field(fields, FIELD_ASSIGNEE, "")).strip()
        manual = extract_text(get_field(fields, FIELD_MANUAL_ASSIGNEE, "")).strip()
        if counts_should_include(final_assignee=final, manual_assignee=manual):
            bump_count(counts, final)
    return counts


def _load_public_region(token: str) -> tuple[int, str]:
    rows = _search(
        token,
        QUEUE_POINTER_TABLE,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": "队列Key", "operator": "is", "value": [PUBLIC_REGION_POINTER_KEY]}
                ],
            },
            "field_names": ["队列Key", "当前顺序号"],
        },
    )
    if not rows:
        return PUBLIC_REGION_ME, ""
    rid = rows[0].get("record_id", "")
    cur = rows[0].get("fields", {}).get("当前顺序号", 1) or 1
    try:
        cur_i = int(cur)
    except (TypeError, ValueError):
        cur_i = 1
    return normalize_public_region(cur_i), rid


def _update(token: str, table_id: str, record_id: str, fields: dict) -> bool:
    resp = feishu_api(
        "PUT",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{table_id}/records/{record_id}",
        token=token,
        json={"fields": fields},
    )
    ok = resp.json().get("code") == 0
    if not ok:
        log.error("update failed %s %s", record_id, resp.json())
    return ok


def main() -> int:
    token = get_feishu_token()
    anomalies = _search(
        token,
        FEISHU_TABLE_ID,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FIELD_STATUS, "operator": "is", "value": ["❌ 分配异常"]},
                    {"field_name": FIELD_ASSIGN_METHOD, "operator": "is", "value": [WRITE_ASSIGN_AUTO]},
                ],
            },
            "field_names": [
                FIELD_LEAD_ID,
                FIELD_QUEUE_KEY,
                FIELD_QUEUE_ASSIGNEE,
                FIELD_ASSIGN_METHOD,
                FIELD_STATUS,
                FIELD_SUCCESS,
                FIELD_ASSIGNEE,
                "是否满足渠道轮转",
                FIELD_ASSIGN_SOURCE,
                FIELD_DUP_READY,
                FIELD_SUBOFFICE,
                FIELD_AGENT_COUNTRY,
                FIELD_AGENT_PRODUCT,
                FIELD_SYSTEM,
                FIELD_CHANNELS,
                FIELD_SUB_CHANNEL,
                FIELD_ENQUIRY,
                FIELD_FB_LEADGEN,
                FIELD_GMAIL_MSG,
            ],
        },
    )
    log.info("分配异常 %d 条", len(anomalies))

    pointers = parse_queue_pointers(
        _search(token, QUEUE_POINTER_TABLE, {"field_names": ["队列Key", "当前顺序号", "最大顺序号"], "page_size": 100})
    )
    queue_map = parse_channel_queue_map(
        _search(
            token,
            CHANNEL_QUEUE_TABLE,
            {
                "filter": {
                    "conjunction": "and",
                    "conditions": [{"field_name": "是否启用", "operator": "is", "value": ["启用"]}],
                },
                "field_names": ["队列Key", "顺位", "业务员", "是否启用"],
                "page_size": 100,
            },
        )
    )
    daily_counts = _load_daily_counts(token) if DAILY_LEAST_ENABLED else {}
    public_region, public_region_rid = (
        _load_public_region(token) if DAILY_LEAST_ENABLED else (PUBLIC_REGION_ME, "")
    )

    fixed = 0
    for item in anomalies:
        rid = item.get("record_id", "")
        fields = item.get("fields", {})
        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))

        # 主渠道无效时：细分渠道 → 询盘正文 → 来源 ID 三级自愈。
        channel = extract_text(get_field(fields, FIELD_CHANNELS, "")).strip()
        if is_invalid_channel(channel):
            healed = heal_invalid_channel(
                channel,
                sub_channel=extract_text(get_field(fields, FIELD_SUB_CHANNEL, "")),
                enquiry=extract_text(get_field(fields, FIELD_ENQUIRY, "")),
                fb_leadgen=extract_text(get_field(fields, FIELD_FB_LEADGEN, "")),
                gmail_msg_id=extract_text(get_field(fields, FIELD_GMAIL_MSG, "")),
            )
            if healed:
                log.info("自愈渠道 %s: %r → %s", lead_id, channel, healed)
                if os.environ.get("FIX_ANOMALY_DRY_RUN", "false").lower() != "true":
                    if _update(token, FEISHU_TABLE_ID, rid, {FIELD_CHANNELS: healed}):
                        fields[FIELD_CHANNELS] = healed
                        time.sleep(2.5)  # 等待队列Key 公式重算
                        refreshed = _search(
                            token,
                            FEISHU_TABLE_ID,
                            {
                                "filter": {
                                    "conjunction": "and",
                                    "conditions": [
                                        {
                                            "field_name": FIELD_LEAD_ID,
                                            "operator": "is",
                                            "value": [lead_id],
                                        }
                                    ],
                                },
                                "field_names": [
                                    FIELD_QUEUE_KEY,
                                    FIELD_CHANNELS,
                                    FIELD_QUEUE_ASSIGNEE,
                                    FIELD_ASSIGN_METHOD,
                                    FIELD_ASSIGN_SOURCE,
                                    FIELD_DUP_READY,
                                    FIELD_SUBOFFICE,
                                    FIELD_AGENT_COUNTRY,
                                    FIELD_AGENT_PRODUCT,
                                    FIELD_SYSTEM,
                                    "是否满足渠道轮转",
                                ],
                            },
                        )
                        if refreshed:
                            fields.update(refreshed[0].get("fields", {}))
                    else:
                        log.error("自愈渠道失败 %s", lead_id)
                        continue
                else:
                    fields[FIELD_CHANNELS] = healed

        # 写前复核：避免与 unblock / 工作流并发时覆盖已有业务员
        if os.environ.get("FIX_ANOMALY_DRY_RUN", "false").lower() != "true":
            live_resp = feishu_api(
                "GET",
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
                f"/tables/{FEISHU_TABLE_ID}/records/{rid}",
                token=token,
            )
            live_data = live_resp.json()
            if live_data.get("code") == 0:
                live_fields = live_data.get("data", {}).get("record", {}).get("fields", {}) or {}
                live_assignee = extract_text(get_field(live_fields, FIELD_QUEUE_ASSIGNEE, "")).strip()
                if live_assignee:
                    log.info("跳过 %s（写前已有业务员 %s）", lead_id or rid, live_assignee)
                    continue

        queue_key = extract_text(fields.get(FIELD_QUEUE_KEY, ""))

        if DAILY_LEAST_ENABLED and eligible_for_daily_least(fields):
            pick = pick_daily_least_assignee(queue_key, daily_counts, public_region)
            if not pick:
                log.warning("按天最少无候选人 %s queue=%s", lead_id, queue_key)
                continue
            patch = {FIELD_QUEUE_ASSIGNEE: pick.assignee, FIELD_SUCCESS: WRITE_SUCCESS_YES}
            log.info(
                "修复(按天最少) %s → %s pool=%s",
                lead_id,
                pick.assignee,
                pick.pool,
            )
            if os.environ.get("FIX_ANOMALY_DRY_RUN", "false").lower() == "true":
                fixed += 1
                bump_count(daily_counts, pick.assignee)
                if pick.advance_public_region and pick.next_public_region:
                    public_region = pick.next_public_region
                continue
            if _update(token, FEISHU_TABLE_ID, rid, patch):
                time.sleep(0.5)
                bump_count(daily_counts, pick.assignee)
                if pick.advance_public_region and pick.next_public_region and public_region_rid:
                    _update(
                        token,
                        QUEUE_POINTER_TABLE,
                        public_region_rid,
                        {"当前顺序号": pick.next_public_region},
                    )
                    public_region = pick.next_public_region
                time.sleep(0.5)
                fixed += 1
            continue

        if not eligible_for_channel_queue(fields):
            log.info("跳过 %s（不满足渠道轮转条件）", lead_id or rid)
            continue
        if DAILY_LEAST_ENABLED and is_daily_least_queue(queue_key):
            log.info("跳过 %s（中东/亚洲/公区已改按天最少）", lead_id or rid)
            continue

        pick = pick_queue_assignee(queue_key, pointers, queue_map)
        if not pick:
            log.warning("无队列业务员 %s queue=%s", lead_id, queue_key)
            continue
        resolved_key = pick.resolved_queue_key or queue_key
        patch = {FIELD_QUEUE_ASSIGNEE: pick.assignee, FIELD_SUCCESS: WRITE_SUCCESS_YES}
        # 若靠区域兜底命中，顺带写回主渠道，避免公式继续产出「无法识别|…」
        if is_invalid_channel(extract_text(get_field(fields, FIELD_CHANNELS, ""))) and "|" in resolved_key:
            patch[FIELD_CHANNELS] = resolved_key.split("|", 1)[0]
        log.info("修复 %s → %s (queue=%s)", lead_id, pick.assignee, resolved_key)
        if os.environ.get("FIX_ANOMALY_DRY_RUN", "false").lower() == "true":
            fixed += 1
            continue
        if _update(token, FEISHU_TABLE_ID, rid, patch):
            time.sleep(0.5)
            _update(token, QUEUE_POINTER_TABLE, pick.pointer_record_id, {"当前顺序号": pick.next_rank})
            time.sleep(0.5)
            pointers[resolved_key] = type(pointers[resolved_key])(
                record_id=pick.pointer_record_id,
                current=pick.next_rank,
                max_rank=pick.max_rank,
            )
            fixed += 1

    log.info("完成 fixed=%d", fixed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
