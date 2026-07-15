#!/usr/bin/env python3
"""
cloud-daily-stats.py — 自动回复每日统计

查询飞书过去 24 小时的自动回复记录，聚合统计后通过飞书 webhook 发送日报。

统计维度：
  - 自动回复：发送成功 / 失败 / 无模板 / 总数
  - 客户回复：已回复 / 已转发 / 待转发
  - 回复率：客户回复数 / 自动回复发送数
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from assignment_fields import FIELD_ASSIGNEE  # noqa: E402
from feishu_utils import (
    get_feishu_token, send_alert_webhook, extract_text,
    feishu_api, FEISHU_APP_TOKEN, FEISHU_TABLE_ID,
    FIELD_AUTOREPLY_STATUS, FIELD_AUTOREPLY_SENT_AT,
)

log = logging.getLogger("daily-stats")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# ── 配置 ──────────────────────────────────────────────────────────────────────
FIELD_REPLY_FWD_STATUS = "Reply Forward Status"
FIELD_CLUE_LEVEL = "Clue level（线索等级）"

WEBHOOK_URL = os.environ.get("FEISHU_ALERT_WEBHOOK", "")


# ═══════════════════════════════════════════════════════════════════════════════
# 数据查询
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_autoreply_records(token: str) -> list:
    """查询过去 24 小时有自动回复活动的记录。"""
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)

    conditions = [
        {"field_name": FIELD_AUTOREPLY_STATUS, "operator": "isNotEmpty", "value": []},
    ]

    import requests

    all_items = []
    page_token = ""

    while True:
        body = {
            "filter": {"conjunction": "and", "conditions": conditions},
            "field_names": [
                FIELD_AUTOREPLY_STATUS, FIELD_AUTOREPLY_SENT_AT,
                FIELD_REPLY_FWD_STATUS, FIELD_ASSIGNEE, FIELD_CLUE_LEVEL,
            ],
            "page_size": 100,
        }
        if page_token:
            body["page_token"] = page_token

        resp = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/search",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书搜索失败: {data}")

        for item in data.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            # Auto-Reply Sent At 是 Text 字段，飞书返回富文本数组
            # [{"text": "2026-05-31T17:03:04Z", "type": "text"}]
            sent_at_raw = extract_text(fields.get(FIELD_AUTOREPLY_SENT_AT, ""))
            if sent_at_raw:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(sent_at_raw.replace("Z", "+00:00"))
                    sent_at_ms = int(dt.timestamp() * 1000)
                    if sent_at_ms >= cutoff_ms:
                        all_items.append(item)
                except (ValueError, TypeError):
                    pass

        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token", "")

    return all_items


# ═══════════════════════════════════════════════════════════════════════════════
# 统计聚合
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_stats(records: list) -> dict:
    stats = {
        "total": len(records),
        "sent": 0,
        "error": 0,
        "no_template": 0,
        "customer_replied": 0,
        "forwarded": 0,
        "pending_forward": 0,
        "skipped_no_email": 0,
        "by_assignee": {},
        "by_level": {},
    }

    for rec in records:
        fields = rec.get("fields", {})
        status = extract_text(fields.get(FIELD_AUTOREPLY_STATUS, ""))
        fwd_status = extract_text(fields.get(FIELD_REPLY_FWD_STATUS, ""))
        assignee = extract_text(fields.get(FIELD_ASSIGNEE, ""))
        level = extract_text(fields.get(FIELD_CLUE_LEVEL, ""))

        # 自动回复状态
        if status == "Sent":
            stats["sent"] += 1
        elif status == "Error":
            stats["error"] += 1
        elif status == "No-Template":
            stats["no_template"] += 1
        elif status == "Customer-Replied":
            stats["customer_replied"] += 1

        # 转发状态
        if fwd_status == "Forwarded":
            stats["forwarded"] += 1
        elif fwd_status == "Skip-No-Email":
            stats["skipped_no_email"] += 1
        elif status == "Customer-Replied" and not fwd_status:
            stats["pending_forward"] += 1

        # 按业务员
        if assignee:
            stats["by_assignee"][assignee] = stats["by_assignee"].get(assignee, 0) + 1

        # 按等级
        if level:
            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1

    # 回复率
    total_replied = stats["customer_replied"] + stats["forwarded"]
    stats["reply_rate"] = f"{total_replied / stats['sent'] * 100:.1f}%" if stats["sent"] else "N/A"

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# 消息发送
# ═══════════════════════════════════════════════════════════════════════════════

def format_and_send(stats: dict):
    """格式化并发送飞书消息卡片。"""
    now_beijing = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")

    assignee_lines = ""
    for name, count in stats["by_assignee"].items():
        assignee_lines += f"\n  {name}: {count} 条"

    level_lines = ""
    for level, count in sorted(stats["by_level"].items()):
        level_lines += f"\n  {level}: {count} 条"

    text = (
        f"邮件自动建联日报 ({now_beijing})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"自动回复: {stats['sent']} 发送成功 / {stats['error']} 失败 / {stats['no_template']} 无模板\n"
        f"客户回复: {stats['customer_replied'] + stats['forwarded']} 已回复 / "
        f"{stats['forwarded']} 已转发 / {stats['pending_forward']} 待转发\n"
        f"回复率: {stats['reply_rate']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"按业务员:{assignee_lines or ' 无数据'}\n"
        f"按等级:{level_lines or ' 无数据'}"
    )

    if not WEBHOOK_URL:
        log.info("无 Webhook URL，打印统计:\n%s", text)
        return

    # 飞书消息卡片格式
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"邮件自动建联日报 ({now_beijing})"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**自动回复**  {stats['sent']} 成功 / {stats['error']} 失败 / {stats['no_template']} 无模板\n"
                            f"**客户回复**  {stats['customer_replied'] + stats['forwarded']} 已回复 / "
                            f"{stats['forwarded']} 已转发 / {stats['pending_forward']} 待转发\n"
                            f"**回复率**  {stats['reply_rate']}"
                        ),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**灰度业务员**: {', '.join(stats['by_assignee'].keys()) or '无'}",
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**等级分布**: {' / '.join(f'{k}:{v}' for k, v in sorted(stats['by_level'].items())) or '无'}",
                    },
                },
            ],
        },
    }

    import requests
    try:
        resp = requests.post(WEBHOOK_URL, json=card, timeout=10)
        log.info("日报发送: status=%s", resp.status_code)
    except Exception as e:
        log.error("日报发送失败: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=== Daily Stats 启动 (UTC %s) ===",
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    token = get_feishu_token()
    records = fetch_autoreply_records(token)
    log.info("查询到 %d 条自动回复记录", len(records))

    stats = aggregate_stats(records)
    log.info("统计: %s", stats)

    format_and_send(stats)
    log.info("=== Daily Stats 完成 ===")


if __name__ == "__main__":
    main()
