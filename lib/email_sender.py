#!/usr/bin/env python3
"""
email_sender.py — Gmail API 邮件发送模块

职责：
  - 构造 MIME 回复邮件（线程化，In-Reply-To / References）
  - 通过 Gmail API messages.send 发送
  - 支持 dry-run 模式（只记日志不实际发送）

依赖：
  - google-api-python-client（已存在于项目依赖）
  - email_generator.py 的输出 dict
"""

import base64
import html as html_module
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import formataddr, parseaddr
from pathlib import Path

log = logging.getLogger("email-sender")

# 附件字节缓存（单次运行中 PDF 文件不会变，避免重复读磁盘）
_att_cache: dict[str, bytes] = {}

SENDER_EMAIL = "soundboxbooth@gmail.com"
SENDER_NAME = "Frank Lin"


def _get_header(msg_data: dict, name: str) -> str:
    """从 Gmail message payload headers 中获取指定头字段值。"""
    for h in msg_data.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _resolve_to_email(original_msg_data: dict, to_email_override: str = "") -> str:
    """解析收件人地址：优先 override，其次 Reply-To，最后 From。"""
    if to_email_override:
        return to_email_override
    reply_to_addr = _get_header(original_msg_data, "Reply-To") or _get_header(original_msg_data, "From")
    _, parsed = parseaddr(reply_to_addr)
    return parsed


def _base64url_encode(data: bytes) -> str:
    """URL-safe base64 编码（无 padding），Gmail API 要求的格式。"""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def build_reply_mime(
    original_msg_data: dict,
    reply_subject: str,
    reply_body_html: str,
    reply_body_plain: str,
    attachments: list = None,
    to_email_override: str = "",
) -> str:
    """构造回复邮件的 MIME 消息。

    自动设置 In-Reply-To / References 头实现线程化，
    并使用 original_msg_data 的 threadId 关联到原邮件线程。

    Args:
        attachments: 附件文件路径列表（如画册 PDF）
        to_email_override: 优先使用的收件人地址

    Returns:
        base64url 编码的 MIME 消息字符串，可直接传给 Gmail API。
    """
    original_msg_id = _get_header(original_msg_data, "Message-ID")
    original_references = _get_header(original_msg_data, "References")
    original_from = _get_header(original_msg_data, "From")

    to_email = _resolve_to_email(original_msg_data, to_email_override)

    # 构造 References 链
    ref_chain = original_references
    if original_msg_id:
        ref_chain = f"{ref_chain} {original_msg_id}".strip() if ref_chain else original_msg_id

    # 确保 subject 有 "Re: " 前缀
    if not reply_subject.lower().startswith("re:"):
        reply_subject = f"Re: {reply_subject}"

    # 文本部分
    text_part = MIMEMultipart("alternative")
    text_part.attach(MIMEText(reply_body_plain, "plain", "utf-8"))
    text_part.attach(MIMEText(reply_body_html, "html", "utf-8"))

    # 有附件时用 multipart/mixed 包裹，否则直接用 alternative
    if attachments:
        att_parts = []
        for att_path in attachments:
            p = Path(att_path)
            try:
                data = _att_cache.get(att_path) or _att_cache.setdefault(att_path, p.read_bytes())
            except FileNotFoundError:
                log.warning("附件不存在，跳过: %s", att_path)
                continue
            att = MIMEApplication(data)
            att.add_header("Content-Disposition", "attachment", filename=p.name)
            att_parts.append((att, p.name, len(data)))
        if att_parts:
            msg = MIMEMultipart("mixed")
            msg.attach(text_part)
            for att, name, size in att_parts:
                msg.attach(att)
                log.info("已添加附件: %s (%.1f KB)", name, size / 1024)
        else:
            msg = text_part
    else:
        msg = text_part

    msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
    msg["To"] = to_email
    msg["Subject"] = reply_subject

    if original_msg_id:
        msg["In-Reply-To"] = original_msg_id
    if ref_chain:
        msg["References"] = ref_chain

    return _base64url_encode(msg.as_bytes())


def compose_email_reply(
    email_gen_result: dict,
    original_msg_data: dict,
) -> tuple:
    """从 email_generator 输出组装最终回复邮件。

    Args:
        email_gen_result: generate_email() 返回的 dict
        original_msg_data: 原始 Gmail 消息资源

    Returns:
        (reply_subject, reply_body_html, reply_body_plain)
    """
    body = email_gen_result.get("body", "")
    signature = email_gen_result.get("signature", "")
    subject = email_gen_result.get("subject", "")

    # 纯文本版
    plain_body = f"{body}\n\n{signature}"

    # HTML 版：转义特殊字符 + 段落换行
    html_body = html_module.escape(body).replace("\n\n", "</p><p>").replace("\n", "<br>")
    html_signature = html_module.escape(signature).replace("\n", "<br>")
    html_full = f"""
<html><body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
<p>{html_body}</p>
<p style="color: #666; font-size: 12px;">{html_signature}</p>
</body></html>""".strip()

    return subject, html_full, plain_body


