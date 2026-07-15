#!/usr/bin/env python3
"""
恢复脚本：移除因管线故障被误标记的邮件标签，让云端 pipeline 重新处理。

背景：2026-05-11 run 25645703728 中 6 封邮件因 full_lower NameError 崩溃，
已被标记 processed-by-openclaw 但未写入飞书。移除标签后触发 pipeline 即可恢复。

用法:
    cd /path/to/soundbox-lead-poller
    # 从 .env 加载凭证
    set -a; source ~/.openclaw/workspace/.env; set +a
    python3 scripts/recover-orphaned-leads.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

LABEL_NAME = "processed-by-openclaw"

ORPHANED_MSG_IDS = [
    "19e148ce4c26d12f",
    "19e1459dd8f74f2a",
    "19e133b5e6b04b35",
    "19e12574b117cbcc",
    "19e1217b77392dd8",
    "19e11e3234b22e30",
]


def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_label_id(service, label_name: str) -> str:
    resp = service.users().labels().list(userId="me").execute()
    for label in resp.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    print(f"ERROR: 标签 '{label_name}' 不存在")
    sys.exit(1)


def main():
    print(f"恢复 {len(ORPHANED_MSG_IDS)} 封孤立邮件...")
    service = get_gmail_service()
    label_id = get_label_id(service, LABEL_NAME)
    print(f"标签 ID: {label_id}")

    for msg_id in ORPHANED_MSG_IDS:
        try:
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"removeLabelIds": [label_id], "addLabelIds": []},
            ).execute()
            print(f"  removed label from {msg_id}")
        except Exception as e:
            print(f"  FAILED {msg_id}: {e}")

    print(f"\n完成。请触发云端 pipeline 重新处理:")
    print(f"  gh workflow run 256701742 --repo pyyzheng/soundbox-lead-poller --ref main")


if __name__ == "__main__":
    main()
