"""
飞书多维表格写入 — token 获取、去重、记录创建/更新
"""

import os
import sys
import logging
from datetime import datetime, timedelta

import requests

from assignment_fields import (
    FIELD_ASSIGN_METHOD,
    FIELD_CHANNELS,
    FIELD_COUNTRY,
    FIELD_LEAD_ID,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_SUB_CHANNEL,
    FIELD_SUCCESS,
    SUB_CHANNEL_TO_CHANNEL,
    WRITE_ASSIGN_AUTO,
    WRITE_SUCCESS_NO,
)

log = logging.getLogger("lead-poller")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    log.error("缺少必需环境变量：%s", name)
    sys.exit(1)


# 飞书 Base 配置必须由环境变量显式提供，避免静默回退到旧表。
FEISHU_APP_TOKEN = _require_env("FEISHU_APP_TOKEN")
FEISHU_TABLE_ID = _require_env("FEISHU_TABLE_ID")
FEISHU_FIELD_NAME = "Enquiry details（询盘内容）"
FEISHU_EMAIL_FIELD = "Email（客户邮箱）"
FEISHU_CLUE_LEVEL = "Clue level（线索等级）"
FEISHU_LEAD_GRADING = "Lead Grading Criteria（分级依据）"
FEISHU_FOLLOWUP_PRIORITY = "Follow-up Priority（跟进优先级）"
FEISHU_MSGID_FIELD = "Gmail_Msg_ID"
FEISHU_FB_LEADGEN_FIELD = "Facebook Leadgen ID"
FEISHU_CHANNELS_FIELD = "Channels（渠道）"
FEISHU_AUTOREPLY_STATUS = "Auto-Reply Status"
FEISHU_AUTOREPLY_SENT_AT = "Auto-Reply Sent At"
FEISHU_AUTOREPLY_TEMPLATE = "Auto-Reply Template"
FEISHU_AUTOREPLY_ERROR = "Auto-Reply Error"


def get_feishu_token() -> str:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        log.error("缺少飞书凭据：FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    data = resp.json()
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"飞书 token 获取失败: {data}")
    return token


def safe_json(resp, label: str) -> dict:
    """安全解析响应 JSON，失败时打印原始内容和响应头便于排查"""
    try:
        return resp.json()
    except Exception as e:
        headers_str = dict(resp.headers)
        log.error("[%s] 飞书响应解析失败 (HTTP %s): %s | 原始内容: %s | 响应头: %s",
                  label, resp.status_code, e, resp.text[:500], headers_str)
        return {"code": -1, "msg": f"json_parse_error: {e}", "raw": resp.text[:200]}


def check_feishu_duplicate(token: str, gmail_msg_id: str,
                           app_token: str = "", table_id: str = "") -> bool:
    """按 Gmail_Msg_ID 精确查重（跨批次防重复写入）"""
    app_token = app_token or FEISHU_APP_TOKEN
    table_id = table_id or FEISHU_TABLE_ID
    if not gmail_msg_id:
        return False
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FEISHU_MSGID_FIELD, "operator": "is", "value": [gmail_msg_id]}
                ],
            },
            "field_names": [FEISHU_MSGID_FIELD],
        },
        timeout=15,
    )
    data = safe_json(resp, "feishu_dedup")
    if data.get("code") != 0:
        log.warning("飞书去重查询异常: %s", data)
        return False
    items = data.get("data", {}).get("items", [])
    return bool(items)


FEISHU_EMAIL_FIELD = "Email（客户邮箱）"
_FEISHU_ENTRY_TIME = "Entry Time（录入时间）"
_EMAIL_DEDUP_HOURS = 12


def _search_feishu_records(token: str, body: dict,
                           app_token: str = "", table_id: str = "") -> list:
    app_token = app_token or FEISHU_APP_TOKEN
    table_id = table_id or FEISHU_TABLE_ID
    items: list = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/search?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        data = safe_json(resp, "feishu_search")
        if data.get("code") != 0:
            log.warning("飞书查询异常: %s", data)
            return items
        chunk = data.get("data", {})
        items.extend(chunk.get("items", []))
        if not chunk.get("has_more"):
            break
        page_token = chunk.get("page_token", "")
        if not page_token:
            break
    return items