def build_standalone_mime(
    to_email: str,
    subject: str,
    body_html: str,
    body_plain: str,
    attachments: list = None,
) -> str:
    """构造独立邮件（非回复线程），用于 Facebook 等非 Gmail 来源线索。"""
    text_part = MIMEMultipart("alternative")
    text_part.attach(MIMEText(body_plain, "plain", "utf-8"))
    text_part.attach(MIMEText(body_html, "html", "utf-8"))

    if attachments:
        att_parts = []
        for att_path in attachments:
            p = Path(att_path)
            try:
                data = _att_cache.get(att_path) or _att_cache.setdefault(att_path, p.read_bytes())
            except FileNotFoundError:
                log.warning("附件不存在，跳过: %s", att_path)
                continue
            att = MIMEApplication(data)
            att.add_header("Content-Disposition", "attachment", filename=p.name)
            att_parts.append((att, p.name, len(data)))
        if att_parts:
            msg = MIMEMultipart("mixed")
            msg.attach(text_part)
            for att, name, size in att_parts:
                msg.attach(att)
                log.info("已添加附件: %s (%.1f KB)", name, size / 1024)
        else:
            msg = text_part
    else:
        msg = text_part

    msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
    msg["To"] = to_email
    msg["Subject"] = subject

    return _base64url_encode(msg.as_bytes())


def send_standalone_email(
    gmail_service,
    to_email: str,
    subject: str,
    body_html: str,
    body_plain: str,
    dry_run: bool = False,
    attachments: list = None,
) -> dict:
    """发送独立邮件（非回复线程），用于 Facebook 等非 Gmail 来源线索。"""
    result = {
        "status": "error",
        "sent_message_id": "",
        "sent_thread_id": "",
        "error": None,
    }

    try:
        raw_mime = build_standalone_mime(
            to_email, subject, body_html, body_plain,
            attachments=attachments,
        )

        if dry_run:
            log.info(
                "[DRY-RUN] 模拟发送独立邮件 | To: %s | Subject: %s",
                to_email, subject,
            )
            result["status"] = "dry_run"
            return result

        send_result = gmail_service.users().messages().send(
            userId="me",
            body={"raw": raw_mime},
        ).execute()

        result["status"] = "sent"
        result["sent_message_id"] = send_result.get("id", "")
        result["sent_thread_id"] = send_result.get("threadId", "")
        log.info(
            "独立邮件已发送 | To: %s | MsgId: %s | Thread: %s",
            to_email, result["sent_message_id"], result["sent_thread_id"],
        )

    except Exception as e:
        result["error"] = str(e)
        log.error("独立邮件发送失败: %s | To: %s", e, to_email)

    return result


def send_reply_email(
    gmail_service,
    original_msg_data: dict,
    reply_subject: str,
    reply_body_html: str,
    reply_body_plain: str,
    dry_run: bool = False,
    attachments: list = None,
    to_email_override: str = "",
) -> dict:
    """通过 Gmail API 发送回复邮件。

    Args:
        gmail_service: 已认证的 Gmail API service 对象
        original_msg_data: 原始 Gmail 消息资源（含 payload headers 和 threadId）
        reply_subject: 回复主题
        reply_body_html: HTML 正文
        reply_body_plain: 纯文本正文
        dry_run: True 则只记日志不实际发送
        attachments: 附件文件路径列表
        to_email_override: 优先使用的收件人地址（网站表单邮件的 From 不是客户邮箱）

    Returns:
        {"status": "sent"|"dry_run"|"error",
         "sent_message_id": str,
         "sent_thread_id": str,
         "error": str | None}
    """
    thread_id = original_msg_data.get("threadId", "")
    to_email = _resolve_to_email(original_msg_data, to_email_override)

    result = {
        "status": "error",
        "sent_message_id": "",
        "sent_thread_id": thread_id,
        "error": None,
    }

    try:
        raw_mime = build_reply_mime(
            original_msg_data, reply_subject, reply_body_html, reply_body_plain,
            attachments=attachments, to_email_override=to_email_override,
        )

        if dry_run:
            log.info(
                "[DRY-RUN] 模拟发送回复 | To: %s | Subject: %s | Thread: %s",
                to_email, reply_subject, thread_id,
            )
            result["status"] = "dry_run"
            return result

        # 实际发送
        send_result = gmail_service.users().messages().send(
            userId="me",
            body={"raw": raw_mime, "threadId": thread_id},
        ).execute()

        result["status"] = "sent"
        result["sent_message_id"] = send_result.get("id", "")
        result["sent_thread_id"] = send_result.get("threadId", thread_id)
        log.info(
            "回复已发送 | To: %s | MsgId: %s | Thread: %s",
            to_email, result["sent_message_id"], result["sent_thread_id"],
        )

    except Exception as e:
        result["error"] = str(e)
        log.error("邮件发送失败: %s | To: %s | Thread: %s", e, to_email, thread_id)

    return result
