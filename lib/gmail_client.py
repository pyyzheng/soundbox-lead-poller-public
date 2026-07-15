"""
Gmail API 客户端 — 认证、邮件搜索/读取、标签管理、附件处理
"""

import os
import sys
import base64
import logging

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lead_fallback_parser import strip_html

log = logging.getLogger("lead-poller")

GMAIL_LABEL = "processed-by-openclaw"
MAX_EMAILS_PER_RUN = 30
MAX_EMAIL_AGE_DAYS = 7


def get_gmail_service():
    """用 refresh_token 创建 Gmail API 客户端（无需本地 Keychain）"""
    required = ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error("缺少 Gmail 凭据环境变量: %s", ", ".join(missing))
        sys.exit(1)

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.modify",
                 "https://www.googleapis.com/auth/gmail.send"],
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_or_create_label(service, label_name: str) -> str:
    """获取或创建 Gmail 标签，返回标签 ID"""
    resp = service.users().labels().list(userId="me").execute()
    for label in resp.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    log.info("已创建 Gmail 标签: %s (id=%s)", label_name, created["id"])
    return created["id"]


def search_unprocessed_emails(service, rules: dict) -> list:
    """搜索来自目标发件人且尚未打标签的邮件"""
    from datetime import datetime, timezone, timedelta

    channels = rules.get("channels", {})
    senders = [s for s in channels.keys() if not s.startswith("_")]
    if not senders:
        log.warning("lead-rules.json 中未找到有效渠道发件人")
        return []

    from_query = " OR ".join(f"from:{s}" for s in senders)
    after_date = (datetime.now(timezone.utc) - timedelta(days=MAX_EMAIL_AGE_DAYS)).strftime("%Y/%m/%d")
    account = rules.get("account", "soundboxbooth@gmail.com")
    skip_list = rules.get("skip_senders", [])
    skip_emails = [f"-from:{s}" for s in skip_list if isinstance(s, str) and "@" in s]
    skip_query = " ".join(skip_emails)
    query = f"(({from_query}) OR deliveredto:{account}) -label:{GMAIL_LABEL} {skip_query} after:{after_date}"
    log.info("Gmail 搜索条件: %s", query)

    try:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=MAX_EMAILS_PER_RUN
        ).execute()
        messages = result.get("messages", [])
        log.info("找到 %d 封未处理邮件", len(messages))
        return messages
    except HttpError as e:
        log.error("Gmail 搜索失败: %s", e)
        return []


def get_message_detail(service, message_id: str) -> dict:
    """获取邮件完整内容"""
    return service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()


def apply_label(service, message_id: str, label_id: str) -> bool:
    """给邮件打上已处理标签"""
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id], "removeLabelIds": []},
        ).execute()
        return True
    except HttpError as e:
        log.error("打标签失败 (msgId=%s): %s", message_id, e)
        return False


# ── 邮件体解码 ──────────────────────────────────────────────────────────────

def _b64url_decode(data: str) -> bytes:
    """base64url 解码（内部共用）"""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def decode_body_data(data: str) -> str:
    """解码 base64url 编码的邮件体"""
    if not data:
        return ""
    try:
        return _b64url_decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_email_body(msg_data: dict) -> str:
    """从 Gmail API 返回的 payload 中提取纯文本正文"""
    payload = msg_data.get("payload", {})

    def walk(part) -> tuple[str, str]:
        """返回 (plain_text, html_text)"""
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data", "")
        if mime == "text/plain" and body_data:
            return decode_body_data(body_data), ""
        if mime == "text/html" and body_data:
            return "", decode_body_data(body_data)
        plain, html = "", ""
        for sub in part.get("parts", []):
            p, h = walk(sub)
            plain = plain or p
            html = html or h
        return plain, html

    plain, html = walk(payload)
    if plain:
        return plain
    if html:
        return strip_html(html)
    return ""


def extract_attachments(msg_data: dict) -> list:
    """从 Gmail message 递归提取附件列表"""
    result = []
    def walk(part):
        filename = part.get("filename", "")
        if filename:
            att_id = part.get("body", {}).get("attachmentId", "")
            if att_id:
                result.append({
                    "filename": filename,
                    "attachment_id": att_id,
                    "mime_type": part.get("mimeType", "application/octet-stream"),
                })
        for sub in part.get("parts", []):
            walk(sub)
    walk(msg_data.get("payload", {}))
    return result


def download_gmail_attachment(service, msg_id: str, attachment_id: str) -> bytes | None:
    """通过 Gmail API 下载附件，返回原始二进制数据"""
    try:
        att = service.users().messages().attachments().get(
            userId="me", id=attachment_id, messageId=msg_id
        ).execute()
        data = att.get("data", "")
        if not data:
            return None
        return _b64url_decode(data)
    except Exception as e:
        log.error("下载附件失败 (msg=%s, att=%s): %s", msg_id, attachment_id, e)
        return None


def upload_to_feishu(token: str, file_data: bytes, filename: str, mime_type: str,
                     app_token: str) -> str | None:
    """上传文件到飞书，返回 file_token"""
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, file_data, mime_type)},
            data={
                "parent_node": app_token,
                "parent_type": "bitable_file",
                "file_name": filename,
                "size": str(len(file_data)),
            },
            timeout=60,
        )
        result = resp.json()
        if result.get("code") == 0:
            return result["data"]["file_token"]
        log.error("飞书上传失败: %s", result)
        return None
    except Exception as e:
        log.error("飞书上传异常: %s", e)
        return None


def process_attachments(service, msg_data: dict, msg_id: str,
                        feishu_token: str, app_token: str) -> list:
    """下载邮件附件并上传飞书，返回 [{"file_token": str}, ...]"""
    attachments = extract_attachments(msg_data)
    if not attachments:
        return []
    tokens = []
    for att in attachments:
        file_data = download_gmail_attachment(service, msg_id, att["attachment_id"])
        if not file_data:
            continue
        if len(file_data) > 20 * 1024 * 1024:
            log.warning("附件过大，跳过: %s (%d bytes)", att["filename"], len(file_data))
            continue
        file_token = upload_to_feishu(feishu_token, file_data, att["filename"], att["mime_type"], app_token)
        if file_token:
            tokens.append({"file_token": file_token})
            log.info("附件上传成功: %s → %s", att["filename"], file_token)
        else:
            log.warning("附件上传失败: %s", att["filename"])
    return tokens


def get_header(msg_data: dict, name: str) -> str:
    """从邮件头部获取指定字段值"""
    headers = msg_data.get("payload", {}).get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def get_reply_to(msg_data: dict) -> str:
    """从 Gmail 消息提取回复地址（Reply-To 优先，其次 From）。"""
    from email.utils import parseaddr
    for h in msg_data.get("payload", {}).get("headers", []):
        if h["name"].lower() == "reply-to" and h["value"].strip():
            return parseaddr(h["value"])[1]
    for h in msg_data.get("payload", {}).get("headers", []):
        if h["name"].lower() == "from":
            return parseaddr(h["value"])[1]
    return ""
