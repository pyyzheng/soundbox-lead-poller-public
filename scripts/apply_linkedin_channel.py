#!/usr/bin/env python3
"""新增主渠道 LinkedIn（领英），按启用中的 Facebook 队列克隆人员/顺位 + 指针。

参考 Facebook：
- 主表 Channels（渠道）增加选项
- 渠道顺序队列表.渠道 增加选项
- 每个启用区域队列复制 Facebook 同顺位业务员为 LinkedIn|{队列}
- 队列指针表为每个 LinkedIn|{队列} 建指针（当前顺序号=1）

仅配置分配池；不改历史线索跟进人。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import defaultdict
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
AS = ["--as", "user"]

T_MAIN = "tbluuuXn9WexH8LV"
T_QUEUE = "tblav9GLrm8Vnf1j"
T_PTR = "tblGWSsPla3eRfuY"

SOURCE_CHANNEL = "Facebook"
NEW_CHANNEL = "LinkedIn"  # 领英；用户口语 Linkedln 用别名映射

HUES = ["Blue", "Orange", "Wathet", "Yellow", "Turquoise", "Red", "Purple", "Green", "Carmine", "Lime", "Gray"]
DRY_RUN = "--dry-run" in sys.argv


def run(args: list[str], check: bool = True) -> dict[str, Any]:
    r = subprocess.run(["lark-cli", *args], capture_output=True, text=True)
    if r.returncode != 0 and check:
        print("CMD FAIL", " ".join(args[:10]), file=sys.stderr)
        print((r.stderr or r.stdout)[:2500], file=sys.stderr)
        raise SystemExit(r.returncode)
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": r.stdout, "stderr": r.stderr, "code": r.returncode}


def cell(v: Any) -> str:
    if v is None or v == []:
        return ""
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        if not v:
            return ""
        if isinstance(v[0], dict):
            return str(v[0].get("text") or v[0].get("name") or v[0])
        return str(v[0])
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v)
    return str(v)


def field_get(table_id: str, field_id: str) -> dict[str, Any]:
    out = run(
        [
            "base", "+field-get",
            "--base-token", BASE, "--table-id", table_id,
            "--field-id", field_id, *AS, "--format", "json",
        ]
    )
    return out["data"]["field"]


def field_update(table_id: str, field_id: str, payload: dict[str, Any]) -> None:
    if DRY_RUN:
        print(f"  [dry] field-update {table_id} {field_id}")
        return
    out = run(
        [
            "base", "+field-update",
            "--base-token", BASE, "--table-id", table_id,
            "--field-id", field_id,
            "--json", json.dumps(payload, ensure_ascii=False),
            "--yes", *AS, "--format", "json",
        ]
    )
    if out.get("ok") is False:
        print(out)
        raise SystemExit(1)


def ensure_select_options(table_id: str, field_id: str, extra_names: list[str]) -> None:
    f = field_get(table_id, field_id)
    opts = list(f.get("options") or [])
    existing = {o["name"] for o in opts}
    add = [n for n in extra_names if n not in existing]
    if not add:
        print(f"  options ok {f.get('name')}: +0")
        return
    i0 = len(opts)
    for j, name in enumerate(add):
        opts.append({"name": name, "hue": HUES[(i0 + j) % len(HUES)], "lightness": "Lighter"})
    payload: dict[str, Any] = {
        "name": f["name"],
        "type": "select",
        "multiple": bool(f.get("multiple")),
        "options": opts,
    }
    if f.get("dynamic_options_source"):
        payload["dynamic_options_source"] = f["dynamic_options_source"]
    if f.get("description"):
        payload["description"] = f["description"]
    field_update(table_id, field_id, payload)
    print(f"  added to {f.get('name')}: {add}")


def search_channel_rows(channel: str) -> list[dict[str, Any]]:
    out = run(
        [
            "base", "+record-search",
            "--base-token", BASE, "--table-id", T_QUEUE,
            "--keyword", channel, "--search-field", "渠道",
            "--limit", "200", *AS, "--format", "json",
        ]
    )
    data = out["data"]
    fields = data["fields"]
    rows = []
    for row, rid in zip(data["data"], data["record_id_list"]):
        m = {k: cell(v) for k, v in zip(fields, row)}
        m["_id"] = rid
        if m.get("渠道") == channel:
            rows.append(m)
    return rows


def list_pointers() -> list[dict[str, Any]]:
    out = run(
        [
            "base", "+record-list",
            "--base-token", BASE, "--table-id", T_PTR,
            "--limit", "200", *AS, "--format", "json",
        ],
        check=False,
    )
    data = out.get("data") or {}
    rows = []
    if isinstance(data, dict) and "fields" in data and "data" in data:
        colnames = data["fields"]
        for row, rid in zip(data["data"], data.get("record_id_list") or []):
            m = {k: cell(v) for k, v in zip(colnames, row)}
            m["_id"] = rid
            rows.append(m)
        return rows
    items = data.get("items") if isinstance(data, dict) else None
    for it in items or []:
        f = it.get("fields") or {}
        m = {k: cell(f.get(k)) for k in ("队列Key", "当前顺序号", "是否启用")}
        m["_id"] = it.get("record_id") or ""
        rows.append(m)
    return rows


def batch_create(table_id: str, fields: list[str], rows: list[list[Any]]) -> None:
    if not rows:
        return
    if DRY_RUN:
        print(f"  [dry] batch-create {table_id} +{len(rows)}")
        for r in rows[:5]:
            print("   ", r)
        if len(rows) > 5:
            print(f"    ... +{len(rows)-5} more")
        return
    for i in range(0, len(rows), 100):
        chunk = rows[i : i + 100]
        out = run(
            [
                "base", "+record-batch-create",
                "--base-token", BASE, "--table-id", table_id,
                "--json", json.dumps({"fields": fields, "rows": chunk}, ensure_ascii=False),
                *AS, "--format", "json",
            ]
        )
        if out.get("ok") is False:
            print(out)
            raise SystemExit(1)
        print(f"  batch-create {table_id} +{len(chunk)}")
        time.sleep(0.25)


def step_options() -> None:
    print("\n== 1. select options ==")
    ensure_select_options(T_MAIN, "Channels（渠道）", [NEW_CHANNEL])
    ensure_select_options(T_MAIN, "Channel segmentation (细分渠道)", [NEW_CHANNEL, "领英"])
    ensure_select_options(T_QUEUE, "渠道", [NEW_CHANNEL])


def step_clone_queues() -> dict[str, list[tuple[int, str]]]:
    print(f"\n== 2. clone enabled {SOURCE_CHANNEL} queues → {NEW_CHANNEL} ==")
    src = search_channel_rows(SOURCE_CHANNEL)
    enabled: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in src:
        if r.get("是否启用") != "启用":
            continue
        qn = r.get("队列名称") or ""
        try:
            rank = int(r.get("顺位") or 0)
        except ValueError:
            continue
        person = r.get("业务员") or ""
        if qn and person and rank > 0:
            enabled[qn].append((rank, person))

    existing_new = search_channel_rows(NEW_CHANNEL)
    have = {(r.get("队列名称"), str(r.get("顺位")), r.get("业务员")) for r in existing_new}

    rows: list[list[Any]] = []
    for qn, people in sorted(enabled.items()):
        for rank, person in sorted(set(people)):
            key = (qn, str(rank), person)
            if key in have:
                continue
            rows.append([qn, NEW_CHANNEL, person, rank, "启用"])
        print(f"  {qn}: {sorted(set(people))}")

    if rows:
        batch_create(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用"], rows)
    else:
        print("  queue rows already exist")
    return {k: sorted(set(v)) for k, v in enabled.items()}


def step_pointers(enabled_queues: dict[str, list[tuple[int, str]]]) -> None:
    print(f"\n== 3. pointers for {NEW_CHANNEL}|* ==")
    ptrs = list_pointers()
    phave = {r.get("队列Key") for r in ptrs}
    prows = []
    for qn in sorted(enabled_queues):
        key = f"{NEW_CHANNEL}|{qn}"
        if key in phave:
            print(f"  exists {key}")
            continue
        prows.append([key, 1, "启用"])
    if prows:
        batch_create(T_PTR, ["队列Key", "当前顺序号", "是否启用"], prows)
    else:
        print("  pointer rows already exist")


def verify() -> None:
    print("\n== 4. verify ==")
    new_rows = search_channel_rows(NEW_CHANNEL)
    enabled = [r for r in new_rows if r.get("是否启用") == "启用"]
    print(f"  enabled {NEW_CHANNEL} queue rows: {len(enabled)}")
    by_q: dict[str, list[str]] = defaultdict(list)
    for r in enabled:
        by_q[r.get("队列名称") or ""].append(f"#{r.get('顺位')}{r.get('业务员')}")
    for qn, people in sorted(by_q.items()):
        print(f"    {NEW_CHANNEL}|{qn}: {sorted(people)}")

    ptrs = [r for r in list_pointers() if (r.get("队列Key") or "").startswith(f"{NEW_CHANNEL}|")]
    for r in sorted(ptrs, key=lambda x: x.get("队列Key") or ""):
        print(f"  ptr {r.get('队列Key')} current={r.get('当前顺序号')} {r.get('是否启用')}")


def main() -> int:
    print(f"Add channel {NEW_CHANNEL} (from {SOURCE_CHANNEL}) dry_run={DRY_RUN}")
    step_options()
    enabled = step_clone_queues()
    step_pointers(enabled)
    verify()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
