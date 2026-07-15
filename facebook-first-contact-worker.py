#!/usr/bin/env python3
"""
facebook-first-contact-worker.py — Facebook 历史人工分配线索 Gmail 首联 Worker

新 Facebook Lead Ads 线索已改为直接写 分配方式=自动，和 Google 一样由飞书
自动分配。本 worker 仅保留给历史 分配方式=人工 的冷线索发 Gmail 首联邮件，
存 Gmail_Thread_ID，待客户回复后由飞书自动化触发正式分配。

查询条件（与 cloud-auto-reply-worker 的 Stephanie 查询互斥，互不干扰）：
  - Channels（渠道） = Facebook
  - Auto-Reply Status = Pending
  - 分配方式 = 人工

复用 cloud-auto-reply-worker 的 parse_record_context / process_record / get_gmail_service
（文件名含横杠不能直接 import，用 importlib 加载）。process_record 对无 gmail_msg_id
的记录走 send_standalone_email 分支发独立邮件。
"""

import os
import sys
import logging
import importlib.util
from pathlib import Path
from datetime import datetime, timezone

import requests

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "lib"))
from assignment_fields import FIELD_ASSIGN_METHOD, WRITE_ASSIGN_MANUAL  # noqa: E402

# 加载 cloud-auto-reply-worker（文件名含横杠，不能直接 import）
_spec = importlib.util.spec_from_file_location(
    "cloud_auto_reply_worker", _HERE / "cloud-auto-reply-worker.py"
)
carw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(carw)

from feishu_utils import get_feishu_token

log = logging.getLogger("fb-first-contact")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# ── 配置 ──────────────────────────────────────────────────────────────────────
WORKER_DRY_RUN = os.environ.get("FB_FIRST_CONTACT_DRY_RUN", "true") == "true"
WORKER_MAX_RECORDS = int(os.environ.get("FB_FIRST_CONTACT_MAX_RECORDS", "20"))

FEISHU_APP_TOKEN = os.environ.get("FEISHU_APP_TOKEN", carw.FEISHU_APP_TOKEN)
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID", carw.FEISHU_TABLE_ID)

FIELD_CHANNELS = "Channels（渠道）"
FIELD_AUTOREPLY = "Auto-Reply Status"


def fetch_pending_facebook(token: str) -> list:
    """查询历史待首联的 Facebook 冷线索：Facebook + Pending + 分配方式=人工。"""
    conditions = [
        {"field_name": FIELD_CHANNELS, "operator": "is", "value": ["Facebook"]},
        {"field_name": FIELD_AUTOREPLY, "operator": "is", "value": ["Pending"]},
        {"field_name": FIELD_ASSIGN_METHOD, "operator": "is", "value": [WRITE_ASSIGN_MANUAL]},
    ]
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "filter": {"conjunction": "and", "conditions": conditions},
            "page_size": WORKER_MAX_RECORDS,
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书搜索失败: {data}")
    return data.get("data", {}).get("items", [])


def main():
    log.info("=== Facebook First-Contact Worker 启动 (UTC %s) ===",
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    log.info("配置: dry_run=%s max=%d", WORKER_DRY_RUN, WORKER_MAX_RECORDS)

    # carw.process_record 用模块级 WORKER_DRY_RUN 控制是否真发邮件
    carw.WORKER_DRY_RUN = WORKER_DRY_RUN

    token = get_feishu_token()
    records = fetch_pending_facebook(token)
    if not records:
        log.info("无待首联 Facebook 线索")
        return
    log.info("找到 %d 条待首联 Facebook 线索", len(records))

    service = carw.get_gmail_service()

    results = {}
    for rec in records:
        ctx = carw.parse_record_context(rec)
        if not ctx:
            results["Skip"] = results.get("Skip", 0) + 1
            continue
        if not ctx["customer_email"]:
            log.error("Facebook 线索无客户邮箱，跳过: record=%s", ctx["record_id"])
            carw.update_feishu_autoreply(
                token, ctx["record_id"], "Error", error="no customer email"
            )
            results["Error"] = results.get("Error", 0) + 1
            continue
        first_contact_done = carw._extract_text(
            rec.get("fields", {}).get(carw.FIELD_FIRST_CONTACT_DONE, "")
        ) == "Yes"
        try:
            # 无 gmail_msg_id → process_record 走 send_standalone_email 分支
            status = carw.process_record(
                service, token, ctx, first_contact_done=first_contact_done
            )
            results[status] = results.get(status, 0) + 1
        except Exception as e:
            log.error("处理失败: record=%s | %s", ctx["record_id"], e)
            carw.update_feishu_autoreply(
                token, ctx["record_id"], "Error", error=str(e)[:200]
            )
            results["Error"] = results.get("Error", 0) + 1

    log.info("=== Worker 完成: %s ===", results)


if __name__ == "__main__":
    main()
