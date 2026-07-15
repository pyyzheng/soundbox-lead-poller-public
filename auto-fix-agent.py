#!/usr/bin/env python3
"""
auto-fix-agent.py — AI 自动修复代理

流程：
  1. 读取 Issue 内容 + 项目上下文
  2. 调用智谱 API 生成修复方案
  3. 校验范围、运行回归测试
  4. 提交到 fix/issue-{number} 分支并创建 PR

环境变量：
  ZHIPU_API_KEY   — 智谱 API Key（必需）
  GITHUB_TOKEN    — GitHub PAT（必需，用于分支/PR/评论）
  ISSUE_NUMBER    — Issue 编号
  ISSUE_TITLE     — Issue 标题
  ISSUE_BODY      — Issue 正文
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

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from auto_fix_utils import (
    validate_scope, call_zhipu_api, parse_ai_response,
    comment_on_issue, check_daily_budget, GH_REPO, ALLOWED_FILES,
)

TZ_SH = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).parent.resolve()
log = logging.getLogger("auto-fix-agent")

# ── 回归测试用例（来自 QA-RULES.md 第五节）────────────────────────────

REJECT_CASES = [
    {"name": "GUYMaKqfADLmClzw", "email": "ok.oculoso.s31@gmail.com",
     "message": "iSOYNUuFvvqGKGUSahECXtj", "phone": "2918585836", "company": ""},
    {"name": "EVOUODhlLtMlKJzJumhti", "email": "e.n.e.f.o.t.e.c.6.0@gmail.com",
     "message": "DhLENREjeAgMLHMZOtZ", "phone": "8451863022", "company": ""},
    {"name": "Sarah", "email": "sarah@seocompany.com",
     "message": "I'd like to write a guest post for your website. We can offer backlinks with DA 50+.",
     "phone": "", "company": "SEO Masters"},
    {"name": "John Smith", "email": "john@company.com",
     "message": "test", "phone": "1234567890", "company": "Acme"},
]

PASS_CASES = [
    {"name": "John Smith", "email": "john@realcompany.com",
     "message": "I need a soundproof booth for my studio, please send quote",
     "phone": "5551234567", "company": "Real Studio LLC"},
    {"name": "张伟", "email": "zhangwei@163.com",
     "message": "我想了解一下静音舱的价格和规格", "phone": "13800138000", "company": ""},
    {"name": "Maria Garcia", "email": "maria@gmail.com",
     "message": "Looking for acoustic panels for office", "phone": "", "company": ""},
    {"name": "Ahmed Hassan", "email": "ahmed@techcorp.ae",
     "message": "We also need the VR series for our second location",
     "phone": "+971501234567", "company": "TechCorp UAE"},
]

HTML_CASES = [
    {"name": "David Chen", "email": "david@av-install.com",
     "message": "<html><body><p>I'm looking for a <b>soundproof booth</b> for our recording studio. "
                "Please send pricing for the SB-100 model.</p></body></html>",
     "phone": "+14155551234", "company": "AV Install Co.", "expect": "pass"},
    {"name": "GUYMaKqfADLmClzw", "email": "ok.oculoso.s31@gmail.com",
     "message": "<div><span>iSOYNUuFvvqGKGUSahECXtj</span></div>",
     "phone": "2918585836", "company": "", "expect": "reject"},
    {"name": "Maria Lopez", "email": "maria@eventpro.es",
     "message": "<p>Hello, we are organizing a conference and need &nbsp; acoustic booths. "
                "Can you provide a quote? Visit our site for reference.</p>",
     "phone": "+34612345678", "company": "EventPro Madrid", "expect": "pass"},
]

REVIEW_CASES = [
    {"name": "Yuki Tanaka", "email": "yuki@music.co.jp",
     "message": "Can you help me?", "phone": "", "company": ""},
    {"name": "Li Wei", "email": "liwei@outlook.com",
     "message": "", "phone": "13900139000", "company": ""},
]

# ── 智谱 API Prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是 SoundBox 线索管道系统的自动代码修复代理。

你的任务：根据 GitHub Issue 描述的异常，生成最小化的代码修复。

## 范围约束
- 只能修改以下文件：{allowed_files}
- 绝不能修改：cloud-health-check.py, .github/workflows/*, auto-fix-agent.py
- 修改必须是最小化、精准的，不做无关重构
- 不引入新依赖（只允许用：requests, json, re, os, sys, logging, pathlib）

## 敏感区域（必须标记 needs_human_review=true）
- cloud-lead-poller.py 中的 LLM_SYSTEM_PROMPT 变量（L3 intent 判断依赖此 prompt，改动会影响线索分级）
- lib/lead_filter_common.py 中的 check_spam / check_irrelevant_business 核心过滤逻辑
- lead-rules.json 中的过滤关键词列表
触及以上区域的修改，即使你有信心，也必须设 needs_human_review=true

## 输出格式
只输出一个 JSON 块，不要有其他文字：
{{
  "analysis": "一段话根因分析",
  "files": [
    {{
      "path": "相对文件路径",
      "action": "replace",
      "description": "修改说明",
      "content": "完整的新文件内容"
    }}
  ],
  "confidence": "high 或 medium 或 low",
  "needs_human_review": true 或 false,
  "regression_notes": "验证说明"
}}

## 规则
1. 输出每个修改文件的完整内容，不是 diff
2. 如果不确定修复是否正确，设 confidence 为 "low"，needs_human_review 为 true
3. 如果 Issue 无法通过代码修复（如 token 过期），files 设为空数组，在 analysis 中说明
4. 修复后，下面列出的回归测试用例必须全部通过
5. 所有网络请求必须有 timeout 设置（≤30 秒）
6. 不硬编码凭证
""".format(allowed_files=", ".join(sorted(ALLOWED_FILES)))


