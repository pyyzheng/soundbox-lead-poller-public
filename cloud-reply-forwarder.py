#!/usr/bin/env python3
"""
cloud-reply-forwarder.py — 客户回复邮件转发 Worker

查询飞书中已检测到客户回复但未转发的记录，
通过 Gmail API 将客户回复邮件转发给对应业务员。

触发条件：
  - Auto-Reply Status = Customer-Replied
  - Reply Forward Status 为空
  - 最终分配的业务员 在「业务通知名单」表中有对应邮箱
  - 有 Gmail_Msg_ID（能获取原始邮件）
"""

import base64
import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from assignment_fields import FIELD_ASSIGNEE, get_field  # noqa: E402
from feishu_utils import (
    get_feishu_token, send_alert_webhook, extract_text,
    feishu_api, FEISHU_APP_TOKEN, FEISHU_TABLE_ID,
    FIELD_AUTOREPLY_STATUS, require_env,
)
from gmail_client import extract_email_body

log = logging.getLogger("reply-forwarder")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# ── 配置 ──────────────────────────────────────────────────────────────────────
FIELD_REPLY_FWD_STATUS = "Reply Forward Status"
FIELD_REPLY_FWD_AT = "Reply Forwarded At"
FIELD_GMAIL_MSG_ID = "Gmail_Msg_ID"
FIELD_GMAIL_THREAD_ID = "Gmail_Thread_ID"
FIELD_CUSTOMER_EMAIL = "Email（客户邮箱）"
FIELD_CUSTOMER_NAME = "Name（客户姓名）"
FIELD_CLUE_LEVEL = "Clue level（线索等级）"

FORWARDER_DRY_RUN = os.environ.get("FORWARDER_DRY_RUN", "true") == "true"
FORWARDER_MAX_RECORDS = int(os.environ.get("FORWARDER_MAX_RECORDS", "20"))
SENDER_EMAIL = "soundboxbooth@gmail.com"
SENDER_NAME = "Frank Lin"

# Follow-up Records 表（转发留痕）
FOLLOWUP_TABLE_ID = require_env("FEISHU_FOLLOWUP_TABLE")
FIELD_RELATED_LEAD = "Related Lead"
FIELD_FOLLOWUP_DETAILS = "Follow-up Details"
FIELD_CONTACT_RESULT = "Contact Result"
FIELD_CONTACT_METHOD = "Contact Method"
FIELD_FOLLOWUP_TIME = "Follow-up Time"

# 业务员邮箱映射：从飞书「业务通知名单」表读取（动态，改表即生效）
SALES_NOTIFY_TABLE_ID = require_env("FEISHU_SALES_NOTIFY_TABLE")
FIELD_SALES_NAME = "业务名单"
FIELD_SALES_EMAIL = "邮箱"


# ═══════════════════════════════════════════════════════════════════════════════
# Gmail API
# ═══════════════════════════════════════════════════════════════════════════════

def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    required = ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"缺少 Gmail 凭据: {', '.join(missing)}")

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 飞书查询 / 更新
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_replied_records(token: str) -> list:
    """查询 Customer-Replied 且未转发的记录。"""
    conditions = [
        {"field_name": FIELD_AUTOREPLY_STATUS, "operator": "is", "value": ["Customer-Replied"]},
        {"field_name": FIELD_GMAIL_MSG_ID, "operator": "isNotEmpty", "value": []},
        {"field_name": FIELD_REPLY_FWD_STATUS, "operator": "isEmpty", "value": []},
    ]

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "filter": {"conjunction": "and", "conditions": conditions},
            "page_size": FORWARDER_MAX_RECORDS,
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书搜索失败: {data}")
    return data.get("data", {}).get("items", [])


def fetch_sales_email_map(token: str) -> dict:
    """从「业务通知名单」表读取 {业务员名称: 邮箱} 映射。"""
    sales_map = {}
    try:
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{SALES_NOTIFY_TABLE_ID}/records/search",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"page_size": 100},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            log.warning("业务通知名单查询失败: %s", data)
            return sales_map
        for item in data.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            name = extract_text(fields.get(FIELD_SALES_NAME, ""))
            # 邮箱字段是 URL 类型（{"link":"mailto:x","text":"x","type":"url"}），extract_text 取 value 取不到
            raw_email = fields.get(FIELD_SALES_EMAIL, "")
            email = (raw_email.get("text", "") if isinstance(raw_email, dict)
                     else extract_text(raw_email))
            if name and email:
                sales_map[name.strip()] = email.strip()
    except Exception as e:
        log.warning("业务通知名单查询异常: %s", e)
    return sales_map


