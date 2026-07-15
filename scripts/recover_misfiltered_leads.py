#!/usr/bin/env python3
"""移除误拦截邮件的 processed 标签并直接重新入库（不受 7 天搜索窗口限制）。

用法:
  python3 scripts/recover_misfiltered_leads.py --msg-id 19e9c4fc14984c1b
  python3 scripts/recover_misfiltered_leads.py --customer-email r.awadis@rajcco.com
  python3 scripts/recover_misfiltered_leads.py --msg-id ID1 --msg-id ID2 --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "lib"))


def _load_poller():
    spec = importlib.util.spec_from_file_location("clp", ROOT / "cloud-lead-poller.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from gmail_client import (  # noqa: E402
    GMAIL_LABEL,
    get_gmail_service,
    get_message_detail,
    get_header,
    get_or_create_label,
)
from feishu_writer import check_feishu_duplicate, get_feishu_token  # noqa: E402

LABEL_NAME = GMAIL_LABEL


def _label_id(service) -> str:
    for label in service.users().labels().list(userId="me").execute().get("labels", []):
        if label["name"] == LABEL_NAME:
            return label["id"]
    raise RuntimeError(f"Gmail 标签不存在: {LABEL_NAME}")


def _find_by_customer_email(service, email: str) -> list[str]:
    msgs = (
        service.users()
        .messages()
        .list(userId="me", q=f"{email} newer_than:365d", maxResults=10)
        .execute()
        .get("messages", [])
        or []
    )
    return [m["id"] for m in msgs]


def remove_label(service, label_id: str, msg_id: str, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] 移除标签 {msg_id}")
        return
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": [label_id], "addLabelIds": []},
    ).execute()
    print(f"  已移除标签 {msg_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复误过滤线索")
    parser.add_argument("--msg-id", action="append", default=[], help="Gmail message id（可重复）")
    parser.add_argument("--customer-email", action="append", default=[], help="客户邮箱，自动搜索 Gmail")
    parser.add_argument("--dry-run", action="store_true", help="仅打印，不修改")
    args = parser.parse_args()

    msg_ids: list[str] = list(args.msg_id)
    service = get_gmail_service()
    for email in args.customer_email:
        found = _find_by_customer_email(service, email)
        if not found:
            print(f"警告: Gmail 未找到 {email}", file=sys.stderr)
        msg_ids.extend(found)

    if not msg_ids:
        print("无待恢复邮件", file=sys.stderr)
        return 1

    clp = _load_poller()
    rules_path = ROOT / "lead-rules.json"
    with open(rules_path, encoding="utf-8") as f:
        rules = json.load(f)

    label_id = _label_id(service)
    token = get_feishu_token()
    results = []

    for msg_id in dict.fromkeys(msg_ids):
        detail = get_message_detail(service, msg_id)
        subj = get_header(detail, "Subject")
        if check_feishu_duplicate(token, msg_id):
            print(f"  跳过（已在飞书）{msg_id} | {subj[:50]}")
            continue
        print(f"  待恢复 {msg_id} | {subj[:60]}")
        remove_label(service, label_id, msg_id, args.dry_run)
        if args.dry_run:
            continue
        result = clp.process_email(service, detail, label_id, token, rules)
        results.append(result)
        print(f"  → {result.get('status')} | {result.get('reason') or result.get('feishu_record_id', '')}")

    if args.dry_run:
        return 0

    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n完成: 成功={ok} 总计={len(results)}")
    return 0 if ok == len(results) or not results else 1


if __name__ == "__main__":
    raise SystemExit(main())
