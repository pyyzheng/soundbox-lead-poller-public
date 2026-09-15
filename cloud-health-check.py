#!/usr/bin/env python3
"""
cloud-health-check.py — 线索管道自动化健康检查

检查项:
  1. 飞书最近记录中的垃圾邮件漏网（用过滤链重验）
  2. GitHub Actions 运行状态
  3. Gmail OAuth token 有效性
  4. Pipeline 触发频率（workflow_dispatch 运行间隔）
发现异常时发飞书告警（含修复指引）。

GitHub Secrets:
  FEISHU_APP_ID, FEISHU_APP_SECRET, GITHUB_TOKEN
可选:
  FEISHU_ALERT_WEBHOOK, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
"""
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from feishu_utils import (
    get_feishu_token, feishu_search_url, extract_text, FIELD_CONTENT, FIELD_DATE,
    FIELD_EMAIL, fetch_records_since, alert_webhook_url,
)

TZ_SH = timezone(timedelta(hours=8))
PAGE_SIZE = 100
GH_REPO = os.environ.get("GITHUB_REPO", "pyyzheng/soundbox-lead-poller-public")

# Issue 标题中使用的告警类型短名
ALERT_TYPE_MAP = {
    "spam_leaked": "垃圾漏网",
    "github_consecutive_failures": "Actions连续失败",
    "gmail_oauth_expired": "OAuth失效",
    "cronjob_trigger_failed": "Cron触发异常",
    "apps_script_silent": "Apps Script静默",
}


# ═══════════════════════════════════════════════════════════════
# 检查 1: 飞书记录垃圾邮件漏网
# ═══════════════════════════════════════════════════════════════

def fetch_recent_records(token: str, hours: int = 24) -> list:
    """获取最近 N 小时的飞书记录"""
    cutoff_ms = int((datetime.now(TZ_SH) - timedelta(hours=hours)).timestamp() * 1000)
    return fetch_records_since(token, cutoff_ms)


def parse_tag_line(content: str) -> dict:
    """从标签行提取 name/email/message 等字段"""
    fields = {"name": "", "email": "", "message": "", "phone": "", "company": ""}

    # 提取表单字段
    for key, pattern in [
        ("name", r"Name:\s*(.+?)(?:<br|<br/|\n|$)"),
        ("email", r"Email:\s*(.+?)(?:<br|<br/|\n|$)"),
        ("phone", r"(?:Phone|Telephone Number):\s*(.+?)(?:<br|<br/|\n|$)"),
        ("company", r"Company:\s*(.+?)(?:<br|<br/|\n|$)"),
        ("message", r"Message:\s*(.+?)(?:<br|<br/|---|\n|$)"),
    ]:
        m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if m:
            fields[key] = m.group(1).strip()

    return fields


def check_spam_leaked(records: list, rules: dict) -> list:
    """用过滤链重验飞书记录，找出应被拦截但漏网的"""
    from lead_filter_common import check_spam, check_placeholder, check_promotional_content, check_irrelevant_business, check_inquiry_keywords

    leaked = []
    for rec in records:
        fields = rec.get("fields", {})
        content = extract_text(fields.get(FIELD_CONTENT, ""))
        parsed = parse_tag_line(content)

        name = parsed["name"]
        email = parsed["email"]
        message = parsed["message"]
        phone = parsed["phone"]
        company = parsed["company"]

        # 运行过滤链
        signals = []
        s, sr = check_spam(name, email, message, rules)
        if s:
            signals.append(sr)
        p, pr = check_placeholder(name, email, phone, company)
        if p:
            signals.append(pr)
        sc, scr = check_promotional_content(name, "", message, company, rules, raw_body=content)
        if sc:
            signals.append(scr)
        kw, kwr = check_inquiry_keywords(name, message, company, rules)
        if not kw:
            signals.append(kwr)

        if len(signals) >= 2:
            record_id = rec.get("record_id", "?")
            leaked.append({
                "record_id": record_id,
                "name": name[:30],
                "email": email,
                "message": message[:60],
                "signals": signals,
                "content_preview": content[:200],
            })

    return leaked


# ═══════════════════════════════════════════════════════════════
# 检查 2: GitHub Actions 运行状态
# ═══════════════════════════════════════════════════════════════

