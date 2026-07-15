#!/usr/bin/env python3
"""
cloud-format-audit.py — 飞书写入格式只读审查

检查最近 N 小时谷歌渠道线索的写入格式是否正确：
  1. 标签行结构（国家-细分渠道-产品大类-型号）
  2. 标签行 vs 飞书字段一致性
  3. 邮箱域名 vs 细分渠道
  4. 关键字段非空
  5. 产品大类 vs 型号对应

不修改任何数据，只读审查。
"""
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from assignment_fields import FIELD_LEAD_ID, get_field  # noqa: E402
from feishu_utils import (
    get_feishu_token, feishu_search_url, extract_text, FIELD_CONTENT, FIELD_DATE,
    fetch_records_since, FEISHU_APP_TOKEN, FEISHU_TABLE_ID, alert_webhook_url,
)
TZ_SH = timezone(timedelta(hours=8))
PAGE_SIZE = 100
GH_REPO = os.environ.get("GITHUB_REPO", "pyyzheng/soundbox-lead-poller-public")

# 邮箱域名 → 细分渠道
EMAIL_SUB_CHANNEL = {
    "inquiry@soundboxacoustic.com": "谷歌1",
    "email@soundboxbooth.com": "谷歌2",
}

# 产品大类 → 允许的型号
CATEGORY_MODELS = {
    "静音舱": {"SR", "VR", "VRT", "ART", "尖顶", "平顶", "全系列", "无法识别"},
    "家居舱": {"Homepod", "无法识别"},
    "声学产品": {"无法识别", "SR", "EQ", "DQ", "AQ"},  # 声学子型号较多，宽松检查
}

# 需要检查非空的关键字段
REQUIRED_FIELDS = [
    "Country（国家）",
    "Channels（渠道）",
    "Channel segmentation (细分渠道)",
    "Product Categories（产品大类）",
    "Product model（具体型号）",
    "Clue level（线索等级）",
    "Lead Grading Criteria（分级依据）",
]


# ═══════════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════════

def fetch_google_records(token: str, hours: int = 24) -> list:
    """获取最近 N 小时的谷歌渠道记录"""
    cutoff_ms = int((datetime.now(TZ_SH) - timedelta(hours=hours)).timestamp() * 1000)
    return fetch_records_since(token, cutoff_ms, channel="谷歌")


# ═══════════════════════════════════════════════════════════════
# 检查函数
# ═══════════════════════════════════════════════════════════════

def extract_tag_line(content: str):
    """从 Enquiry details 提取标签行（最后一个非空行）"""
    if not content:
        return None
    lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
    if not lines:
        return None
    return lines[-1]


def parse_email_from_content(content: str) -> str:
    """从 Enquiry details 提取 Email 字段值"""
    if not content:
        return ""
    m = re.search(r"Email:\s*(.+?)(?:<br|<br/|\n|$)", content, re.IGNORECASE)
    return m.group(1).strip().lower() if m else ""


def check_tag_structure(record) -> dict | None:
    """检查 1：标签行结构"""
    f = record.get("fields", {})
    content = extract_text(f.get(FIELD_CONTENT, ""))
    tag = extract_tag_line(content)

    if not tag:
        return {"issue": "无标签行", "detail": "Enquiry details 最后一行为空"}

    segments = tag.split("-")
    if len(segments) < 4:
        return {"issue": "标签行段数不足", "detail": f"期望≥4段，实际{len(segments)}段: {tag}"}

    country, sub_ch, category, model = segments[0], segments[1], segments[2], segments[3]
    problems = []
    if not country:
        problems.append("国家为空")
    if not sub_ch:
        problems.append("细分渠道为空")
    if not category:
        problems.append("产品大类为空")
    if not model:
        problems.append("型号为空")

    if problems:
        return {"issue": "标签行有空段", "detail": f"{'+'.join(problems)}: {tag}"}

    return None


