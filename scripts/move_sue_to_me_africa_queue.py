#!/usr/bin/env python3
"""Sue 从欧洲区转入中东/非洲区队列（方案 A）。

变更：
1. 中东/非洲区队列：各渠道追加 Sue 顺位 3；指针最大顺序号 2→3
2. 欧洲区队列：停用 Sue；Kaka→1、Snow→2；指针最大顺序号 3→2（越界归位）
3. 业务员在职情况汇总：Sue 所属部门 → 外贸三部（中东/非洲）

不回修历史线索。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

BASE = os.environ.get("FEISHU_APP_TOKEN", "ZpbUb7SP7azsNasniFjc0bWSnHg")
QUEUE_TABLE = "tblav9GLrm8Vnf1j"
POINTER_TABLE = "tblGWSsPla3eRfuY"
EMPLOYMENT_TABLE = "tbl1VLoTjKb3YIu8"

CHANNELS = ["谷歌", "阿里国际站", "Facebook", "国内渠道", "Outbound渠道"]
ME_QUEUE = "中东/非洲区队列"
EU_QUEUE = "欧洲区队列"


def _cli(*args: str) -> dict:
    cmd = ["lark-cli", "base", *args, "--as", "user", "--format", "json"]
    raw = subprocess.check_output(cmd, text=True)
    return json.loads(raw)


def _upsert(table: str, body: dict, record_id: str | None = None) -> None:
    cmd = [
        "lark-cli",
        "base",
        "+record-upsert",
        "--base-token",
        BASE,
        "--table-id",
        table,
        "--json",
        json.dumps(body, ensure_ascii=False),
        "--as",
        "user",
        "--format",
        "json",
    ]
    if record_id:
        cmd.extend(["--record-id", record_id])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stdout or result.stderr)
    payload = json.loads(result.stdout)
    if not payload.get("ok", True) and "error" in payload:
        raise RuntimeError(payload)


def _cell(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def search_queue(keyword: str) -> list[dict]:
    payload = _cli(
        "+record-search",
        "--base-token",
        BASE,
        "--table-id",
        QUEUE_TABLE,
        "--keyword",
        keyword,
        "--search-field",
        "队列名称",
        "--field-id",
        "队列Key",
        "--field-id",
        "队列名称",
        "--field-id",
        "顺位",
        "--field-id",
        "业务员",
        "--field-id",
        "渠道",
        "--field-id",
        "是否启用",
        "--limit",
        "100",
    )
    rows = []
    for row, rid in zip(payload["data"]["data"], payload["data"]["record_id_list"]):
        rows.append(
            {
                "rid": rid,
                "queue_key": row[0],
                "queue_name": _cell(row[1]),
                "rank": row[2],
                "assignee": _cell(row[3]),
                "channel": _cell(row[4]),
                "enabled": _cell(row[5]),
            }
        )
    return rows


def patch_me_queue() -> None:
    existing = search_queue("中东")
    by_channel: dict[str, set[str]] = {c: set() for c in CHANNELS}
    for r in existing:
        if r["queue_name"] != ME_QUEUE:
            continue
        if r["enabled"] == "启用" and r["channel"] in by_channel:
            by_channel[r["channel"]].add(r["assignee"] or "")

    for channel in CHANNELS:
        if "Sue" in by_channel[channel]:
            print(f"skip ME Sue exists: {channel}")
            continue
        body = {
            "队列Key": f"{channel}|{ME_QUEUE}",
            "队列名称": ME_QUEUE,
            "顺位": 3,
            "业务员": "Sue",
            "渠道": channel,
            "是否启用": "启用",
        }
        _upsert(QUEUE_TABLE, body)
        print(f"created ME Sue rank3: {channel}")


def patch_eu_queue() -> None:
    rows = [r for r in search_queue("欧洲") if r["queue_name"] == EU_QUEUE]
    for r in rows:
        if r["assignee"] == "Sue" and r["enabled"] == "启用":
            _upsert(QUEUE_TABLE, {"是否启用": "停用", "顺位": None}, r["rid"])
            print(f"disabled EU Sue: {r['channel']} {r['rid']}")
        elif r["assignee"] == "Kaka" and r["enabled"] == "启用" and r["rank"] != 1:
            _upsert(QUEUE_TABLE, {"顺位": 1}, r["rid"])
            print(f"EU Kaka →1: {r['channel']}")
        elif r["assignee"] == "Snow" and r["enabled"] == "启用" and r["rank"] != 2:
            _upsert(QUEUE_TABLE, {"顺位": 2}, r["rid"])
            print(f"EU Snow →2: {r['channel']}")


def patch_pointers() -> None:
    payload = _cli(
        "+record-search",
        "--base-token",
        BASE,
        "--table-id",
        POINTER_TABLE,
        "--keyword",
        "队列",
        "--search-field",
        "队列Key",
        "--field-id",
        "队列Key",
        "--field-id",
        "当前顺序号",
        "--field-id",
        "最大顺序号",
        "--field-id",
        "是否启用",
        "--limit",
        "50",
    )
    for row, rid in zip(payload["data"]["data"], payload["data"]["record_id_list"]):
        qk = row[0] or ""
        cur = row[1]
        try:
            cur_i = int(cur) if cur is not None else 0
        except (TypeError, ValueError):
            cur_i = 0
        if ME_QUEUE in qk:
            # max 3; keep current if still valid (1..3), else wrap to 1
            new_cur = cur_i if 1 <= cur_i <= 3 else 1
            if cur_i == 0:
                new_cur = 1
            _upsert(
                POINTER_TABLE,
                {"最大顺序号": "3", "当前顺序号": new_cur},
                rid,
            )
            print(f"pointer ME {qk}: max=3 cur={new_cur}")
        elif EU_QUEUE in qk:
            # max 2; if current was 3 (Sue), wrap to 1
            new_cur = cur_i if 1 <= cur_i <= 2 else 1
            _upsert(
                POINTER_TABLE,
                {"最大顺序号": "2", "当前顺序号": new_cur},
                rid,
            )
            print(f"pointer EU {qk}: max=2 cur={new_cur}")


def patch_employment() -> None:
    payload = _cli(
        "+record-search",
        "--base-token",
        BASE,
        "--table-id",
        EMPLOYMENT_TABLE,
        "--keyword",
        "Sue",
        "--search-field",
        "业务员姓名",
        "--field-id",
        "业务员姓名",
        "--field-id",
        "所属部门",
        "--field-id",
        "是否在职",
        "--limit",
        "5",
    )
    if not payload["data"]["data"]:
        print("WARN: Sue not found in employment table")
        return
    rid = payload["data"]["record_id_list"][0]
    dept = _cell(payload["data"]["data"][0][1])
    if dept == "外贸三部（中东/非洲）":
        print("employment already ME")
        return
    _upsert(EMPLOYMENT_TABLE, {"所属部门": "外贸三部（中东/非洲）"}, rid)
    print(f"employment Sue dept: {dept} → 外贸三部（中东/非洲）")


def main() -> int:
    print("=== 1 ME queue +Sue ===")
    patch_me_queue()
    print("=== 2 EU queue -Sue reorder ===")
    patch_eu_queue()
    print("=== 3 pointers ===")
    patch_pointers()
    print("=== 4 employment ===")
    patch_employment()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
