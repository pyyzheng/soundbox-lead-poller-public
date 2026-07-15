# -*- coding: utf-8 -*-
"""zhipu_client.py — 智谱 GLM 调用（Anthropic 兼容端点）

供 cloud-company-research.py / cloud-research-audit.py 共享，避免两处重复维护端点逻辑。

为什么走 Anthropic 端点而非 OpenAI 端点（/api/paas/v4）:
该账号 OpenAI 端点付费余额已耗尽，glm-5.2/4.6/4.5-air 等均 HTTP 429（code:1113 余额不足）；
但同一把 ZHIPU_API_KEY 在 Anthropic 端点有独立可用额度（Claude Code 即用此通道）。
参考实现：~/.claude/scripts/glm_core.py（2026-06 验证可用）。

密钥来源：环境变量 ZHIPU_API_KEY（GitHub Actions 由 secrets 注入；本地由 infisical/.env 提供）。
"""
import os
import time

import requests

# Anthropic 兼容端点（与 glm_core.py 同源，已验证）
URL = "https://open.bigmodel.cn/api/anthropic/v1/messages"
TIMEOUT = 60


def call_zhipu(system, user, model=None, max_tokens=2048,
               temperature=0.1, retries=2):
    """调用 GLM（Anthropic Messages 协议）。

    返回 (content, stop_reason)：
      - 成功：content 为模型文本，stop_reason 为 anthropic 停止原因（end_turn/max_tokens/...）
      - 失败：content 为 ""，stop_reason 为错误描述（HTTP 状态+body 片段 或 NO_API_KEY）

    不抛异常，由调用方根据返回值决定降级策略。
    """
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key:
        return "", "NO_API_KEY"

    model = model or os.environ.get("ZHIPU_MODEL", "glm-4.5-air")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        body["system"] = system

    last = ""
    for i in range(retries + 1):
        try:
            r = requests.post(
                URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                # anthropic 响应：content[] 为 block 数组，stop_reason 在顶层
                txt = "".join(
                    b.get("text", "")
                    for b in data.get("content", [])
                    if b.get("type") == "text"
                )
                return txt, data.get("stop_reason", "")
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.Timeout:
            last = "timeout"
        except Exception as e:
            last = str(e)[:200]
        if i < retries:
            time.sleep(1.5)
    return "", last