# ── Gmail OAuth 评论模板 ────────────────────────────────────────────────

GMAIL_OAUTH_COMMENT = """## 自动修复分析

此 Issue 为 **Gmail OAuth token 失效**，无法通过代码自动修复。

### 手动修复步骤

1. **确认 Google Cloud 项目状态**
   - 打开 [Google Cloud Console](https://console.cloud.google.com/)
   - 确认项目已切换到**「生产」状态**（非「测试」模式）
   - 测试模式下 OAuth token 每 7 天过期

2. **重新获取 refresh token**
   ```bash
   # 从 macOS 钥匙串提取 token
   security find-generic-password -s gogcli -a 'token:default:soundboxbooth@gmail.com' -w
   ```
   如果钥匙串中没有，需要重新走 OAuth 授权流程。

3. **更新 GitHub Secret**
   - 进入仓库 Settings → Secrets and variables → Actions
   - 更新 `GMAIL_REFRESH_TOKEN` 为新获取的 token
   - 同时检查 `GMAIL_CLIENT_ID` 和 `GMAIL_CLIENT_SECRET` 是否正确

4. **验证修复**
   - 手动触发 `health-check.yml` workflow
   - 确认检查 3（Gmail OAuth）通过
"""


# ═══════════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════════

def detect_alert_type(issue_title: str, issue_body: str = "") -> str:
    """从 Issue 标题或 body 推断告警类型"""
    text = (issue_title or "") + " " + (issue_body or "")
    if "垃圾漏网" in text or "spam_leaked" in text:
        return "spam_leaked"
    if "Actions连续失败" in text or "连续失败" in text or "github_consecutive_failures" in text:
        return "github_consecutive_failures"
    if "OAuth失效" in text or "OAuth" in text.upper():
        return "gmail_oauth_expired"
    if "格式异常" in text or "format_anomaly" in text:
        return "format_anomaly"
    if "管线健康" in text or "health_check" in text:
        return "health_check"
    # 混合类型 Issue（如"检测到 N 条异常"），从 body 中提取优先级最高的可修复类型
    if "spam_leaked" not in text and "format_anomaly" in text:
        return "format_anomaly"
    if "Cron触发" in text or "cronjob_trigger" in text.lower():
        return "cronjob_trigger_failed"
    return "unknown"


