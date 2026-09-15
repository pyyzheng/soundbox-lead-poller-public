#!/usr/bin/env python3
"""
post-merge-agent.py — 合并后自动更新异常模式库

流程：
  1. 从 PR body 提取关联的 Issue 编号
  2. 获取 Issue 内容（异常描述、根因、修复步骤）
  3. 调用智谱 API 生成文档更新内容
  4. 更新 PROJECT_CONTEXT.md 第四节（异常模式库追加一行）
  5. 更新 QA-RULES.md 第五节（如有新增回归用例）
  6. git commit + push

环境变量：
  ZHIPU_API_KEY   — 智谱 API Key（必需）
  GITHUB_TOKEN    — GitHub PAT（必需）
  PR_NUMBER       — PR 编号
  PR_TITLE        — PR 标题
  PR_BODY         — PR 正文
  PR_BRANCH       — PR 来源分支
  DRY_RUN         — true 时只分析不推送
"""
import json
import os
import re
import sys
import subprocess
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from auto_fix_utils import call_zhipu_api, parse_ai_response, GH_REPO

TZ_SH = timezone(timedelta(hours=8))
log = logging.getLogger("post-merge-agent")

# ── 文档路径 ────────────────────────────────────────────────────────────

PROJECT_CTX = SCRIPT_DIR / "PROJECT_CONTEXT.md"
QA_RULES = SCRIPT_DIR / "QA-RULES.md"

SYSTEM_PROMPT = """你是 SoundBox 线索管道系统的文档更新代理。

你的任务：根据修复 PR 和关联 Issue 的内容，生成异常模式库条目。

## 输出格式
只输出一个 JSON 块：
{
  "anomaly_entry": {
    "description": "异常描述（10-30字）",
    "root_cause": "根因分析（一句话）",
    "auto_detect": true,
    "status": "已修复"
  },
  "regression_test": null,
  "summary": "一句话总结（用于 commit message）"
}

如果修复涉及新的过滤规则或阈值调整，regression_test 应为一个测试用例对象：
{
  "category": "应拦截 或 应放行",
  "test": {"name": "...", "email": "...", "message": "...", "phone": "...", "company": "..."},
  "reason": "为什么需要这个测试用例"
}

如果不涉及新测试用例，regression_test 设为 null。

规则：
1. description 要具体，不要笼统地说"修复了一个bug"
2. root_cause 要指明代码层面的原因
3. auto_detect 如果健康检查能自动发现则设为 true
"""


# ═══════════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════════

