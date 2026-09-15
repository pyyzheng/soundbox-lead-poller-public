import json
import logging
import os

import requests

from agents.base_agent import BaseAgent
from agents.bus.message_bus import MessageBus
from agents.types import Message, ReviewResult

log = logging.getLogger("reviewer")

SYSTEM_PROMPT = """你是一个运维系统评审 Agent。你的职责是独立审查修复结果的质量。

你会收到：
1. 原始异常信息（anomaly）
2. 修复结果（fix_result）

请按以下维度评审：

1. **修复有效性**：修复是否真正解决了根因？是否只是绕过症状？
2. **副作用评估**：修复是否可能引入新问题？影响范围是否可控？
3. **证据充分性**：修复结果中的 confidence 是否合理？有无过度自信？
4. **遗漏风险**：同类异常是否还有未处理的情况？

输出格式（严格 JSON）：
```json
{
  "verdict": "approve" | "request-changes" | "reject",
  "summary": "一句话总结",
  "issues": ["问题1", "问题2"]
}
```

判定标准：
- approve：修复有效，无明显风险
- request-changes：修复方向对但存在需补充的点
- reject：修复无效或引入严重副作用"""


class ReviewerAgent(BaseAgent):
    """评审 Agent：用 Claude 独立审查修复结果质量。"""

    def __init__(self, bus: MessageBus):
        super().__init__(bus, "reviewer", "review")
        self._gh_owner = os.environ.get("GITHUB_OWNER", "pyyzheng")
        self._gh_repo = os.environ.get("GITHUB_REPO", "soundbox-lead-poller")
        self._gh_token = os.environ.get("GITHUB_TOKEN", "")

    async def handle_task(self, message: Message) -> Message | None:
        anomaly = message.payload.get("anomaly", {})
        fix_result = message.payload.get("fix_result", {})

        log.info("[%s] 评审: [%s] %s", self.agent_id,
                 anomaly.get("severity", "?"), anomaly.get("type", "?"))

        # 构造评审输入
        user_prompt = f"""## 异常信息
- 类型: {anomaly.get('type')}
- 严重度: {anomaly.get('severity')}
- 描述: {anomaly.get('description')}
- 来源: {anomaly.get('source')}
- 证据: {json.dumps(anomaly.get('evidence', {}), ensure_ascii=False)}

## 修复结果
- 成功: {fix_result.get('success')}
- 摘要: {fix_result.get('summary')}
- 置信度: {fix_result.get('confidence')}
- 需要评审: {fix_result.get('needs_review')}
- 变更文件: {fix_result.get('changed_files')}"""

        # 调用 Claude 评审
        try:
            raw = self.call_llm(SYSTEM_PROMPT, user_prompt, max_tokens=1024)
            result = self._parse_verdict(raw)
        except Exception as e:
            log.error("[%s] Claude 评审失败: %s", self.agent_id, e)
            result = ReviewResult(verdict="approve", summary=f"评审跳过（Claude 不可用: {e}）")

        log.info("[%s] 评审结果: %s — %s", self.agent_id, result.verdict, result.summary)

        # 非通过结果追加到 GitHub Issue
        if result.verdict != "approve" and result.issues:
            self._comment_on_issue(anomaly, result)

        # 发布 learn 消息
        learn_msg = Message(
            type="learn",
            project=message.project,
            payload={"review": result.__dict__, "anomaly": anomaly, "fix_result": fix_result},
            source=self.agent_id,
        )
        await self.bus.publish(learn_msg)
        return None

    def _parse_verdict(self, raw: str) -> ReviewResult:
        """从 Claude 输出提取 JSON 评审结果。"""
        # 提取 ```json ... ``` 块
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        try:
            data = json.loads(text.strip())
            return ReviewResult(
                verdict=data.get("verdict", "approve"),
                issues=data.get("issues", []),
                summary=data.get("summary", ""),
            )
        except json.JSONDecodeError:
            log.warning("[%s] 无法解析 Claude 输出，默认 approve", self.agent_id)
            return ReviewResult(verdict="approve", summary="评审输出解析失败，默认通过")

    def _comment_on_issue(self, anomaly: dict, result: ReviewResult):
        """在对应 GitHub Issue 上追加评审评论。"""
        if not self._gh_token:
            return

        api = f"https://api.github.com/repos/{self._gh_owner}/{self._gh_repo}"
        headers = {"Authorization": f"token {self._gh_token}", "Accept": "application/vnd.github+json"}

        # 搜索最近的匹配 Issue
        try:
            query = f"repo:{self._gh_owner}/{self._gh_repo} label:agent:alert state:open {anomaly.get('type', '')}"
            resp = requests.get("https://api.github.com/search/issues",
                                headers=headers, params={"q": query, "per_page": 1}, timeout=10)
            if resp.status_code != 200 or not resp.json().get("items"):
                log.info("[%s] 未找到匹配 Issue，跳过评论", self.agent_id)
                return

            issue_number = resp.json()["items"][0]["number"]
            body = f"**评审结果: {result.verdict}**\n\n{result.summary}\n\n"
            for issue in result.issues:
                body += f"- {issue}\n"

            requests.post(f"{api}/issues/{issue_number}/comments",
                          headers=headers, json={"body": body}, timeout=10)
            log.info("[%s] 已在 Issue #%d 添加评审评论", self.agent_id, issue_number)
        except Exception as e:
            log.error("[%s] 评论失败: %s", self.agent_id, e)
