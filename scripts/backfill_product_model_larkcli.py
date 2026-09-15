#!/usr/bin/env python3
"""
用 lark-cli 回填产品大类/型号（绕过 requests 直连超时）。

两阶段：先拉全量「无法识别」候选并本地推断，再逐条 upsert。
默认正式写入；PRODUCT_BACKFILL_DRY_RUN=true 时只打印。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))

from product_model_backfill import infer_product_updates  # noqa: E402
from tagline_fields import (  # noqa: E402
    FIELD_CHANNELS,
    FIELD_COUNTRY,
    FIELD_ENQUIRY,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_SUB_CHANNEL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("product-backfill-cli")

FIELD_ENTRY_TIME = "Entry Time（录入时间）"
FIELD_LEAD_ID = "Clue ID"
RULES = json.loads((ROOT / "lead-rules.json").read_text(encoding="utf-8"))
DRY_RUN = os.environ.get("PRODUCT_BACKFILL_DRY_RUN", "false").lower() == "true"
SINCE = os.environ.get("PRODUCT_BACKFILL_SINCE", "2026-07-01")
MAX_RECORDS = int(os.environ.get("PRODUCT_BACKFILL_MAX_RECORDS", "2000"))
PAGE = int(os.environ.get("PRODUCT_BACKFILL_PAGE", "50"))
SLEEP_MS = int(os.environ.get("PRODUCT_BACKFILL_SLEEP_MS", "150"))
AS_IDENTITY = os.environ.get("PRODUCT_BACKFILL_AS", "user")

BASE = os.environ.get("FEISHU_APP_TOKEN", "ZpbUb7SP7azsNasniFjc0bWSnHg")
TABLE = os.environ.get("FEISHU_TABLE_ID", "tbluuuXn9WexH8LV")

FIELD_ORDER = [
    FIELD_ENQUIRY,
    FIELD_PRODUCT_MODEL,
    FIELD_PRODUCT_CAT,
    FIELD_CHANNELS,
    FIELD_SUB_CHANNEL,
    FIELD_COUNTRY,
    FIELD_ENTRY_TIME,
    FIELD_LEAD_ID,
]


def _since_ms(since: str) -> int:
    day = datetime.strptime(since.strip()[:10], "%Y-%m-%d")
    day = day.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(day.timestamp() * 1000)


def _sel(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        if not v:
            return ""
        first = v[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("name") or "").strip()
        return str(first).strip()
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or "").strip()
    return str(v).strip()


def _lark(args: list[str], retries: int = 5) -> dict:
    cmd = ["lark-cli", "base", *args, "--format", "json", "--as", AS_IDENTITY]
    last_err = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        raw = proc.stdout or proc.stderr or ""
        try:
            data = json.loads(raw) if raw.strip().startswith("{") else {}
        except json.JSONDecodeError:
            data = {}
        if proc.returncode == 0 and data.get("ok"):
            return data
        last_err = raw or f"exit={proc.returncode}"
        if any(x in last_err for x in ("Timeout", "timeout", "canceled", "i/o timeout")):
            wait = min(2**attempt, 25)
            log.warning("lark-cli 超时 attempt=%s/%s，%ss 后重试", attempt, retries, wait)
            time.sleep(wait)
            continue
        if data.get("ok") is False:
            raise RuntimeError(f"lark-cli error: {data.get('error')}")
        raise RuntimeError(f"lark-cli failed: {last_err}")
    raise RuntimeError(f"lark-cli failed after retries: {last_err}")


def _list_page(offset: int) -> tuple[list[dict], bool]:
    filter_json = json.dumps(
        {
            "logic": "and",
            "conditions": [
                [FIELD_ENTRY_TIME, ">", f"ExactDate({SINCE})"],
                [FIELD_PRODUCT_MODEL, "==", "无法识别"],
            ],
        },
        ensure_ascii=False,
    )
    sort_json = json.dumps([{"field": FIELD_ENTRY_TIME, "desc": True}], ensure_ascii=False)
    args = [
        "+record-list",
        "--base-token",
        BASE,
        "--table-id",
        TABLE,
        "--filter-json",
        filter_json,
        "--sort-json",
        sort_json,
        "--limit",
        str(PAGE),
        "--offset",
        str(offset),
    ]
    for fid in FIELD_ORDER:
        args.extend(["--field-id", fid])
    data = _lark(args)["data"]
    rows = data.get("data") or []
    record_ids = data.get("record_id_list") or []
    has_more = bool(data.get("has_more"))
    items = []
    for i, row in enumerate(rows):
        rid = record_ids[i] if i < len(record_ids) else ""
        mapped = {
            FIELD_ORDER[j]: row[j] if j < len(row) else None for j in range(len(FIELD_ORDER))
        }
        items.append({"record_id": rid, "fields": mapped})
    return items, has_more


def _upsert(record_id: str, patch: dict) -> None:
    args = [
        "+record-upsert",
        "--base-token",
        BASE,
        "--table-id",
        TABLE,
        "--record-id",
        record_id,
        "--json",
        json.dumps(patch, ensure_ascii=False),
    ]
    _lark(args)


def _entry_ms(entry) -> int:
    if isinstance(entry, (int, float)):
        return int(entry)
    if isinstance(entry, str) and entry:
        try:
            dt = datetime.strptime(entry[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone(timedelta(hours=8))
            )
            return int(dt.timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def _collect_jobs(cutoff: int) -> list[dict]:
    jobs: list[dict] = []
    offset = 0
    while len(jobs) < MAX_RECORDS:
        items, has_more = _list_page(offset)
        if not items:
            break
        for item in items:
            rid = item.get("record_id") or ""
            fields = item.get("fields") or {}
            entry_ms = _entry_ms(fields.get(FIELD_ENTRY_TIME))
            if entry_ms and entry_ms < cutoff:
                continue
            enquiry = fields.get(FIELD_ENQUIRY) or ""
            if not isinstance(enquiry, str):
                enquiry = _sel(enquiry)
            updates = infer_product_updates(
                enquiry=enquiry,
                country=_sel(fields.get(FIELD_COUNTRY)),
                channels=_sel(fields.get(FIELD_CHANNELS)),
                sub_channel=_sel(fields.get(FIELD_SUB_CHANNEL)),
                current_category=_sel(fields.get(FIELD_PRODUCT_CAT)),
                current_model=_sel(fields.get(FIELD_PRODUCT_MODEL)),
                rules=RULES,
            )
            if updates and rid:
                jobs.append(
                    {
                        "record_id": rid,
                        "lead": _sel(fields.get(FIELD_LEAD_ID)),
                        "before_cat": _sel(fields.get(FIELD_PRODUCT_CAT)),
                        "before_model": _sel(fields.get(FIELD_PRODUCT_MODEL)),
                        "updates": updates,
                    }
                )
            if len(jobs) >= MAX_RECORDS:
                break
        if not has_more:
            break
        offset += len(items)
    return jobs


def run() -> int:
    cutoff = _since_ms(SINCE)
    log.info(
        "lark-cli 回填 since=%s dry_run=%s as=%s max=%s",
        SINCE,
        DRY_RUN,
        AS_IDENTITY,
        MAX_RECORDS,
    )
    jobs = _collect_jobs(cutoff)
    log.info("可回填候选 %s 条", len(jobs))

    fixed = 0
    failed = 0
    for job in jobs:
        rid = job["record_id"]
        updates = job["updates"]
        log.info(
            "回填 %s record=%s %s/%s → %s",
            job["lead"] or rid,
            rid,
            job["before_cat"] or "(空)",
            job["before_model"] or "(空)",
            updates,
        )
        if DRY_RUN:
            fixed += 1
            continue
        try:
            _upsert(rid, updates)
            fixed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.error("写入失败 %s: %s", rid, exc)
        if SLEEP_MS > 0:
            time.sleep(SLEEP_MS / 1000.0)

    log.info("完成: 回填=%s 失败=%s 候选=%s dry_run=%s", fixed, failed, len(jobs), DRY_RUN)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