def check_feishu_fb_leadgen_duplicate(token: str, leadgen_id: str,
                                      app_token: str = "", table_id: str = "") -> dict | None:
    """按 Facebook Leadgen ID 精确查重（跨 webhook / cron / poller）。"""
    leadgen_id = (leadgen_id or "").strip()
    if not leadgen_id:
        return None
    items = _search_feishu_records(
        token,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FEISHU_FB_LEADGEN_FIELD, "operator": "is", "value": [leadgen_id]},
                ],
            },
            "field_names": [FEISHU_FB_LEADGEN_FIELD, FIELD_LEAD_ID],
            "page_size": 5,
        },
        app_token=app_token,
        table_id=table_id,
    )
    return items[0] if items else None


def check_feishu_fb_contact_duplicate(token: str, customer_email: str = "",
                                      phone: str = "",
                                      hours: int = 720,
                                      app_token: str = "", table_id: str = "") -> dict | None:
    """Facebook 渠道按邮箱或电话查重（默认 7 天窗口）。"""
    email = (customer_email or "").strip().lower()
    phone_digits = "".join(c for c in (phone or "") if c.isdigit())
    if not email and len(phone_digits) < 8:
        return None
    cutoff_ms = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
    conditions = [
        {"field_name": FEISHU_CHANNELS_FIELD, "operator": "is", "value": ["Facebook"]},
    ]
    if email and email not in {"n/a", "na", "none", "-"}:
        # contains + 小写：飞书文本 is 区分大小写，避免 Megaton@x / megaton@x 漏判
        conditions.append(
            {"field_name": FEISHU_EMAIL_FIELD, "operator": "contains", "value": [email]}
        )
    elif phone_digits:
        conditions.append(
            {"field_name": "Phone（客户电话）", "operator": "contains", "value": [phone_digits[-8:]]}
        )
    else:
        return None
    items = _search_feishu_records(
        token,
        {
            "filter": {"conjunction": "and", "conditions": conditions},
            "field_names": [FEISHU_EMAIL_FIELD, "Phone（客户电话）", _FEISHU_ENTRY_TIME, FIELD_LEAD_ID],
            "sort": [{"field_name": _FEISHU_ENTRY_TIME, "desc": True}],
            "page_size": 20,
        },
        app_token=app_token,
        table_id=table_id,
    )
    for it in items:
        entry_ms = it.get("fields", {}).get(_FEISHU_ENTRY_TIME)
        if isinstance(entry_ms, (int, float)) and entry_ms >= cutoff_ms:
            return it
    return None


def check_feishu_email_duplicate(token: str, customer_email: str,
                                  hours: int = _EMAIL_DEDUP_HOURS,
                                  extra_fields: list = None,
                                  app_token: str = "", table_id: str = "") -> dict | None:
    """按客户邮箱查重（近 N 小时内同邮箱记录）。
    hours 控制时间窗口（默认 12h）；extra_fields 追加返回字段（供调用方读取更多列）。
    返回已有记录，无记录或超过时间窗口返回 None。
    """
    app_token = app_token or FEISHU_APP_TOKEN
    table_id = table_id or FEISHU_TABLE_ID
    if not customer_email:
        return None
    cutoff_ms = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
    field_names = [FEISHU_EMAIL_FIELD, _FEISHU_ENTRY_TIME, FEISHU_FIELD_NAME]
    if extra_fields:
        field_names.extend(f for f in extra_fields if f not in field_names)
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FEISHU_EMAIL_FIELD, "operator": "is", "value": [customer_email]},
                ],
            },
            "field_names": field_names,
            "sort": [{"field_name": _FEISHU_ENTRY_TIME, "desc": True}],
            "page_size": 20,
        },
        timeout=15,
    )
    data = safe_json(resp, "feishu_email_dedup")
    if data.get("code") != 0:
        log.warning("飞书邮箱去重查询异常: %s", data)
        return None
    # 飞书 Date 字段 isGreater 不支持带时间格式，改代码层按毫秒时间戳过滤窗口
    for it in data.get("data", {}).get("items", []):
        entry_ms = it.get("fields", {}).get(_FEISHU_ENTRY_TIME)
        if isinstance(entry_ms, (int, float)) and entry_ms >= cutoff_ms:
            return it
    return None


