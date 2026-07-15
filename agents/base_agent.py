import logging
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from agents.bus.message_bus import MessageBus
from agents.types import Message

# 确保 lib/ 可导入
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

log = logging.getLogger("agent")


class BaseAgent(ABC):
    def __init__(self, bus: MessageBus, agent_id: str, task_type: str):
        self.bus = bus
        self.agent_id = agent_id
        self.task_type = task_type

    @abstractmethod
    async def handle_task(self, message: Message) -> Message | None: ...

    async def start(self):
        self.bus.subscribe(self.task_type, self.handle_task)
        log.info("[%s] started, listening for '%s'", self.agent_id, self.task_type)

    async def stop(self):
        log.info("[%s] stopped", self.agent_id)

    def call_llm(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 4096) -> str:
        """调用智谱 GLM API。环境变量 ZHIPU_API_KEY 必须配置。"""
        from auto_fix_utils import call_zhipu_api
        result = call_zhipu_api(system_prompt, user_prompt, max_tokens=max_tokens)
        return result.get("content", "")
