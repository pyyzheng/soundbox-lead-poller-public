#!/usr/bin/env python3
"""删除 Facebook 表单线索重复行（同邮箱/电话，保留线索ID最小的一条）。

典型场景：Webhook 实时写入 + Cron/Poller 补录，或补录时本地 cache 丢失。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from assignment_fields import FIELD_LEAD_ID  # noqa: E402
from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fb-form-dedup")

FIELD_ENTRY = "Entry Time（录入时间）"
FIELD_CHANNELS = "Channels（渠道）"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_PHONE = "Phone（客户电话）"
CHANNEL_FB = "Facebook"

RECENT_DAYS = int(os.environ.get("FB_FORM_DEDUP_DAYS", "90"))
DRY_RUN = os.environ.get("FB_FORM_DEDUP_DRY_RUN", "false").lower() == "true"


def _lead_num(lead_id: str) -> int:
    return int(re.sub(r"\D", "", lead_id or "0") or "0")


def _normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value or value in {"n/a", "na", "none", "-"}:
        return ""
    if "," in value:
        for part in value.split(","):
            part = part.strip()
            if part and part not in {"n/a", "na"}:
                return part
        return ""
    return value


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit())


def _search_all(token: str, body: dict) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/search?page_size=500"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json=body, max_retries=3)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书查询失败: {data}")
        chunk = data.get("data", {})
        items.extend(chunk.get("items", []))
        if not chunk.get("has_more"):
            break
        page_token = chunk.get("page_token", "")
        if not page_token:
            break
    return items


def _delete_record(token: str, record_id: str) -> bool:
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records/{record_id}"
    )
    resp = feishu_api("DELETE", url, token=token, max_retries=3)
    data = resp.json()
    return data.get("code") == 0


def main() -> int:
    token = get_feishu_token()
    cutoff_ms = int(cutoff.timestamp() * 1000) if RECENT_DAYS > 0 else None

    conditions = [
        {"field_name": FIELD_CHANNELS, "operator": "is", "value": [CHANNEL_FB]},
    ]
    if cutoff_ms is not None:
        conditions.append(
            {"field_name": FIELD_ENTRY, "operator": "isGreater", "value": ["ExactDate", cutoff_ms]}
        )

    items = _search_all(
        token,
        {
            "filter": {"conjunction": "and", "conditions": conditions},
            "field_names": [FIELD_ENTRY, FIELD_EMAIL, FIELD_PHONE, FIELD_LEAD_ID],
            "sort": [{"field_name": FIELD_ENTRY, "desc": True}],
        },
    )
    window = f"近 {RECENT_DAYS} 天" if RECENT_DAYS > 0 else "全量"
    log.info("扫描 Facebook 线索 %s 条（%s）", len(items), window)

    by_email: dict[str, list[dict]] = defaultdict(list)
    by_phone: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        fields = it.get("fields", {})
        email = _normalize_email(extract_text(fields.get(FIELD_EMAIL)))
        phone = _normalize_phone(extract_text(fields.get(FIELD_PHONE)))
        row = {
            "record_id": it["record_id"],
            "lead_id": extract_text(fields.get(FIELD_LEAD_ID)),
            "email": email,
            "phone": phone,
        }
        if email:
            by_email[email].append(row)
        elif len(phone) >= 8:
            by_phone[phone].append(row)

    to_delete: dict[str, dict] = {}
    for group in list(by_email.values()) + list(by_phone.values()):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda r: _lead_num(r["lead_id"]))
        keeper = ordered[0]
        for dup in ordered[1:]:
            to_delete[dup["record_id"]] = {
                "lead_id": dup["lead_id"],
                "keep": keeper["lead_id"],
                "email": dup["email"] or dup["phone"],
            }

    if not to_delete:
        log.info("未发现可删除的重复 Facebook 表单线索")
        return 0

    log.info("将删除 %s 条重复记录（DRY_RUN=%s）", len(to_delete), DRY_RUN)
    deleted = 0
    for record_id, info in sorted(to_delete.items(), key=lambda x: _lead_num(x[1]["lead_id"])):
        log.info("DEL %s (keep %s) contact=%s", info["lead_id"], info["keep"], info["email"])
        if DRY_RUN:
            continue
        if _delete_record(token, record_id):
            deleted += 1
        else:
            log.error("删除失败 %s", info["lead_id"])

    log.info("完成: deleted=%s", deleted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