def merge_feishu_record(token: str, record_id: str, merged_content: str,
                        new_msg_id: str,
                        app_token: str = "", table_id: str = "",
                        extra_fields: dict | None = None) -> dict:
    """合并追加新询盘到已有飞书记录。默认更新 Enquiry details 和 Gmail_Msg_ID。"""
    app_token = app_token or FEISHU_APP_TOKEN
    table_id = table_id or FEISHU_TABLE_ID
    fields = {
        FEISHU_FIELD_NAME: merged_content,
        FEISHU_MSGID_FIELD: new_msg_id,
    }
    if extra_fields:
        fields.update(extra_fields)
    resp = requests.put(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
        f"/tables/{table_id}/records/{record_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"fields": fields},
        timeout=15,
    )
    return safe_json(resp, "feishu_merge")


def create_feishu_record(token: str, inquiry_content: str, clue_level: str = "",
                         grading_text: str = "", attachment_tokens: list = None,
                         gmail_msg_id: str = "", keyword: str = "",
                         app_token: str = "", table_id: str = "",
                         channels: str = "", sub_channel: str = "",
                         country: str = "", product_category: str = "",
                         product_model: str = "") -> dict:
    """在飞书多维表格中新建记录"""
    app_token = app_token or FEISHU_APP_TOKEN
    table_id = table_id or FEISHU_TABLE_ID
    fields = {FEISHU_FIELD_NAME: inquiry_content}
    if gmail_msg_id:
        fields[FEISHU_MSGID_FIELD] = gmail_msg_id
    if clue_level:
        fields[FEISHU_CLUE_LEVEL] = clue_level
    if grading_text:
        fields[FEISHU_LEAD_GRADING] = grading_text
    if channels:
        fields[FEISHU_CHANNELS_FIELD] = channels
    if sub_channel:
        fields[FIELD_SUB_CHANNEL] = sub_channel
    elif channels and channels in SUB_CHANNEL_TO_CHANNEL:
        fields[FIELD_SUB_CHANNEL] = channels
    if country:
        fields[FIELD_COUNTRY] = country
    if product_category:
        fields[FIELD_PRODUCT_CAT] = product_category
    if product_model and product_model != "无法识别":
        fields[FIELD_PRODUCT_MODEL] = product_model
    fields[FEISHU_FOLLOWUP_PRIORITY] = "Pending"
    fields[FEISHU_AUTOREPLY_STATUS] = "Pending"
    # 与 Facebook 一致：写入分配触发前置字段，避免公式就绪后工作流因缺省值不再触发
    fields[FIELD_ASSIGN_METHOD] = WRITE_ASSIGN_AUTO
    fields[FIELD_SUCCESS] = WRITE_SUCCESS_NO
    if attachment_tokens:
        fields["Enquiry attachments（询盘附件）"] = attachment_tokens
    # Keyword 字段已于 2026-06-23 从飞书表删除，不再写入
    if keyword:
        log.debug("URL keyword omitted (field removed): %s", keyword)
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"fields": fields},
        timeout=15,
    )
    return safe_json(resp, "feishu_create")


def update_feishu_autoreply(token: str, record_id: str,
                            status: str, sent_at: str = "",
                            template: str = "", error: str = "", msg_id: str = "",
                            app_token: str = "", table_id: str = "") -> bool:
    """更新飞书记录的自动回复状态。msg_id 写入 Gmail_Msg_ID（指向最新回复邮件）。"""
    app_token = app_token or FEISHU_APP_TOKEN
    table_id = table_id or FEISHU_TABLE_ID
    fields = {FEISHU_AUTOREPLY_STATUS: status}
    if sent_at:
        fields[FEISHU_AUTOREPLY_SENT_AT] = sent_at
    if template:
        fields[FEISHU_AUTOREPLY_TEMPLATE] = template
    if error:
        fields[FEISHU_AUTOREPLY_ERROR] = error
    if msg_id:
        fields[FEISHU_MSGID_FIELD] = msg_id
    try:
        resp = requests.put(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/{record_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"fields": fields},
            timeout=15,
        )
        data = safe_json(resp, "feishu_autoreply_update")
        return data.get("code") == 0
    except Exception as e:
        log.warning("飞书自动回复状态更新失败: %s", e)
        return False