def get_relevant_files(alert_type: str) -> list:
    """根据告警类型返回需要读入上下文的文件列表"""
    mapping = {
        "spam_leaked": ["lib/lead_filter_common.py", "lead-rules.json"],
        "github_consecutive_failures": ["lib/feishu_utils.py"],
        "format_anomaly": ["cloud-lead-poller.py", "lib/lead_fallback_parser.py", "lead-rules.json"],
        "health_check": ["cloud-health-check.py"],
        "cronjob_trigger_failed": [],
        "unknown": ["lib/lead_filter_common.py", "lead-rules.json"],
    }
    return mapping.get(alert_type, [])


def read_file_content(path: str) -> str:
    """读取项目文件内容"""
    full_path = SCRIPT_DIR / path
    if not full_path.exists():
        return f"[文件不存在: {path}]"
    return full_path.read_text(encoding="utf-8")


def build_user_prompt(issue_title: str, issue_body: str,
                      qa_rules: str, file_contents: dict) -> str:
    """构造用户 prompt"""
    files_section = []
    for path, content in file_contents.items():
        files_section.append(f"--- {path} ---\n{content}\n")

    return f"""## 需要修复的 Issue
标题: {issue_title}

正文:
{issue_body or '(无正文)'}

## 回归测试用例（修复后必须全部通过）

### 应该 reject
{json.dumps(REJECT_CASES, ensure_ascii=False, indent=2)}

### 应该 pass
{json.dumps(PASS_CASES, ensure_ascii=False, indent=2)}

### HTML 邮件测试
{json.dumps(HTML_CASES, ensure_ascii=False, indent=2)}

### 应该 review
{json.dumps(REVIEW_CASES, ensure_ascii=False, indent=2)}

## QA 规则文档
{qa_rules}

## 相关源文件
{"".join(files_section)}

请生成修复方案。要求：
1. 解决 Issue 描述的根因
2. 修复后所有回归测试用例必须通过
3. 改动最小化，不修改无关逻辑
"""


def run_regression_tests() -> dict:
    """运行 QA-RULES.md 第五节回归测试用例。
    返回 {"passed": int, "failed": int, "failures": list}
    """
    from lead_filter_common import (
        check_spam, check_placeholder, check_promotional_content,
        check_irrelevant_business, check_inquiry_keywords, load_lead_rules,
    )

    rules = load_lead_rules()
    results = {"passed": 0, "failed": 0, "failures": []}

    def run_filter_chain(name, email, message, phone="", company=""):
        """运行完整过滤链，返回 (信号数, [原因列表])"""
        signals = 0
        reasons = []
        s, sr = check_spam(name, email, message, rules)
        if s:
            signals += 1
            reasons.append(sr)
        p, pr = check_placeholder(name, email, phone, company)
        if p:
            signals += 1
            reasons.append(pr)
        sc, scr = check_promotional_content(name, "", message, company, rules)
        if sc:
            signals += 1
            reasons.append(scr)
        ib, ibr = check_irrelevant_business(name, company, message, rules)
        if ib:
            signals += 1
            reasons.append(ibr)
        kw, _ = check_inquiry_keywords(name, message, company, rules)
        if not kw:
            signals += 1
            reasons.append("no_keyword")
        return signals, reasons

    # reject 用例：信号 >= 2 应被拦截
    for case in REJECT_CASES:
        signals, reasons = run_filter_chain(
            case["name"], case["email"], case["message"],
            case.get("phone", ""), case.get("company", ""),
        )
        if signals >= 2:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append(
                f"REJECT 失败: {case['name']} | signals={signals} reasons={reasons}"
            )

    # pass 用例：信号 < 2 应被放行
    for case in PASS_CASES:
        signals, reasons = run_filter_chain(
            case["name"], case["email"], case["message"],
            case.get("phone", ""), case.get("company", ""),
        )
        if signals < 2:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append(
                f"PASS 失败: {case['name']} | signals={signals} reasons={reasons}"
            )

    # HTML 用例
    for case in HTML_CASES:
        signals, reasons = run_filter_chain(
            case["name"], case["email"], case["message"],
            case.get("phone", ""), case.get("company", ""),
        )
        expect_reject = case["expect"] == "reject"
        is_rejected = signals >= 2
        if expect_reject == is_rejected:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append(
                f"HTML 测试失败: {case['name']} expect={case['expect']} signals={signals} reasons={reasons}"
            )

    # 1 信号用例：信号 == 1 应放行（交给 grader 评估）
    for case in REVIEW_CASES:
        signals, reasons = run_filter_chain(
            case["name"], case["email"], case["message"],
            case.get("phone", ""), case.get("company", ""),
        )
        if signals <= 1:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append(
                f"REVIEW 失败: {case['name']} | signals={signals} reasons={reasons}"
            )

    return results


