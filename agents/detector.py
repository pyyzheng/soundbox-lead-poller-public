import logging
import os

import requests

from agents.base_agent import BaseAgent
from agents.bus.message_bus import MessageBus
from agents.types import Message, Anomaly, DetectorFn

log = logging.getLogger("detector")


class DetectorAgent(BaseAgent):
    """检测 Agent：运行所有注册的检测器，收集异常后创建 Issue 并发布 fix 任务。"""

    # 可触发 auto-fix workflow 的异常类型
    AUTO_FIXABLE_TYPES = frozenset(["spam_leaked", "format_anomaly"])
    # 需人工介入的类型（添加 needs-human 标签方便筛选）
    NEEDS_HUMAN_TYPES = frozenset([
        "github_consecutive_failures", "gmail_oauth_expired",
        "unassigned", "pending_confirmation",
    ])

    def __init__(self, bus: MessageBus):
        super().__init__(bus, "detector", "detect")
        self._detectors: dict[str, DetectorFn] = {}
        self._gh_owner = os.environ.get("GITHUB_OWNER", "pyyzheng")
        self._gh_repo = os.environ.get("GITHUB_REPO", "soundbox-lead-poller")
        self._gh_token = os.environ.get("GITHUB_TOKEN", "")

    def register_detector(self, name: str, fn: DetectorFn):
        self._detectors[name] = fn
        log.info("[%s] 注册检测器: %s", self.agent_id, name)

    async def handle_task(self, message: Message) -> Message | None:
        log.info("[%s] 收到检测任务 (%s)", self.agent_id, message.project)

        all_anomalies: list[Anomaly] = []
        for name, fn in self._detectors.items():
            try:
                anomalies = await fn(message.project, message.payload)
                all_anomalies.extend(anomalies)
                log.info("[%s] 检测器 %s 发现 %d 条异常", self.agent_id, name, len(anomalies))
            except Exception as e:
                log.error("[%s] 检测器 %s 执行失败: %s", self.agent_id, name, e)

        if not all_anomalies:
            log.info("[%s] 无异常，跳过后续处理", self.agent_id)
            return None

        log.info("[%s] 共发现 %d 条异常，全部转发 fixer（由策略层决定是否跳过）",
                 self.agent_id, len(all_anomalies))

        # 创建 GitHub Issue（告警闭环）
        self._report_anomalies(all_anomalies, message.project)

        # 所有异常都发 fixer，由策略层 SKIP_TYPES 判断能否自动修复
        fix_msg = Message(
            type="fix",
            project=message.project,
            payload={"anomalies": [a.__dict__ for a in all_anomalies], "scan_id": message.payload.get("scan_id")},
            source=self.agent_id,
        )
        await self.bus.publish(fix_msg)
        return None

    def _report_anomalies(self, anomalies: list[Anomaly], project: str):
        """检测到异常后创建 GitHub Issue，含标签路由和去重。"""
        if not self._gh_token:
            log.info("[%s] 无 GITHUB_TOKEN，跳过 Issue 创建", self.agent_id)
            return

        by_severity: dict[str, list[Anomaly]] = {}
        for a in anomalies:
            by_severity.setdefault(a.severity, []).append(a)

        severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}

        # 去重：按异常类型+关键描述分组，检查是否已有 open Issue
        anomaly_types = {a.type for a in anomalies}
        existing = self._find_existing_issues(anomaly_types)

        # 分为可追加评论的和需新建的
        to_comment: dict[int, list[Anomaly]] = {}
        new_anomalies: list[Anomaly] = []

        for a in anomalies:
            # 用 type+description 前 60 字符作为去重 key
            dedup_key = f"{a.type}:{a.description[:60]}"
            if dedup_key in existing:
                issue_num = existing[dedup_key]
                to_comment.setdefault(issue_num, []).append(a)
            else:
                new_anomalies.append(a)

        # 追加评论到已有 Issue
        for issue_num, dup_anomalies in to_comment.items():
            comment_lines = [f"**{len(dup_anomalies)} 条异常仍存在**（{project} 扫描）\n"]
            for a in dup_anomalies:
                emoji = severity_emoji.get(a.severity, "⚪")
                comment_lines.append(f"- {emoji} [{a.severity}] {a.type}: {a.description[:100]}")
            self._comment_on_issue(issue_num, "\n".join(comment_lines))

        # 新建 Issue
        if not new_anomalies:
            log.info("[%s] 所有异常均已有对应 Issue，仅追加评论", self.agent_id)
            return

        title = f"[{project}] 检测到 {len(new_anomalies)} 条异常"
        lines = [f"**项目**: {project}\n"]
        lines.append("| # | 严重度 | 类型 | 来源 | 描述 |")
        lines.append("|---|--------|------|------|------|")
        for i, a in enumerate(new_anomalies, 1):
            emoji = severity_emoji.get(a.severity, "⚪")
            desc = a.description[:80].replace("|", "\\|")
            lines.append(f"| {i} | {emoji} {a.severity} | {a.type} | {a.source} | {desc} |")

        lines.append("\n**详细证据：**\n")
        for i, a in enumerate(new_anomalies, 1):
            lines.append(f"### {i}. [{a.severity}] {a.type}")
            for k, v in a.evidence.items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")

        body = "\n".join(lines)
        top_severity = max(new_anomalies, key=lambda a: ["low", "medium", "high", "critical"].index(a.severity)).severity
        labels = ["agent:alert", f"severity:{top_severity}"]

        # 标签路由：可自动修复的加 auto-detected 触发 auto-fix workflow
        has_auto_fixable = any(a.type in self.AUTO_FIXABLE_TYPES for a in new_anomalies)
        if has_auto_fixable:
            labels.append("auto-detected")

        has_needs_human = any(a.type in self.NEEDS_HUMAN_TYPES for a in new_anomalies)
        if has_needs_human:
            labels.append("needs-human")

        self._create_issue(title, body, labels)

    def _find_existing_issues(self, anomaly_types: set[str]) -> dict[str, int]:
        """搜索已有 open Issue，返回 {dedup_key: issue_number}。"""
        api = f"https://api.github.com/repos/{self._gh_owner}/{self._gh_repo}"
        headers = {"Authorization": f"token {self._gh_token}", "Accept": "application/vnd.github+json"}
        existing: dict[str, int] = {}

        try:
            # 搜索最近 7 天内带 agent:alert 标签的 open Issue
            resp = requests.get(
                f"{api}/issues",
                params={"labels": "agent:alert", "state": "open", "per_page": 30},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                log.warning("[%s] 搜索已有 Issue 失败: %d", self.agent_id, resp.status_code)
                return existing

            for issue in resp.json():
                body = issue.get("body", "") or ""
                number = issue["number"]
                # 从 Issue body 中提取已有异常的 type:description 组合
                for line in body.split("\n"):
                    line = line.strip()
                    # 匹配表格行: | 1 | 🔵 low | pending_confirmation | ... |
                    if line.startswith("|") and "严重度" not in line and "---" not in line:
                        parts = [p.strip() for p in line.split("|")]
                        # parts: ['', '#', 'severity', 'type', 'source', 'desc', '']
                        if len(parts) >= 6:
                            atype = parts[3].strip()
                            adesc = parts[5].strip()[:60]
                            if atype in anomaly_types:
                                existing[f"{atype}:{adesc}"] = number
        except Exception as e:
            log.warning("[%s] 搜索已有 Issue 异常: %s", self.agent_id, e)

        log.info("[%s] 找到 %d 条已有 Issue 去重记录", self.agent_id, len(existing))
        return existing

    def _comment_on_issue(self, issue_number: int, comment: str):
        """在已有 Issue 上追加评论。"""
        api = f"https://api.github.com/repos/{self._gh_owner}/{self._gh_repo}"
        headers = {"Authorization": f"token {self._gh_token}", "Accept": "application/vnd.github+json"}
        try:
            resp = requests.post(
                f"{api}/issues/{issue_number}/comments",
                headers=headers, json={"body": comment}, timeout=15,
            )
            if resp.status_code in (200, 201):
                log.info("[%s] Issue #%d 追加评论成功", self.agent_id, issue_number)
            else:
                log.warning("[%s] Issue #%d 评论失败: %d", self.agent_id, issue_number, resp.status_code)
        except Exception as e:
            log.warning("[%s] Issue 评论异常: %s", self.agent_id, e)

    def _create_issue(self, title: str, body: str, labels: list[str]):
        api = f"https://api.github.com/repos/{self._gh_owner}/{self._gh_repo}"
        headers = {"Authorization": f"token {self._gh_token}", "Accept": "application/vnd.github+json"}

        try:
            for label in labels:
                requests.post(f"{api}/labels", headers=headers,
                              json={"name": label, "color": "c5def5"}, timeout=10)
            resp = requests.post(f"{api}/issues", headers=headers,
                                 json={"title": title, "body": body, "labels": labels},
                                 timeout=15)
            if resp.status_code in (200, 201):
                number = resp.json()["number"]
                log.info("[%s] Issue #%d 已创建: %s", self.agent_id, number, title)
            else:
                log.error("[%s] Issue 创建失败: %d %s", self.agent_id, resp.status_code, resp.text[:200])
        except Exception as e:
            log.error("[%s] Issue 创建异常: %s", self.agent_id, e)
