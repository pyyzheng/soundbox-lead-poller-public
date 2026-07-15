#!/usr/bin/env python3
"""
cloud-check-unassigned.py — 云端版未分配线索检查

检查飞书 Bitable 中的异常线索（未命中规则/匹配错误/公式异常/待人工确认），发飞书通知。
不依赖本地文件系统，可直接在 GitHub Actions 运行。

GitHub Secrets:
  FEISHU_APP_ID, FEISHU_APP_SECRET
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from assignment_fields import FIELD_ASSIGNEE, FIELD_ASSIGN_SOURCE, FIELD_SYSTEM, get_field  # noqa: E402
from feishu_utils import get_feishu_token, feishu_search_url, extract_text, FIELD_CONTENT, FIELD_DATE, FEISHU_APP_TOKEN, FEISHU_TABLE_ID, alert_webhook_url
MIN_AGE_MINUTES = 30
PAGE_SIZE = 100
BASE_URL = os.environ.get(
    "FEISHU_BASE_URL",
    f"https://rcn1z5q6iyyc.feishu.cn/base/{FEISHU_APP_TOKEN}?table={FEISHU_TABLE_ID}",
)

ASSIGN_BASIS_FIELD = "分配依据"
ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")


def search_records(token: str, field_name: str, value: str) -> tuple:
    """搜索指定字段值的记录（含分页）"""
    all_items = []
    page_token = None
    total = 0

    while True:
        body = {
            "filter": {
                "conjunction": "and",
                "conditions": [{"field_name": field_name, "operator": "is", "value": [value]}],
            },
            "page_size": PAGE_SIZE,
        }
        if page_token:
            body["page_token"] = page_token

        resp = requests.post(
            feishu_search_url(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"查询错误: {data}", file=sys.stderr)
            break

        items = data.get("data", {}).get("items", [])
        all_items.extend(items)
        total = data.get("data", {}).get("total", total)

        page_token = data.get("data", {}).get("page_token")
        if not data.get("data", {}).get("has_more"):
            break

    return all_items, total


def collect_records(token: str, queries: list[tuple[str, str]]) -> tuple[list[dict], dict[str, int]]:
    """按多个字段条件收集异常记录，并按 record_id 去重。"""
    records_by_id = {}
    totals = {}
    for field_name, value in queries:
        items, total = search_records(token, field_name, value)
        totals[f"{field_name}={value}"] = total
        for item in items:
            record_id = item.get("record_id")
            if record_id:
                item.setdefault("_issue_reasons", []).append(f"{field_name}={value}")
                if record_id in records_by_id:
                    records_by_id[record_id].setdefault("_issue_reasons", []).append(f"{field_name}={value}")
                else:
                    records_by_id[record_id] = item
    return list(records_by_id.values()), totals


def is_old_enough(record) -> bool:
    """检查记录创建时间超过阈值"""
    fields = record.get("fields", {})
    created = extract_text(fields.get(FIELD_DATE, ""))
    if not created:
        return True
    try:
        created_date = datetime.strptime(created, "%Y/%m/%d").replace(
            tzinfo=timezone(timedelta(hours=8)))
        cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(minutes=MIN_AGE_MINUTES)
        return created_date < cutoff
    except ValueError:
        return True


def diagnose_record(fields: dict) -> dict:
    """根据记录字段推断异常环节"""
    country = extract_text(fields.get("Country（国家）", ""))
    dept = extract_text(fields.get("分配部门", ""))
    sub_office = extract_text(fields.get("子办规则命中负责人", ""))
    agent = extract_text(fields.get("代理规则命中业务员", ""))
    queue = extract_text(fields.get("渠道顺序队列匹配业务员", ""))
    final = extract_text(get_field(fields, FIELD_ASSIGNEE, ""))
    system = extract_text(get_field(fields, FIELD_SYSTEM, ""))
    basis = extract_text(fields.get(ASSIGN_BASIS_FIELD, ""))
    source = extract_text(get_field(fields, FIELD_ASSIGN_SOURCE, ""))

    if system == "匹配错误请检查" or source == "查重冲突":
        return {
            "stage": "查重冲突",
            "reason": f"分配来源={source or '空'}，系统匹配业务员={system or '空'}",
            "check_fields": "Dup_Match_Result、Dup_Match_Conflict、Dup_Match_Owner、分配来源",
            "handler": "业务 + 技术",
        }
    if system == "公式计算异常":
        return {
            "stage": "公式计算异常",
            "reason": "系统匹配业务员公式返回异常",
            "check_fields": "分配来源、分配依据、系统匹配业务员、相关 lookup 字段",
            "handler": "技术",
        }
    if basis == "待人工确认":
        return {
            "stage": "待人工确认",
            "reason": "代理国家产品待确认，或查重/分配条件需要人工判断",
            "check_fields": "分配依据、是否命中代理产品、Product model、Dup_Match_Result",
            "handler": "业务",
        }

    if not country:
        return {
            "stage": "国家识别失败",
            "reason": "国家字段为空，解析环节未提取到国家",
            "check_fields": "Country（国家）、Enquiry details",
            "handler": "技术",
        }
    if not dept:
        return {
            "stage": "国家→部门映射缺失",
            "reason": f"国家={country} 未在国家区域映射表中配置",
            "check_fields": "分配部门、国家区域映射表",
            "handler": "业务",
        }
    if not sub_office and not agent and not queue:
        return {
            "stage": "无匹配规则",
            "reason": f"国家={country}，部门={dept}，但子办/代理/队列均未命中",
            "check_fields": "子办规则命中负责人、代理规则命中业务员、渠道顺序队列匹配业务员",
            "handler": "业务",
        }
    if queue and not final:
        return {
            "stage": "队列轮转异常",
            "reason": f"队列匹配到 {queue}，但最终业务员为空",
            "check_fields": "渠道顺序队列匹配业务员、最终分配的业务员",
            "handler": "技术",
        }
    return {
        "stage": "未知",
        "reason": "字段组合不在已知异常模式中，需人工排查",
        "check_fields": "分配依据、系统匹配业务员、最终分配的业务员",
        "handler": "业务",
    }


CATEGORY_META = {
    "查重冲突": {
        "异常环节": "查重继承",
        "初步判断": "多个查重来源得到不可自动继承的结果",
        "排查顺序": "1. 查 Dup_Match_Result / Dup_Match_Owner → 2. 查历史数据汇总 → 3. 确认是否人工改派",
        "是否可自动修复": "部分可自动修复，需先确认冲突来源",
        "建议处理角色": "业务 + Claude执行",
    },
    "公式计算异常": {
        "异常环节": "公式字段",
        "初步判断": "分配公式或 lookup 返回异常",
        "排查顺序": "1. 查系统匹配业务员 → 2. 查分配来源/分配依据 → 3. 检查最近字段变更",
        "是否可自动修复": "可由 Claude 检查公式后修复",
        "建议处理角色": "技术",
    },
    "待人工确认": {
        "异常环节": "人工确认",
        "初步判断": "代理产品/型号或查重结果需要人工确认",
        "排查顺序": "1. 查产品大类/型号 → 2. 查代理优先规则表 → 3. 查 Dup_Match_Result",
        "是否可自动修复": "通常需业务确认",
        "建议处理角色": "业务",
    },
    "国家识别失败": {
        "异常环节": "国家解析",
        "初步判断": "国家字段为空，解析环节未提取到国家",
        "排查顺序": "1. 查 Enquiry details 原文 → 2. 检查 slot_extractor 国家提取逻辑 → 3. 检查 LLM prompt",
        "是否可自动修复": "需业务确认后可由 Claude 修改配置",
        "建议处理角色": "业务确认 + Claude执行",
    },
    "国家→部门映射缺失": {
        "异常环节": "国家→部门映射",
        "初步判断": "国家未在映射表中配置",
        "排查顺序": "1. 确认该国家是否应分配 → 2. 在 lead-rules.json country_region_map 新增映射",
        "是否可自动修复": "需业务确认后可由 Claude 修改配置",
        "建议处理角色": "业务确认 + Claude执行",
    },
    "无匹配规则": {
        "异常环节": "分配规则匹配",
        "初步判断": "国家+部门组合无对应子办/代理/队列规则",
        "排查顺序": "1. 确认该国家+部门是否应有规则 → 2. 检查 lead-rules.json office_rules / agent_rules",
        "是否可自动修复": "需业务确认后可由 Claude 修改配置",
        "建议处理角色": "业务确认 + Claude执行",
    },
    "队列轮转异常": {
        "异常环节": "队列轮转",
        "初步判断": "队列匹配到业务员但最终分配为空",
        "排查顺序": "1. 查渠道顺序队列匹配业务员字段 → 2. 检查轮转计数器 → 3. 检查 cloud-lead-poller.py 队列逻辑",
        "是否可自动修复": "否",
        "建议处理角色": "技术",
    },
    "未知": {
        "异常环节": "未知",
        "初步判断": "字段组合不在已知异常模式中",
        "排查顺序": "1. 查分配依据、系统匹配业务员字段 → 2. 对照 lead-rules.json 逐条排查",
        "是否可自动修复": "否",
        "建议处理角色": "业务 + 技术",
    },
}


def format_lead(record, idx=0) -> dict:
    """格式化单条记录为一行摘要 + 飞书链接"""
    f = record.get("fields", {})
    record_id = record.get("record_id", "")
    date = extract_text(f.get(FIELD_DATE, ""))
    content = extract_text(f.get("Enquiry details（询盘内容）", ""))
    source = extract_text(get_field(f, FIELD_ASSIGN_SOURCE, ""))
    basis = extract_text(f.get(ASSIGN_BASIS_FIELD, ""))
    system = extract_text(get_field(f, FIELD_SYSTEM, ""))

    first_line = content.split("\n")[0][:40] if content else ""

    diag = diagnose_record(f)
    category = diag["stage"]

    # 一行摘要
    reason = diag["reason"][:36]
    link = f"{BASE_URL}&record={record_id}" if record_id else BASE_URL
    status = f"来源={source or '-'} / 依据={basis or '-'} / 系统={system or '-'}"
    text = f"{idx}. [{record_id[:8]}]({link}) | {date} | {first_line} | {status} | {reason}"

    return {
        "text": text,
        "fields": f,
        "category": category,
    }


def send_notification(异常列表, totals: dict[str, int]) -> bool:
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines = [
        f"谷歌询盘 | {now_str} | 分配异常 {len(异常列表)} 条",
        "",
    ]

    if totals:
        lines.append("**查询口径：**")
        for label, total in totals.items():
            lines.append(f"- {label}: {total}")
        lines.append("")

    if 异常列表:
        lines.append("**异常明细：**")
        for item in 异常列表:
            lines.append(item["text"] if isinstance(item, dict) else item)
        lines.append("")

    # 一行建议
    cats = [item["category"] for item in 异常列表
            if isinstance(item, dict) and item.get("category")]
    if cats:
        cat_counts = Counter(cats)
        top_cat = cat_counts.most_common(1)[0][0]
        top_meta = CATEGORY_META.get(top_cat, {})
        suggestion = top_meta.get("排查顺序", "").split("→")[0].strip(" 1.")
        lines.append(f"建议：{top_cat} → {suggestion}")

    md_content = "\n".join(lines)
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "数据分配异常"}},
            "elements": [{"tag": "markdown", "content": md_content}]
        }
    }

    try:
        webhook_url = alert_webhook_url()
        if not webhook_url:
            print("FEISHU_ALERT_WEBHOOK 未配置，跳过通知", file=sys.stderr)
            return False
        resp = requests.post(webhook_url, json=card, timeout=15)
        result = resp.json()
        return result.get("code") == 0
    except Exception as e:
        print(f"通知发送失败: {e}", file=sys.stderr)
        return False


def main():
    dry_run = os.environ.get("DRY_RUN", "false") == "true"

    token = get_feishu_token()

    queries = [(FIELD_SYSTEM, value) for value in ERROR_ASSIGNEES]
    queries.append((ASSIGN_BASIS_FIELD, "待人工确认"))
    records, totals = collect_records(token, queries)
    old_records = [r for r in records if is_old_enough(r)]

    print(f"分配异常: {len(old_records)} 条（去重前查询命中 {sum(totals.values())}）")
    for label, total in totals.items():
        print(f"- {label}: {total}")

    if not old_records:
        print("无异常，跳过通知")
        return

    formatted = [format_lead(r, i) for i, r in enumerate(old_records, 1)]

    if dry_run:
        print("\n--- 分配异常 ---")
        for item in formatted:
            print(item["text"] if isinstance(item, dict) else item)
        print("\n[dry-run] 跳过发送通知")
        return

    success = send_notification(formatted, totals)
    if success:
        print("通知已发送")
    else:
        print("通知发送失败", file=sys.stderr)


if __name__ == "__main__":
    main()
