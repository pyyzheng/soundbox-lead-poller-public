import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agents.base_agent import BaseAgent
from agents.bus.message_bus import MessageBus
from agents.types import Message

log = logging.getLogger("learner")


class LearnerAgent(BaseAgent):
    """学习 Agent：统计异常类型出现次数和修复成功率，写入 metrics.json。"""

    def __init__(self, bus: MessageBus, repo_path: str = "."):
        super().__init__(bus, "learner", "learn")
        self.metrics_path = Path(repo_path) / "metrics.json"

    async def handle_task(self, message: Message) -> Message | None:
        review = message.payload.get("review", {})
        anomaly = message.payload.get("anomaly", {})
        fix_result = message.payload.get("fix_result", {})

        anomaly_type = anomaly.get("type", "unknown")
        fix_success = fix_result.get("success", False)
        verdict = review.get("verdict", "unknown")

        log.info("[%s] 记录: type=%s fix=%s verdict=%s",
                 self.agent_id, anomaly_type, fix_success, verdict)

        self._update_metrics(anomaly_type, fix_success, verdict)
        return None

    def _update_metrics(self, anomaly_type: str, fix_success: bool, verdict: str):
        metrics = self._load_metrics()

        # 全局统计：total_anomalies_processed 是异常处理次数（每条异常 +1）
        metrics["total_anomalies_processed"] = metrics.get("total_anomalies_processed", 0) + 1
        metrics["last_updated"] = datetime.now(timezone.utc).isoformat()

        # 按异常类型统计
        types = metrics.setdefault("by_type", {})
        entry = types.setdefault(anomaly_type, {
            "count": 0, "fix_attempts": 0, "fix_successes": 0,
            "verdicts": {"approve": 0, "request-changes": 0, "reject": 0},
        })
        entry["count"] += 1

        if fix_success or verdict != "unknown":
            entry["fix_attempts"] += 1
            if fix_success:
                entry["fix_successes"] += 1

        if verdict in entry["verdicts"]:
            entry["verdicts"][verdict] += 1

        if entry["fix_attempts"] > 0:
            entry["fix_rate"] = round(entry["fix_successes"] / entry["fix_attempts"], 3)

        # 按日期统计（最近 30 天）
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = metrics.setdefault("daily", {})
        day_entry = daily.setdefault(today, {"count": 0, "types": {}})
        day_entry["count"] += 1
        day_entry["types"][anomaly_type] = day_entry["types"].get(anomaly_type, 0) + 1

        # 清理超过 30 天的日志
        cutoff = (datetime.now(timezone.utc).timestamp() - 30 * 86400)
        stale = [d for d in daily
                 if datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() < cutoff]
        for d in stale:
            del daily[d]

        self._save_metrics(metrics)
        log.info("[%s] metrics 已更新: %s (累计 %d 条)",
                 self.agent_id, anomaly_type, entry["count"])

    def _load_metrics(self) -> dict:
        if self.metrics_path.exists():
            try:
                return json.loads(self.metrics_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("[%s] metrics.json 损坏，重建", self.agent_id)
        return {"total_anomalies_processed": 0, "by_type": {}, "daily": {}}

    def _save_metrics(self, metrics: dict):
        self.metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
