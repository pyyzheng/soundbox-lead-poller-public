"""线索分配 / 询盘更新 — 飞书 IM 通知（工作流兜底）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

import requests

from feishu_utils import FEISHU_APP_TOKEN, FEISHU_TABLE_ID, extract_text, feishu_api

from assignment_fields import FIELD_ASSIGNEE, FIELD_LEAD_ID, FIELD_STATUS

log = logging.getLogger("lead-notify")

ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")

FIELD_NOTIFY_USER = "匹配的业务员账号"
FIELD_ENQUIRY = "Enquiry details（询盘内容）"
FIELD_CUSTOMER = "Customer Name（客户名称）"
FIELD_COUNTRY = "Country（国家）"
FIELD_ENTRY_TIME = "Entry Time（录入时间）"

SALES_NOTIFY_TABLE = "tblXq1rE7OQCrSgJ"
FOLLOWUP_MARKER_ASSIGN = "SYS_NOTIFY:assign"
FOLLOWUP_MARKER_ENQUIRY = "SYS_NOTIFY:enquiry"


def is_valid_assignee(name: str) -> bool:
    name = (name or "").strip()
    return bool(name) and name not in ERROR_ASSIGNEES


def extract_user_open_ids(field_val: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(field_val, list):
        for item in field_val:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
    elif isinstance(field_val, dict) and field_val.get("id"):
        ids.append(str(field_val["id"]))
    return ids


def record_url(record_id: str) -> str:
    return (
        f"https://rcn1z5q6iyyc.feishu.cn/base/{FEISHU_APP_TOKEN}"
        f"?table={FEISHU_TABLE_ID}&record={quote(record_id)}"
    )


def enquiry_snippet(text: str, limit: int = 120) -> str:
    body = (text or "").strip()
    body = re.sub(r"\s+", " ", body)
    if len(body) <= limit:
        return body
    return body[: limit - 3] + "..."


def build_assign_card(lead_id: str, customer: str, country: str, assignee: str, url: str) -> dict:
    lines = [f"线索 **{lead_id or '—'}** 已分配给您（{assignee}）。"]
    if customer:
        lines.append(f"客户：{customer}")
    if country:
        lines.append(f"国家：{country}")
    lines.append("请及时打开线索跟进。")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚀 新线索分配提醒"},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": "\n".join(lines)},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开线索"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            },
        ],
    }


def build_enquiry_update_card(
    lead_id: str, customer: str, snippet: str, url: str,
) -> dict:
    content = f"线索 **{lead_id or '—'}** 的询盘内容已更新，请及时查看。"
    if customer:
        content += f"\n客户：{customer}"
    if snippet:
        content += f"\n\n更新摘要：\n{snippet}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📝 询盘内容更新提醒"},
            "template": "orange",
        },
        "elements": [
            {"tag": "markdown", "content": content},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看详情"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            },
        ],
    }


def send_im_card(token: str, open_id: str, card: dict) -> bool:
    resp = feishu_api(
        "POST",
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        token=token,
        json={
            "receive_id": open_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
    )
    data = resp.json()
    if data.get("code") == 0:
        return True
    log.error("IM 发送失败 open_id=%s resp=%s", open_id, data)
    return False


def load_sales_open_id_map(token: str) -> dict[str, str]:
    """业务通知名单：业务员姓名 -> open_id"""
    mapping: dict[str, str] = {}
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{SALES_NOTIFY_TABLE}/records/search?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json={"page_size": 100})
        data = resp.json()
        if data.get("code") != 0:
            log.warning("业务通知名单查询失败: %s", data)
            break
        for item in data.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            name = extract_text(fields.get("业务名单", "")).strip()
            users = fields.get("对应业务", [])
            for open_id in extract_user_open_ids(users):
                mapping[name] = open_id
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token", "")
    return mapping


def resolve_notify_open_id(fields: dict, sales_map: dict[str, str]) -> str:
    for open_id in extract_user_open_ids(fields.get(FIELD_NOTIFY_USER)):
        return open_id
    assignee = extract_text(fields.get(FIELD_ASSIGNEE, "")).strip()
    return sales_map.get(assignee, "")


def followup_marker_exists(
    token: str,
    followup_table_id: str,
    lead_record_id: str,
    marker_prefix: str,
) -> bool:
    resp = feishu_api(
        "POST",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{followup_table_id}/records/search",
        token=token,
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "Related Lead",
                        "operator": "is",
                        "value": [lead_record_id],
                    }
                ],
            },
            "page_size": 20,
        },
    )
    data = resp.json()
    if data.get("code") != 0:
        return False
    for item in data.get("data", {}).get("items", []):
        details = extract_text(item.get("fields", {}).get("Follow-up Details", ""))
        if details.startswith(marker_prefix):
            return True
    return False


def write_followup_marker(
    token: str,
    followup_table_id: str,
    lead_record_id: str,
    marker: str,
    details: str,
) -> None:
    from datetime import datetime, timezone

    fields = {
        "Related Lead": [{"id": lead_record_id, "type": "text"}],
        "Follow-up Details": details,
        "Contact Result": "Other",
        "Contact Method": "Feishu IM",
        "Follow-up Time": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    feishu_api(
        "POST",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{followup_table_id}/records",
        token=token,
        json={"fields": fields},
    )