def git_run(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """执行 git 命令"""
    return subprocess.run(
        ["git"] + args,
        cwd=str(SCRIPT_DIR),
        capture_output=True, text=True, check=check,
        timeout=30,
    )


def apply_file_changes(changes: list, issue_number: int) -> bool:
    """写入文件修改并提交到 fix/issue-{n} 分支"""
    branch = f"fix/issue-{issue_number}"

    try:
        git_run(["checkout", "-b", branch])
    except subprocess.CalledProcessError as e:
        log.error("创建分支失败: %s", e.stderr)
        return False

    try:
        for change in changes:
            raw_path = change["path"].replace("\\", "/")
            path = (SCRIPT_DIR / raw_path).resolve()
            if not str(path).startswith(str(SCRIPT_DIR)):
                log.error("路径遍历被拦截: %s", raw_path)
                raise ValueError(f"路径遍历被拦截: {raw_path}")
            path.write_text(change["content"], encoding="utf-8")
            log.info("写入文件: %s", change["path"])

        add_args = ["add", "--"] + [c["path"].replace("\\", "/") for c in changes]
        git_run(add_args)

        description = changes[0].get("description", "auto-fix") if changes else "auto-fix"
        git_run(["commit", "-m",
                 f"fix: auto-fix for issue #{issue_number} — {description}"])
    except subprocess.CalledProcessError as e:
        log.error("文件修改失败 (git exit %d): %s", e.returncode, e.stderr)
        git_run(["checkout", "main"], check=False)
        git_run(["branch", "-D", branch], check=False)
        return False
    except Exception as e:
        log.error("文件修改失败: %s", e)
        git_run(["checkout", "main"], check=False)
        git_run(["branch", "-D", branch], check=False)
        return False

    return True


def create_fix_pr(issue_number: int, analysis: str, changes: list) -> bool:
    """推送分支并创建 PR"""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        log.error("GITHUB_TOKEN 未配置，无法创建 PR")
        return False

    branch = f"fix/issue-{issue_number}"

    # 推送分支
    try:
        git_run(["push", "origin", branch])
    except subprocess.CalledProcessError as e:
        log.error("推送失败: %s", e.stderr)
        return False

    # 构造 PR body
    body_lines = [
        f"Auto-generated fix for #{issue_number}\n",
        f"## 根因分析\n{analysis}\n",
        "## 修改内容",
    ]
    for c in changes:
        body_lines.append(f"- `{c['path']}`: {c.get('description', '')}")
    body_lines += [
        "",
        "## 验证",
        "- [x] 回归测试用例全部通过",
        f"Closes #{issue_number}",
    ]

    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{GH_REPO}/pulls",
            headers=headers,
            json={
                "title": f"fix: #{issue_number} — {analysis[:60]}",
                "body": "\n".join(body_lines),
                "head": branch,
                "base": "main",
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            pr = resp.json()
            log.info("PR 已创建: #%d — %s", pr["number"], pr["html_url"])
            return True
        log.error("PR 创建失败: %d %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error("PR 创建异常: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

def main():
    dry_run = os.environ.get("DRY_RUN", "false") == "true"
    issue_number = int(os.environ.get("ISSUE_NUMBER", "0"))
    issue_title = os.environ.get("ISSUE_TITLE", "")
    issue_body = os.environ.get("ISSUE_BODY", "")

    # 设置日志
    log_file = SCRIPT_DIR / f"auto-fix-{issue_number}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    log.info("=" * 50)
    log.info("Auto-fix agent 启动: Issue #%d — %s", issue_number, issue_title)
    log.info("DRY_RUN=%s", dry_run)

    if not issue_number:
        log.error("ISSUE_NUMBER 未设置")
        sys.exit(1)

    # ── 每日预算检查 ──
    if not check_daily_budget():
        msg = "今日自动修复次数已达上限，请明天再试或手动修复。"
        comment_on_issue(issue_number, msg)
        log.info(msg)
        return

    # ── 判断告警类型 ──
    alert_type = detect_alert_type(issue_title, issue_body)
    log.info("告警类型: %s", alert_type)

    # gmail_oauth_expired：不需要代码修复，评论具体操作步骤后退出
    if alert_type == "gmail_oauth_expired":
        comment_on_issue(issue_number, GMAIL_OAUTH_COMMENT)
        log.info("OAuth 类 Issue，已评论修复步骤，退出")
        return

    # health_check：涉及核心脚本，不在自动修复范围
    if alert_type == "health_check":
        msg = (f"## 自动修复跳过\n\n"
               f"此类型告警（`{alert_type}`）涉及核心脚本，需人工排查。\n\n"
               f"请对照 Issue 中的诊断信息手动修复。")
        comment_on_issue(issue_number, msg)
        log.info("告警类型 %s 不在自动修复范围，已评论退出", alert_type)
        return

    # cronjob_trigger_failed：外部 cron 触发问题，需人工排查 cron-job.org
    if alert_type == "cronjob_trigger_failed":
        msg = ("## 自动修复跳过\n\n"
               "此类型告警为外部 cron 触发异常，无法通过代码自动修复。\n\n"
               "### 手动排查步骤\n"
               "1. 登录 [cron-job.org](https://cron-job.org) 检查任务是否启用\n"
               "2. 检查 GHA_PAT 是否过期\n"
               "3. 手动触发 lead-poller workflow 验证")
        comment_on_issue(issue_number, msg)
        log.info("告警类型 %s 不在自动修复范围，已评论退出", alert_type)
        return

    # 涉及 cloud-lead-poller.py 的 Issue：需人工介入
    if "cloud-lead-poller" in (issue_body or "").lower() and alert_type == "github_consecutive_failures":
        msg = "## 自动修复分析\n\n此 Issue 涉及主管道文件 `cloud-lead-poller.py` 的修改，主管道文件修改需人工介入，自动修复跳过。\n\n请手动排查 GitHub Actions 日志确认错误原因。"
        comment_on_issue(issue_number, msg)
        log.info("涉及主管道文件，已评论说明，退出")
        return

    # ── 读取项目上下文 ──
    project_ctx = read_file_content("PROJECT_CONTEXT.md")
    qa_rules = read_file_content("QA-RULES.md")

    # 读取相关源文件
    relevant_files = get_relevant_files(alert_type)
    file_contents = {f: read_file_content(f) for f in relevant_files}
    log.info("读入上下文文件: %s", list(file_contents.keys()))

    # ── 调用智谱 API ──
    user_prompt = build_user_prompt(issue_title, issue_body, qa_rules, file_contents)
    log.info("调用智谱 API...")

    try:
        api_result = call_zhipu_api(SYSTEM_PROMPT, user_prompt, max_tokens=16384)
        raw_content = api_result["content"]
    except Exception as e:
        log.error("智谱 API 调用失败: %s", e)
        comment_on_issue(issue_number, f"## 自动修复失败\n\nAI API 调用异常: {e}")
        sys.exit(1)

    # ── 解析 AI 响应 ──
    try:
        parsed = parse_ai_response(raw_content)
    except json.JSONDecodeError as e:
        log.error("AI 返回非 JSON: %s", raw_content[:200])
        comment_on_issue(issue_number,
            f"## 自动修复失败\n\nAI 返回格式无法解析。\n\n原始输出:\n```\n{raw_content[:1000]}\n```")
        sys.exit(1)

    analysis = parsed.get("analysis", "")
    confidence = parsed.get("confidence", "low")
    needs_human = parsed.get("needs_human_review", False)
    files = parsed.get("files", [])
    log.info("AI 分析: %s", analysis[:100])
    log.info("置信度: %s, 需人工: %s, 文件数: %d", confidence, needs_human, len(files))

    # 低置信度或需人工审查：只评论，不创建 PR
    if confidence == "low" or needs_human:
        msg = f"## AI 分析结果\n\n**置信度**: {confidence}\n**需要人工审查**: {'是' if needs_human else '否'}\n\n### 根因分析\n{analysis}\n\n### 验证说明\n{parsed.get('regression_notes', '')}"
        comment_on_issue(issue_number, msg)
        log.info("置信度低或需人工审查，已评论分析结果，退出")
        return

    # 无文件修改
    if not files:
        msg = f"## AI 分析结果\n\n此 Issue 无法通过代码自动修复。\n\n### 根因分析\n{analysis}"
        comment_on_issue(issue_number, msg)
        log.info("AI 未生成文件修改，退出")
        return

    # ── 范围校验 ──
    ok, scope_err = validate_scope(files)
    if not ok:
        comment_on_issue(issue_number,
            f"## 自动修复被拦截\n\n{scope_err}\n\n此文件不在自动修复允许范围内，需人工处理。")
        log.error("范围校验失败: %s", scope_err)
        sys.exit(1)

    # ── Dry run 模式：只输出不推送 ──
    if dry_run:
        log.info("[DRY-RUN] 跳过文件修改和 PR 创建")
        for f in files:
            log.info("  修改文件: %s — %s", f["path"], f.get("description", ""))
        log.info("分析: %s", analysis)
        return

    # ── 应用文件修改 ──
    if not apply_file_changes(files, issue_number):
        comment_on_issue(issue_number,
            "## 自动修复失败\n\n文件修改或 git commit 失败，修复未推送。\n\n请手动排查。")
        log.error("apply_file_changes 失败，退出")
        sys.exit(1)

    # ── 运行回归测试 ──
    log.info("运行回归测试...")
    test_results = run_regression_tests()
    log.info("回归测试: 通过=%d, 失败=%d", test_results["passed"], test_results["failed"])

    if test_results["failed"] > 0:
        # 回归测试失败：不推送，评论失败详情
        failure_text = "\n".join(f"- {f}" for f in test_results["failures"])
        comment_on_issue(issue_number,
            f"## 自动修复被拦截 — 回归测试失败\n\n"
            f"通过: {test_results['passed']}, 失败: {test_results['failed']}\n\n"
            f"失败详情:\n{failure_text}\n\n"
            f"修复未推送，请人工处理。")
        log.error("回归测试失败，修复未推送")
        # 清理分支
        git_run(["checkout", "main"], check=False)
        git_run(["branch", "-D", f"fix/issue-{issue_number}"], check=False)
        sys.exit(1)

    # ── 创建 PR ──
    pr_ok = create_fix_pr(issue_number, analysis, files)
    if pr_ok:
        comment_on_issue(issue_number,
            f"## 自动修复 PR 已创建\n\n{analysis}\n\n请审查 PR 后合并。")
        log.info("修复完成")
    else:
        comment_on_issue(issue_number,
            f"## PR 创建失败\n\n分析: {analysis}\n\n修复已提交到本地分支，但 PR 创建失败。请手动创建 PR。")
        log.error("PR 创建失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
