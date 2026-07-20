#!/usr/bin/env python3
"""
cloud-assignment-unblock.py — 解除分配阻塞/异常

常见阻塞原因：
1. Cloudflare Worker 写入 分配方式=人工，但渠道轮转自动化要求 分配方式=自动
2. 子办/代理工作流误写 是否成功分配=是，但未回填业务员字段，阻断后续轮转
3. 代理国家产品未命中时，未写 是否命中代理产品=否，导致渠道轮转公式为否
4. 渠道轮转工作流未执行时，由本脚本按队列指针表补分配

子办国家负责人回填见 cloud-suboffice-assignee-fix.py。
渠道轮转纯逻辑见 lib/channel_queue_assign.py。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from assignment_fields import (  # noqa: E402
    ACOUSTIC_CATEGORY,
    AGENT_RULE_TABLE,
    CHANNEL_QUEUE_TABLE,
    ERROR_ASSIGNEES,
    FIELD_AGENT_ASSIGNEE,
    FIELD_AGENT_COUNTRY,
    FIELD_AGENT_PRODUCT,
    FIELD_ASSIGNEE,
    FIELD_ASSIGN_METHOD,
    FIELD_ASSIGN_SOURCE,
    FIELD_CHANNELS,
    FIELD_COUNTRY,
    FIELD_DUP_READY,
    FIELD_EMAIL,
    FIELD_ENTRY_TIME,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_QUEUE_ASSIGNEE,
    FIELD_QUEUE_KEY,
    FIELD_ROTATION,
    FIELD_STATUS,
    FIELD_SUBOFFICE,
    FIELD_SUBOFFICE_OWNER,
    FIELD_SUCCESS,
    FIELD_SYSTEM,
    FIELD_LEAD_ID,
    WRITE_ASSIGN_AUTO,
    WRITE_SUCCESS_NO,
    WRITE_SUCCESS_YES,
    QUEUE_POINTER_TABLE,
    get_field,
)
from channel_queue_assign import (  # noqa: E402
    eligible_for_channel_queue,
    parse_channel_queue_map,
    parse_queue_pointers,
    pick_queue_assignee,
)
from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
    send_alert_webhook,
)
from option_field_match import (  # noqa: E402
    is_agent_country,
    is_agent_product_empty,
    is_agent_product_pending,
    is_assign_auto,
    is_assign_manual,
    is_assignment_exception,
    is_dup_ready,
    is_rotation_eligible,
    is_suboffice_country,
    is_success_assigned,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("assign-unblock")

RECENT_HOURS = int(os.environ.get("ASSIGN_UNBLOCK_RECENT_HOURS", "168"))
MAX_RECORDS = int(os.environ.get("ASSIGN_UNBLOCK_MAX_RECORDS", "500"))
DRY_RUN = os.environ.get("ASSIGN_UNBLOCK_DRY_RUN", "false").lower() == "true"
PENDING_ALERT_MINUTES = int(os.environ.get("ASSIGN_PENDING_ALERT_MINUTES", "10"))
PENDING_ALERT_WINDOW_MINUTES = int(os.environ.get("ASSIGN_PENDING_ALERT_WINDOW_MINUTES", "3"))
FIELD_PENDING_ALERT_AT = os.environ.get("ASSIGN_PENDING_ALERT_FIELD", "待确认超时告警时间")


def _search_records(token: str, table_id: str, body: dict) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/search?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json=body, max_retries=3)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书查询失败 table={table_id}: {data}")
        body_data = data.get("data", {})
        items.extend(body_data.get("items", []))
        if not body_data.get("has_more"):
            break
        page_token = body_data.get("page_token", "")
        if not page_token:
            break
    return items


def _update_record(token: str, table_id: str, record_id: str, fields: dict) -> bool:
    resp = feishu_api(
        "PUT",
        (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/{record_id}"
        ),
        token=token,
        json={"fields": fields},
        max_retries=3,
    )
    ok = resp.json().get("code") == 0
    if not ok:
        log.error("更新失败 table=%s record=%s fields=%s resp=%s", table_id, record_id, fields, resp.json())
    return ok


def _assignee_fields_empty(fields: dict) -> bool:
    for key in (FIELD_QUEUE_ASSIGNEE, FIELD_SUBOFFICE_OWNER, FIELD_AGENT_ASSIGNEE):
        if extract_text(fields.get(key, "")):
            return False
    return True


def _is_stuck_success(fields: dict) -> bool:
    if not is_success_assigned(fields.get(FIELD_SUCCESS, "")):
        return False
    if not _assignee_fields_empty(fields):
        return False
    system = extract_text(fields.get(FIELD_SYSTEM, ""))
    final = extract_text(fields.get(FIELD_ASSIGNEE, ""))
    return system in ERROR_ASSIGNEES or final in ERROR_ASSIGNEES or (not system and not final)


def _load_agent_rules(token: str) -> list[dict]:
    records = _search_records(
        token,
        AGENT_RULE_TABLE,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [{"field_name": "是否启用", "operator": "is", "value": ["启用"]}],
            },
            "field_names": ["国家", "产品大类", "具体型号", "业务员"],
            "page_size": 100,
        },
    )
    rules: list[dict] = []
    for record in records:
        fields = record.get("fields", {})
        country = extract_text(fields.get("国家", "")).strip()
        category = extract_text(fields.get("产品大类", "")).strip()
        model = extract_text(fields.get("具体型号", "")).strip()
        assignee = extract_text(fields.get("业务员", "")).strip()
        if country and category and model and assignee:
            rules.append({"country": country, "category": category, "model": model, "assignee": assignee})
    return rules


def _match_agent_rule(rules: list[dict], country: str, category: str, model: str) -> str | None:
    for rule in rules:
        if rule["country"] != country or rule["category"] != category:
            continue
        if rule["model"] in (model, "全系列"):
            return rule["assignee"]
    return None


def _advance_pointer_if_stale(
    token: str,
    fields: dict,
    pointers: dict,
    queue_map: dict,
) -> bool:
    """工作流已写业务员但未推进指针时，若当前指针仍指向该业务员则 +1。

    单人队列（max_rank=1）推进后仍是同一顺位，跳过以免空转。
    """
    queue_assignee = extract_text(fields.get(FIELD_QUEUE_ASSIGNEE, "")).strip()
    queue_key = extract_text(fields.get(FIELD_QUEUE_KEY, "")).strip()
    if not queue_assignee or not queue_key:
        return False
    if not is_success_assigned(fields.get(FIELD_SUCCESS, "")):
        return False

    pick = pick_queue_assignee(queue_key, pointers, queue_map)
    if not pick or pick.assignee != queue_assignee:
        return False
    if pick.next_rank == pick.used_rank:
        return False

    lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
    log.info(
        "同步队列指针 %s queue=%s %s→%s (assignee=%s)",
        lead_id,
        pick.resolved_queue_key or queue_key,
        pick.used_rank,
        pick.next_rank,
        pick.assignee,
    )
    if DRY_RUN:
        return True
    if not _update_record(
        token,
        QUEUE_POINTER_TABLE,
        pick.pointer_record_id,
        {"当前顺序号": pick.next_rank},
    ):
        return False
    resolved_key = pick.resolved_queue_key or queue_key
    pointers[resolved_key] = type(pointers[resolved_key])(
        record_id=pick.pointer_record_id,
        current=pick.next_rank,
        max_rank=pick.max_rank,
    )
    return True


def _needs_agent_product_clear(fields: dict) -> bool:
    if not is_assign_auto(fields.get(FIELD_ASSIGN_METHOD, "")):
        return False
    if not is_agent_country(fields.get(FIELD_AGENT_COUNTRY, "")):
        return False
    if is_suboffice_country(fields.get(FIELD_SUBOFFICE, "")):
        return False
    agent_product = fields.get(FIELD_AGENT_PRODUCT, "")
    if not (is_agent_product_empty(agent_product) or is_agent_product_pending(agent_product)):
        return False
    if extract_text(fields.get(FIELD_AGENT_ASSIGNEE, "")):
        return False
    category = extract_text(fields.get(FIELD_PRODUCT_CAT, ""))
    model = extract_text(fields.get(FIELD_PRODUCT_MODEL, ""))
    return bool(category and model)


def _sync_messenger_duplicates(token: str, records: list[dict], cutoff_ms: int) -> int:
    by_email: dict[str, str] = {}
    for item in records:
        fields = item.get("fields", {})
        if extract_text(fields.get(FIELD_CHANNELS, "")) != "Facebook":
            continue
        email = extract_text(fields.get(FIELD_EMAIL, "")).lower().strip()
        queue = extract_text(fields.get(FIELD_QUEUE_ASSIGNEE, ""))
        if email and queue:
            by_email[email] = queue

    fixed = 0
    for item in records:
        fields = item.get("fields", {})
        entry_ms = fields.get(FIELD_ENTRY_TIME, 0) or 0
        if entry_ms and entry_ms < cutoff_ms:
            continue
        if extract_text(fields.get(FIELD_CHANNELS, "")) not in ("Facebook-Messenger", "Instagram"):
            continue
        assignee = extract_text(fields.get(FIELD_ASSIGNEE, ""))
        queue = extract_text(fields.get(FIELD_QUEUE_ASSIGNEE, ""))
        if queue or assignee not in ("", "未命中规则"):
            continue
        email = extract_text(fields.get(FIELD_EMAIL, "")).lower().strip()
        sibling_queue = by_email.get(email)
        if not sibling_queue:
            continue

        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        record_id = item.get("record_id", "")
        log.info("同步 Messenger 业务员 %s -> %s", lead_id or record_id, sibling_queue)
        if DRY_RUN:
            fixed += 1
            continue
        if _update_record(
            token,
            FEISHU_TABLE_ID,
            record_id,
            {FIELD_QUEUE_ASSIGNEE: sibling_queue, FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO, FIELD_SUCCESS: WRITE_SUCCESS_YES},
        ):
            fixed += 1
    return fixed


def _record_field_names() -> list[str]:
    return [
        FIELD_ENTRY_TIME,
        FIELD_LEAD_ID,
        FIELD_ASSIGN_METHOD,
        FIELD_CHANNELS,
        FIELD_COUNTRY,
        FIELD_SUBOFFICE,
        FIELD_ROTATION,
        FIELD_DUP_READY,
        FIELD_STATUS,
        FIELD_ASSIGNEE,
        FIELD_SYSTEM,
        FIELD_EMAIL,
        FIELD_QUEUE_ASSIGNEE,
        FIELD_QUEUE_KEY,
        FIELD_SUCCESS,
        FIELD_AGENT_COUNTRY,
        FIELD_AGENT_PRODUCT,
        FIELD_AGENT_ASSIGNEE,
        FIELD_SUBOFFICE_OWNER,
        FIELD_ASSIGN_SOURCE,
        FIELD_PRODUCT_CAT,
        FIELD_PRODUCT_MODEL,
        FIELD_PENDING_ALERT_AT,
    ]


def _fetch_exception_records(token: str) -> list[dict]:
    return _search_records(
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
            "field_names": _record_field_names(),
            "page_size": 100,
        },
    )


def _sort_records_exceptions_first(records: list[dict]) -> list[dict]:
    """优先处理分配异常，缩短 FB/Gmail 新线索的可见异常窗口。"""
    exceptions: list[dict] = []
    others: list[dict] = []
    for item in records:
        fields = item.get("fields", {})
        if is_assignment_exception(get_field(fields, FIELD_STATUS, "")):
            exceptions.append(item)
        else:
            others.append(item)
    return exceptions + others


def _merge_records(primary: list[dict], extra: list[dict]) -> list[dict]:
    seen = {item.get("record_id") for item in primary if item.get("record_id")}
    merged = list(primary)
    for item in extra:
        record_id = item.get("record_id")
        if record_id and record_id not in seen:
            merged.append(item)
            seen.add(record_id)
    return merged


def _collect_pending_agent_confirm_alerts(records: list[dict], now_ms: int) -> list[tuple[str, str]]:
    """收集「代理产品待确认超过阈值」且落入告警窗口的线索摘要。"""
    alert_items: list[tuple[str, str]] = []
    upper_minutes = PENDING_ALERT_MINUTES + max(PENDING_ALERT_WINDOW_MINUTES, 1)

    for item in records:
        fields = item.get("fields", {})
        if not is_assign_auto(fields.get(FIELD_ASSIGN_METHOD, "")):
            continue
        if not is_agent_country(fields.get(FIELD_AGENT_COUNTRY, "")):
            continue
        if not is_agent_product_pending(fields.get(FIELD_AGENT_PRODUCT, "")):
            continue
        if is_suboffice_country(fields.get(FIELD_SUBOFFICE, "")):
            continue
        if is_success_assigned(fields.get(FIELD_SUCCESS, "")):
            continue
        if extract_text(fields.get(FIELD_PENDING_ALERT_AT, "")):
            continue

        entry_ms = fields.get(FIELD_ENTRY_TIME, 0) or 0
        if not isinstance(entry_ms, (int, float)) or entry_ms <= 0:
            continue
        age_minutes = (now_ms - int(entry_ms)) / 60000
        if age_minutes < PENDING_ALERT_MINUTES or age_minutes >= upper_minutes:
            continue

        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, "")) or item.get("record_id", "")
        record_id = item.get("record_id", "")
        if not record_id:
            continue
        queue_key = extract_text(fields.get(FIELD_QUEUE_KEY, "")) or "-"
        status = extract_text(fields.get(FIELD_STATUS, "")) or "-"
        alert_items.append(
            (
                record_id,
                f"- 线索ID={lead_id} 待确认{int(age_minutes)}分钟 队列Key={queue_key} 状态={status}",
            )
        )

    return alert_items


def run() -> int:
    token = get_feishu_token()
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).timestamp() * 1000)

    records = _sort_records_exceptions_first(
        _merge_records(
            _search_records(
                token,
                FEISHU_TABLE_ID,
                {
                    "sort": [{"field_name": FIELD_ENTRY_TIME, "desc": True}],
                    "field_names": _record_field_names(),
                },
            )[:MAX_RECORDS],
            _fetch_exception_records(token),
        )
    )

    pointers = parse_queue_pointers(
        _search_records(
            token,
            QUEUE_POINTER_TABLE,
            {"field_names": ["队列Key", "当前顺序号", "最大顺序号"], "page_size": 100},
        )
    )
    queue_map = parse_channel_queue_map(
        _search_records(
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
    agent_rules = _load_agent_rules(token)

    reset_count = 0
    agent_clear_count = 0
    queue_assign_count = 0
    pointer_sync_count = 0
    manual_to_auto_count = 0
    messenger_fixed = _sync_messenger_duplicates(token, records, cutoff_ms)

    for item in records:
        fields = item.get("fields", {})
        entry_ms = fields.get(FIELD_ENTRY_TIME, 0) or 0
        is_exception = is_assignment_exception(fields.get(FIELD_STATUS, ""))
        if entry_ms and entry_ms < cutoff_ms and not is_exception:
            continue

        lead_id = extract_text(get_field(fields, FIELD_LEAD_ID, ""))
        record_id = item.get("record_id", "")

        if _is_stuck_success(fields):
            log.info("重置卡住的分配标记 %s 是否成功分配 是→否", lead_id or record_id)
            if DRY_RUN:
                reset_count += 1
            elif _update_record(token, FEISHU_TABLE_ID, record_id, {FIELD_SUCCESS: WRITE_SUCCESS_NO}):
                fields[FIELD_SUCCESS] = WRITE_SUCCESS_NO
                reset_count += 1

        if _needs_agent_product_clear(fields):
            country = extract_text(fields.get(FIELD_COUNTRY, ""))
            category = extract_text(fields.get(FIELD_PRODUCT_CAT, ""))
            model = extract_text(fields.get(FIELD_PRODUCT_MODEL, ""))
            if category == ACOUSTIC_CATEGORY:
                patch = {FIELD_AGENT_PRODUCT: "否"}
            else:
                matched = _match_agent_rule(agent_rules, country, category, model)
                patch = (
                    {FIELD_AGENT_PRODUCT: "是", FIELD_AGENT_ASSIGNEE: matched, FIELD_SUCCESS: WRITE_SUCCESS_YES}
                    if matched
                    else {FIELD_AGENT_PRODUCT: "否"}
                )
            log.info("代理判断 %s patch=%s", lead_id or record_id, patch)
            if DRY_RUN:
                agent_clear_count += 1
                fields.update(patch)
            elif _update_record(token, FEISHU_TABLE_ID, record_id, patch):
                fields.update(patch)
                agent_clear_count += 1

        if eligible_for_channel_queue(fields):
            queue_key = extract_text(fields.get(FIELD_QUEUE_KEY, ""))
            pick = pick_queue_assignee(queue_key, pointers, queue_map)
            if pick:
                log.info("渠道轮转分配 %s queue=%s -> %s", lead_id or record_id, pick.resolved_queue_key or queue_key, pick.assignee)
                if DRY_RUN:
                    queue_assign_count += 1
                elif _update_record(
                    token,
                    FEISHU_TABLE_ID,
                    record_id,
                    {FIELD_QUEUE_ASSIGNEE: pick.assignee, FIELD_SUCCESS: WRITE_SUCCESS_YES},
                ):
                    queue_assign_count += 1
                    resolved_key = pick.resolved_queue_key or queue_key
                    if _update_record(
                        token,
                        QUEUE_POINTER_TABLE,
                        pick.pointer_record_id,
                        {"当前顺序号": pick.next_rank},
                    ):
                        pointers[resolved_key] = type(pointers[resolved_key])(
                            record_id=pick.pointer_record_id,
                            current=pick.next_rank,
                            max_rank=pick.max_rank,
                        )
            else:
                log.warning("队列无可用业务员 %s queue=%s", lead_id or record_id, queue_key)
        elif _advance_pointer_if_stale(token, fields, pointers, queue_map):
            pointer_sync_count += 1

        channels = extract_text(fields.get(FIELD_CHANNELS, ""))
        assignee = extract_text(fields.get(FIELD_ASSIGNEE, ""))

        if not is_assign_manual(fields.get(FIELD_ASSIGN_METHOD, "")):
            continue
        if channels not in ("Facebook",):
            continue
        if is_suboffice_country(fields.get(FIELD_SUBOFFICE, "")):
            continue
        if not is_dup_ready(fields.get(FIELD_DUP_READY, "")):
            continue
        if not is_rotation_eligible(fields.get(FIELD_ROTATION, "")):
            continue
        if assignee and assignee not in ("未命中规则", ""):
            continue
        # 分配状态为公式字段，API 常返回 option id 而非中文标签，不能据此跳过

        log.info("解除阻塞 %s 分配方式 人工→自动", lead_id or record_id)
        if DRY_RUN:
            manual_to_auto_count += 1
        elif _update_record(token, FEISHU_TABLE_ID, record_id, {FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO}):
            manual_to_auto_count += 1

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    pending_alerts = _collect_pending_agent_confirm_alerts(records, now_ms)
    if pending_alerts:
        alert_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        send_alert_webhook(
            "⚠️ 线索分配告警：代理产品“待确认”超过"
            f"{PENDING_ALERT_MINUTES}分钟\n"
            + "\n".join(line for _, line in pending_alerts[:20])
        )
        if not DRY_RUN:
            marked = 0
            for record_id, _ in pending_alerts:
                if _update_record(token, FEISHU_TABLE_ID, record_id, {FIELD_PENDING_ALERT_AT: alert_time}):
                    marked += 1
            log.warning("已写入告警去重标记 count=%s", marked)
        log.warning("已发送待确认超时告警 count=%s", len(pending_alerts))

    log.info(
        "完成: reset=%s agent=%s queue=%s pointer_sync=%s manual→auto=%s messenger=%s pending_alert=%s dry_run=%s",
        reset_count,
        agent_clear_count,
        queue_assign_count,
        pointer_sync_count,
        manual_to_auto_count,
        messenger_fixed,
        len(pending_alerts),
        DRY_RUN,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