def check_field_consistency(record) -> dict | None:
    """检查 2：标签行 vs 飞书字段"""
    f = record.get("fields", {})
    content = extract_text(f.get(FIELD_CONTENT, ""))
    tag = extract_tag_line(content)
    if not tag:
        return None

    segments = tag.split("-")
    if len(segments) < 4:
        return None  # 结构问题已在检查1报告

    tag_country, tag_sub_ch, tag_category, tag_model = segments[0], segments[1], segments[2], segments[3]

    field_country = extract_text(f.get("Country（国家）", ""))
    field_sub_ch = extract_text(f.get("Channel segmentation (细分渠道)", "") or f.get("细分渠道（Channel segmentation）", ""))
    field_category = extract_text(f.get("Product Categories（产品大类）", ""))
    field_model = extract_text(f.get("Product model（具体型号）", ""))

    mismatches = []
    if tag_country and field_country and tag_country != field_country:
        mismatches.append(f"国家: 标签={tag_country} vs 字段={field_country}")
    if tag_sub_ch and field_sub_ch and tag_sub_ch != field_sub_ch:
        mismatches.append(f"渠道: 标签={tag_sub_ch} vs 字段={field_sub_ch}")
    # 产品字段：飞书可能是双语（如 "Silence Booth 静音舱"），做包含匹配
    if tag_category and field_category and tag_category not in field_category:
        mismatches.append(f"产品: 标签={tag_category} vs 字段={field_category}")
    if tag_model and field_model and tag_model != field_model:
        mismatches.append(f"型号: 标签={tag_model} vs 字段={field_model}")

    if mismatches:
        return {"issue": "字段不一致", "detail": "; ".join(mismatches)}
    return None


def check_email_subchannel(record) -> dict | None:
    """检查 3：邮箱域名 vs 细分渠道"""
    f = record.get("fields", {})
    content = extract_text(f.get(FIELD_CONTENT, ""))
    email = parse_email_from_content(content)
    field_sub_ch = extract_text(f.get("Channel segmentation (细分渠道)", "") or f.get("细分渠道（Channel segmentation）", ""))

    if not email or not field_sub_ch:
        return None

    expected = EMAIL_SUB_CHANNEL.get(email)
    if expected and field_sub_ch != expected:
        return {"issue": "邮箱与渠道不匹配", "detail": f"邮箱={email} 期望={expected} 实际={field_sub_ch}"}
    return None


def check_required_fields(record) -> dict | None:
    """检查 4：关键字段非空"""
    f = record.get("fields", {})
    empty = []
    for field_name in REQUIRED_FIELDS:
        val = extract_text(f.get(field_name, ""))
        if not val:
            empty.append(field_name)

    if empty:
        return {"issue": "关键字段为空", "detail": f"空字段: {', '.join(empty)}"}
    return None


def check_product_model(record) -> dict | None:
    """检查 5：产品大类 vs 型号对应"""
    f = record.get("fields", {})
    category = extract_text(f.get("Product Categories（产品大类）", ""))
    model = extract_text(f.get("Product model（具体型号）", ""))

    if not category or not model:
        return None

    allowed = CATEGORY_MODELS.get(category)
    if allowed and model not in allowed:
        return {"issue": "产品型号不匹配", "detail": f"大类={category} 型号={model} (允许: {', '.join(sorted(allowed))})"}
    return None


# ═══════════════════════════════════════════════════════════════
# 审查主流程
# ═══════════════════════════════════════════════════════════════

CHECKS = [
    ("标签行结构", check_tag_structure),
    ("字段不一致", check_field_consistency),
    ("邮箱渠道不匹配", check_email_subchannel),
    ("关键字段为空", check_required_fields),
    ("产品型号不匹配", check_product_model),
]

CHECK_META = {
    "标签行结构": {
        "异常环节": "标签行生成 → 飞书写入",
        "初步判断": "标签行生成逻辑可能异常",
        "排查顺序": "1. 查 cloud-lead-poller.py 标签行生成函数 → 2. 确认是全局还是个别 → 3. 检查输入参数",
        "是否可自动修复": "否",
        "建议处理角色": "Claude",
    },
    "字段不一致": {
        "异常环节": "标签行 → Bitable 字段映射",
        "初步判断": "标签行与飞书字段写入不一致",
        "排查顺序": "1. 对比标签行四段与飞书四字段 → 2. 检查 tag_line 写入映射代码",
        "是否可自动修复": "否",
        "建议处理角色": "Claude",
    },
    "邮箱渠道不匹配": {
        "异常环节": "子渠道路由",
        "初步判断": "邮箱域名与子渠道路由规则不一致",
        "排查顺序": "1. 确认邮箱实际域名 → 2. 检查 lead-rules.json sub_channel_map",
        "是否可自动修复": "需业务确认后可由 Claude 修改配置",
        "建议处理角色": "业务确认 + Claude执行",
    },
    "关键字段为空": {
        "异常环节": "字段解析 → 写入",
        "初步判断": "解析逻辑遗漏导致关键字段为空",
        "排查顺序": "1. 查空字段名 → 2. 检查 slot_extractor 对应逻辑 → 3. 检查 LLM prompt",
        "是否可自动修复": "可自动生成建议但需人工确认",
        "建议处理角色": "Claude",
    },
    "产品型号不匹配": {
        "异常环节": "产品分类规则",
        "初步判断": "产品大类与型号对应规则需更新",
        "排查顺序": "1. 确认新型号是否已上线 → 2. 更新 CATEGORY_MODELS → 3. 同步 lead-rules.json",
        "是否可自动修复": "需业务确认后可由 Claude 修改配置",
        "建议处理角色": "业务确认 + Claude执行",
    },
}


