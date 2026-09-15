import logging

from agents.base_agent import BaseAgent
from agents.bus.message_bus import MessageBus
from agents.types import Message

log = logging.getLogger("orchestrator")


class OrchestratorAgent(BaseAgent):
    """编排 Agent：收到 scan 信号后发布 detect 任务。"""

    def __init__(self, bus: MessageBus):
        super().__init__(bus, "orchestrator", "scan")

    async def handle_task(self, message: Message) -> Message | None:
        log.info("[%s] 收到扫描请求: %s", self.agent_id, message.project)
        detect_msg = Message(
            type="detect",
            project=message.project,
            payload={"scan_id": message.id},
            source=self.agent_id,
        )
        await self.bus.publish(detect_msg)
        log.info("[%s] 已发布 detect 任务 (%s)", self.agent_id, detect_msg.id)
        return None

    async def trigger_scan(self, project: str):
        """外部调用的扫描触发方法。"""
        scan_msg = Message(type="scan", project=project, source="cli")
        log.info("[%s] 触发扫描: %s", self.agent_id, project)
        await self.handle_task(scan_msg)