def check_github_actions(repo: str, gh_token: str, max_runs: int = 10) -> dict:
    """检查最近 N 次 workflow 运行状态"""
    headers = {"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"}
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/runs?per_page={max_runs}",
        headers=headers, timeout=15,
    )
    if resp.status_code != 200:
        return {"error": f"GitHub API 返回 {resp.status_code}"}

    data = resp.json()
    runs = data.get("workflow_runs", [])

    results = {
        "total_checked": len(runs),
        "success": 0,
        "failure": 0,
        "consecutive_failures": 0,
        "last_success": None,
        "last_failure": None,
        "failures": [],
    }

    for run in runs:
        if run["name"] != "Gmail Lead Poller":
            continue
        status = run.get("conclusion", "running")
        if status == "success":
            results["success"] += 1
            if not results["last_success"]:
                results["last_success"] = run["created_at"]
            if results["consecutive_failures"] > 0:
                break
        elif status == "failure":
            results["failure"] += 1
            results["consecutive_failures"] += 1
            if not results["last_failure"]:
                results["last_failure"] = run["created_at"]
            results["failures"].append({
                "id": run["id"],
                "created": run["created_at"],
                "url": run["html_url"],
            })

    return results


# ═══════════════════════════════════════════════════════════════
# 检查 3: Pipeline 触发频率
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 检查 4: Gmail OAuth token 有效性
# ═══════════════════════════════════════════════════════════════

