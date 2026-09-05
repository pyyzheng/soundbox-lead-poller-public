#!/usr/bin/env python3
"""停用中东/亚洲/南美非洲公区的旧渠道顺序队列成员，避免飞书渠道轮转工作流与按天最少双写。

默认 dry-run。真正执行：CUTOVER_APPLY=true

不改历史线索跟进人；仅改「渠道顺序队列表」是否启用。
欧洲公区/英德/俄白等队列不动。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from assignment_fields import CHANNEL_QUEUE_TABLE  # noqa: E402
from daily_least_assign import (  # noqa: E402
    QUEUE_SUFFIX_ASIA,
    QUEUE_SUFFIX_ME,
    QUEUE_SUFFIX_ME_LEGACY,
    QUEUE_SUFFIX_PUBLIC,
)
from feishu_utils import FEISHU_APP_TOKEN, extract_text, feishu_api, get_feishu_token  # noqa: E402

APPLY = os.environ.get("CUTOVER_APPLY", "false").lower() == "true"
TARGET_SUFFIXES = QUEUE_SUFFIX_ME | QUEUE_SUFFIX_ASIA | QUEUE_SUFFIX_PUBLIC | QUEUE_SUFFIX_ME_LEGACY


def _search(token: str) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{CHANNEL_QUEUE_TABLE}/records/search?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api(
            "POST",
            url,
            token=token,
            json={"field_names": ["队列Key", "队列名称", "渠道", "业务员", "顺位", "是否启用"]},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        body = data.get("data", {})
        items.extend(body.get("items", []))
        if not body.get("has_more"):
            break
        page_token = body.get("page_token", "")
        if not page_token:
            break
    return items


def _suffix(queue_key: str, queue_name: str) -> str:
    key = (queue_key or "").strip()
    if "|" in key:
        return key.split("|", 1)[1].strip()
    return (queue_name or "").strip()


def main() -> int:
    token = get_feishu_token()
    rows = _search(token)
    targets = []
    for item in rows:
        fields = item.get("fields", {}) or {}
        qk = extract_text(fields.get("队列Key", ""))
        qn = extract_text(fields.get("队列名称", ""))
        status = extract_text(fields.get("是否启用", ""))
        suf = _suffix(qk, qn)
        if suf not in TARGET_SUFFIXES:
            continue
        if status == "停用":
            continue
        targets.append((item.get("record_id", ""), qk or qn, extract_text(fields.get("业务员", "")), status))

    print(f"将停用 {len(targets)} 条渠道顺序队列行（中东/亚洲/公区） apply={APPLY}")
    for rid, key, person, status in targets[:30]:
        print(f"  {key} {person} [{status}] {rid}")
    if len(targets) > 30:
        print(f"  ... +{len(targets) - 30} more")

    if not APPLY:
        print("dry-run 结束。执行：CUTOVER_APPLY=true python scripts/cutover_daily_least_queues.py")
        return 0

    ok = 0
    for rid, key, person, _ in targets:
        resp = feishu_api(
            "PUT",
            (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
                f"/tables/{CHANNEL_QUEUE_TABLE}/records/{rid}"
            ),
            token=token,
            json={"fields": {"是否启用": "停用"}},
        )
        if resp.json().get("code") == 0:
            ok += 1
            print(f"停用 OK {key} {person}")
        else:
            print(f"停用 FAIL {key} {resp.json()}")
    print(f"完成 stopped={ok}/{len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
