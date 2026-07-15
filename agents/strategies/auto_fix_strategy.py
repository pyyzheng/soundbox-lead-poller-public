"""auto_fix_strategy — 封装 auto-fix-agent.py 核心修复链路为 FixStrategyFn。

链路：智谱调用 → 范围校验 → 回归测试 → Git PR。
适配 fixer agent 的 (anomalies, context) → FixResult 接口。
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from auto_fix_utils import (
    validate_scope, call_zhipu_api, parse_ai_response,
    comment_on_issue, check_daily_budget, GH_REPO, ALLOWED_FILES,
)
from agents.types import Anomaly, FixResult

log = logging.getLogger("strategy:auto-fix")

SCRIPT_DIR = REPO_ROOT

# ── 回归测试用例 ─────────────────────────────────────────────────────

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

# ── 智谱 System Prompt ──────────────────────────────────────────────

SYSTEM_PROMPT = """你是 SoundBox 线索管道系统的自动代码修复代理。

你的任务：根据异常描述，生成最小化的代码修复。

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
3. 如果异常无法通过代码修复（如 token 过期），files 设为空数组，在 analysis 中说明
4. 修复后，下面列出的回归测试用例必须全部通过
5. 所有网络请求必须有 timeout 设置（≤30 秒）
6. 不硬编码凭证
""".format(allowed_files=", ".join(sorted(ALLOWED_FILES)))

# ── 不可自动修复的异常类型 ────────────────────────────────────────────

SKIP_TYPES = {
    "gmail_oauth_expired": "OAuth token 失效需手动更新，无法代码修复",
    "health_check": "健康检查异常涉及核心脚本，需人工排查",
    "github_consecutive_failures": "GitHub Actions 连续失败需人工排查日志",
}

# ── 敏感区域定义 ───────────────────────────────────────────────────────

# (文件, 关键词) → 改动触及关键词所在区域时强制 needs_review
SENSITIVE_AREAS = [
    ("cloud-lead-poller.py", "LLM_SYSTEM_PROMPT"),
    ("lib/lead_filter_common.py", "def check_spam"),
    ("lib/lead_filter_common.py", "def check_irrelevant_business"),
]


# ── 辅助函数 ─────────────────────────────────────────────────────────


def _check_sensitive_areas(files: list) -> list[str]:
    """检查 AI 生成的改动是否触及敏感区域，返回命中的敏感区域描述列表。"""
    hits = []
    for f in files:
        path = f.get("path", "").replace("\\", "/")
        content = f.get("content", "")
        for sensitive_path, keyword in SENSITIVE_AREAS:
            if path != sensitive_path:
                continue
            # 关键词出现在 diff 内容中 → 触及敏感区域
            if keyword in content:
                hits.append(f"{path}:{keyword}")
    return hits


def _read_file(path: str) -> str:
    full = SCRIPT_DIR / path
    return full.read_text(encoding="utf-8") if full.exists() else f"[文件不存在: {path}]"


def _relevant_files(alert_type: str) -> list:
    mapping = {
        "spam_leaked": ["lib/lead_filter_common.py", "lead-rules.json"],
        "github_consecutive_failures": ["lib/feishu_utils.py"],
        "format_anomaly": ["cloud-lead-poller.py", "lib/lead_fallback_parser.py", "lead-rules.json"],
        "health_check": ["cloud-health-check.py"],
    }
    return mapping.get(alert_type, ["lib/lead_filter_common.py", "lead-rules.json"])


def _build_prompt(anomaly: Anomaly) -> str:
    files = _relevant_files(anomaly.type)
    contents = {f: _read_file(f) for f in files}
    qa_rules = _read_file("QA-RULES.md")

    files_section = "".join(f"--- {p} ---\n{c}\n" for p, c in contents.items())

    return f"""## 需要修复的异常
类型: {anomaly.type}
严重度: {anomaly.severity}
描述: {anomaly.description}
证据: {json.dumps(anomaly.evidence, ensure_ascii=False)}
来源: {anomaly.source}

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
{files_section}