def check_gmail_oauth(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """验证 Gmail refresh token 是否有效"""
    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        data = resp.json()
        if "access_token" in data:
            return {"valid": True, "token_type": data.get("token_type", "")}
        return {
            "valid": False,
            "error": data.get("error", "unknown"),
            "error_desc": data.get("error_description", ""),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 生成修复指引
# ═══════════════════════════════════════════════════════════════

REMEDY_MAP = {
    "spam_leaked": {
        "title": "垃圾邮件漏网",
        "diagnose": "过滤链检测阈值可能不适用于新型垃圾模式",
        "normal": "垃圾邮件过滤链应拦截所有垃圾，飞书中不应出现漏网记录",
        "meta": {
            "异常环节": "邮件过滤 → 飞书写入",
            "初步判断": "过滤链阈值不适用于新型垃圾模式",
            "排查顺序": "1. 查飞书记录 Enquiry details 确认垃圾特征 → 2. 检查 lead_filter_common.py 对应规则 → 3. 调整阈值并用 QA-RULES 回归",
            "是否可自动修复": "需业务确认后可由 Claude 修改配置",
            "建议处理角色": "业务确认 + Claude执行",
        },
        "fix": [
            "1. 在 lib/lead_filter_common.py has_random_chars() 中调整阈值",
            "2. 用 QA-RULES.md 第五节回归用例验证修改",
            "3. git push 到 main，等待下次定时运行生效",
        ],
    },
    "github_consecutive_failures": {
        "title": "GitHub Actions 连续失败",
        "diagnose": "可能是 Gmail OAuth token 过期或飞书 API 异常",
        "normal": "Gmail Lead Poller 应持续稳定运行，无连续失败",
        "meta": {
            "异常环节": "GitHub Actions 定时任务",
            "初步判断": "Gmail OAuth 过期或飞书 API 异常",
            "排查顺序": "1. 查 Actions 最近失败日志 → 2. 确认错误类型 → 3. 按类型修复",
            "是否可自动修复": "否",
            "建议处理角色": "技术",
        },
        "fix": [
            "1. 检查 GitHub Actions 日志确认错误原因",
            "2. 如果是 invalid_grant → 重新生成 refresh token（从 gog CLI 钥匙串提取）",
            "3. 更新 GitHub Secret: GMAIL_REFRESH_TOKEN",
            "4. 如果是飞书 API 错误 → 检查 FEISHU_APP_SECRET 是否正确",
        ],
    },
    "gmail_oauth_expired": {
        "title": "Gmail OAuth token 失效",
        "diagnose": "refresh_token 已过期或被撤销",
        "normal": "Gmail OAuth refresh token 应保持有效，可正常刷新 access token",
        "meta": {
            "异常环节": "Gmail OAuth 认证",
            "初步判断": "refresh_token 过期或被撤销",
            "排查顺序": "1. 确认 Google Cloud 项目状态 → 2. 从 gog CLI 提取新 token → 3. 更新 GitHub Secret",
            "是否可自动修复": "否",
            "建议处理角色": "技术",
        },
        "fix": [
            "1. 确认 Google Cloud Console 项目已切到「生产」状态（非测试）",
            "2. 从 gog CLI 钥匙串提取新 token:",
            "   security find-generic-password -s gogcli -a 'token:default:soundboxbooth@gmail.com' -w",
            "3. 更新 GitHub Secret: GMAIL_REFRESH_TOKEN",
        ],
    },
    "cronjob_trigger_failed": {
        "title": "Pipeline 触发异常",
        "diagnose": "Lead Poller workflow 长时间未成功运行",
        "normal": "Lead Poller 应通过 Apps Script 近实时触发，或由 schedule 每 30 分钟兜底（成功间隔不超过 2 小时）",
        "meta": {
            "异常环节": "Gmail Lead Poller 触发/执行",
            "初步判断": "Gmail Apps Script 授权失效，或 schedule/Gmail OAuth 失败",
            "排查顺序": "1. 查最近 Gmail Lead Poller 失败日志 → 2. script.google.com 重新授权 Lead-poller → 3. 手动 workflow_dispatch",
            "是否可自动修复": "否",
            "建议处理角色": "技术",
        },
        "fix": [
            "1. 打开 script.google.com → 项目 Lead-poller → 执行记录，确认 Authorization / History API 错误",
            "2. 手动运行 authorizeOAuth() / initHistoryId()，确认时间驱动器仍每分钟跑 checkNewEmails",
            "3. 确认脚本属性 GITHUB_TOKEN 仍可 workflow_dispatch（仓库 Actions: write）",
            "4. 在 GitHub Actions 手动触发 Gmail Lead Poller；确认 schedule */30 仍启用",
            "5. 若日志为 invalid_grant → 更新 GitHub Secret GMAIL_REFRESH_TOKEN",
        ],
    },
    "apps_script_silent": {
        "title": "Gmail Apps Script 近实时触发静默",
        "diagnose": "长时间没有 workflow_dispatch 触发 Gmail Lead Poller（仅靠 schedule）",
        "normal": "有新询盘时应由 Apps Script 发起 workflow_dispatch（通常数小时内至少一次，视邮件量）",
        "meta": {
            "异常环节": "Google Apps Script gmail-trigger",
            "初步判断": "Apps Script 授权失效、触发器被删、或 History API historyId 异常",
            "排查顺序": "1. script.google.com 执行记录 → 2. 重新授权 → 3. 核对 GITHUB_TOKEN 脚本属性",
            "是否可自动修复": "否",
            "建议处理角色": "技术",
        },
        "fix": [
            "1. script.google.com 打开 Lead-poller，查看「执行记录」里的失败堆栈",
            "2. 运行 authorizeOAuth() 与 initHistoryId()，确认时间驱动器存在",
            "3. 同步仓库 scripts/gmail-trigger.gs 到 Apps Script 后保存",
            "4. 临时依赖 GitHub schedule */30 兜底，避免再次降为每天 1 次",
        ],
    },
}


# ═══════════════════════════════════════════════════════════════
# 创建 GitHub Issue
# ═══════════════════════════════════════════════════════════════

def create_github_issue(alert_key: str, issue: dict) -> bool:
    """为健康检查异常自动创建 GitHub Issue（与飞书告警互不依赖）"""
    gha_pat = os.environ.get("GHA_PAT", "")
    if not gha_pat:
        print("  GHA_PAT 未配置，跳过 Issue 创建", file=sys.stderr)
        return False

    api_base = f"https://api.github.com/repos/{GH_REPO}"
    headers = {
        "Authorization": f"token {gha_pat}",
        "Accept": "application/vnd.github+json",
    }

    alert_type = ALERT_TYPE_MAP.get(alert_key, issue.get("title", "未知"))
    now = datetime.now(TZ_SH).strftime("%Y-%m-%d")

    # 生成一句话摘要
    details = issue.get("details", [])
    if alert_key == "spam_leaked" and details:
        summary = f"发现 {len(details)} 条异常记录未被过滤"
    elif alert_key == "github_consecutive_failures" and details:
        summary = f"最近 {len(details)} 次运行失败"
    elif alert_key == "gmail_oauth_expired":
        summary = "refresh token 验证失败"
    else:
        summary = "详见正文"

    issue_title = f"[{alert_type}] {now} — {summary}"

    # 去重：检查是否已有同类 open issue
    try:
        search_resp = requests.get(
            "https://api.github.com/search/issues",
            params={"q": f'repo:{GH_REPO} is:issue is:open in:title "[{alert_type}]"'},
            headers=headers, timeout=15,
        )
        if search_resp.status_code == 200:
            for item in search_resp.json().get("items", []):
                if item["title"].startswith(f"[{alert_type}]"):
                    print(f"  已存在同类 Issue #{item['number']}: {item['title']}，跳过")
                    return False
    except Exception as e:
        print(f"  Issue 去重查询失败: {e}", file=sys.stderr)

    # 确保 auto-detected 标签存在（已存在会返回 422，忽略）
    try:
        requests.post(
            f"{api_base}/labels",
            headers=headers,
            json={"name": "auto-detected", "color": "ff6b6b", "description": "健康检查自动检测到的异常"},
            timeout=15,
        )
    except Exception:
        pass

    # 正常标准（按异常类型）
    normal_state = REMEDY_MAP.get(alert_key, {}).get("normal", "系统正常运行")

    # 是否涉及客户数据
    involves_customer = "是" if alert_key == "spam_leaked" else "否"

    # 构造 Issue 正文（标准任务单格式）
    body_lines = [
        "## 类型", "监控异常", "",
        "## 影响范围",
        f"- 异常类型：{alert_type}",
        f"- 检查时间：{now}",
        f"- 影响记录数：{len(details) if details else 0}",
        f"- 是否涉及客户数据：{involves_customer}",
        "",
        "## 证据",
    ]
    for d in (details or []):
        body_lines.append(f"- {d}")
    if not details:
        body_lines.append("- 详见下方异常描述")

    body_lines += [
        "",
        "## 正常标准", normal_state, "",
        "## 当前异常",
        summary,
    ]

    # 诊断 5 字段块
    meta = REMEDY_MAP.get(alert_key, {}).get("meta", {})
    if meta:
        body_lines += ["", "## 诊断"]
        for k, v in meta.items():
            body_lines.append(f"- **{k}**：{v}")

    body_lines += ["", "## 建议下一步"]
    for step in issue.get("fix", []):
        body_lines.append(f"- {step}" if not step.startswith("- ") else step)

    body_lines += [
        "",
        "## 验收标准",
        "- [ ] 可复现异常",
        "- [ ] 找到根因",
        "- [ ] 最小修复",
        "- [ ] dry-run 或回归测试通过",
        "- [ ] 独立评审通过",
    ]

    # 创建 Issue
    try:
        resp = requests.post(
            f"{api_base}/issues",
            headers=headers,
            json={"title": issue_title, "body": "\n".join(body_lines), "labels": ["auto-detected"]},
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
# 发送告警
# ═══════════════════════════════════════════════════════════════

def send_alert(issues: list) -> bool:
    """发送飞书告警卡片（精简摘要，完整诊断留 GitHub Issue 供 auto-fix 消费）"""
    now_str = datetime.now(TZ_SH).strftime('%Y-%m-%d %H:%M')

    lines = [
        f"**检查时间**：{now_str}",
        f"**发现问题**：{len(issues)} 项",
        "",
        "**当前异常**",
    ]
    # 每项一句话摘要（details / 正常标准 / 诊断 / 建议步骤留 Issue，避免双通道重复）
    for issue in issues:
        lines.append(f"【{issue['title']}】{issue['diagnose']}")

    # 按最严重的异常给处理建议
    alert_keys = [i.get("alert_key", "") for i in issues]
    if "gmail_oauth_expired" in alert_keys:
        suggestion = "优先处理 OAuth 失效，否则所有邮件拉取停摆。"
    elif "cronjob_trigger_failed" in alert_keys:
        suggestion = "优先检查 Pipeline 触发是否正常，否则线索处理停摆。"
    elif "apps_script_silent" in alert_keys:
        suggestion = "Apps Script 近实时触发已停，请重新授权；当前依赖 schedule 兜底。"
    elif "github_consecutive_failures" in alert_keys:
        suggestion = "检查 GitHub Actions 日志，确认错误根因后修复。"
    else:
        suggestion = "详见 GitHub Issue 逐项处理。"
    lines += ["", f"**处理建议**：{suggestion}", "",
              "_完整诊断与证据见 GitHub Issue（auto-detected 标签）_"]

    md_content = "\n".join(lines)
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "【线索告警】系统运行异常"}},
            "elements": [{"tag": "markdown", "content": md_content}],
        },
    }

    try:
        webhook_url = alert_webhook_url()
        if not webhook_url:
            print("FEISHU_ALERT_WEBHOOK 未配置，跳过告警", file=sys.stderr)
            return False
        resp = requests.post(webhook_url, json=card, timeout=15)
        return resp.json().get("code") == 0
    except Exception as e:
        print(f"告警发送失败: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    dry_run = os.environ.get("DRY_RUN", "false") == "true"
    check_hours = int(os.environ.get("CHECK_HOURS", "24"))
    issues = []

    # 加载过滤规则
    rules_path = Path(__file__).parent / "lead-rules.json"
    with open(rules_path, encoding="utf-8") as f:
        rules = json.load(f)

    # ── 检查 1: 垃圾邮件漏网 ──
    print(f"[检查1] 扫描飞书最近 {check_hours} 小时记录...")
    try:
        token = get_feishu_token()
        records = fetch_recent_records(token, hours=check_hours)
        print(f"  获取到 {len(records)} 条记录")

        leaked = check_spam_leaked(records, rules)
        if leaked:
            print(f"  ⚠️ 发现 {len(leaked)} 条漏网垃圾")
            details = [f"record={l['record_id']} | name={l['name']} | signals={'+'.join(l['signals'][:2])}" for l in leaked[:5]]
            issues.append({
                **REMEDY_MAP["spam_leaked"],
                "alert_key": "spam_leaked",
                "details": details,
            })
        else:
            print("  无漏网垃圾")
    except Exception as e:
        print(f"  检查失败: {e}", file=sys.stderr)

    # ── 检查 2: GitHub Actions ──
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if gh_token:
        print(f"[检查2] 检查 GitHub Actions 状态...")
        try:
            gh_status = check_github_actions(GH_REPO, gh_token)
            print(f"  最近 {gh_status['total_checked']} 次运行: 成功={gh_status['success']} 失败={gh_status['failure']}")
            if gh_status["consecutive_failures"] >= 2:
                details = [f"run {f['id']}: {f['created']} → {f['url']}" for f in gh_status["failures"][:3]]
                issues.append({
                    **REMEDY_MAP["github_consecutive_failures"],
                    "alert_key": "github_consecutive_failures",
                    "details": details,
                })
        except Exception as e:
            print(f"  检查失败: {e}", file=sys.stderr)
    else:
        print("[检查2] 跳过（未配置 GITHUB_TOKEN）")

    # ── 检查 3: Pipeline 触发频率 ──
    # schedule */30 + Apps Script workflow_dispatch；以「最近成功」为准，失败不算健康。
    if gh_token:
        print("[检查3] 检查 Pipeline 触发频率...")
        try:
            MAX_SUCCESS_GAP_HOURS = 2  # schedule 每 30min，留 GitHub jitter 余量
            APPS_SCRIPT_SILENT_HOURS = 24  # 超过 1 天无 workflow_dispatch → Apps Script 可能挂了
            resp = requests.get(
                f"https://api.github.com/repos/{GH_REPO}/actions/workflows/lead-poller.yml/runs",
                params={"per_page": 30},
                headers={"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            if resp.status_code == 200:
                poller_runs = resp.json().get("workflow_runs", [])
                now = datetime.now(timezone.utc)
                success_runs = [r for r in poller_runs if r.get("conclusion") == "success"]
                if success_runs:
                    last_ok = success_runs[0]
                    last_time = datetime.fromisoformat(
                        last_ok["created_at"].replace("Z", "+00:00")
                    )
                    gap_hours = round((now - last_time).total_seconds() / 3600, 1)
                    if gap_hours > MAX_SUCCESS_GAP_HOURS:
                        print(f"  ⚠️ Pipeline {gap_hours}h 无成功运行，超过阈值 {MAX_SUCCESS_GAP_HOURS}h")
                        issues.append({
                            **REMEDY_MAP["cronjob_trigger_failed"],
                            "alert_key": "cronjob_trigger_failed",
                            "details": [
                                f"最近成功距今 {gap_hours}h（阈值 {MAX_SUCCESS_GAP_HOURS}h）",
                                f"最近一次: {last_ok.get('html_url', '')}",
                            ],
                        })
                    else:
                        print(f"  正常: 最近成功 {gap_hours}h 前")
                elif poller_runs:
                    last_run = poller_runs[0]
                    print(f"  ⚠️ 有运行但无成功记录，最近结论={last_run.get('conclusion')}")
                    issues.append({
                        **REMEDY_MAP["cronjob_trigger_failed"],
                        "alert_key": "cronjob_trigger_failed",
                        "details": [
                            f"最近结论={last_run.get('conclusion')}",
                            f"url={last_run.get('html_url', '')}",
                        ],
                    })
                else:
                    print("  无 Lead Poller 运行记录")

                dispatch_runs = [r for r in poller_runs if r.get("event") == "workflow_dispatch"]
                if dispatch_runs:
                    last_dispatch = datetime.fromisoformat(
                        dispatch_runs[0]["created_at"].replace("Z", "+00:00")
                    )
                    silent_h = round((now - last_dispatch).total_seconds() / 3600, 1)
                    if silent_h > APPS_SCRIPT_SILENT_HOURS:
                        print(f"  ⚠️ Apps Script 疑似静默: {silent_h}h 无 workflow_dispatch")
                        issues.append({
                            **REMEDY_MAP["apps_script_silent"],
                            "alert_key": "apps_script_silent",
                            "details": [f"最近 workflow_dispatch 距今 {silent_h}h"],
                        })
                    else:
                        print(f"  Apps Script/手动 dispatch: {silent_h}h 前有触发")
                else:
                    print("  ⚠️ 近期无 workflow_dispatch（Apps Script 可能未触发）")
                    issues.append({
                        **REMEDY_MAP["apps_script_silent"],
                        "alert_key": "apps_script_silent",
                        "details": ["最近 30 次 Lead Poller 运行中无 workflow_dispatch"],
                    })
            else:
                print(f"  API 返回 {resp.status_code}，跳过", file=sys.stderr)
        except Exception as e:
            print(f"  检查失败: {e}", file=sys.stderr)
    else:
        print("[检查3] 跳过（未配置 GITHUB_TOKEN）")

    # ── 检查 4: Gmail OAuth ──
    gmail_cid = os.environ.get("GMAIL_CLIENT_ID", "")
    gmail_cs = os.environ.get("GMAIL_CLIENT_SECRET", "")
    gmail_rt = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    if gmail_cid and gmail_cs and gmail_rt:
        print("[检查4] 验证 Gmail OAuth token...")
        try:
            oauth_result = check_gmail_oauth(gmail_cid, gmail_cs, gmail_rt)
            if oauth_result["valid"]:
                print("  token 有效")
            else:
                print(f"  ⚠️ token 无效: {oauth_result.get('error', '')}")
                issues.append({
                    **REMEDY_MAP["gmail_oauth_expired"],
                    "alert_key": "gmail_oauth_expired",
                    "details": [f"错误: {oauth_result.get('error', '')} - {oauth_result.get('error_desc', '')}"],
                })
        except Exception as e:
            print(f"  检查失败: {e}", file=sys.stderr)
    else:
        print("[检查4] 跳过（未配置 Gmail 凭据）")

    # ── 输出与告警 ──
    print(f"\n{'='*40}")
    print(f"检查完成: {len(issues)} 个问题")

    if not issues:
        print("无异常")
        return

    if dry_run:
        print("\n[DRY-RUN] 跳过发送告警和创建 Issue")
        for issue in issues:
            print(f"  - {issue['title']}: {issue['diagnose']}")
        return

    # 飞书告警（独立通道）
    alert_ok = send_alert(issues)
    if alert_ok:
        print("告警已发送")
    else:
        print("告警发送失败", file=sys.stderr)

    # GitHub Issue（独立通道，与飞书互不影响）
    if os.environ.get("GHA_PAT"):
        for issue in issues:
            create_github_issue(issue.get("alert_key", ""), issue)
    else:
        print("GHA_PAT 未配置，跳过 Issue 创建")

    if not alert_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
