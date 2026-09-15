#!/usr/bin/env python3
"""测试脚本：向 frank990513@gmail.com 发送静音舱建联邮件"""

import os
import sys
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import formataddr
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
import google_auth_httplib2
import httplib2

# 加载 .env
load_dotenv(Path(__file__).parent / ".env")

SENDER_EMAIL = "soundboxbooth@gmail.com"
SENDER_NAME = "Frank Lin"
TO_EMAIL = "frank990513@gmail.com"

# 静音舱 general 模板
SUBJECT = "Quotation for Silence Booth – Soundbox Acoustic"
BODY = (
    "Dear Frank,\n\n"
    "Thanks for your inquiry about our soundproof booth from our website.\n\n"
    "This is Frank from Soundbox Acoustic – we specialize in R&D and manufacturing "
    "of high-performance acoustic booths for offices, home studios, and commercial spaces. "
    "Our booths are trusted by clients in your region.\n\n"
    "To give you the most accurate recommendation, could you kindly share:\n\n"
    "1) How many people will use the booth? (1-person / 2-person / 4-person / Customized)\n"
    "2) What is the main purpose? (Office calls / Music practice / Meeting room / Other)\n"
    "3) Which country will it be shipped to and how many units do you need?"
)

# 签名（从 email_generator 同步）
SIGNATURE = (
    "Best regards,\n"
    "Frank Lin | Sales Engineer\n"
    "Soundbox Acoustic Technology Co., Ltd.\n"
    "WhatsApp: +86 18620723890 | Email: soundboxbooth@gmail.com\n"
    "Web: www.soundboxacoustic.com\n"
    "Address: No.8, Xingye Road, Dalingshan, Dongguan, Guangdong, China"
)

ATTACHMENT = Path(__file__).parent / "attachments" / "VRT+pod-NEW.pdf"


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def build_mime():
    """构造带附件的 MIME 邮件"""
    plain = f"{BODY}\n\n{SIGNATURE}"

    import html
    html_body = html.escape(BODY).replace("\n\n", "</p><p>").replace("\n", "<br>")
    html_sig = html.escape(SIGNATURE).replace("\n", "<br>")
    html_full = (
        '<html><body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">'
        f'<p>{html_body}</p>'
        f'<p style="color: #666; font-size: 12px;">{html_sig}</p>'
        '</body></html>'
    )

    text_part = MIMEMultipart("alternative")
    text_part.attach(MIMEText(plain, "plain", "utf-8"))
    text_part.attach(MIMEText(html_full, "html", "utf-8"))

    if ATTACHMENT.exists():
        msg = MIMEMultipart("mixed")
        msg.attach(text_part)
        data = ATTACHMENT.read_bytes()
        att = MIMEApplication(data)
        att.add_header("Content-Disposition", "attachment", filename=ATTACHMENT.name)
        msg.attach(att)
        print(f"附件: {ATTACHMENT.name} ({len(data)/1024:.0f} KB)")
    else:
        msg = text_part
        print("无附件（文件不存在）")

    msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
    msg["To"] = TO_EMAIL
    msg["Subject"] = SUBJECT

    return msg


def main():
    # Gmail 认证
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    creds.refresh(GoogleRequest())

    # 通过代理连接
    http = httplib2.Http(
        proxy_info=httplib2.ProxyInfo(
            httplib2.socks.PROXY_TYPE_HTTP, "127.0.0.1", 7890))
    authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
    service = build("gmail", "v1", http=authed_http, cache_discovery=False)

    # 构造并发送
    mime = build_mime()
    raw = _base64url_encode(mime.as_bytes())

    result = service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    print(f"\n发送成功!")
    print(f"  Message ID: {result['id']}")
    print(f"  Thread ID:  {result['threadId']}")
    print(f"  To: {TO_EMAIL}")
    print(f"  Subject: {SUBJECT}")


if __name__ == "__main__":
    main()
