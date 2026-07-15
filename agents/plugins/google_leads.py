import importlib
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

# 确保 lib/ 和根目录可导入
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))


def _import_module(name: str):
    """导入连字符命名的模块（如 cloud-check-unassigned）。"""
    return importlib.import_module(name)

from agents.types import Anomaly, FixResult

log = logging.getLogger("plugin:google-leads")

TZ_SH = timezone(timedelta(hours=8))


# ── 共享：Feishu token ──────────────────────────────────────────────

def _get_feishu_token() -> str:
    from feishu_utils import get_feishu_token
    return get_feishu_token()


# ── 检测器 1：未分配线索 ─────────────────────────────────────────────

async def unassigned_detector(project: str, scope: dict) -> list[Anomaly]:
    """检查未命中分配规则 + 待人工确认的线索。"""
    _mod = _import_module("cloud-check-unassigned")
    collect_records, is_old_enough, diagnose_record, format_lead = (
        _mod.collect_records, _mod.is_old_enough, _mod.diagnose_record, _mod.format_lead
    )

    log.info("[unassigned] 检查未分配线索...")
    token = _get_feishu_token()
    anomalies = []

    queries = [(_mod.SYSTEM_ASSIGNEE_FIELD, value) for value in _mod.ERROR_ASSIGNEES]
    queries.append((_mod.ASSIGN_BASIS_FIELD, "待人工确认"))
    records, totals = collect_records(token, queries)
    records_old = [r for r in records if is_old_enough(r)]
    for r in records_old:
        f = r.get("fields", {})
        diag = diagnose_record(f)
        formatted = format_lead(r)
        anomaly_type = "pending_confirmation" if diag["stage"] == "待人工确认" else "unassigned"
        anomalies.append(Anomaly(
            type=anomaly_type,
            severity="low" if anomaly_type == "pending_confirmation" else "medium",
            description=formatted["text"],
            evidence={"stage": diag["stage"], "reason": diag["reason"], "handler": diag["handler"],
                   "record_id": r.get("record_id", ""), "country": f.get("Country（国家）", "")},
            source="unassigned-detector",
        ))

    log.info("[unassigned] 分配异常 %d 条: %s", len(records_old), totals)
    return anomalies


# ── 检测器 2：格式审查 ───────────────────────────────────────────────

async def format_audit_detector(project: str, scope: dict) -> list[Anomaly]:
    """检查标签行结构、字段一致性、必填字段等。"""
    _mod = _import_module("cloud-format-audit")
    fetch_google_records, audit_records = _mod.fetch_google_records, _mod.audit_records

    log.info("[format-audit] 执行格式审查...")
    token = _get_feishu_token()
    hours = int(os.environ.get("CHECK_HOURS", "24"))
    records = fetch_google_records(token, hours=hours)
    if not records:
        log.info("[format-audit] 无记录，跳过")
        return []

    results = audit_records(records)
    anomalies = []
    for check_name, items in results.items():
        if not items:
            continue
        for item in items[:10]:
            anomalies.append(Anomaly(
                type="format_anomaly",
                severity="medium",
                description=f"{check_name}: {item.get('preview', '')}",
                evidence={"check": check_name, "detail": item.get("detail", ""), "date": item.get("date", ""),
                           "id": item.get("id", "")},
                source="format-audit-detector",
            ))

    log.info("[format-audit] 发现 %d 条格式异常（共 %d 条记录）", sum(len(v) for v in results.values()), len(records))
    return anomalies


# ── 检测器 3：健康检查 ───────────────────────────────────────────────

async def health_check_detector(project: str, scope: dict) -> list[Anomaly]:
    """检查垃圾漏网、GitHub Actions 状态、Gmail OAuth。"""
    _mod = _import_module("cloud-health-check")
    fetch_recent_records = _mod.fetch_recent_records
    check_spam_leaked = _mod.check_spam_leaked
    check_github_actions = _mod.check_github_actions
    check_gmail_oauth = _mod.check_gmail_oauth
    import json

    log.info("[health] 执行健康检查...")
    token = _get_feishu_token()
    anomalies = []

    # 垃圾漏网
    rules_path = REPO_ROOT / "lead-rules.json"
    rules = json.loads(rules_path.read_text()) if rules_path.exists() else {}
    records = fetch_recent_records(token, hours=24)
    spam_results = check_spam_leaked(records, rules)
    for item in spam_results:
        anomalies.append(Anomaly(
            type="spam_leaked",
            severity="high",
            description=f"垃圾线索漏网: {item.get('name', '')} <{item.get('email', '')}>",
            evidence=item,
            source="health-detector",
        ))

    # GitHub Actions
    gh_token = os.environ.get("GHA_PAT", "")
    if gh_token:
        gh_repo = os.environ.get("GITHUB_REPO", "pyyzheng/soundbox-lead-poller")
        gh_result = check_github_actions(gh_repo, gh_token)
        if gh_result.get("consecutive_failures"):
            anomalies.append(Anomaly(
                type="github_consecutive_failures",
                severity="critical",
                description=f"GitHub Actions 连续失败 {gh_result.get('count', 0)} 次",
                evidence=gh_result,
                source="health-detector",
            ))

    # Gmail OAuth
    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    if client_id and client_secret and refresh_token:
        oauth_result = check_gmail_oauth(client_id, client_secret, refresh_token)
        if oauth_result.get("expired"):
            anomalies.append(Anomaly(
                type="gmail_oauth_expired",
                severity="critical",
                description="Gmail OAuth token 已失效",
                evidence=oauth_result,
                source="health-detector",
            ))

    log.info("[health] 发现 %d 个健康问题", len(anomalies))
    return anomalies


