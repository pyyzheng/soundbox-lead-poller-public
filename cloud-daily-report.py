#!/usr/bin/env python3
"""
cloud-daily-report.py — 云端每日线索报告

从飞书「过滤日志」表查询完整处理记录（含 pass/reject/duplicate），
按渠道分类统计有效/无效/过滤原因，推送飞书卡片。

恢复旧版 OpenClaw 日报格式。

GitHub Secrets:
  FEISHU_APP_ID, FEISHU_APP_SECRET
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from assignment_fields import FIELD_ASSIGNEE  # noqa: E402
from feishu_utils import (
    get_feishu_token, extract_text, FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID, FIELD_AUTOREPLY_STATUS, FIELD_AUTOREPLY_SENT_AT,
    search_filter_logs, require_env, report_webhook_url,
)

FILTER_LOG_TABLE = require_env("FEISHU_FILTER_LOG_TABLE_ID")

# 自动建联统计字段（合并自 cloud-daily-stats）
FIELD_REPLY_FWD_STATUS = "Reply Forward Status"
FIELD_CLUE_LEVEL = "Clue level（线索等级）"

CHANNEL_GROUPS = [
    ("谷歌1官方", "谷歌1", "🔵"),
    ("谷歌2官方", "谷歌2", "🟢"),
    ("新官网", "新官网", "🟣"),
    ("总舱网", "总舱网", "⚪"),
    ("美国舱网", "美国舱网", "🔴"),
    ("加拿大舱网", "加拿大舱网", "🟠"),
]


def classify_channel(channel: str) -> str:
    """将 Channel 字段值映射到标准渠道名。"""
    for _, kw, _ in CHANNEL_GROUPS:
        if kw in channel:
            return kw
    return "谷歌2"


def analyze_channel(records: list, channel_kw: str) -> dict:
    """分析单个渠道的统计数据。"""
    total = 0
    valid = 0
    invalid = 0
    duplicate = 0
    feishu_ok = 0
    invalid_types = defaultdict(int)

    for rec in records:
        fields = rec.get("fields", {})
        ch = extract_text(fields.get("Channel", ""))
        if channel_kw not in ch:
            continue
        total += 1
        action = extract_text(fields.get("Action", ""))
        reason = extract_text(fields.get("Reason", ""))

        if action == "pass":
            valid += 1
            feishu_ok += 1
        elif action == "reject":
            invalid += 1
            # 解析 reason（格式: gate_reject: spam+placeholder 或 L3_non_inquiry 等）
            if ":" in reason:
                reason = reason.split(":", 1)[1].strip()
            invalid_types[reason or "unknown"] += 1
        elif action == "duplicate":
            duplicate += 1
        elif action == "error":
            invalid += 1
            invalid_types["处理错误"] += 1
        else:
            invalid += 1
            invalid_types["unknown"] += 1

    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "duplicate": duplicate,
        "invalid_types": dict(invalid_types),
        "feishu_ok": feishu_ok,
    }


def format_channel_block(channel_name: str, stats: dict, emoji: str) -> str:
    lines = [f"{emoji} **{channel_name}**"]
    lines.append(f"　　📨 总收到：{stats['total']} 条")
    lines.append(f"　　✅ 有效询盘：{stats['valid']} 条")
    lines.append(f"　　❌ 无效询盘：{stats['invalid']} 条")

    if stats["invalid_types"]:
        lines.append(f"　　📋 无效类型：")
        for reason, count in sorted(stats["invalid_types"].items(), key=lambda x: -x[1])[:3]:
            lines.append(f"　　　• {reason}：{count} 条")

    if stats["duplicate"] > 0:
        lines.append(f"　　🔄 去重跳过：{stats['duplicate']} 条")
    lines.append(f"　　📤 飞书写入成功：{stats['feishu_ok']} 条")

    return "\n".join(lines)


def fetch_autoreply_records(token: str) -> list:
    """查询过去 24 小时有自动回复活动的记录（合并自 cloud-daily-stats）。"""
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    conditions = [
        {"field_name": FIELD_AUTOREPLY_STATUS, "operator": "isNotEmpty", "value": []},
    ]
    all_items = []
    page_token = ""
    while True:
        body = {
            "filter": {"conjunction": "and", "conditions": conditions},
            "field_names": [
                FIELD_AUTOREPLY_STATUS, FIELD_AUTOREPLY_SENT_AT,
                FIELD_REPLY_FWD_STATUS, FIELD_ASSIGNEE, FIELD_CLUE_LEVEL,
            ],
            "page_size": 500,
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
            print(f"建联记录查询失败: {data}", file=sys.stderr)
            break
        for item in data.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            sent_at_raw = extract_text(fields.get(FIELD_AUTOREPLY_SENT_AT, ""))
            if sent_at_raw:
                try:
                    dt = datetime.fromisoformat(sent_at_raw.replace("Z", "+00:00"))
                    if int(dt.timestamp() * 1000) >= cutoff_ms:
                        all_items.append(item)
                except (ValueError, TypeError):
                    pass
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token", "")
    return all_items


def aggregate_stats(records: list) -> dict:
    """聚合自动建联统计（合并自 cloud-daily-stats）。"""
    stats = {
        "total": len(records), "sent": 0, "error": 0, "no_template": 0,
        "customer_replied": 0, "forwarded": 0, "pending_forward": 0,
        "by_assignee": {}, "by_level": {},
    }
    for rec in records:
        fields = rec.get("fields", {})
        status = extract_text(fields.get(FIELD_AUTOREPLY_STATUS, ""))
        fwd_status = extract_text(fields.get(FIELD_REPLY_FWD_STATUS, ""))
        assignee = extract_text(fields.get(FIELD_ASSIGNEE, ""))
        level = extract_text(fields.get(FIELD_CLUE_LEVEL, ""))
        if status == "Sent":
            stats["sent"] += 1
        elif status == "Error":
            stats["error"] += 1
        elif status == "No-Template":
            stats["no_template"] += 1
        elif status == "Customer-Replied":
            stats["customer_replied"] += 1
        if fwd_status == "Forwarded":
            stats["forwarded"] += 1
        elif status == "Customer-Replied" and not fwd_status:
            stats["pending_forward"] += 1
        if assignee:
            stats["by_assignee"][assignee] = stats["by_assignee"].get(assignee, 0) + 1
        if level:
            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1
    total_replied = stats["customer_replied"] + stats["forwarded"]
    stats["reply_rate"] = f"{total_replied / stats['sent'] * 100:.1f}%" if stats["sent"] else "N/A"
    return stats


def format_stats_block(stats: dict) -> list:
    """建联统计 → 卡片 elements（无数据返回空列表）。"""
    if stats["total"] == 0:
        return []
    block = [{"tag": "div", "text": {"tag": "lark_md", "content": "🤖 **自动建联（过去24h）**"}}]
    block.append({"tag": "div", "text": {"tag": "lark_md", "content": (
        f"　　发送：{stats['sent']} 成功 / {stats['error']} 失败 / {stats['no_template']} 无模板\n"
        f"　　回复：{stats['customer_replied'] + stats['forwarded']} 已回复 / "
        f"{stats['forwarded']} 已转发 / {stats['pending_forward']} 待转发\n"
        f"　　回复率：{stats['reply_rate']}"
    )}})
    if stats["by_assignee"]:
        assignee = ' / '.join(f'{k}:{v}' for k, v in sorted(stats["by_assignee"].items(), key=lambda x: -x[1]))
        block.append({"tag": "div", "text": {"tag": "lark_md", "content": f"　　业务员：{assignee}"}})
    if stats["by_level"]:
        level = ' / '.join(f'{k}:{v}' for k, v in sorted(stats["by_level"].items()))
        block.append({"tag": "div", "text": {"tag": "lark_md", "content": f"　　等级：{level}"}})
    block.append({"tag": "hr"})
    return block


def push_to_feishu(card_elements: list, date_str: str) -> bool:
    payload = json.dumps({
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📋 每日线索报告（{date_str}）"},
                "template": "blue"
            },
            "elements": card_elements
        }
    }, ensure_ascii=False).encode("utf-8")
    try:
        webhook_url = report_webhook_url()
        if not webhook_url:
            print("FEISHU_REPORT_WEBHOOK / FEISHU_ALERT_WEBHOOK 未配置，跳过推送", file=sys.stderr)
            return False
        resp = requests.post(webhook_url, data=payload,
            headers={"Content-Type": "application/json"}, timeout=15)
        result = resp.json()
        if result.get("code", -1) == 0:
            print("飞书推送成功")
            return True
        print(f"飞书返回错误: {result}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"推送失败: {e}", file=sys.stderr)
        return False


def main():
    tz_sh = timezone(timedelta(hours=8))
    now_sh = datetime.now(tz_sh)
    today_cutoff = now_sh.replace(hour=9, minute=0, second=0, microsecond=0)
    yesterday_start = (now_sh - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    yesterday_label = yesterday_start.strftime("%-m月%-d日")
    today_label = now_sh.strftime("%-m月%-d日")
    time_range = f"{yesterday_label} 00:00 ~ {today_label} 09:00"
    print(f"[{time_range}] 开始生成线索报告...")

    token = get_feishu_token()
    start_ms = int(yesterday_start.timestamp() * 1000)
    end_ms = int(today_cutoff.timestamp() * 1000)
    records = search_filter_logs(token, FILTER_LOG_TABLE, start_ms, end_ms)
    print(f"查询到 {len(records)} 条过滤日志记录")

    total_all = len(records)

    card_elements = []
    card_elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"📊 **收到邮件（进入流程）：{total_all} 条**\n"}
    })
    card_elements.append({"tag": "hr"})

    if total_all == 0:
        card_elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"📭 {time_range} 无新线索邮件。"}
        })
    else:
        for channel_name, kw, emoji in CHANNEL_GROUPS:
            stats = analyze_channel(records, kw)
            if stats["total"] > 0:
                block = format_channel_block(channel_name, stats, emoji)
                card_elements.append({"tag": "div", "text": {"tag": "lark_md", "content": block}})
                card_elements.append({"tag": "hr"})

    # 自动建联统计（合并自 cloud-daily-stats）
    stats_block = []
    try:
        ar_records = fetch_autoreply_records(token)
        print(f"查询到 {len(ar_records)} 条建联记录")
        if ar_records:
            stats_block = format_stats_block(aggregate_stats(ar_records))
    except Exception as e:
        print(f"建联统计失败（不影响线索报告）: {e}", file=sys.stderr)
    card_elements.extend(stats_block)

    # 空数据跳过：线索 0 且无建联数据 → 不发日报
    if total_all == 0 and not stats_block:
        print(f"[跳过] {time_range} 无线索邮件且无建联数据，不发日报")
        sys.exit(0)

    # 打印预览
    for el in card_elements:
        if el.get("tag") == "div":
            print(el["text"]["content"])
        elif el.get("tag") == "hr":
            print("---")

    ok = push_to_feishu(card_elements, time_range)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
