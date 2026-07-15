#!/usr/bin/env python3
"""
auto_fix_utils.py — AI 自动修复共享工具

提供：范围校验、智谱 API 封装、GitHub Issue 评论、每日预算检查。
"""

import json
import os
import re
import time
import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger("auto-fix-utils")

GH_REPO = os.environ.get("GITHUB_REPO", "pyyzheng/soundbox-lead-poller-public")
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# AI 允许修改的文件白名单
ALLOWED_FILES = frozenset([
    "lib/lead_filter_common.py",
    "lib/lead_fallback_parser.py",
    "lib/slot_extractor.py",
    "lib/email_generator.py",
    "lib/feishu_utils.py",
    "lib/lead_grader.py",
    "lib/lead_filter_cli.py",
    "lead-rules.json",
    "cloud-lead-poller.py",   # 主管道：仅允许非核心逻辑修复（字段映射、日志格式等）
])

MAX_DAILY_FIXES = 5


def validate_scope(file_list: list) -> tuple:
    """校验 AI 生成的文件修改是否在允许范围内。
    返回 (ok, error_msg)
    """
    for f in file_list:
        path = f.get("path", "").replace("\\", "/")
        # 路径规范化：去掉 ./ 前缀，禁止路径遍历
        parts = [p for p in path.split("/") if p and p != "."]
        if ".." in parts:
            return False, f"路径遍历被拦截: '{path}'"
        normalized = "/".join(parts)
        if normalized not in ALLOWED_FILES:
            return False, f"文件 '{path}' 不在允许修改范围内，自动修复跳过"
    return True, ""


def call_zhipu_api(system_prompt: str, user_prompt: str,
                   model: str = "", max_tokens: int = 4096,
                   temperature: float = 0.1, retries: int = 3) -> dict:
    """调用智谱 API，返回 {"content": str, "usage": dict}。
    429 限速时按 2s→4s→8s 退避重试。
    """
    api_key = os.environ.get("ZHIPU_API_KEY", "")
    if not api_key:
        raise RuntimeError("ZHIPU_API_KEY 未配置")

    model = model or os.environ.get("ZHIPU_FIX_MODEL", "glm-4-flash")

    backoff = 2
    for attempt in range(retries):
        resp = requests.post(
            f"{ZHIPU_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=120,
        )
        if resp.status_code == 429 and attempt < retries - 1:
            log.warning("智谱 API 429 限速，%ds 后重试（%d/%d）", backoff, attempt + 1, retries)
            time.sleep(backoff)
            backoff *= 2
            continue
        break

    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        log.warning("智谱 API 返回空 choices")
        return {"content": "", "usage": {}}
    content = choices[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    log.info("智谱 API: model=%s, prompt_tokens=%s, completion_tokens=%s",
             model, usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"))
    return {"content": content, "usage": usage}


def _strip_code_fence(text: str) -> str:
    """去除 AI 返回的 ```json``` 包裹"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    # 截断场景：有开头 ```json 但无闭合
    m2 = re.match(r"```(?:json)?\s*", text)
    if m2:
        return text[m2.end():].strip()
    return text


def parse_ai_response(raw_text: str) -> dict:
    """从 AI 返回文本中提取 JSON，处理 ```json``` 包裹和截断场景"""
    text = raw_text.strip()
    candidate = _strip_code_fence(text)

    # 尝试完整解析
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 截断场景：用 raw_decode 找到最长的合法 JSON 前缀
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(candidate)
        return obj
    except json.JSONDecodeError:
        pass

    # 截断场景：尝试正则提取 analysis，返回低置信度部分结果
    analysis_match = re.search(r'"analysis"\s*:\s*"((?:[^"\\]|\\.)*)"', candidate)
    if analysis_match:
        analysis_text = analysis_match.group(1)
        # 处理 JSON 转义序列（\n, \t, \\, \"），但不做 unicode_escape（避免中文乱码）
        analysis_text = analysis_text.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\").replace('\\"', '"')
        log.warning("AI 响应截断，仅提取到 analysis: %s", analysis_text[:100])
        return {
            "analysis": analysis_text,
            "files": [],
            "confidence": "low",
            "needs_human_review": True,
            "regression_notes": "AI 响应被截断，无法生成完整修复方案",
        }

    raise json.JSONDecodeError("AI 响应无法解析（可能被截断）", text, 0)


def comment_on_issue(issue_number: int, message: str, gh_token: str = ""):
    """在 GitHub Issue 上发表评论"""
    gh_token = gh_token or os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        log.error("GITHUB_TOKEN 未配置，无法评论 Issue")
        return False

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{GH_REPO}/issues/{issue_number}/comments",
            headers={
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github+json",
            },
            json={"body": message},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info("Issue #%d 评论成功", issue_number)
            return True
        log.error("Issue 评论失败: %d %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error("Issue 评论异常: %s", e)
        return False


def check_daily_budget(gh_token: str = "") -> bool:
    """检查今日自动修复 PR 数是否超出预算。
    返回 True 表示还有预算。
    """
    gh_token = gh_token or os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        log.warning("GITHUB_TOKEN 未配置，预算检查跳过，拒绝执行")
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/pulls",
            params={"state": "all", "per_page": 50},
            headers={"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("预算检查 API 返回 %d，拒绝", resp.status_code)
            return False

        today_fix_prs = [
            pr for pr in resp.json()
            if pr.get("created_at", "").startswith(today)
            and pr.get("head", {}).get("ref", "").startswith("fix/")
        ]
        remaining = MAX_DAILY_FIXES - len(today_fix_prs)
        log.info("今日已创建 %d 个 fix PR，预算剩余 %d", len(today_fix_prs), remaining)
        return remaining > 0
    except Exception as e:
        log.warning("预算检查失败，拒绝: %s", e)
        return False
