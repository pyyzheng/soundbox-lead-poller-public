#!/usr/bin/env python3
"""
cloud-auto-reply-worker.py — 自动回复 Worker（按业务员灰度）

独立于主管线运行。查询飞书中已分配业务员且状态为 Pending 的线索，
生成模板邮件并发送。

触发条件：
  - Auto-Reply Status = Pending
  - 最终分配的业务员 contains ALLOWED_SALESPERSONS 中的任一值
  - 有 Gmail_Msg_ID（能回溯原始邮件）
"""

import os
import sys
import logging
import html as html_module
from pathlib import Path
from datetime import datetime, timezone

import requests

# 复用主管线模块
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from assignment_fields import FIELD_ASSIGNEE  # noqa: E402
from email_template import generate_template_email
from email_sender import compose_email_reply, send_reply_email, send_standalone_email
from email_generator import SOUNDBOX_TEAM_SIGNATURE
from feishu_utils import (
    get_feishu_token, update_feishu_autoreply,
    extract_text as _extract_text, feishu_api,
    FEISHU_APP_TOKEN, FEISHU_TABLE_ID, require_env,
)

log = logging.getLogger("auto-reply-worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

WORKER_ENABLED = os.environ.get("AUTO_REPLY_WORKER_ENABLED", "false") == "true"
WORKER_DRY_RUN = os.environ.get("AUTO_REPLY_WORKER_DRY_RUN", "true") == "true"
ALLOWED_SALESPERSONS = [
    s.strip()
    for s in os.environ.get("AUTO_REPLY_ALLOWED_SALESPERSONS", "Stephanie").split(",")
    if s.strip()
]
WORKER_MAX_RECORDS = int(os.environ.get("AUTO_REPLY_WORKER_MAX_RECORDS", "20"))

FEISHU_AUTOREPLY_STATUS = "Auto-Reply Status"
FEISHU_AUTOREPLY_SENT_AT = "Auto-Reply Sent At"
FEISHU_AUTOREPLY_TEMPLATE = "Auto-Reply Template"
FEISHU_AUTOREPLY_ERROR = "Auto-Reply Error"
FEISHU_MSGID_FIELD = "Gmail_Msg_ID"
FEISHU_FIELD_NAME = "Enquiry details（询盘内容）"
FEISHU_CLUE_LEVEL = "Clue level（线索等级）"
FEISHU_EMAIL_FIELD = "Email（客户邮箱）"
FEISHU_CHANNELS_FIELD = "Channels（渠道）"

# Follow-up Records 表
FOLLOWUP_TABLE_ID = require_env("FEISHU_FOLLOWUP_TABLE", "FEISHU_FOLLOWUP_TABLE_ID")
FU_FIELD_FOLLOWUP_TIME = "Follow-up Time"
FU_FIELD_CONTACT_METHOD = "Contact Method"
FU_FIELD_CONTACT_RESULT = "Contact Result"
FU_FIELD_NEXT_STEP = "Next Step"
FU_FIELD_DETAILS = "Follow-up Details"
FU_FIELD_RELATED_LEAD = "Related Lead"

# Case Database 首联相关字段
FIELD_FIRST_CONTACT_DONE = "🌟First Contact Completed（是否已首联）"
FIELD_FIRST_CONTACT_DATE = "Date of First Contact"


# ═══════════════════════════════════════════════════════════════════════════════
# 飞书 API（查询 / 更新使用共享模块 lib/feishu_utils.py）
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_pending_for_salesperson(token: str) -> list:
    """查询飞书中 Pending 的记录，代码层 OR 过滤：
    - Facebook 渠道 lead（任意业务员，轮转分配）
    - 或分配给 ALLOWED_SALESPERSONS 的 lead（Google 渠道）
    飞书不支持嵌套 OR，故查所有 Pending 再代码过滤。
    """
    conditions = [
        {"field_name": FEISHU_AUTOREPLY_STATUS, "operator": "is", "value": ["Pending"]},
    ]

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "filter": {"conjunction": "and", "conditions": conditions},
            "field_names": [
                FEISHU_FIELD_NAME, FEISHU_MSGID_FIELD, FEISHU_EMAIL_FIELD,
                FIELD_ASSIGNEE, FEISHU_CHANNELS_FIELD,
            ],
            "page_size": WORKER_MAX_RECORDS,
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书搜索失败: {data}")
    items = data.get("data", {}).get("items", [])

    # 代码层 OR 过滤：Facebook 渠道 OR 目标业务员
    allow_all = ALLOWED_SALESPERSONS == ["*"]
    filtered = []
    for it in items:
        fields = it.get("fields", {})
        channels = _extract_text(fields.get(FEISHU_CHANNELS_FIELD, ""))
        if "Facebook" in channels or allow_all:
            filtered.append(it)
        else:
            assignee = _extract_text(fields.get(FIELD_ASSIGNEE, ""))
            if any(name in assignee for name in ALLOWED_SALESPERSONS):
                filtered.append(it)
    return filtered


def update_feishu_autoreply_worker(token, record_id, status, sent_at="",
                                    template="", error="", thread_id=""):
    """Worker 专用包装：调用共享模块的 update_feishu_autoreply。"""
    return update_feishu_autoreply(token, record_id, status,
                                   sent_at=sent_at, template=template,
                                   error=error, thread_id=thread_id)


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
# 上下文解析
# ═══════════════════════════════════════════════════════════════════════════════

def parse_record_context(record: dict) -> dict | None:
    """从飞书记录提取自动回复所需的上下文。"""
    fields = record.get("fields", {})
    inquiry = _extract_text(fields.get(FEISHU_FIELD_NAME, ""))
    if not inquiry:
        log.warning("记录缺少询盘内容: %s", record.get("record_id"))
        return None

    # tag_line 在最后一个非空行，格式: 国家-渠道-产品品类-产品型号
    lines = [l for l in inquiry.strip().split("\n") if l.strip()]
    tag_line = lines[-1] if lines else ""
    parts = tag_line.split("-")
    country = parts[0] if len(parts) >= 1 else ""
    sub_channel = parts[1] if len(parts) >= 2 else ""
    product_category = parts[2] if len(parts) >= 3 else ""

    # 从 inquiry content 解析客户名
    name = ""
    for line in inquiry.split("\n"):
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
            break

    # 邮件正文：Message: 之后到空行（tag_line 前的空行分隔符）
    message = ""
    in_message = False
    for line in inquiry.split("\n"):
        if line.startswith("Message:"):
            in_message = True
            message = line.split(":", 1)[1].strip() if ":" in line else ""
            continue
        if in_message:
            if not line.strip():
                break
            message += "\n" + line

    gmail_msg_id = _extract_text(fields.get(FEISHU_MSGID_FIELD, ""))
    clue_level = _extract_text(fields.get(FEISHU_CLUE_LEVEL, ""))
    customer_email = _extract_text(fields.get(FEISHU_EMAIL_FIELD, ""))

    # fallback：飞书自动化可能未填充 Email 字段，从询盘内容解析
    if not customer_email:
        for line in lines:
            if line.startswith("Email:"):
                candidate = line.split(":", 1)[1].strip()
                if "@" in candidate:
                    customer_email = candidate
                    break

    return {
        "record_id": record.get("record_id"),
        "gmail_msg_id": gmail_msg_id,
        "country": country,
        "channel": sub_channel,
        "product_category": product_category,
        "customer_name": name,
        "customer_email": customer_email,
        "message": message.strip(),
        "clue_level": clue_level,
        "assignee": _extract_text(fields.get(FIELD_ASSIGNEE, "")),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Follow-up 记录创建
# ═══════════════════════════════════════════════════════════════════════════════

def create_followup_record(token: str, case_record_id: str, sent_at_ms: int) -> str | None:
    """自动首联成功后，在 Follow-up Records 表创建一条跟进记录。"""
    fields = {
        FU_FIELD_FOLLOWUP_TIME: sent_at_ms,
        FU_FIELD_CONTACT_METHOD: "Email",
        FU_FIELD_CONTACT_RESULT: "Contacted - No Reply 已联系，暂无回复",
        FU_FIELD_NEXT_STEP: "Wait for Customer Reply 等客户回复",
        FU_FIELD_DETAILS: "[Auto] 自动首联邮件已发送，等待客户回复",
        FU_FIELD_RELATED_LEAD: [case_record_id],
    }
    try:
        resp = feishu_api("POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FOLLOWUP_TABLE_ID}/records",
            token=token, json={"fields": fields})
        data = resp.json()
        if data.get("code") != 0:
            log.warning("Follow-up 记录创建失败: %s", data)
            return None
        fu_record_id = data.get("data", {}).get("record", {}).get("record_id", "")
        log.info("Follow-up 记录已创建: %s → case=%s", fu_record_id, case_record_id)
        return fu_record_id
    except Exception as e:
        log.warning("Follow-up 记录创建异常: %s", e)
        return None


def update_case_after_first_contact(token: str, record_id: str, sent_at_ms: int):
    """自动首联成功后，标记首联完成。"""
    fields = {
        FIELD_FIRST_CONTACT_DONE: "Yes",
        FIELD_FIRST_CONTACT_DATE: sent_at_ms,
    }
    try:
        resp = feishu_api("PUT",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/{record_id}",
            token=token, json={"fields": fields})
        data = resp.json()
        if data.get("code") != 0:
            log.warning("Case 状态更新失败: %s", data)
    except Exception as e:
        log.warning("Case 状态更新异常: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def process_record(service, token: str, ctx: dict,
                   first_contact_done: bool = False) -> str:
    """处理单条记录，返回状态字符串。"""
    record_id = ctx["record_id"]
    is_facebook = not ctx["gmail_msg_id"]

    # 生成模板邮件
    email_result = generate_template_email(
        product_category=ctx["product_category"],
        grading={},
        customer_name=ctx["customer_name"],
        channel=ctx.get("channel", "Facebook" if is_facebook else "Google"),
        country=ctx["country"],
        message=ctx["message"],
    )
    if not email_result:
        update_feishu_autoreply(token, record_id, "No-Template")
        log.info("无匹配模板: product=%s | record=%s", ctx["product_category"], record_id)
        return "No-Template"

    template_key = email_result.get("email_model", "?")
    sent_thread_id = ""

    if is_facebook:
        # Facebook 渠道：发送独立邮件（无原始 Gmail 邮件可回复）
        subject = email_result.get("subject", "Inquiry")
        body = email_result.get("body", "")
        # Facebook 渠道轮转分配，统一用团队签名（不绑定具体业务员）
        signature = SOUNDBOX_TEAM_SIGNATURE

        plain_body = f"{body}\n\n{signature}"
        html_body = html_module.escape(body).replace("\n\n", "</p><p>").replace("\n", "<br>")
        html_sig = html_module.escape(signature).replace("\n", "<br>")
        html_full = (
            f'<html><body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">'
            f'<p>{html_body}</p>'
            f'<p style="color: #666; font-size: 12px;">{html_sig}</p>'
            f'</body></html>'
        )

        send_result = send_standalone_email(
            gmail_service=service,
            to_email=ctx["customer_email"],
            subject=subject,
            body_html=html_full,
            body_plain=plain_body,
            dry_run=WORKER_DRY_RUN,
            attachments=email_result.get("attachments"),
        )
    else:
        # Google 渠道：回复到 Gmail 线程
        msg_data = service.users().messages().get(
            userId="me", id=ctx["gmail_msg_id"], format="full"
        ).execute()

        # 收件人：仅使用 LLM/规则提取的客户邮箱
        # 不 fallback 到邮件 header（舱网表单的 From/Reply-To 是系统邮箱，不是客户）
        to_email = ctx.get("customer_email", "")

        if not to_email:
            log.error("无法确定收件人邮箱，跳过: record=%s", record_id)
            update_feishu_autoreply(token, record_id, "Error", error="no recipient email")
            return "Error"

        reply_subject, reply_html, reply_plain = compose_email_reply(email_result, msg_data)

        send_result = send_reply_email(
            gmail_service=service,
            original_msg_data=msg_data,
            reply_subject=reply_subject,
            reply_body_html=reply_html,
            reply_body_plain=reply_plain,
            dry_run=WORKER_DRY_RUN,
            attachments=email_result.get("attachments"),
            to_email_override=to_email,
        )

    # 共享逻辑：更新飞书 + Follow-up
    status = send_result.get("status", "Error").replace("_", "-").title()
    error = send_result.get("error", "") or ""
    template_display = template_key

    if WORKER_DRY_RUN and status == "Dry-Run":
        preview = f"[Subject] {email_result.get('subject', '')}\n\n{email_result.get('body', '')[:500]}"
        template_display = f"[PREVIEW] {preview}"
        if not is_facebook:
            sent_thread_id = msg_data.get("threadId", "")
    elif status == "Sent":
        sent_thread_id = send_result.get("sent_thread_id", "")

    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if status == "Sent" else ""
    update_feishu_autoreply(token, record_id, status, sent_at,
                            template=template_display, error=error,
                            thread_id=sent_thread_id)

    # 自动首联成功 → 创建 Follow-up 记录 + 更新 Case 状态
    if status == "Sent" and not first_contact_done:
        sent_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        fu_id = create_followup_record(token, record_id, sent_at_ms)
        if fu_id:
            update_case_after_first_contact(token, record_id, sent_at_ms)
    elif status == "Sent" and first_contact_done:
        log.info("已首联，跳过 Follow-up 创建: record=%s", record_id)
    elif WORKER_DRY_RUN and status == "Dry-Run":
        log.info("[DRY-RUN] 模拟创建 Follow-up 记录: record=%s", record_id)

    channel_label = "Facebook" if is_facebook else "Google"
    log.info("处理完成: status=%s | model=%s | assignee=%s | channel=%s | record=%s",
             status, template_key, ctx["assignee"], channel_label, record_id)

    return status


def main():
    log.info("=== Auto-Reply Worker 启动 (UTC %s) ===",
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    log.info("配置: enabled=%s dry_run=%s salespersons=%s max=%d",
             WORKER_ENABLED, WORKER_DRY_RUN, ALLOWED_SALESPERSONS, WORKER_MAX_RECORDS)

    if not WORKER_ENABLED:
        log.info("Worker 未启用，退出")
        return

    # 1. 飞书 token
    token = get_feishu_token()

    # 2. 查询 Pending 记录（Facebook 全渠道 + 目标业务员的 Google lead）
    records = fetch_pending_for_salesperson(token)
    if not records:
        log.info("无待处理记录")
        return
    log.info("找到 %d 条待处理记录", len(records))

    # 3. Gmail 认证
    service = get_gmail_service()

    # 4. 逐条处理
    results = {"Sent": 0, "Dry-Run": 0, "No-Template": 0, "Error": 0, "Skip": 0}
    for rec in records:
        ctx = parse_record_context(rec)
        if not ctx or (not ctx["gmail_msg_id"] and not ctx["customer_email"]):
            results["Skip"] += 1
            continue
        first_contact_done = _extract_text(
            rec.get("fields", {}).get(FIELD_FIRST_CONTACT_DONE, "")) == "Yes"
        try:
            status = process_record(service, token, ctx,
                                   first_contact_done=first_contact_done)
            results[status] = results.get(status, 0) + 1
        except Exception as e:
            log.error("处理失败: record=%s | %s", ctx.get("record_id"), e)
            update_feishu_autoreply(token, ctx["record_id"], "Error", error=str(e)[:200])
            results["Error"] += 1

    log.info("=== Worker 完成: %s ===", results)


if __name__ == "__main__":
    main()
