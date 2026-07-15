import logging

from agents.base_agent import BaseAgent
from agents.bus.message_bus import MessageBus
from agents.types import Message, Anomaly, FixResult, FixStrategyFn

log = logging.getLogger("fixer")


class FixerAgent(BaseAgent):
    """修复 Agent：根据策略映射选择修复策略执行。"""

    def __init__(self, bus: MessageBus):
        super().__init__(bus, "fixer", "fix")
        self._strategies: dict[str, FixStrategyFn] = {}
        self._type_to_strategy: dict[str, str] = {}

    def register_strategy(self, name: str, fn: FixStrategyFn):
        self._strategies[name] = fn
        log.info("[%s] 注册修复策略: %s", self.agent_id, name)

    def set_strategy_mapping(self, anomaly_type: str, strategy_name: str):
        self._type_to_strategy[anomaly_type] = strategy_name
        log.info("[%s] 映射: %s → %s", self.agent_id, anomaly_type, strategy_name)

    async def handle_task(self, message: Message) -> Message | None:
        raw_anomalies = message.payload.get("anomalies", [])
        anomalies = [Anomaly(**a) for a in raw_anomalies]
        log.info("[%s] 收到修复任务: %d 条异常", self.agent_id, len(anomalies))

        # 按异常类型分组，选对应策略
        for anomaly in anomalies:
            strategy_name = self._type_to_strategy.get(anomaly.type)
            if not strategy_name or strategy_name not in self._strategies:
                log.warning("[%s] 无匹配策略: %s", self.agent_id, anomaly.type)
                continue
            try:
                result = await self._strategies[strategy_name]([anomaly], message.payload)
                log.info("[%s] 策略 %s 执行结果: success=%s confidence=%.2f",
                         self.agent_id, strategy_name, result.success, result.confidence)
                if result.needs_review:
                    review_msg = Message(
                        type="review",
                        project=message.project,
                        payload={"fix_result": result.__dict__, "anomaly": anomaly.__dict__},
                        source=self.agent_id,
                    )
                    await self.bus.publish(review_msg)
            except Exception as e:
                log.error("[%s] 策略 %s 执行失败: %s", self.agent_id, strategy_name, e)

        return None
