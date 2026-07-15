#!/usr/bin/env python3
"""
多 Agent 运维系统 — 启动入口

使用方式：
  开发模式（内存总线）: python -m agents
  单次扫描:            python -m agents --scan --project google-leads
  生产模式（GitHub Issue）: ZHIPU_API_KEY=xxx GITHUB_TOKEN=xxx python -m agents --prod
"""
import argparse
import asyncio
import logging
import os
import signal
import sys

from agents.bus.message_bus import MessageBus, InMemoryBus, GitHubIssueBus
from agents.orchestrator import OrchestratorAgent
from agents.detector import DetectorAgent
from agents.fixer import FixerAgent
from agents.reviewer import ReviewerAgent
from agents.learner import LearnerAgent
from agents.plugins.google_leads import register as register_google_leads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


def load_config() -> dict:
    return {
        "mode": "production" if "--prod" in sys.argv else "development",
        "github": {
            "owner": os.environ.get("GITHUB_OWNER", "pyyzheng"),
            "repo": os.environ.get("GITHUB_REPO", "soundbox-lead-poller"),
            "token": os.environ.get("GITHUB_TOKEN", ""),
        },
        "repo_path": os.environ.get("REPO_PATH", "."),
        "poll_interval_ms": int(os.environ.get("POLL_INTERVAL_MS", "30000")),
    }


async def shutdown(bus: MessageBus, agents: list):
    print("\n[main] 正在关闭...")
    for agent in agents:
        await agent.stop()
    await bus.stop()
    print("[main] 已安全关闭")


async def main():
    parser = argparse.ArgumentParser(description="多 Agent 运维系统")
    parser.add_argument("--scan", action="store_true", help="单次扫描模式")
    parser.add_argument("--project", default="google-leads", help="项目名称")
    parser.add_argument("--prod", action="store_true", help="生产模式（GitHub Issue Bus）")
    args = parser.parse_args()

    config = load_config()

    print(f"\n=== 多 Agent 运维系统 ===")
    print(f"模式: {config['mode']}")
    print(f"时间: {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()}\n")

    # 1. 创建消息总线
    if config["mode"] == "production":
        bus = GitHubIssueBus(
            owner=config["github"]["owner"],
            repo=config["github"]["repo"],
            token=config["github"]["token"],
            poll_interval_ms=config["poll_interval_ms"],
        )
        print(f"[main] 使用 GitHub Issue Bus ({config['github']['owner']}/{config['github']['repo']})")
    else:
        bus = InMemoryBus()
        print("[main] 使用 InMemory Bus（开发模式）")

    # 2. 创建所有 agent
    orchestrator = OrchestratorAgent(bus)
    detector = DetectorAgent(bus)
    fixer = FixerAgent(bus)
    reviewer = ReviewerAgent(bus)
    learner = LearnerAgent(bus, config["repo_path"])

    # 3. 注册项目特定逻辑
    register_google_leads(detector, fixer)

    # 4. 启动所有 agent
    agents = [orchestrator, detector, fixer, reviewer, learner]
    for agent in agents:
        await agent.start()

    # 5. 启动消息总线
    await bus.start()

    print(f"\n[main] 所有 agent 已启动，系统就绪\n")

    # 6. 单次扫描模式
    if args.scan:
        print(f"[main] 触发单次扫描: {args.project}")
        await orchestrator.trigger_scan(args.project)

        # 等待消息处理完成
        await asyncio.sleep(3)
        await shutdown(bus, agents)
        return

    # 7. 优雅关闭
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(bus, agents)))

    # 持续运行
    print("[main] 持续运行中，Ctrl+C 退出\n")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
