import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod

import requests

from agents.types import Message, MessageHandler

log = logging.getLogger("bus")


class MessageBus(ABC):
    @abstractmethod
    async def start(self): ...

    @abstractmethod
    async def stop(self): ...

    @abstractmethod
    async def publish(self, message: Message): ...

    @abstractmethod
    def subscribe(self, msg_type: str, handler: MessageHandler): ...


class InMemoryBus(MessageBus):
    """开发模式：asyncio.Queue 实现，消息即时传递。"""

    def __init__(self):
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._queue: asyncio.Queue | None = None
        self._running = False

    async def start(self):
        self._queue = asyncio.Queue()
        self._running = True
        asyncio.create_task(self._dispatch_loop())

    async def stop(self):
        self._running = False
        if self._queue:
            await self._queue.put(None)  # sentinel

    async def publish(self, message: Message):
        if self._queue:
            await self._queue.put(message)
            log.info("[InMemory] publish %s (%s)", message.type, message.id)

    def subscribe(self, msg_type: str, handler: MessageHandler):
        self._handlers.setdefault(msg_type, []).append(handler)
        log.info("[InMemory] subscribe %s → %s", msg_type, getattr(handler, '__self__', handler))

    async def _dispatch_loop(self):
        while self._running:
            msg = await self._queue.get()
            if msg is None:
                break
            handlers = self._handlers.get(msg.type, [])
            for handler in handlers:
                try:
                    await handler(msg)
                except Exception as e:
                    log.error("[InMemory] handler error: %s", e)


class GitHubIssueBus(MessageBus):
    """生产模式：GitHub Issue 标签路由。

    - publish → 创建/更新 Issue 并打标签
    - subscribe → 轮询带特定标签的 Issue
    """

    def __init__(self, owner: str, repo: str, token: str, poll_interval_ms: int = 30000):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.poll_interval_s = poll_interval_ms / 1000
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._running = False
        self._processed_ids: set[str] = set()
        self._max_processed = 1000

    async def start(self):
        self._running = True
        asyncio.create_task(self._poll_loop())
        log.info("[GitHub] bus started (%s/%s, poll=%ds)", self.owner, self.repo, self.poll_interval_s)

    async def stop(self):
        self._running = False

    async def publish(self, message: Message):
        # 创建 Issue 并打上 agent:{type} 标签
        api = f"https://api.github.com/repos/{self.owner}/{self.repo}"
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github+json"}

        label = f"agent:{message.type}"
        title = f"[{message.type}] {message.project} — {message.id}"
        body = (f"**Source**: {message.source}\n**Timestamp**: {message.timestamp}\n"
                f"**Project**: {message.project}\n\n"
                f"```json\n{json.dumps(message.payload, ensure_ascii=False, indent=2)}\n```")

        try:
            # 确保标签存在
            requests.post(f"{api}/labels", headers=headers,
                          json={"name": label, "color": "c5def5"}, timeout=10)
            # 创建 Issue
            resp = requests.post(f"{api}/issues", headers=headers,
                                 json={"title": title, "body": body, "labels": [label, "agent-message"]},
                                 timeout=15)
            if resp.status_code in (200, 201):
                log.info("[GitHub] Issue #%d created (%s)", resp.json()["number"], label)
            else:
                log.error("[GitHub] publish failed: %d %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.error("[GitHub] publish error: %s", e)

    def subscribe(self, msg_type: str, handler: MessageHandler):
        self._handlers.setdefault(msg_type, []).append(handler)
        log.info("[GitHub] subscribe %s", msg_type)

    async def _poll_loop(self):
        """轮询带 agent:{type} 标签的 Issue，分发给对应 handler。"""
        import json

        api = f"https://api.github.com/repos/{self.owner}/{self.repo}/issues"
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github+json"}

        while self._running:
            for msg_type in self._handlers:
                label = f"agent:{msg_type}"
                try:
                    resp = requests.get(api, headers=headers,
                                        params={"labels": label, "state": "open", "per_page": 10},
                                        timeout=15)
                    if resp.status_code != 200:
                        continue
                    for issue in resp.json():
                        iid = str(issue["number"])
                        if iid in self._processed_ids:
                            continue
                        self._processed_ids.add(iid)
                        # 防止内存泄漏：超过上限时清理最早的
                        if len(self._processed_ids) > self._max_processed:
                            self._processed_ids = set(list(self._processed_ids)[-self._max_processed // 2:])
                        # 从 Issue body 还原完整 Message
                        payload, project = self._parse_issue_body(issue.get("body", ""))
                        msg = Message(
                            type=msg_type, project=project,
                            payload={**payload, "issue_number": iid, "url": issue["html_url"]},
                            source="github",
                        )
                        for handler in self._handlers[msg_type]:
                            try:
                                await handler(msg)
                            except Exception as e:
                                log.error("[GitHub] handler error: %s", e)
                except Exception as e:
                    log.error("[GitHub] poll error: %s", e)

            await asyncio.sleep(self.poll_interval_s)

    def _parse_issue_body(self, body: str) -> tuple[dict, str]:
        """从 Issue body 提取 JSON payload 和 project。"""
        payload, project = {}, ""
        try:
            # 提取 project
            m = re.search(r"\*\*Project\*\*:\s*(.+)", body)
            if m:
                project = m.group(1).strip()
            # 提取 ```json ... ``` 块
            m = re.search(r"```json\s*([\s\S]*?)```", body)
            if m:
                payload = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, AttributeError):
            log.warning("[GitHub] Issue body 解析失败，payload 为空")
        return payload, project
