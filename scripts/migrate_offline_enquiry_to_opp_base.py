#!/usr/bin/env python3
"""Migrate offline enquiry/follow-up tables + dashboards into Opportunity Base,
and keep them near-real-time synced.

Source Base: ZpbUb7SP7azsNasniFjc0bWSnHg
Target Base: Ktddb4mhtaixgYs9CIkcNnXFngh

Idempotent keys:
  enquiry  -> field「源 Record ID」= source record_id
  followup -> field「源 Record ID」= source record_id
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SRC_BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
DST_BASE = "Ktddb4mhtaixgYs9CIkcNnXFngh"
SRC_ENQ = "tblzrNcFHAISzi5a"
SRC_FU = "tblgqC1lbgFazdUB"
SRC_DASH = {
    "daily": "blkRpy9aTmt6Xe0f",
    "weekly": "blkBQcexbS2ry6Rr",
    "monthly": "blkff08UjOX7XOhO",
}

FOLDER_NAME = "线下询盘 Offline Enquiry"
ENQ_NAME = "线下询盘记录 Offline Enquiry"
FU_NAME = "线下跟进记录 Offline Follow-up"
SRC_REC_FIELD = "源 Record ID"
SRC_CLUE_FIELD = "源 Clue ID"
SRC_FU_ID_FIELD = "源 Follow-up ID"

TZ = timezone(timedelta(hours=8))
STATE_PATH = Path(__file__).resolve().parent / ".offline_enquiry_opp_sync_state.json"


def run(args: list[str], *, check: bool = True, retries: int = 5) -> dict[str, Any]:
    cmd = ["lark-cli", *args, "--as", "user", "--format", "json"]
    last: dict[str, Any] = {}
    for attempt in range(retries):
        p = subprocess.run(cmd, capture_output=True, text=True)
        raw = (p.stdout or "").strip() or (p.stderr or "").strip()
        try:
            data = json.loads(raw) if raw.startswith("{") or raw.startswith("[") else {"raw": raw}
        except json.JSONDecodeError:
            data = {"raw": raw, "stderr": p.stderr}
        last = data if isinstance(data, dict) else {"data": data}
        if last.get("ok") is True:
            return last
        msg = str((last.get("error") or {}).get("message") or last.get("raw") or "")
        transient = any(x in msg.lower() for x in ("timeout", "rate", "内部错误", "800008006", "1204"))
        if not check:
            return last
        if transient and attempt < retries - 1:
            wait = 2.0 * (attempt + 1)
            print(f"  retry {attempt + 1}: {msg[:140]}")
            time.sleep(wait)
            continue
        raise RuntimeError(f"cmd failed: {' '.join(args[:7])} -> {raw[:900]}")
    raise RuntimeError(f"cmd failed after retries: {last}")


def find_block(base: str, *, name: str | None = None, typ: str | None = None) -> dict | None:
    resp = run(["base", "+base-block-list", "--base-token", base])
    blocks = (resp.get("data") or {}).get("blocks") or []
    for b in blocks:
        if name and b.get("name") != name:
            continue
        if typ and b.get("type") != typ:
            continue
        if name or typ:
            return b
    return None


def ensure_folder() -> str:
    existing = find_block(DST_BASE, name=FOLDER_NAME, typ="folder")
    if existing:
        print(f"folder exists: {existing['id']}")
        return existing["id"]
    resp = run(
        [
            "base",
            "+base-block-create",
            "--base-token",
            DST_BASE,
            "--type",
            "folder",
            "--name",
            FOLDER_NAME,
        ]
    )
    data = resp.get("data") or {}
    block = data.get("block") or data
    fid = block.get("id") or data.get("id") or data.get("block_id")
    if not fid:
        raise RuntimeError(f"no folder id: {resp}")
    print(f"created folder: {fid}")
    return fid


def list_select_options(base: str, table: str, field_id: str) -> list[dict]:
    opts: list[dict] = []
    offset = 0
    while True:
        resp = run(
            [
                "base",
                "+field-search-options",
                "--base-token",
                base,
                "--table-id",
                table,
                "--field-id",
                field_id,
                "--limit",
                "200",
                "--offset",
                str(offset),
            ]
        )
        batch = (resp.get("data") or {}).get("options") or []
        if not batch:
            break
        opts.extend(batch)
        if len(batch) < 200:
            break
        offset += 200
    return opts


def opts_payload(names: list[str] | list[dict]) -> list[dict]:
    out = []
    for o in names:
        if isinstance(o, dict):
            item = {"name": o["name"]}
            if o.get("hue"):
                item["hue"] = o["hue"]
            if o.get("lightness"):
                item["lightness"] = o["lightness"]
            out.append(item)
        else:
            out.append({"name": o})
    return out


def create_enquiry_table() -> str:
    existing = find_block(DST_BASE, name=ENQ_NAME, typ="table")
    if existing:
        print(f"enquiry table exists: {existing['id']}")
        return existing["id"]

    countries = list_select_options(SRC_BASE, SRC_ENQ, "fldU4ihLmZ")
    depts = list_select_options(SRC_BASE, SRC_ENQ, "fldhFbp4Pi")
    levels = list_select_options(SRC_BASE, SRC_ENQ, "fld1WVFJPo")
    ctypes = list_select_options(SRC_BASE, SRC_ENQ, "fldrPEs8eh")

    fields = [
        {"name": "Customer Name 客戶單位", "type": "text"},
        {"name": SRC_REC_FIELD, "type": "text"},
        {"name": SRC_CLUE_FIELD, "type": "text"},
        {"name": "Clue ID 线索ID", "type": "text"},
        {"name": "Contact 对接人", "type": "text"},
        {"name": "Contact Info 联系方式", "type": "text"},
        {"name": "Lead Source 商机来源", "type": "text"},
        {"name": "Address 单位地址", "type": "text"},
        {
            "name": "Reg. Date 备案时间",
            "type": "datetime",
            "style": {"format": "yyyy/MM/dd"},
        },
        {
            "name": "🌟Case Level / 线索分级",
            "type": "select",
            "multiple": False,
            "options": opts_payload(levels),
        },
        {
            "name": "Country（国家）",
            "type": "select",
            "multiple": False,
            "options": opts_payload(countries),
        },
        {
            "name": "Customer Type 客户类型",
            "type": "select",
            "multiple": False,
            "options": opts_payload(ctypes),
        },
        {
            "name": "所属部门 Department",
            "type": "select",
            "multiple": False,
            "options": opts_payload(depts),
        },
        {"name": "Salesperson 业务员", "type": "user", "multiple": False},
    ]
    resp = run(
        [
            "base",
            "+table-create",
            "--base-token",
            DST_BASE,
            "--name",
            ENQ_NAME,
            "--fields",
            json.dumps(fields, ensure_ascii=False),
        ]
    )
    data = resp.get("data") or {}
    table = data.get("table") or data
    tid = table.get("table_id") or table.get("id") or data.get("table_id")
    if not tid:
        raise RuntimeError(f"no enquiry table id: {resp}")
    print(f"created enquiry table: {tid}")
    return tid


def create_followup_table(enq_table_id: str) -> str:
    existing = find_block(DST_BASE, name=FU_NAME, typ="table")
    if existing:
        print(f"followup table exists: {existing['id']}")
        return existing["id"]

    methods = list_select_options(SRC_BASE, SRC_FU, "fldJqhtk8o")
    fields = [
        {"name": "Follow-up ID", "type": "text"},
        {"name": SRC_REC_FIELD, "type": "text"},
        {"name": SRC_FU_ID_FIELD, "type": "text"},
        {
            "name": "Follow-up Time 跟进时间",
            "type": "datetime",
            "style": {"format": "yyyy/MM/dd HH:mm"},
        },
        {
            "name": "Method 方式",
            "type": "select",
            "multiple": False,
            "options": opts_payload(methods),
        },
        {"name": "Products 意向产品", "type": "text"},
        {"name": "Location 地点", "type": "text"},
        {"name": "Region 合作区域", "type": "text"},
        {"name": "Next Step 推进计划", "type": "text"},
        {"name": "Quote/Disc. 报价折扣", "type": "text"},
        {"name": "Gifts 赠送礼品", "type": "text"},
        {"name": "Attendees 参会人", "type": "text"},
        {"name": "Other 其他事项", "type": "text"},
        {"name": "Salesperson 业务员", "type": "user", "multiple": False},
        {
            "name": "Related Offline Enquiry 关联线下询盘",
            "type": "link",
            "link_table": enq_table_id,
            "bidirectional": True,
            "bidirectional_link_field_name": "新增跟进 Offline Follow-ups",
        },
    ]
    resp = run(
        [
            "base",
            "+table-create",
            "--base-token",
            DST_BASE,
            "--name",
            FU_NAME,
            "--fields",
            json.dumps(fields, ensure_ascii=False),
        ]
    )
    data = resp.get("data") or {}
    table = data.get("table") or data
    tid = table.get("table_id") or table.get("id") or data.get("table_id")
    if not tid:
        raise RuntimeError(f"no followup table id: {resp}")
    print(f"created followup table: {tid}")
    # Lookup 字段看板主路径不依赖；跨表 lookup where 协议较脆，同步脚本侧跳过以免阻断迁移。
    return tid


def move_into_folder(folder_id: str, block_ids: list[str]) -> None:
    for bid in block_ids:
        run(
            [
                "base",
                "+base-block-move",
                "--base-token",
                DST_BASE,
                "--block-id",
                bid,
                "--parent-id",
                folder_id,
            ]
        )
        print(f"moved {bid} -> folder")
        time.sleep(0.2)


def list_all_records(base: str, table: str) -> tuple[list[str], list[str], list[list[Any]]]:
    """Return (record_ids, field_names, rows) using CLI matrix format."""
    all_ids: list[str] = []
    field_names: list[str] | None = None
    rows: list[list[Any]] = []
    page_token = None
    while True:
        args = [
            "base",
            "+record-list",
            "--base-token",
            base,
            "--table-id",
            table,
            "--page-size",
            "100",
        ]
        if page_token:
            args.extend(["--page-token", page_token])
        resp = run(args)
        data = resp.get("data") or {}
        ids = data.get("record_id_list") or []
        fields = data.get("fields") or []
        matrix = data.get("data") or []
        if field_names is None:
            field_names = fields
        all_ids.extend(ids)
        rows.extend(matrix)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            # some responses bury token
            qc = data.get("query_context") or {}
            page_token = qc.get("page_token")
        if not page_token:
            break
    return all_ids, field_names or [], rows


def cell_to_writable(field_name: str, value: Any, *, kind: str) -> Any | None:
    if value is None:
        return None
    if kind in ("created_by", "auto_number", "lookup", "formula", "created_time", "modified_time"):
        return None
    if kind == "link":
        # list of {id} or ids
        if isinstance(value, list):
            ids = []
            for v in value:
                if isinstance(v, dict) and v.get("id"):
                    ids.append({"id": v["id"]})
                elif isinstance(v, str) and v.startswith("rec"):
                    ids.append({"id": v})
            return ids or None
        return None
    if kind == "user":
        if isinstance(value, list):
            users = []
            for v in value:
                if isinstance(v, dict) and v.get("id"):
                    users.append({"id": v["id"]})
            return users or None
        if isinstance(value, dict) and value.get("id"):
            return [{"id": value["id"]}]
        return None
    if kind == "select":
        if isinstance(value, list):
            names = []
            for v in value:
                if isinstance(v, dict):
                    names.append(v.get("name") or v.get("text"))
                else:
                    names.append(str(v))
            names = [n for n in names if n]
            return names or None
        if isinstance(value, dict):
            n = value.get("name") or value.get("text")
            return [n] if n else None
        return [str(value)]
    if kind == "datetime":
        if isinstance(value, (int, float)):
            return int(value)
        return value
    if kind == "text":
        if isinstance(value, list):
            parts = []
            for v in value:
                if isinstance(v, dict):
                    parts.append(str(v.get("text") or v.get("name") or ""))
                else:
                    parts.append(str(v))
            return "".join(parts)
        return str(value)
    # default
    if isinstance(value, list) and value and isinstance(value[0], dict) and "text" in value[0]:
        return "".join(str(v.get("text") or "") for v in value)
    return value


def field_types(base: str, table: str) -> dict[str, str]:
    resp = run(["base", "+field-list", "--base-token", base, "--table-id", table])
    fields = (resp.get("data") or {}).get("fields") or []
    return {f["name"]: f["type"] for f in fields}


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for v in value:
            if isinstance(v, dict):
                parts.append(str(v.get("text") or v.get("name") or ""))
            else:
                parts.append(str(v))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "")
    return str(value)


def sync_enquiries(dst_enq: str, *, full: bool = False) -> dict[str, str]:
    """Return mapping source_record_id -> dest_record_id."""
    src_types = field_types(SRC_BASE, SRC_ENQ)
    src_ids, src_fields, src_rows = list_all_records(SRC_BASE, SRC_ENQ)
    print(f"source enquiries: {len(src_ids)}")

    # existing dest map by 源 Record ID
    dst_ids, dst_fields, dst_rows = list_all_records(DST_BASE, dst_enq)
    src_key_idx = dst_fields.index(SRC_REC_FIELD) if SRC_REC_FIELD in dst_fields else -1
    existing: dict[str, str] = {}
    if src_key_idx >= 0:
        for rid, row in zip(dst_ids, dst_rows):
            key = extract_text(row[src_key_idx] if src_key_idx < len(row) else None)
            if key:
                existing[key] = rid
    print(f"dest enquiry existing mapped: {len(existing)}")

    writable_src_fields = [
        "Customer Name 客戶單位",
        "Contact 对接人",
        "Contact Info 联系方式",
        "Lead Source 商机来源",
        "Address 单位地址",
        "Reg. Date 备案时间",
        "🌟Case Level / 线索分级",
        "Country（国家）",
        "Customer Type 客户类型",
        "所属部门 Department",
        "Salesperson 业务员",
    ]

    creates: list[dict] = []
    updates: list[dict] = []
    mapping: dict[str, str] = dict(existing)

    for sid, row in zip(src_ids, src_rows):
        fmap = {src_fields[i]: row[i] for i in range(min(len(src_fields), len(row)))}
        payload: dict[str, Any] = {SRC_REC_FIELD: sid}
        clue = extract_text(fmap.get("Clue ID 线索ID"))
        if clue:
            payload[SRC_CLUE_FIELD] = clue
            payload["Clue ID 线索ID"] = clue
        for fn in writable_src_fields:
            cv = cell_to_writable(fn, fmap.get(fn), kind=src_types.get(fn, "text"))
            if cv is not None:
                payload[fn] = cv
        if sid in existing:
            updates.append({"record_id": existing[sid], "fields": payload})
        else:
            creates.append(payload)

    # batch create
    for i in range(0, len(creates), 40):
        chunk = creates[i : i + 40]
        resp = run(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                DST_BASE,
                "--table-id",
                dst_enq,
                "--json",
                json.dumps({"create_records": chunk}, ensure_ascii=False),
            ]
        )
        id_list = (resp.get("data") or {}).get("record_id_list") or []
        for src_payload, new_id in zip(chunk, id_list):
            mapping[src_payload[SRC_REC_FIELD]] = new_id
        print(f"  enquiry create batch {i // 40 + 1}: {len(id_list)}")
        time.sleep(0.3)

    # batch update
    for i in range(0, len(updates), 40):
        chunk = updates[i : i + 40]
        # convert to batch-update shape
        records = [{"record_id": u["record_id"], **{k: v for k, v in u["fields"].items()}} for u in chunk]
        # Prefer official shape: {"update_records":[{"record_id":..., "fields":{...}}]}
        body = {"update_records": [{"record_id": u["record_id"], "fields": u["fields"]} for u in chunk]}
        try:
            run(
                [
                    "base",
                    "+record-batch-update",
                    "--base-token",
                    DST_BASE,
                    "--table-id",
                    dst_enq,
                    "--json",
                    json.dumps(body, ensure_ascii=False),
                ]
            )
            print(f"  enquiry update batch {i // 40 + 1}: {len(chunk)}")
        except Exception as e:
            print(f"  enquiry update batch failed, fallback single: {e}")
            for u in chunk:
                run(
                    [
                        "base",
                        "+record-batch-update",
                        "--base-token",
                        DST_BASE,
                        "--table-id",
                        dst_enq,
                        "--json",
                        json.dumps(
                            {"update_records": [{"record_id": u["record_id"], "fields": u["fields"]}]},
                            ensure_ascii=False,
                        ),
                    ]
                )
        time.sleep(0.3)

    print(f"enquiry mapping size: {len(mapping)} (created={len(creates)} updated={len(updates)})")
    return mapping


def sync_followups(dst_fu: str, enq_map: dict[str, str]) -> None:
    src_types = field_types(SRC_BASE, SRC_FU)
    src_ids, src_fields, src_rows = list_all_records(SRC_BASE, SRC_FU)
    print(f"source followups: {len(src_ids)}")

    dst_ids, dst_fields, dst_rows = list_all_records(DST_BASE, dst_fu)
    src_key_idx = dst_fields.index(SRC_REC_FIELD) if SRC_REC_FIELD in dst_fields else -1
    existing: dict[str, str] = {}
    if src_key_idx >= 0:
        for rid, row in zip(dst_ids, dst_rows):
            key = extract_text(row[src_key_idx] if src_key_idx < len(row) else None)
            if key:
                existing[key] = rid
    print(f"dest followup existing mapped: {len(existing)}")

    writable = [
        "Follow-up Time 跟进时间",
        "Method 方式",
        "Products 意向产品",
        "Location 地点",
        "Region 合作区域",
        "Next Step 推进计划",
        "Quote/Disc. 报价折扣",
        "Gifts 赠送礼品",
        "Attendees 参会人",
        "Other 其他事项",
        "Salesperson 业务员",
    ]

    creates: list[dict] = []
    updates: list[dict] = []
    for sid, row in zip(src_ids, src_rows):
        fmap = {src_fields[i]: row[i] for i in range(min(len(src_fields), len(row)))}
        payload: dict[str, Any] = {SRC_REC_FIELD: sid}
        fu_id = extract_text(fmap.get("Follow-up ID"))
        if fu_id:
            payload[SRC_FU_ID_FIELD] = fu_id
            payload["Follow-up ID"] = fu_id
        for fn in writable:
            cv = cell_to_writable(fn, fmap.get(fn), kind=src_types.get(fn, "text"))
            if cv is not None:
                payload[fn] = cv
        # remap link
        link_val = fmap.get("Related Offline Enquiry 关联线下询盘")
        link_ids = cell_to_writable("Related Offline Enquiry 关联线下询盘", link_val, kind="link") or []
        remapped = []
        for item in link_ids:
            src_link = item.get("id")
            dst_link = enq_map.get(src_link)
            if dst_link:
                remapped.append({"id": dst_link})
        if remapped:
            payload["Related Offline Enquiry 关联线下询盘"] = remapped
        if sid in existing:
            updates.append({"record_id": existing[sid], "fields": payload})
        else:
            creates.append(payload)

    for i in range(0, len(creates), 40):
        chunk = creates[i : i + 40]
        resp = run(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                DST_BASE,
                "--table-id",
                dst_fu,
                "--json",
                json.dumps({"create_records": chunk}, ensure_ascii=False),
            ]
        )
        print(f"  followup create batch {i // 40 + 1}: {len((resp.get('data') or {}).get('record_id_list') or [])}")
        time.sleep(0.3)

    for i in range(0, len(updates), 40):
        chunk = updates[i : i + 40]
        body = {"update_records": [{"record_id": u["record_id"], "fields": u["fields"]} for u in chunk]}
        run(
            [
                "base",
                "+record-batch-update",
                "--base-token",
                DST_BASE,
                "--table-id",
                dst_fu,
                "--json",
                json.dumps(body, ensure_ascii=False),
            ]
        )
        print(f"  followup update batch {i // 40 + 1}: {len(chunk)}")
        time.sleep(0.3)
    print(f"followups synced creates={len(creates)} updates={len(updates)}")


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def date_range_filter(field: str, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "conjunction": "and",
        "conditions": [
            {
                "field_name": field,
                "operator": "isGreater",
                "value": ["ExactDate", ms(start - timedelta(seconds=1))],
            },
            {"field_name": field, "operator": "isLess", "value": ["ExactDate", ms(end)]},
        ],
    }


def add_block(dashboard_id: str, name: str, btype: str, cfg: dict, position: dict | None = None) -> None:
    args = [
        "base",
        "+dashboard-block-create",
        "--base-token",
        DST_BASE,
        "--dashboard-id",
        dashboard_id,
        "--name",
        name,
        "--type",
        btype,
        "--data-config",
        json.dumps(cfg, ensure_ascii=False),
    ]
    if position:
        args.extend(["--position", json.dumps(position)])
    run(args)
    print(f"  + {btype}: {name}")
    time.sleep(0.3)


def create_or_get_dashboard(name: str) -> str:
    existing = find_block(DST_BASE, name=name, typ="dashboard")
    if existing:
        # if has blocks already, reuse
        did = existing["id"]
        listed = run(
            [
                "base",
                "+dashboard-block-list",
                "--base-token",
                DST_BASE,
                "--dashboard-id",
                did,
                "--page-size",
                "5",
            ]
        )
        total = (listed.get("data") or {}).get("total") or 0
        if total:
            print(f"dashboard exists with blocks, skip rebuild: {name} {did}")
            return did
        print(f"reuse empty dashboard: {name} {did}")
        return did
    resp = run(["base", "+dashboard-create", "--base-token", DST_BASE, "--name", name])
    data = resp.get("data") or {}
    dash = data.get("dashboard") if isinstance(data.get("dashboard"), dict) else {}
    did = data.get("dashboard_id") or dash.get("dashboard_id") or data.get("id")
    if not did:
        raise RuntimeError(f"no dashboard id: {resp}")
    print(f"created dashboard: {name} {did}")
    return did


def build_dashboards() -> dict[str, str]:
    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    week_end = week_start + timedelta(days=7)
    month_start = today_start.replace(day=1)
    month_end = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    last7_start = today_start - timedelta(days=6)
    last8w_start = today_start - timedelta(days=55)
    last6m_start = today_start - timedelta(days=180)
    nf = {"formatName": "digital", "precision": 0}

    boards: dict[str, str] = {}

    # Daily
    daily = create_or_get_dashboard("线下询盘_每日看板")
    boards["daily"] = daily
    listed = run(
        ["base", "+dashboard-block-list", "--base-token", DST_BASE, "--dashboard-id", daily, "--page-size", "5"]
    )
    if not ((listed.get("data") or {}).get("total") or 0):
        day_f = date_range_filter("Reg. Date 备案时间", today_start, day_end)
        day_fu = date_range_filter("Follow-up Time 跟进时间", today_start, day_end)
        last7 = date_range_filter("Reg. Date 备案时间", last7_start, day_end)
        last7_fu = date_range_filter("Follow-up Time 跟进时间", last7_start, day_end)
        add_block(
            daily,
            "说明",
            "text",
            {
                "text": (
                    "# 线下询盘 · 每日看板（商机Base镜像）\n"
                    f"数据近实时同步自线索Base；今日 {today_start.strftime('%Y-%m-%d')}"
                )
            },
            {"x": 0, "y": 0, "w": 12, "h": 2},
        )
        add_block(daily, "今日新增询盘", "statistics", {"table_name": ENQ_NAME, "count_all": True, "number_format": nf, "filter": day_f}, {"x": 0, "y": 2, "w": 3, "h": 2})
        add_block(daily, "今日跟进次数", "statistics", {"table_name": FU_NAME, "count_all": True, "number_format": nf, "filter": day_fu}, {"x": 3, "y": 2, "w": 3, "h": 2})
        add_block(
            daily,
            "今日高意向(A3)",
            "statistics",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "number_format": nf,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        *day_f["conditions"],
                        {
                            "field_name": "🌟Case Level / 线索分级",
                            "operator": "is",
                            "value": "A3-High-Intent Lead / 高意向客户（真实需求)",
                        },
                    ],
                },
            },
            {"x": 6, "y": 2, "w": 3, "h": 2},
        )
        add_block(
            daily,
            "今日成单(A4)",
            "statistics",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "number_format": nf,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        *day_f["conditions"],
                        {
                            "field_name": "🌟Case Level / 线索分级",
                            "operator": "is",
                            "value": "A4-Won Customer / 成单客户 ",
                        },
                    ],
                },
            },
            {"x": 9, "y": 2, "w": 3, "h": 2},
        )
        add_block(
            daily,
            "近7日询盘趋势",
            "line",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": last7,
                "group_by": [
                    {"field_name": "Reg. Date 备案时间", "mode": "integrated", "sort": {"type": "group", "order": "asc"}}
                ],
            },
            {"x": 0, "y": 4, "w": 6, "h": 5},
        )
        add_block(
            daily,
            "近7日跟进趋势",
            "area",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": last7_fu,
                "group_by": [
                    {
                        "field_name": "Follow-up Time 跟进时间",
                        "mode": "integrated",
                        "sort": {"type": "group", "order": "asc"},
                    }
                ],
            },
            {"x": 6, "y": 4, "w": 6, "h": 5},
        )
        add_block(
            daily,
            "今日部门询盘",
            "column",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": day_f,
                "group_by": [
                    {
                        "field_name": "所属部门 Department",
                        "mode": "integrated",
                        "sort": {"type": "value", "order": "desc"},
                    }
                ],
            },
            {"x": 0, "y": 9, "w": 6, "h": 5},
        )
        add_block(
            daily,
            "今日跟进方式",
            "ring",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": day_fu,
                "group_by": [
                    {"field_name": "Method 方式", "mode": "integrated", "sort": {"type": "value", "order": "desc"}}
                ],
            },
            {"x": 6, "y": 9, "w": 6, "h": 5},
        )
        add_block(
            daily,
            "近7日线索分级",
            "funnel",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": last7,
                "group_by": [
                    {
                        "field_name": "🌟Case Level / 线索分级",
                        "mode": "integrated",
                        "sort": {"type": "view", "order": "asc"},
                    }
                ],
            },
            {"x": 0, "y": 14, "w": 6, "h": 5},
        )
        add_block(
            daily,
            "近7日国家TOP",
            "ranking",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "limit_size": 8,
                "filter": last7,
                "group_by": [
                    {"field_name": "Country（国家）", "mode": "integrated", "sort": {"type": "value", "order": "desc"}}
                ],
            },
            {"x": 6, "y": 14, "w": 6, "h": 5},
        )
        run(["base", "+dashboard-arrange", "--base-token", DST_BASE, "--dashboard-id", daily])

    # Weekly
    weekly = create_or_get_dashboard("线下询盘_每周看板")
    boards["weekly"] = weekly
    listed = run(
        ["base", "+dashboard-block-list", "--base-token", DST_BASE, "--dashboard-id", weekly, "--page-size", "5"]
    )
    if not ((listed.get("data") or {}).get("total") or 0):
        week_f = date_range_filter("Reg. Date 备案时间", week_start, week_end)
        week_fu = date_range_filter("Follow-up Time 跟进时间", week_start, week_end)
        last8w = date_range_filter("Reg. Date 备案时间", last8w_start, day_end)
        last8w_fu = date_range_filter("Follow-up Time 跟进时间", last8w_start, day_end)
        add_block(
            weekly,
            "说明",
            "text",
            {
                "text": (
                    "# 线下询盘 · 每周看板（商机Base镜像）\n"
                    f"本周 {week_start.strftime('%Y-%m-%d')} ~ {(week_end - timedelta(days=1)).strftime('%Y-%m-%d')}"
                )
            },
            {"x": 0, "y": 0, "w": 12, "h": 2},
        )
        add_block(weekly, "本周新增询盘", "statistics", {"table_name": ENQ_NAME, "count_all": True, "number_format": nf, "filter": week_f}, {"x": 0, "y": 2, "w": 3, "h": 2})
        add_block(weekly, "本周跟进次数", "statistics", {"table_name": FU_NAME, "count_all": True, "number_format": nf, "filter": week_fu}, {"x": 3, "y": 2, "w": 3, "h": 2})
        add_block(
            weekly,
            "本周高意向(A3)",
            "statistics",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "number_format": nf,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        *week_f["conditions"],
                        {
                            "field_name": "🌟Case Level / 线索分级",
                            "operator": "is",
                            "value": "A3-High-Intent Lead / 高意向客户（真实需求)",
                        },
                    ],
                },
            },
            {"x": 6, "y": 2, "w": 3, "h": 2},
        )
        add_block(
            weekly,
            "本周成单(A4)",
            "statistics",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "number_format": nf,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        *week_f["conditions"],
                        {
                            "field_name": "🌟Case Level / 线索分级",
                            "operator": "is",
                            "value": "A4-Won Customer / 成单客户 ",
                        },
                    ],
                },
            },
            {"x": 9, "y": 2, "w": 3, "h": 2},
        )
        add_block(
            weekly,
            "近8周询盘趋势",
            "line",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": last8w,
                "group_by": [
                    {"field_name": "Reg. Date 备案时间", "mode": "integrated", "sort": {"type": "group", "order": "asc"}}
                ],
            },
            {"x": 0, "y": 4, "w": 6, "h": 5},
        )
        add_block(
            weekly,
            "近8周跟进趋势",
            "area",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": last8w_fu,
                "group_by": [
                    {
                        "field_name": "Follow-up Time 跟进时间",
                        "mode": "integrated",
                        "sort": {"type": "group", "order": "asc"},
                    }
                ],
            },
            {"x": 6, "y": 4, "w": 6, "h": 5},
        )
        add_block(
            weekly,
            "本周部门对比",
            "bar",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": week_f,
                "group_by": [
                    {
                        "field_name": "所属部门 Department",
                        "mode": "integrated",
                        "sort": {"type": "value", "order": "desc"},
                    }
                ],
            },
            {"x": 0, "y": 9, "w": 6, "h": 5},
        )
        add_block(
            weekly,
            "本周商机来源",
            "pie",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": week_f,
                "group_by": [
                    {
                        "field_name": "Lead Source 商机来源",
                        "mode": "integrated",
                        "sort": {"type": "value", "order": "desc"},
                    }
                ],
            },
            {"x": 6, "y": 9, "w": 6, "h": 5},
        )
        add_block(
            weekly,
            "本周客户类型",
            "column",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": week_f,
                "group_by": [
                    {
                        "field_name": "Customer Type 客户类型",
                        "mode": "integrated",
                        "sort": {"type": "value", "order": "desc"},
                    }
                ],
            },
            {"x": 0, "y": 14, "w": 6, "h": 5},
        )
        add_block(
            weekly,
            "本周跟进方式",
            "ring",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": week_fu,
                "group_by": [
                    {"field_name": "Method 方式", "mode": "integrated", "sort": {"type": "value", "order": "desc"}}
                ],
            },
            {"x": 6, "y": 14, "w": 6, "h": 5},
        )
        run(["base", "+dashboard-arrange", "--base-token", DST_BASE, "--dashboard-id", weekly])

    # Monthly
    monthly = create_or_get_dashboard("线下询盘_每月看板")
    boards["monthly"] = monthly
    listed = run(
        ["base", "+dashboard-block-list", "--base-token", DST_BASE, "--dashboard-id", monthly, "--page-size", "5"]
    )
    if not ((listed.get("data") or {}).get("total") or 0):
        month_f = date_range_filter("Reg. Date 备案时间", month_start, month_end)
        month_fu = date_range_filter("Follow-up Time 跟进时间", month_start, month_end)
        last6m = date_range_filter("Reg. Date 备案时间", last6m_start, day_end)
        last6m_fu = date_range_filter("Follow-up Time 跟进时间", last6m_start, day_end)
        add_block(
            monthly,
            "说明",
            "text",
            {"text": f"# 线下询盘 · 每月看板（商机Base镜像）\n本月 {month_start.strftime('%Y-%m')}"},
            {"x": 0, "y": 0, "w": 12, "h": 2},
        )
        add_block(monthly, "本月新增询盘", "statistics", {"table_name": ENQ_NAME, "count_all": True, "number_format": nf, "filter": month_f}, {"x": 0, "y": 2, "w": 3, "h": 2})
        add_block(monthly, "本月跟进次数", "statistics", {"table_name": FU_NAME, "count_all": True, "number_format": nf, "filter": month_fu}, {"x": 3, "y": 2, "w": 3, "h": 2})
        add_block(
            monthly,
            "本月高意向(A3)",
            "statistics",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "number_format": nf,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        *month_f["conditions"],
                        {
                            "field_name": "🌟Case Level / 线索分级",
                            "operator": "is",
                            "value": "A3-High-Intent Lead / 高意向客户（真实需求)",
                        },
                    ],
                },
            },
            {"x": 6, "y": 2, "w": 3, "h": 2},
        )
        add_block(
            monthly,
            "本月成单(A4)",
            "statistics",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "number_format": nf,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        *month_f["conditions"],
                        {
                            "field_name": "🌟Case Level / 线索分级",
                            "operator": "is",
                            "value": "A4-Won Customer / 成单客户 ",
                        },
                    ],
                },
            },
            {"x": 9, "y": 2, "w": 3, "h": 2},
        )
        add_block(
            monthly,
            "近半年询盘趋势",
            "line",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": last6m,
                "group_by": [
                    {"field_name": "Reg. Date 备案时间", "mode": "integrated", "sort": {"type": "group", "order": "asc"}}
                ],
            },
            {"x": 0, "y": 4, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "近半年跟进趋势",
            "area",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": last6m_fu,
                "group_by": [
                    {
                        "field_name": "Follow-up Time 跟进时间",
                        "mode": "integrated",
                        "sort": {"type": "group", "order": "asc"},
                    }
                ],
            },
            {"x": 6, "y": 4, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "本月部门询盘",
            "column",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": month_f,
                "group_by": [
                    {
                        "field_name": "所属部门 Department",
                        "mode": "integrated",
                        "sort": {"type": "value", "order": "desc"},
                    }
                ],
            },
            {"x": 0, "y": 9, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "本月国家分布",
            "pie",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": month_f,
                "group_by": [
                    {"field_name": "Country（国家）", "mode": "integrated", "sort": {"type": "value", "order": "desc"}}
                ],
            },
            {"x": 6, "y": 9, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "本月线索漏斗",
            "funnel",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "filter": month_f,
                "group_by": [
                    {
                        "field_name": "🌟Case Level / 线索分级",
                        "mode": "integrated",
                        "sort": {"type": "view", "order": "asc"},
                    }
                ],
            },
            {"x": 0, "y": 14, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "本月商机来源TOP",
            "ranking",
            {
                "table_name": ENQ_NAME,
                "count_all": True,
                "limit_size": 10,
                "filter": month_f,
                "group_by": [
                    {
                        "field_name": "Lead Source 商机来源",
                        "mode": "integrated",
                        "sort": {"type": "value", "order": "desc"},
                    }
                ],
            },
            {"x": 6, "y": 14, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "本月意向产品词云",
            "wordCloud",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": month_fu,
                "group_by": [{"field_name": "Products 意向产品", "mode": "integrated"}],
            },
            {"x": 0, "y": 19, "w": 6, "h": 5},
        )
        add_block(
            monthly,
            "本月跟进方式",
            "ring",
            {
                "table_name": FU_NAME,
                "count_all": True,
                "filter": month_fu,
                "group_by": [
                    {"field_name": "Method 方式", "mode": "integrated", "sort": {"type": "value", "order": "desc"}}
                ],
            },
            {"x": 6, "y": 19, "w": 6, "h": 5},
        )
        run(["base", "+dashboard-arrange", "--base-token", DST_BASE, "--dashboard-id", monthly])

    return boards


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def cmd_setup() -> dict:
    folder_id = ensure_folder()
    enq_id = create_enquiry_table()
    fu_id = create_followup_table(enq_id)
    move_into_folder(folder_id, [enq_id, fu_id])
    mapping = sync_enquiries(enq_id, full=True)
    sync_followups(fu_id, mapping)
    boards = build_dashboards()
    move_into_folder(folder_id, list(boards.values()))
    state = {
        "folder_id": folder_id,
        "enquiry_table_id": enq_id,
        "followup_table_id": fu_id,
        "dashboards": boards,
        "updated_at": datetime.now(TZ).isoformat(),
    }
    save_state(state)
    return state


def cmd_sync() -> dict:
    state = load_state()
    enq_id = state.get("enquiry_table_id") or (find_block(DST_BASE, name=ENQ_NAME, typ="table") or {}).get("id")
    fu_id = state.get("followup_table_id") or (find_block(DST_BASE, name=FU_NAME, typ="table") or {}).get("id")
    if not enq_id or not fu_id:
        raise RuntimeError("target tables missing; run --setup first")
    mapping = sync_enquiries(enq_id, full=True)
    sync_followups(fu_id, mapping)
    state.update(
        {
            "enquiry_table_id": enq_id,
            "followup_table_id": fu_id,
            "updated_at": datetime.now(TZ).isoformat(),
        }
    )
    save_state(state)
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="create folder/tables/dashboards + full sync")
    ap.add_argument("--sync", action="store_true", help="incremental/full upsert sync only")
    args = ap.parse_args()
    if args.setup:
        state = cmd_setup()
    elif args.sync:
        state = cmd_sync()
    else:
        ap.print_help()
        return 2
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