请生成修复方案。"""


def _run_regression() -> dict:
    from lead_filter_common import (
        check_spam, check_placeholder, check_promotional_content,
        check_irrelevant_business, check_inquiry_keywords, load_lead_rules,
    )

    rules = load_lead_rules()
    results = {"passed": 0, "failed": 0, "failures": []}

    def chain(name, email, message, phone="", company=""):
        signals, reasons = 0, []
        for fn, args in [
            (check_spam, (name, email, message, rules)),
            (check_placeholder, (name, email, phone, company)),
            (check_promotional_content, (name, "", message, company, rules)),
            (check_irrelevant_business, (name, company, message, rules)),
        ]:
            s, r = fn(*args)
            if s:
                signals += 1
                reasons.append(r)
        kw, _ = check_inquiry_keywords(name, message, company, rules)
        if not kw:
            signals += 1
            reasons.append("no_keyword")
        return signals, reasons

    for c in REJECT_CASES:
        s, r = chain(c["name"], c["email"], c["message"], c.get("phone", ""), c.get("company", ""))
        (results.__setitem__("passed", results["passed"] + 1) if s >= 2
         else results.update(failed=results["failed"]+1, failures=results["failures"]+[f"REJECT: {c['name']} s={s} {r}"]))

    for c in PASS_CASES:
        s, r = chain(c["name"], c["email"], c["message"], c.get("phone", ""), c.get("company", ""))
        (results.__setitem__("passed", results["passed"] + 1) if s < 2
         else results.update(failed=results["failed"]+1, failures=results["failures"]+[f"PASS: {c['name']} s={s} {r}"]))

    for c in HTML_CASES:
        s, r = chain(c["name"], c["email"], c["message"], c.get("phone", ""), c.get("company", ""))
        ok = (c["expect"] == "reject") == (s >= 2)
        (results.__setitem__("passed", results["passed"] + 1) if ok
         else results.update(failed=results["failed"]+1, failures=results["failures"]+[f"HTML: {c['name']} expect={c['expect']} s={s}"]))

    for c in REVIEW_CASES:
        s, r = chain(c["name"], c["email"], c["message"], c.get("phone", ""), c.get("company", ""))
        (results.__setitem__("passed", results["passed"] + 1) if s <= 1
         else results.update(failed=results["failed"]+1, failures=results["failures"]+[f"REVIEW: {c['name']} s={s}"]))

    return results


def _git_run(args: list, check: bool = True):
    return subprocess.run(
        ["git"] + args, cwd=str(SCRIPT_DIR),
        capture_output=True, text=True, check=check, timeout=30,
    )


def _apply_and_pr(changes: list, analysis: str, scan_id: str, warnings: list = None) -> bool:
    """写入文件 → 运行回归测试 → 推送分支 → 创建 PR。"""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token or not changes:
        return False

    branch = f"fix/agent-{scan_id[:8]}"
    try:
        _git_run(["checkout", "-b", branch])
    except subprocess.CalledProcessError as e:
        log.error("分支创建失败: %s", e.stderr)
        return False

    try:
        for c in changes:
            path = (SCRIPT_DIR / c["path"].replace("\\", "/")).resolve()
            if not str(path).startswith(str(SCRIPT_DIR)):
                raise ValueError(f"路径遍历: {c['path']}")
            path.write_text(c["content"], encoding="utf-8")

        _git_run(["add", "--"] + [c["path"] for c in changes])
        desc = changes[0].get("description", "auto-fix")
        _git_run(["commit", "-m", f"fix: agent-scan {scan_id[:8]} — {desc}"])
    except Exception as e:
        log.error("文件写入失败: %s", e)
        _git_run(["checkout", "main"])
        _git_run(["branch", "-D", branch], check=False)
        return False

    # 回归测试
    test = _run_regression()
    if test["failed"] > 0:
        log.error("回归测试失败: %s", test["failures"])
        _git_run(["checkout", "main"])
        _git_run(["branch", "-D", branch], check=False)
        return False

    # 推送 + 创建 PR
    try:
        _git_run(["push", "origin", branch])
    except subprocess.CalledProcessError as e:
        log.error("推送失败: %s", e.stderr)
        return False

    body_lines = [
        f"Auto-generated fix (scan {scan_id[:8]})\n",
    ]
    if warnings:
        body_lines.append(f"**敏感区域警告** — 以下区域被修改，合并前需确认不影响 L3 intent 判断：")
        for w in warnings:
            body_lines.append(f"- `{w}`")
        body_lines.append("")
    body_lines.extend([
        f"## 根因分析\n{analysis}\n",
        "## 修改内容",
    ])
    for c in changes:
        body_lines.append(f"- `{c['path']}`: {c.get('description', '')}")

    import requests
    resp = requests.post(
        f"https://api.github.com/repos/{GH_REPO}/pulls",
        headers={"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"},
        json={
            "title": f"fix: scan-{scan_id[:8]} — {analysis[:60]}",
            "body": "\n".join(body_lines),
            "head": branch,
            "base": "main",
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        log.info("PR #%d 已创建", resp.json()["number"])
        return True
    log.error("PR 创建失败: %d %s", resp.status_code, resp.text[:200])
    return False


# ── FixStrategyFn 实现 ──────────────────────────────────────────────


async def auto_fix_strategy(anomalies: list[Anomaly], context: dict) -> FixResult:
    """核心修复策略：智谱分析 → 范围校验 → 回归测试 → Git PR。"""
    scan_id = context.get("scan_id", "unknown")

    for anomaly in anomalies:
        # 不可自动修复的类型直接跳过
        if anomaly.type in SKIP_TYPES:
            log.info("[auto-fix] 跳过 %s: %s", anomaly.type, SKIP_TYPES[anomaly.type])
            return FixResult(
                success=True, summary=SKIP_TYPES[anomaly.type],
                changed_files=[], confidence=1.0, needs_review=False,
            )

    # 预算检查
    if not check_daily_budget():
        return FixResult(
            success=False, summary="今日自动修复次数已达上限",
            changed_files=[], confidence=1.0, needs_review=False,
        )

    # 取第一条异常作为修复目标（一次修一个）
    target = anomalies[0]
    user_prompt = _build_prompt(target)

    # 智谱 API 调用
    try:
        api_result = call_zhipu_api(SYSTEM_PROMPT, user_prompt, max_tokens=8192)
        raw = api_result["content"]
    except Exception as e:
        return FixResult(success=False, summary=f"智谱 API 失败: {e}",
                         changed_files=[], confidence=0.0, needs_review=False)

    # 解析 AI 输出
    try:
        parsed = parse_ai_response(raw)
    except json.JSONDecodeError:
        return FixResult(success=False, summary=f"AI 输出解析失败: {raw[:100]}",
                         changed_files=[], confidence=0.0, needs_review=False)

    analysis = parsed.get("analysis", "")
    confidence = parsed.get("confidence", "low")
    needs_human = parsed.get("needs_human_review", False)
    files = parsed.get("files", [])

    # 低置信度 / 需人工 → 不执行
    if confidence == "low" or needs_human or not files:
        return FixResult(
            success=True, summary=f"AI 分析（未执行）: {analysis}",
            changed_files=[], confidence=0.5, needs_review=True,
        )

    # 范围校验
    ok, err = validate_scope(files)
    if not ok:
        return FixResult(success=False, summary=f"范围校验失败: {err}",
                         changed_files=[], confidence=0.0, needs_review=False)

    # 敏感区域检查：涉及 LLM prompt / 核心过滤逻辑的改动强制需人工 review
    sensitive_touched = _check_sensitive_areas(files)
    if sensitive_touched:
        log.warning("[auto-fix] 敏感区域被修改: %s — 强制 needs_review", sensitive_touched)
        needs_review = True

    # Dry run 检查
    if os.environ.get("DRY_RUN", "false") == "true":
        return FixResult(
            success=True, summary=f"[DRY-RUN] {analysis}",
            changed_files=[f["path"] for f in files],
            confidence=0.8, needs_review=False,
        )

    # 执行：写入文件 → 回归测试 → PR
    pr_ok = _apply_and_pr(files, analysis, scan_id, warnings=sensitive_touched)
    if pr_ok:
        return FixResult(
            success=True, summary=analysis,
            changed_files=[f["path"] for f in files],
            confidence=0.9, needs_review=True,
        )
    return FixResult(
        success=False, summary=f"PR 创建失败: {analysis}",
        changed_files=[], confidence=0.5, needs_review=False,
    )
