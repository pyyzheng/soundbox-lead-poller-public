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
from feishu_utils import (
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("fix-anomaly")


def _search(token: str, table_id: str, body: dict) -> list[dict]:
    resp = feishu_api(
        "POST",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{table_id}/records/search?page_size=50",
        token=token,
        json=body,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(data)
    return data.get("data", {}).get("items", [])


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
                "field_names": ["队列Key", "顺位", "业务员"],
                "page_size": 100,
            },
        )
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

        if not eligible_for_channel_queue(fields):
            log.info("跳过 %s（不满足渠道轮转条件）", lead_id or rid)
            continue
        queue_key = extract_text(fields.get(FIELD_QUEUE_KEY, ""))
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
