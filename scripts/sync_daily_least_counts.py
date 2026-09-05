#!/usr/bin/env python3
"""同步「按天最少优先计数」看板表。

口径与 cloud-assignment-unblock 一致：
- 人级一本账：Gigi/Cathy/Kevin/Rita
- 昨+今（Asia/Shanghai）；人工改派非空不计
- 公区下一区来自指针表 `__DAILY_LEAST__|公区区指针`

用法：
  python scripts/sync_daily_least_counts.py
  DRY_RUN=true python scripts/sync_daily_least_counts.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from assignment_fields import (  # noqa: E402
    DAILY_LEAST_COUNT_TABLE,
    FIELD_ASSIGNEE,
    FIELD_ENTRY_TIME,
    FIELD_MANUAL_ASSIGNEE,
    QUEUE_POINTER_TABLE,
    get_field,
)
from daily_least_assign import (  # noqa: E402
    ASIA_ROSTER,
    ME_ROSTER,
    PUBLIC_REGION_ME,
    PUBLIC_REGION_POINTER_KEY,
    TRACKED_ASSIGNEES,
    accumulate_split_count,
    debt_within_roster,
    empty_split_counts,
    normalize_public_region,
    person_details_from_split,
    public_region_label,
    shanghai_day_bounds,
    to_utc_ms,
    totals_from_split,
)
from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync-daily-least")

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
TZ = ZoneInfo("Asia/Shanghai")
RULE_NOTE = (
    "计入=自动分配成功（中东/亚洲/公区/代理/查重）；"
    "不计=人工改派/Case handler 转接；"
    "公区代理不占区指针；欧洲仍顺序轮"
)


def _search(token: str, table_id: str, body: dict) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/search?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        payload = data.get("data", {}) or {}
        items.extend(payload.get("items", []) or [])
        if not payload.get("has_more"):
            break
        page_token = payload.get("page_token", "")
        if not page_token:
            break
    return items


def _update(token: str, table_id: str, record_id: str, fields: dict) -> bool:
    resp = feishu_api(
        "PUT",
        (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/{record_id}"
        ),
        token=token,
        json={"fields": fields},
    )
    data = resp.json()
    if data.get("code") != 0:
        log.error("update fail %s %s", record_id, data)
        return False
    return True


def _create(token: str, table_id: str, fields: dict) -> str:
    resp = feishu_api(
        "POST",
        (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records"
        ),
        token=token,
        json={"fields": fields},
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(data)
    return (data.get("data", {}) or {}).get("record", {}).get("record_id", "")


def load_split_counts(token: str) -> dict[str, dict[str, int]]:
    split = empty_split_counts()
    yesterday_start, today_start, tomorrow_start = shanghai_day_bounds()
    y_ms = to_utc_ms(yesterday_start)
    t_ms = to_utc_ms(today_start)
    n_ms = to_utc_ms(tomorrow_start)
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
                        "value": ["ExactDate", str(y_ms - 1)],
                    },
                    {
                        "field_name": FIELD_ENTRY_TIME,
                        "operator": "isLess",
                        "value": ["ExactDate", str(n_ms)],
                    },
                ],
            },
            # 人工改派字段名超 OpenAPI field_names 单项 50 字符上限，故不投影字段，全量取后本地读
            "page_size": 100,
        },
    )
    for item in items:
        fields = item.get("fields", {}) or {}
        entry_ms = int(fields.get(FIELD_ENTRY_TIME, 0) or 0)
        final = extract_text(get_field(fields, FIELD_ASSIGNEE, "")).strip()
        manual = extract_text(get_field(fields, FIELD_MANUAL_ASSIGNEE, "")).strip()
        accumulate_split_count(
            split,
            assignee=final,
            entry_ms=entry_ms,
            manual_assignee=manual,
            yesterday_start_ms=y_ms,
            today_start_ms=t_ms,
            tomorrow_start_ms=n_ms,
        )
    return split


def load_public_region(token: str) -> int:
    rows = _search(
        token,
        QUEUE_POINTER_TABLE,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "队列Key",
                        "operator": "is",
                        "value": [PUBLIC_REGION_POINTER_KEY],
                    }
                ],
            },
            "field_names": ["队列Key", "当前顺序号"],
            "page_size": 20,
        },
    )
    if not rows:
        return PUBLIC_REGION_ME
    cur = rows[0].get("fields", {}).get("当前顺序号")
    try:
        return normalize_public_region(int(cur))
    except (TypeError, ValueError):
        return PUBLIC_REGION_ME


def existing_rows(token: str) -> dict[str, str]:
    """业务员 → record_id"""
    rows = _search(
        token,
        DAILY_LEAST_COUNT_TABLE,
        {"field_names": ["业务员"], "page_size": 100},
    )
    out: dict[str, str] = {}
    for item in rows:
        name = extract_text((item.get("fields") or {}).get("业务员", "")).strip()
        rid = item.get("record_id", "")
        if name and rid:
            out[name] = rid
    return out


def sync(token: str) -> int:
    split = load_split_counts(token)
    totals = totals_from_split(split)
    public_region = load_public_region(token)
    public_label = public_region_label(public_region)
    details = person_details_from_split(split)
    existing = existing_rows(token)

    _, today_start, _ = shanghai_day_bounds()
    now = datetime.now(TZ)
    today_ms = to_utc_ms(today_start)
    now_ms = int(now.timestamp() * 1000)

    changed = 0
    for detail in details:
        roster = ME_ROSTER if detail.name in ME_ROSTER else ASIA_ROSTER
        debt = debt_within_roster(detail.name, totals, roster)
        fields = {
            "业务员": detail.name,
            "所属区": detail.region,
            "昨日计入": detail.yesterday,
            "今日计入": detail.today,
            "昨+今累计": detail.total,
            "区内待补": debt,
            "公区下一区": public_label,
            "统计日": today_ms,
            "刷新时间": now_ms,
            "说明": RULE_NOTE,
        }
        log.info(
            "%s %s y=%s t=%s sum=%s debt=%s public=%s",
            detail.name,
            detail.region,
            detail.yesterday,
            detail.today,
            detail.total,
            debt,
            public_label,
        )
        if DRY_RUN:
            changed += 1
            continue
        rid = existing.get(detail.name)
        if rid:
            if _update(token, DAILY_LEAST_COUNT_TABLE, rid, fields):
                changed += 1
        else:
            _create(token, DAILY_LEAST_COUNT_TABLE, fields)
            changed += 1

    missing = [n for n in TRACKED_ASSIGNEES if n not in {d.name for d in details}]
    if missing:
        log.warning("未覆盖名单: %s", missing)
    return changed


def main() -> int:
    token = get_feishu_token()
    n = sync(token)
    log.info("同步完成 rows=%s dry_run=%s table=%s", n, DRY_RUN, DAILY_LEAST_COUNT_TABLE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