def audit_records(records: list) -> dict:
    """对记录列表执行所有检查，返回分类结果"""
    results = {name: [] for name, _ in CHECKS}

    for rec in records:
        rid = rec.get("record_id", "?")
        f = rec.get("fields", {})
        date = extract_text(f.get(FIELD_DATE, ""))
        content = extract_text(f.get(FIELD_CONTENT, ""))
        first_line = content.split("\n")[0][:50] if content else ""
        lead_id = extract_text(get_field(f, FIELD_LEAD_ID, ""))

        for name, check_fn in CHECKS:
            issue = check_fn(rec)
            if issue:
                results[name].append({
                    "id": rid,
                    "date": date,
                    "preview": first_line,
                    "lead_id": lead_id,
                    **issue,
                })

    return results


def format_issue(item: dict, idx: int) -> str:
    record_id = item.get("id", "")
    lead_id = item.get("lead_id", "")
    detail = item.get("detail", "")[:50]
    link = f"https://bytedance.larkoffice.com/base/{FEISHU_APP_TOKEN}/table/{FEISHU_TABLE_ID}/record/{record_id}"
    label = f"#{lead_id}" if lead_id else record_id[:8]
    return f"{idx}. [{label}]({link}) | {item['date']} | {item['preview'][:30]} | {detail}"


def build_notification(results: dict, total: int) -> str:
    """构建飞书卡片内容（精简格式：每条记录一行）"""
    now_str = datetime.now(TZ_SH).strftime("%Y-%m-%d %H:%M")
    anomaly_count = sum(len(v) for v in results.values())

    lines = [
        f"谷歌询盘格式 | {now_str} | {anomaly_count} 条异常 / 共 {total} 条",
        "",
    ]

    for name, items in results.items():
        if not items:
            continue
        lines.append(f"**{name} ({len(items)})：**")
        for i, item in enumerate(items[:5], 1):
            lines.append(format_issue(item, i))
        if len(items) > 5:
            lines.append(f"   ...还有 {len(items) - 5} 条")
        lines.append("")

    # 一行建议
    active = {k: v for k, v in results.items() if v}
    if active:
        top_issue = max(active, key=lambda k: len(active[k]))
        top_meta = CHECK_META.get(top_issue, {})
        suggestion = top_meta.get("排查顺序", "").split("→")[0].strip(" 1.")
        lines.append(f"建议：{top_issue} → {suggestion}")

    return "\n".join(lines)


def send_notification(md_content: str) -> bool:
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "任务单：飞书写入格式异常"}},
            "elements": [{"tag": "markdown", "content": md_content}],
        },
    }
    try:
        webhook_url = alert_webhook_url()
        if not webhook_url:
            print("FEISHU_ALERT_WEBHOOK 未配置，跳过通知", file=sys.stderr)
            return False
        resp = requests.post(webhook_url, json=card, timeout=15)
        return resp.json().get("code") == 0
    except Exception as e:
        print(f"通知发送失败: {e}", file=sys.stderr)
        return False