def sync_forward_followup(token: str, lead_record_id: str,
                          sales_name: str, sales_email: str) -> None:
    """转发成功后，在 Follow-up Records 表留痕「已转发给业务员」。"""
    try:
        # 去重：同 lead + Forwarded 已存在则跳过
        chk = feishu_api("POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FOLLOWUP_TABLE_ID}/records/search",
            token=token, json={
                "filter": {"conjunction": "and", "conditions": [
                    {"field_name": FIELD_RELATED_LEAD, "operator": "is", "value": [lead_record_id]},
                    {"field_name": FIELD_CONTACT_RESULT, "operator": "is", "value": ["Forwarded to Sales 已转发业务员"]},
                ]},
                "page_size": 1,
            })
        if chk.json().get("data", {}).get("total", 0) > 0:
            log.info("转发 Follow-up 已存在，跳过: lead=%s", lead_record_id)
            return

        followup_fields = {
            FIELD_RELATED_LEAD: [{"id": lead_record_id, "type": "text"}],
            FIELD_FOLLOWUP_DETAILS: f"客户回复已转发给 {sales_name}（{sales_email}）",
            FIELD_CONTACT_RESULT: "Forwarded to Sales 已转发业务员",
            FIELD_CONTACT_METHOD: "Email",
            FIELD_FOLLOWUP_TIME: int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        resp = feishu_api("POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FOLLOWUP_TABLE_ID}/records",
            token=token, json={"fields": followup_fields})
        data = resp.json()
        if data.get("code") == 0:
            log.info("转发 Follow-up 已留痕: lead=%s | sales=%s", lead_record_id, sales_name)
        else:
            log.warning("转发 Follow-up 创建失败: %s", data)
    except Exception as e:
        log.warning("转发 Follow-up 异常: %s", e)


def update_forward_status(token: str, record_id: str, status: str):
    """更新飞书记录的转发状态。"""
    fields = {FIELD_REPLY_FWD_STATUS: status}
    if status == "Forwarded":
        fields[FIELD_REPLY_FWD_AT] = int(datetime.now(timezone.utc).timestamp() * 1000)

    try:
        resp = feishu_api("PUT",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/{record_id}",
            token=token, json={"fields": fields})
        data = resp.json()
        if data.get("code") != 0:
            log.warning("飞书更新失败: %s", data)
    except Exception as e:
        log.warning("飞书更新异常: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# Gmail 转发
# ═══════════════════════════════════════════════════════════════════════════════

def _get_header(msg_data: dict, name: str) -> str:
    for h in msg_data.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def get_msg_data(service, gmail_msg_id: str) -> dict:
    """获取 Gmail 邮件完整数据。"""
    return service.users().messages().get(
        userId="me", id=gmail_msg_id, format="full"
    ).execute()


def forward_email(service, msg_data: dict, to_email: str) -> dict:
    """构造转发 MIME，通过 messages.send 发送给业务员。"""
    result = {"status": "error", "error": ""}
    try:
        original_from = _get_header(msg_data, "From")
        original_subject = _get_header(msg_data, "Subject")
        original_date = _get_header(msg_data, "Date")

        # 提取邮件正文
        body_text = extract_email_body(msg_data)

        if not body_text:
            body_text = "(原始邮件正文无法提取，请在 Gmail 中查看完整内容)"

        # 构造转发邮件
        fwd_subject = f"Fwd: {original_subject}" if not original_subject.lower().startswith("fwd:") else original_subject
        fwd_body = (
            f"---------- 转发的邮件 ----------\n"
            f"From: {original_from}\n"
            f"Date: {original_date}\n"
            f"Subject: {original_subject}\n"
            f"\n"
            f"{body_text}"
        )

        msg = MIMEText(fwd_body, "plain", "utf-8")
        msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
        msg["To"] = to_email
        msg["Subject"] = fwd_subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")

        send_result = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        result["status"] = "forwarded"
        result["fwd_msg_id"] = send_result.get("id", "")
        log.info("转发成功: to=%s | fwd_id=%s | subject=%s",
                 to_email, send_result.get("id", ""), fwd_subject[:50])
    except Exception as e:
        result["error"] = str(e)
        log.error("转发失败: to=%s | %s", to_email, e)
    return result


def send_customer_acknowledgement(service, msg_data: dict,
                                   customer_email: str, customer_name: str,
                                   sales_name: str, sales_email: str) -> dict:
    """回复客户确认邮件：感谢回复，告知业务员会跟进。"""
    result = {"status": "error", "error": ""}
    try:
        original_msg_id = _get_header(msg_data, "Message-ID")
        original_references = _get_header(msg_data, "References")
        thread_id = msg_data.get("threadId", "")

        name = customer_name or "there"
        body = (
            f"Dear {name},\n\n"
            f"Thank you for your reply.\n\n"
            f"{sales_name}, our specialist, will assist you with the next steps. "
            f"I have already shared your response with {sales_name}, who will follow up with you shortly.\n\n"
            f"You may also reach her directly at {sales_email}.\n\n"
            f"Best regards,\n"
            f"Frank Lin\n"
            f"Sales Engineer of Soundbox"
        )

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
        msg["To"] = customer_email
        msg["Subject"] = "Re: Your Inquiry"

        if original_msg_id:
            msg["In-Reply-To"] = original_msg_id
        ref_chain = original_references or ""
        if original_msg_id:
            ref_chain = f"{ref_chain} {original_msg_id}".strip()
        if ref_chain:
            msg["References"] = ref_chain

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")

        send_result = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()

        result["status"] = "sent"
        result["msg_id"] = send_result.get("id", "")
        log.info("客户确认邮件已发送: to=%s | name=%s | msg_id=%s",
                 customer_email, name, send_result.get("id", ""))
    except Exception as e:
        result["error"] = str(e)
        log.error("客户确认邮件发送失败: to=%s | %s", customer_email, e)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def parse_record(record: dict) -> dict:
    """从飞书记录提取转发所需上下文。"""
    fields = record.get("fields", {})
    return {
        "record_id": record.get("record_id"),
        "gmail_msg_id": extract_text(fields.get(FIELD_GMAIL_MSG_ID, "")),
        "assignee": extract_text(get_field(fields, FIELD_ASSIGNEE, "")),
        "customer_email": extract_text(fields.get(FIELD_CUSTOMER_EMAIL, "")),
        "customer_name": extract_text(fields.get(FIELD_CUSTOMER_NAME, "")),
        "clue_level": extract_text(fields.get(FIELD_CLUE_LEVEL, "")),
    }


def main():
    log.info("=== Reply Forwarder 启动 (UTC %s) ===",
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    token = get_feishu_token()

    # 从飞书「业务通知名单」表读取业务员→邮箱映射
    sales_email_map = fetch_sales_email_map(token)
    log.info("配置: dry_run=%s max=%d | 业务通知名单 %d 人",
             FORWARDER_DRY_RUN, FORWARDER_MAX_RECORDS, len(sales_email_map))
    if not sales_email_map:
        log.error("业务通知名单为空，无法转发")
        send_alert_webhook("Reply Forwarder: 业务通知名单表为空或读取失败")
        return

    records = fetch_replied_records(token)
    if not records:
        log.info("无待转发记录")
        return
    log.info("找到 %d 条待转发记录", len(records))

    service = get_gmail_service()

    stats = {"Forwarded": 0, "Skip-No-Email": 0, "Gmail-API-Error": 0, "Dry-Run": 0,
             "Ack-Sent": 0, "Ack-Error": 0}
    for rec in records:
        ctx = parse_record(rec)
        record_id = ctx["record_id"]
        assignee = ctx["assignee"]

        # 查找业务员邮箱
        sales_name = ""
        sales_email = None
        for name, email in sales_email_map.items():
            if any(name == part for part in assignee.split()):
                sales_name = name
                sales_email = email
                break

        if not sales_email:
            log.info("跳过（无邮箱映射）: assignee=%s | record=%s", assignee, record_id)
            update_forward_status(token, record_id, "Skip-No-Email")
            stats["Skip-No-Email"] += 1
            continue

        # 获取原始邮件数据（转发和确认共用）
        try:
            msg_data = get_msg_data(service, ctx["gmail_msg_id"])
        except Exception as e:
            log.error("获取邮件数据失败: msg=%s | %s", ctx["gmail_msg_id"], e)
            update_forward_status(token, record_id, "Gmail-API-Error")
            stats["Gmail-API-Error"] += 1
            continue

        if FORWARDER_DRY_RUN:
            log.info("[DRY-RUN] 模拟转发: to=%s | msg=%s | assignee=%s | record=%s",
                     sales_email, ctx["gmail_msg_id"], assignee, record_id)
            log.info("[DRY-RUN] 模拟客户确认邮件: to=%s | name=%s | sales=%s",
                     ctx["customer_email"], ctx["customer_name"], sales_name)
            update_forward_status(token, record_id, "Dry-Run")
            stats["Dry-Run"] += 1
            continue

        # 1. 转发给业务员
        fwd_result = forward_email(service, msg_data, sales_email)
        if fwd_result["status"] == "forwarded":
            stats["Forwarded"] += 1

            # 2. 转发成功后才回复客户确认邮件
            if ctx["customer_email"]:
                ack_result = send_customer_acknowledgement(
                    service, msg_data,
                    customer_email=ctx["customer_email"],
                    customer_name=ctx["customer_name"],
                    sales_name=sales_name,
                    sales_email=sales_email,
                )
                if ack_result["status"] == "sent":
                    stats["Ack-Sent"] += 1
                else:
                    stats["Ack-Error"] += 1
            else:
                log.warning("无客户邮箱，跳过确认邮件: record=%s", record_id)

            update_forward_status(token, record_id, "Forwarded")
            # Follow-up 留痕：已转发给业务员
            sync_forward_followup(token, record_id, sales_name, sales_email)
        else:
            # Gmail token 失效告警去重：由 cloud-lead-poller 近实时发出 + cloud-health-check 兜底
            stats["Gmail-API-Error"] += 1
            update_forward_status(token, record_id, "Gmail-API-Error")

    log.info("=== Forwarder 完成: %s ===", stats)


if __name__ == "__main__":
    main()