# ── 修复策略 ────────────────────────────────────────────────────────

def _update_assignment_status(record_id: str, status_text: str, token: str) -> bool:
    """旧版会写“分配结果检查”；当前表已移除该字段，这里只保留诊断日志。"""
    if not record_id:
        return False
    log.info("[reassign] 当前字段体系不写回旧状态字段：%s → %s", record_id, status_text)
    return True


def _fetch_open_issues(gh_token: str) -> list[dict]:
    """获取所有带 agent:alert 标签的 open Issues，一次调用。"""
    from auto_fix_utils import GH_REPO
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/issues",
            params={"labels": "agent:alert", "state": "open", "per_page": 100},
            headers={"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        return resp.json()
    except Exception as e:
        log.warning("[reassign] 搜索 Issue 异常: %s", e)
        return []


def _match_issue(issues: list[dict], anomaly_type: str, desc_prefix: str) -> int | None:
    """在已获取的 Issue 列表中匹配，返回 issue_number。"""
    for issue in issues:
        body = issue.get("body", "") or ""
        if anomaly_type in body and desc_prefix[:40] in body:
            return issue["number"]
    return None


async def reassign_lead(anomalies: list[Anomaly], context: dict) -> FixResult:
    """线索重分配：更新飞书状态 + 在 GitHub Issue 追加诊断评论。"""
    from auto_fix_utils import comment_on_issue

    log.info("[reassign] 处理 %d 条未分配/待确认线索", len(anomalies))

    try:
        token = _get_feishu_token()
    except Exception as e:
        return FixResult(success=False, summary=f"Token 获取失败: {e}",
                         changed_files=[], confidence=0.0, needs_review=False)

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    open_issues = _fetch_open_issues(gh_token) if gh_token else []
    diagnosed = 0

    for anomaly in anomalies:
        record_id = anomaly.evidence.get("record_id", "")
        anomaly_type = anomaly.type
        status = "已诊断-待人工" if anomaly_type == "unassigned" else "已诊断-需人工"

        if record_id:
            _update_assignment_status(record_id, status, token)

        if gh_token and open_issues:
            issue_num = _match_issue(open_issues, anomaly_type, anomaly.description)
            if issue_num:
                comment_on_issue(issue_num, (
                    f"**诊断结果**\n"
                    f"- 类型: {anomaly_type}\n- record_id: {record_id}\n"
                    f"- 国家: {anomaly.evidence.get('country', '')}\n"
                    f"- 阶段: {anomaly.evidence.get('stage', '')}\n"
                    f"- 原因: {anomaly.evidence.get('reason', '')}\n\n"
                    f"{'分配规则不在此代码库，需人工在飞书端处理。' if anomaly_type == 'unassigned' else '需人工确认分配。'}"
                ), gh_token)
        diagnosed += 1

    summary = f"线索分配: {diagnosed} 条已诊断，需人工在飞书端处理"
    log.info("[reassign] %s", summary)
    return FixResult(success=True, summary=summary, changed_files=[],
                     confidence=0.7, needs_review=True)


# ── 注册 ─────────────────────────────────────────────────────────────

def register(detector, fixer):
    detector.register_detector("unassigned-detector", unassigned_detector)
    detector.register_detector("format-audit-detector", format_audit_detector)
    detector.register_detector("health-detector", health_check_detector)

    # 修复策略
    from agents.strategies.auto_fix_strategy import auto_fix_strategy
    fixer.register_strategy("reassign_lead", reassign_lead)
    fixer.register_strategy("auto_fix", auto_fix_strategy)
    # 可自动修复的类型
    fixer.set_strategy_mapping("unassigned", "reassign_lead")
    fixer.set_strategy_mapping("pending_confirmation", "reassign_lead")
    fixer.set_strategy_mapping("spam_leaked", "auto_fix")
    fixer.set_strategy_mapping("format_anomaly", "auto_fix")
    # 以下类型由 auto_fix_strategy.SKIP_TYPES 在策略层跳过，不映射