def create_github_issue(results: dict, total: int) -> bool:
    """格式审查异常 → 自动创建 GitHub Issue"""
    gha_pat = os.environ.get("GHA_PAT", "")
    if not gha_pat:
        print("  GHA_PAT 未配置，跳过 Issue 创建", file=sys.stderr)
        return False

    api_base = f"https://api.github.com/repos/{GH_REPO}"
    headers = {
        "Authorization": f"token {gha_pat}",
        "Accept": "application/vnd.github+json",
    }

    now = datetime.now(TZ_SH)
    active = [(name, len(items)) for name, items in results.items() if items]
    anomaly_count = sum(c for _, c in active)
    type_summary = "+".join(name for name, _ in active)
    summary_parts = [f"{n}({c}条)" for n, c in active]

    issue_title = f"[格式异常-{type_summary}] {now:%Y-%m-%d} — {', '.join(summary_parts)}"

    # 去重：同类 open issue
    try:
        search_resp = requests.get(
            "https://api.github.com/search/issues",
            params={"q": f'repo:{GH_REPO} is:issue is:open in:title "[格式异常-{type_summary}]"'},
            headers=headers, timeout=15,
        )
        if search_resp.status_code == 200:
            for item in search_resp.json().get("items", []):
                if f"[格式异常-{type_summary}]" in item["title"]:
                    print(f"  已存在同类 Issue #{item['number']}: {item['title']}，跳过")
                    return False
    except Exception as e:
        print(f"  Issue 去重查询失败: {e}", file=sys.stderr)

    # 确保 auto-detected 标签
    for label_name, color, desc in [
        ("auto-detected", "ff6b6b", "自动检测到的异常"),
        ("format-audit", "0e8a16", "飞书写入格式审查"),
    ]:
        try:
            requests.post(
                f"{api_base}/labels",
                headers=headers,
                json={"name": label_name, "color": color, "description": desc},
                timeout=15,
            )
        except Exception:
            pass

    # 构造 Issue 正文
    now_str = now.strftime("%Y-%m-%d %H:%M")
    body_lines = [
        "## 影响范围",
        f"- 影响渠道：谷歌",
        f"- 检查时间：{now_str}",
        f"- 影响记录数：{anomaly_count} 条异常 / 共 {total} 条",
        "",
        "## 证据",
    ]

    for name, items in results.items():
        if not items:
            continue
        body_lines.append(f"### {name}（{len(items)}条）")
        for i, item in enumerate(items[:5], 1):
            body_lines.append(format_issue(item, i))
        if len(items) > 5:
            body_lines.append(f"   ...还有 {len(items) - 5} 条")
        body_lines.append("")

    # 一行建议
    active = {k: v for k, v in results.items() if v}
    if active:
        top_issue = max(active, key=lambda k: len(active[k]))
        top_meta = CHECK_META.get(top_issue, {})
        suggestion = top_meta.get("排查顺序", "").split("→")[0].strip(" 1.")
        body_lines += [
            "## 建议下一步",
            f"1. 优先处理 **{top_issue}**（{len(active[top_issue])}条）→ {suggestion}",
            "",
            "## 验收标准",
            "- [ ] 可复现异常（dry-run 确认）",
            "- [ ] 找到根因",
            "- [ ] 最小修复",
            "- [ ] dry-run 或回归测试通过",
        ]

    # 创建 Issue
    try:
        resp = requests.post(
            f"{api_base}/issues",
            headers=headers,
            json={
                "title": issue_title,
                "body": "\n".join(body_lines),
                "labels": ["auto-detected", "format-audit"],
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            created = resp.json()
            print(f"  Issue 已创建: #{created['number']} — {created['html_url']}")
            return True
        print(f"  Issue 创建失败: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  Issue 创建异常: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    dry_run = os.environ.get("DRY_RUN", "false") == "true"
    check_hours = int(os.environ.get("CHECK_HOURS", "24"))

    print(f"[格式审查] 检查最近 {check_hours} 小时谷歌渠道线索...")
    token = get_feishu_token()
    records = fetch_google_records(token, hours=check_hours)
    print(f"  获取到 {len(records)} 条谷歌线索")

    if not records:
        print("无谷歌渠道记录，跳过")
        return

    results = audit_records(records)
    anomaly_count = sum(len(v) for v in results.values())

    for name, items in results.items():
        if items:
            print(f"  {name}: {len(items)} 条")

    if anomaly_count == 0:
        print("所有记录格式正常")
        return

    md_content = build_notification(results, len(records))

    if dry_run:
        print("\n--- dry-run 飞书通知 ---\n")
        print(md_content)
        active = [name for name, items in results.items() if items]
        type_summary = "+".join(active)
        print(f"\n--- dry-run Issue 预览 ---")
        print(f"  标题: [格式异常-{type_summary}] {datetime.now(TZ_SH).strftime('%Y-%m-%d')} — ...")
        print(f"  标签: auto-detected, format-audit")
        return

    success = send_notification(md_content)
    if success:
        print("通知已发送")
    else:
        print("通知发送失败", file=sys.stderr)

    # GitHub Issue（独立通道，与飞书互不影响）
    if os.environ.get("GHA_PAT"):
        create_github_issue(results, len(records))
    else:
        print("GHA_PAT 未配置，跳过 Issue 创建")


if __name__ == "__main__":
    main()
