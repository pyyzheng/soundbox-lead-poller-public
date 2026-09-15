#!/usr/bin/env python3
"""
cloud-weekly-report.py — 周度线索汇总报告

聚合过去 7 天的过滤日志，展示渠道趋势和过滤原因 TOP5。
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from feishu_utils import get_feishu_token, extract_text, search_filter_logs, require_env, report_webhook_url
from audit_core import run_audit

FILTER_LOG_TABLE = require_env("FEISHU_FILTER_LOG_TABLE_ID")

CHANNEL_GROUPS = [
    ("谷歌1官方", "谷歌1", "🔵"),
    ("谷歌2官方", "谷歌2", "🟢"),
    ("新官网", "新官网", "🟣"),
    ("总舱网", "总舱网", "⚪"),
    ("美国舱网", "美国舱网", "🔴"),
    ("加拿大舱网", "加拿大舱网", "🟠"),
]


def format_audit_block(audit: dict) -> list:
    """调研质量抽检 → 卡片 elements（无抽样返回空）。"""
    if audit["total"] == 0:
        return []
    block = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": (
            f"🔍 **调研质量抽检**  抽样 {audit['total']}/{audit['population']} 条，"
            f"异常 {audit['error_count']} 条"
        )},
    }]
    if audit["error_count"] > 0:
        err_lines = []
        for r in audit["results"][:5]:
            if r["format_issues"] or (r["audit"] and not r["audit"].get("grade_match", True)):
                note = (r["audit"].get("notes", "")[:40]) if r["audit"] else ""
                err_lines.append(f"　　• {r['record_id']} ({r['original_grade']}): {note or '格式问题'}")
        if err_lines:
            block.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(err_lines)}})
            if audit["error_count"] > 5:
                block.append({"tag": "div", "text": {"tag": "lark_md", "content": f"_共 {audit['error_count']} 条异常，仅展示前5_"}})
    block.append({"tag": "hr"})
    return block


def push_to_feishu(card_elements: list, date_str: str) -> bool:
    payload = json.dumps({
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 周度线索汇总（{date_str}）"},
                "template": "green"
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
    start = (now_sh - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

    start_label = start.strftime("%-m月%-d日")
    end_label = now_sh.strftime("%-m月%-d日")
    date_str = f"{start_label} ~ {end_label}"
    print(f"[{date_str}] 开始生成周度汇总...")

    token = get_feishu_token()
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now_sh.timestamp() * 1000)
    records = search_filter_logs(token, FILTER_LOG_TABLE, start_ms, end_ms)
    print(f"查询到 {len(records)} 条过滤日志记录")

    total = len(records)
    valid = sum(1 for r in records if extract_text(r.get("fields", {}).get("Action", "")) == "pass")
    invalid = sum(1 for r in records if extract_text(r.get("fields", {}).get("Action", "")) == "reject")
    duplicate = sum(1 for r in records if extract_text(r.get("fields", {}).get("Action", "")) == "duplicate")
    error = sum(1 for r in records if extract_text(r.get("fields", {}).get("Action", "")) == "error")
    valid_rate = f"{valid / total * 100:.1f}%" if total else "N/A"

    # 渠道统计
    channel_stats = {}
    for name, kw, emoji in CHANNEL_GROUPS:
        ch_records = [r for r in records if kw in extract_text(r.get("fields", {}).get("Channel", ""))]
        ch_valid = sum(1 for r in ch_records if extract_text(r.get("fields", {}).get("Action", "")) == "pass")
        ch_invalid = sum(1 for r in ch_records if extract_text(r.get("fields", {}).get("Action", "")) == "reject")
        ch_rate = f"{ch_valid / len(ch_records) * 100:.1f}%" if ch_records else "-"
        channel_stats[kw] = {"name": name, "emoji": emoji, "total": len(ch_records),
                             "valid": ch_valid, "invalid": ch_invalid, "rate": ch_rate}

    # 过滤原因 TOP5
    reason_counts = defaultdict(int)
    for r in records:
        action = extract_text(r.get("fields", {}).get("Action", ""))
        if action == "reject":
            reason = extract_text(r.get("fields", {}).get("Reason", ""))
            if ":" in reason:
                reason = reason.split(":", 1)[1].strip()
            reason_counts[reason or "unknown"] += 1
    top5 = sorted(reason_counts.items(), key=lambda x: -x[1])[:5]

    # 构建卡片
    card_elements = []
    card_elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": (
            f"总邮件：{total} 条 | 有效：{valid} 条 | 无效：{invalid} 条 | "
            f"去重：{duplicate} 条 | 错误：{error} 条\n"
            f"**有效率：{valid_rate}**"
        )}
    })
    card_elements.append({"tag": "hr"})

    # 渠道趋势表格
    table_rows = ""
    for name, kw, emoji in CHANNEL_GROUPS:
        s = channel_stats[kw]
        if s["total"] > 0:
            table_rows += f"\n{emoji} {s['name']} | {s['total']} | {s['valid']} | {s['invalid']} | {s['rate']}"

    if table_rows:
        card_elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**📈 渠道趋势**\n渠道 | 总计 | 有效 | 无效 | 有效率{table_rows}"}
        })
        card_elements.append({"tag": "hr"})

    # 过滤原因 TOP5
    if top5:
        top5_lines = "\n".join(f"{i+1}. {reason}（{count}条）" for i, (reason, count) in enumerate(top5))
        card_elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**🏆 过滤原因 TOP5**\n{top5_lines}"}
        })

    if total == 0:
        card_elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"📝 过去 7 天无过滤日志记录（过滤日志表可能尚未积累数据）。"}
        })

    # 调研质量抽检（合并自 cloud-research-audit，LLM 失败不影响周报）
    try:
        audit = run_audit(token, days=7, sample=8,
                          zhipu_key=os.environ.get("ZHIPU_API_KEY", ""),
                          zhipu_model=os.environ.get("ZHIPU_MODEL", "glm-4.5-air"))
        print(f"调研质量抽检: 总池 {audit['population']}, 抽样 {audit['total']}, 异常 {audit['error_count']}")
        card_elements.extend(format_audit_block(audit))
    except Exception as e:
        print(f"调研质量抽检失败（不影响周报）: {e}", file=sys.stderr)

    for el in card_elements:
        if el.get("tag") == "div":
            print(el["text"]["content"])
        elif el.get("tag") == "hr":
            print("---")

    ok = push_to_feishu(card_elements, date_str)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