def extract_issue_number(pr_body: str) -> int:
    """从 PR body 提取关联的 Issue 编号（Closes #N / Fixes #N）"""
    if not pr_body:
        return 0
    m = re.search(r'(?:Closes|Fixes|Resolves)\s+#(\d+)', pr_body, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def fetch_issue(issue_number: int, gh_token: str) -> dict:
    """通过 GitHub API 获取 Issue 详情"""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/issues/{issue_number}",
            headers={"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"title": data.get("title", ""), "body": data.get("body", "")}
    except Exception as e:
        log.warning("获取 Issue #%d 失败: %s", issue_number, e)
    return {"title": "", "body": ""}


def find_max_anomaly_id(ctx_content: str) -> int:
    """从 PROJECT_CONTEXT.md 中找到最大的 F-NNN 编号"""
    ids = re.findall(r'F-(\d+)', ctx_content)
    return max(int(i) for i in ids) if ids else 0


def append_anomaly_entry(ctx_path: Path, entry: dict, issue_number: int):
    """在 PROJECT_CONTEXT.md 第四节异常模式库追加一行"""
    content = ctx_path.read_text(encoding="utf-8")
    today = datetime.now(TZ_SH).strftime("%Y-%m-%d")
    next_id = find_max_anomaly_id(content) + 1
    auto_detect = "是" if entry.get("auto_detect", True) else "否"

    new_row = (
        f"| F-{next_id:03d} | {entry['description']} | {entry['root_cause']} "
        f"| {today} | {auto_detect} | 已修复 |\n"
    )

    # 找异常模式库表格末尾（最后一个 | F- 开头的行之后）
    lines = content.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("| F-") or line.strip().startswith("|编号"):
            insert_idx = i + 1

    if insert_idx == 0:
        log.warning("未找到异常模式库表格，跳过更新")
        return False

    lines.insert(insert_idx, new_row.rstrip("\n"))

    # 更新"最后更新"日期
    for i, line in enumerate(lines):
        if "| 最后更新 |" in line:
            lines[i] = f"| 最后更新 | {today} |"
            break

    ctx_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("PROJECT_CONTEXT.md 追加 F-%03d", next_id)
    return True


def append_regression_test(qa_path: Path, test: dict):
    """在 QA-RULES.md 第五节追加回归测试用例"""
    if not test:
        return False

    content = qa_path.read_text(encoding="utf-8")
    category = test.get("category", "应拦截")
    case = test.get("test", {})
    reason = test.get("reason", "")

    case_str = json.dumps(case, ensure_ascii=False, indent=1)
    new_block = f"\n```python\n# {reason}\n{case_str}\n```\n"

    # 找到对应分类的位置
    if "应拦截" in category:
        marker = "### 应放行"
    elif "应放行" in category:
        marker = "### HTML 邮件"
    else:
        marker = None

    if marker and marker in content:
        idx = content.index(marker)
        content = content[:idx] + new_block + content[idx:]
    else:
        # 追加到第五节末尾
        content += new_block

    qa_path.write_text(content, encoding="utf-8")
    log.info("QA-RULES.md 追加回归测试: %s", reason[:50])
    return True


def git_run(args: list, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=str(SCRIPT_DIR),
        capture_output=True, text=True, check=check, timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

def main():
    dry_run = os.environ.get("DRY_RUN", "false") == "true"
    pr_number = int(os.environ.get("PR_NUMBER", "0"))
    pr_title = os.environ.get("PR_TITLE", "")
    pr_body = os.environ.get("PR_BODY", "")
    pr_branch = os.environ.get("PR_BRANCH", "")
    gh_token = os.environ.get("GITHUB_TOKEN", "")

    # 日志
    log_file = SCRIPT_DIR / f"post-merge-{pr_number}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    log.info("=" * 50)
    log.info("Post-merge agent 启动: PR #%d (%s)", pr_number, pr_branch)

    if not pr_number:
        log.error("PR_NUMBER 未设置")
        sys.exit(1)

    # ── 提取关联 Issue ──
    issue_number = extract_issue_number(pr_body)
    if not issue_number:
        log.info("PR 未关联 Issue，跳过文档更新")
        return

    log.info("关联 Issue: #%d", issue_number)

    # ── 获取 Issue 内容 ──
    issue = fetch_issue(issue_number, gh_token)
    if not issue["title"]:
        log.warning("无法获取 Issue #%d 内容，使用 PR 信息", issue_number)
        issue = {"title": pr_title, "body": pr_body}

    # ── 调用智谱 API ──
    user_prompt = (
        f"## 修复 PR\n标题: {pr_title}\n正文:\n{pr_body or '(无)'}\n\n"
        f"## 关联 Issue\n标题: {issue['title']}\n正文:\n{issue['body'] or '(无)'}\n\n"
        "请生成异常模式库条目。"
    )

    log.info("调用智谱 API...")
    try:
        api_result = call_zhipu_api(SYSTEM_PROMPT, user_prompt,
                                     max_tokens=2048, temperature=0.1)
        raw_content = api_result["content"]
    except Exception as e:
        log.error("智谱 API 调用失败: %s", e)
        sys.exit(1)

    # ── 解析响应 ──
    try:
        parsed = parse_ai_response(raw_content)
    except json.JSONDecodeError as e:
        log.error("AI 返回非 JSON: %s", raw_content[:200])
        sys.exit(1)

    entry = parsed.get("anomaly_entry", {})
    regression_test = parsed.get("regression_test")
    summary = parsed.get("summary", pr_title[:50])

    log.info("异常条目: %s", entry.get("description", ""))
    log.info("回归测试: %s", "有" if regression_test else "无")

    if not entry:
        log.warning("AI 未生成异常条目，退出")
        return

    # ── Dry run ──
    if dry_run:
        log.info("[DRY-RUN] 跳过文件更新")
        log.info("异常条目: %s", json.dumps(entry, ensure_ascii=False))
        if regression_test:
            log.info("回归测试: %s", json.dumps(regression_test, ensure_ascii=False))
        return

    # ── 更新文档 ──
    today = datetime.now(TZ_SH).strftime("%Y-%m-%d")

    # 更新 PROJECT_CONTEXT.md
    if PROJECT_CTX.exists():
        append_anomaly_entry(PROJECT_CTX, entry, issue_number)

    # 更新 QA-RULES.md
    if regression_test and QA_RULES.exists():
        append_regression_test(QA_RULES, regression_test)

    # 更新变更日志
    if PROJECT_CTX.exists():
        ctx = PROJECT_CTX.read_text(encoding="utf-8")
        changelog_entry = f"| {today} | {summary} (auto-fix PR #{pr_number}) | Auto-fix Agent |"

        # 找变更日志表头 "| 日期 | 变更内容 | 操作人 |"
        # 格式：表头行 → 分隔线 |------|------|------| → 数据行
        lines = ctx.split("\n")
        header_idx = -1
        for i, line in enumerate(lines):
            if "| 日期 | 变更内容" in line:
                header_idx = i
                break

        if header_idx >= 0 and header_idx + 1 < len(lines):
            # 跳过表头 + 分隔线，在第一个数据行前插入
            insert_idx = header_idx + 2  # header + separator
            lines.insert(insert_idx, changelog_entry)
            PROJECT_CTX.write_text("\n".join(lines), encoding="utf-8")
            log.info("变更日志已更新")
        else:
            log.warning("未找到变更日志表头，跳过")

    # ── 提交并推送 ──
    try:
        git_run(["add", "PROJECT_CONTEXT.md", "QA-RULES.md"])
        git_run(["commit", "-m",
                 f"docs: 自动更新异常模式库 — {summary} (PR #{pr_number})"])
    except subprocess.CalledProcessError:
        log.info("无文件变更，跳过提交")
        return

    try:
        git_run(["push", "origin", "main"])
        log.info("文档更新已推送到 main")
    except subprocess.CalledProcessError as e:
        log.error("推送失败: %s", e.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
