#!/usr/bin/env python3
"""按启用顺位重算队列指针表，避免停用/离职后仍指向旧顺位。

用法：
  python3 scripts/sync_channel_queue_pointers.py
  python3 scripts/sync_channel_queue_pointers.py --queue-key '英国|VRT-ART队列'
  python3 scripts/sync_channel_queue_pointers.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from channel_queue_assign import (  # noqa: E402
    parse_channel_queue_map,
    parse_queue_pointers,
    reconcile_pointer_fields,
)
from feishu_utils import extract_text  # noqa: E402

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
CHANNEL_QUEUE_TABLE = "tblav9GLrm8Vnf1j"
QUEUE_POINTER_TABLE = "tblGWSsPla3eRfuY"


def _run(args: list[str]) -> dict:
    proc = subprocess.run(
        ["lark-cli", *args, "--base-token", BASE, "--as", "user", "--format", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def _list_records(table_id: str, filter_json: dict | None = None) -> list[dict]:
    offset = 0
    rows: list[dict] = []
    while True:
        cmd = [
            "base",
            "+record-list",
            "--table-id",
            table_id,
            "--limit",
            "200",
            "--offset",
            str(offset),
        ]
        if filter_json:
            cmd += ["--filter-json", json.dumps(filter_json, ensure_ascii=False)]
        data = _run(cmd)["data"]
        fields = data["fields"]
        for rid, vals in zip(data["record_id_list"], data["data"]):
            rows.append({"record_id": rid, "fields": dict(zip(fields, vals))})
        if not data.get("has_more"):
            break
        offset += 200
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue-key", help="仅同步指定队列Key")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    enabled_filter = {
        "conjunction": "and",
        "conditions": [["是否启用", "intersects", ["启用"]]],
    }
    queue_records = _list_records(CHANNEL_QUEUE_TABLE, enabled_filter)
    queue_map = parse_channel_queue_map(queue_records)

    pointer_records = _list_records(QUEUE_POINTER_TABLE, enabled_filter)
    pointers = parse_queue_pointers(pointer_records)

    updated = 0
    for record in pointer_records:
        fields = record.get("fields", {})
        queue_key = extract_text(fields.get("队列Key", "")).strip()
        if not queue_key:
            continue
        if args.queue_key and queue_key != args.queue_key:
            continue
        ptr = pointers.get(queue_key)
        if not ptr:
            continue
        patch = reconcile_pointer_fields(queue_map, ptr, queue_key)
        if not patch:
            print(f"skip {queue_key}: no enabled ranks")
            continue
        if patch["当前顺序号"] == ptr.current and patch["最大顺序号"] == ptr.max_rank:
            print(f"ok {queue_key}: current={ptr.current} max={ptr.max_rank}")
            continue
        print(
            f"update {queue_key}: current {ptr.current}->{patch['当前顺序号']} "
            f"max {ptr.max_rank}->{patch['最大顺序号']}"
        )
        if args.dry_run:
            updated += 1
            continue
        _run(
            [
                "base",
                "+record-upsert",
                "--table-id",
                QUEUE_POINTER_TABLE,
                "--record-id",
                ptr.record_id,
                "--json",
                json.dumps(patch, ensure_ascii=False),
            ]
        )
        updated += 1

    print(f"done updated={updated} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
