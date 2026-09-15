#!/usr/bin/env python3
"""将线索总池 + Follow-up Records 同步到多维表格「客户跟进记录」。

目标表：tblDT1kCWXEDzXfy
主键：Clue ID 线索ID
映射：
- Customer Name 客戶單位 ← 线索表「Customer Name 客戶單位」（询盘 AI 提取）
- Contact 对接人 ← Customer Name（客户名称）
- Salesperson 业务员 ← 最终分配业务员
- 按线索ID 从大到小排序
- 最多 5 次交流；超出写入 More Follow-ups

用法：
  source .env && python3 scripts/sync_customer_followup_memo_online.py
  source .env && python3 scripts/sync_customer_followup_memo_online.py --force
  source .env && python3 scripts/sync_customer_followup_memo_online.py --watch --interval 60
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

APP_TOKEN = (
    os.environ.get("FEISHU_BITABLE_APP")
    or os.environ.get("FEISHU_APP_TOKEN")
    or "ZpbUb7SP7azsNasniFjc0bWSnHg"
)
CASE_TABLE = "tbluuuXn9WexH8LV"
FOLLOWUP_TABLE = "tbl3n8TTJYXHG12q"
MEMO_TABLE = "tblDT1kCWXEDzXfy"
TZ = ZoneInfo("Asia/Shanghai")
MAX_COMMS = 5
FP_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".cache", "memo_bitable_fingerprint.txt"
)

FIELD_LEAD_ID = "Clue ID"
FIELD_CUSTOMER = "Customer Name（客户名称）"
FIELD_UNIT = "Customer Name 客戶單位"  # AI 从询盘提取
FIELD_FINAL = "The final assigned salesperson（最终分配的业务员）"
FIELD_ADDRESS = "单位地址 Address"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_PHONE = "Phone（客户电话）"
FIELD_WECHAT = "Wechat（微信）"
FIELD_CHANNEL = "Channels（渠道）"
FIELD_SUB = "Channel segmentation (细分渠道)"
FIELD_ENTRY = "Entry Time（录入时间）"
FIELD_CUST_TYPE = "Customer type"
FIELD_CUST_TYPE_US = "Customer Type（US）"

FU_TIME = "Follow-up Time"
FU_METHOD = "Contact Method"
FU_LOCATION = "地点 Location"
FU_ATTENDEES = "参会人 Attendees"
FU_PRODUCTS = "意向产品 Products"
FU_REGION = "合作区域 Region"
FU_QUOTE = "报价折扣 Quote/Disc"
FU_OTHER = "Follow-up Details"
FU_GIFTS = "赠送礼品 Gifts"
FU_NEXT = "Next Step"
FU_RELATED = "Related Lead"
FU_DELETED = "已删除"

MEMO_SYNC_KEY = "Clue ID 线索ID"
MEMO_SALES = "Salesperson 业务员"
MEMO_COMPANY = "Customer Name 客戶單位"
MEMO_ADDRESS = "Address 单位地址"
MEMO_CONTACT = "Contact 对接人"
MEMO_CONTACT_INFO = "Contact Info 联系方式"
MEMO_SOURCE = "Lead Source 商机来源"
MEMO_REG_DATE = "Reg. Date 备案时间"
MEMO_CUST_TYPE = "Customer Type 客户类型"
MEMO_OVERFLOW = "More Follow-ups 更多跟进摘要"

COMM_LABELS = [
    ("Date 时间", FU_TIME),
    ("Method 方式", FU_METHOD),
    ("Location 地点", FU_LOCATION),
    ("Attendees 参会人", FU_ATTENDEES),
    ("Products 意向产品", FU_PRODUCTS),
    ("Region 合作区域", FU_REGION),
    ("Quote/Disc. 报价折扣", FU_QUOTE),
    ("Other 其他事项", FU_OTHER),
    ("Gifts 赠送礼品", FU_GIFTS),
    ("Next Step 推进计划", FU_NEXT),
]
COMM_PREFIX = {
    1: "First Communication",
    2: "Second Communication",
    3: "Third Communication",
    4: "Fourth Communication",
    5: "Fifth Communication",
}


def _token() -> str:
    app_id = os.environ["FEISHU_APP_ID"]
    app_secret = os.environ["FEISHU_APP_SECRET"]
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(data)
    return data["tenant_access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v > 1e11:
            return datetime.fromtimestamp(v / 1000, tz=TZ).strftime("%Y/%m/%d")
        return str(v)
    if isinstance(v, list):
        parts = []
        for item in v:
            if isinstance(item, dict):
                parts.append(
                    item.get("text")
                    or item.get("name")
                    or item.get("email")
                    or ""
                )
            else:
                parts.append(_cell_text(item))
        return " / ".join(p for p in parts if p)
    if isinstance(v, dict):
        if "value" in v:
            return _cell_text(v.get("value"))
        return v.get("text") or v.get("name") or _cell_text(v.get("value")) or ""
    return str(v).strip()


def _parse_time(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=TZ)
    s = _cell_text(v)
    if not s:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def _date_ms(v: Any) -> int | None:
    dt = _parse_time(v)
    if not dt:
        return None
    day = dt.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(day.timestamp() * 1000)


def _list_all(token: str, table: str) -> list[dict]:
    headers = _headers(token)
    items: list[dict] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{table}/records",
            headers=headers,
            params=params,
            timeout=90,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        items.extend(data["data"].get("items") or [])
        print(f"  …{table} 已拉取 {len(items)}", flush=True)
        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return items


def _related_lead_ids(related: Any) -> list[str]:
    link_ids: list[str] = []

    def walk(obj: Any) -> None:
        if not obj:
            return
        if isinstance(obj, str):
            link_ids.append(obj)
            return
        if isinstance(obj, dict):
            for key in ("link_record_ids", "record_ids"):
                vals = obj.get(key)
                if isinstance(vals, list):
                    link_ids.extend(str(x) for x in vals if x)
            for key in ("record_id", "id"):
                if obj.get(key):
                    link_ids.append(str(obj[key]))
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(related)
    return list(dict.fromkeys(link_ids))


def _contact_info(fields: dict) -> str:
    parts = []
    for key, label in (
        (FIELD_EMAIL, "邮箱"),
        (FIELD_PHONE, "电话"),
        (FIELD_WECHAT, "微信"),
    ):
        val = _cell_text(fields.get(key))
        if val:
            parts.append(f"{label}:{val}")
    return "；".join(parts)


def _lead_source(fields: dict) -> str:
    ch = _cell_text(fields.get(FIELD_CHANNEL))
    sub = _cell_text(fields.get(FIELD_SUB))
    if ch and sub:
        return f"{ch}/{sub}"
    return ch or sub


def _customer_type(fields: dict) -> str:
    return (
        _cell_text(fields.get(FIELD_CUST_TYPE))
        or _cell_text(fields.get(FIELD_CUST_TYPE_US))
        or _cell_text(fields.get("Customer Grade"))
        or _cell_text(fields.get("Customer segmentation（客户分级）"))
    )


def _fu_text(fu: dict, key: str) -> str:
    fields = fu.get("fields") or {}
    if key == FU_TIME:
        dt = _parse_time(fields.get(key))
        return dt.strftime("%Y/%m/%d") if dt else _cell_text(fields.get(key))
    return _cell_text(fields.get(key))


def _overflow_summary(fus: list[dict]) -> str:
    if len(fus) <= MAX_COMMS:
        return ""
    lines = []
    for i, fu in enumerate(fus[MAX_COMMS:], MAX_COMMS + 1):
        bits = [
            f"#{i}",
            _fu_text(fu, FU_TIME),
            _fu_text(fu, FU_METHOD),
            (_fu_text(fu, FU_OTHER) or "")[:80],
            (_fu_text(fu, FU_NEXT) or "")[:60],
        ]
        lines.append(" | ".join(b for b in bits if b))
    return "\n".join(lines)


def _lead_sort_key(lid: str) -> tuple:
    s = str(lid).strip()
    if s.isdigit():
        return (0, -int(s))
    return (1, s)


def build_memo_fields(lead: dict, fus: list[dict], seq: int) -> dict[str, Any] | None:
    fields = lead.get("fields") or {}
    final = _cell_text(fields.get(FIELD_FINAL))
    if not final or final in ("未命中规则",):
        return None
    lead_id = _cell_text(fields.get(FIELD_LEAD_ID))
    if not lead_id:
        return None

    sorted_fus = sorted(
        fus,
        key=lambda x: _parse_time((x.get("fields") or {}).get(FU_TIME))
        or datetime.min.replace(tzinfo=TZ),
    )

    unit = _cell_text(fields.get(FIELD_UNIT))
    person = _cell_text(fields.get(FIELD_CUSTOMER))

    out: dict[str, Any] = {
        MEMO_SYNC_KEY: lead_id,
        MEMO_SALES: final,  # 业务员名字（原序号列）
        MEMO_COMPANY: unit,
        MEMO_ADDRESS: _cell_text(fields.get(FIELD_ADDRESS)),
        MEMO_CONTACT: person,  # 客户姓名
        MEMO_CONTACT_INFO: _contact_info(fields),
        MEMO_SOURCE: _lead_source(fields),
        MEMO_CUST_TYPE: _customer_type(fields),
        MEMO_OVERFLOW: _overflow_summary(sorted_fus),
    }
    entry_ms = _date_ms(fields.get(FIELD_ENTRY))
    if entry_ms is not None:
        out[MEMO_REG_DATE] = entry_ms

    for i in range(1, MAX_COMMS + 1):
        prefix = COMM_PREFIX[i]
        if i - 1 < len(sorted_fus):
            fu_fields = sorted_fus[i - 1].get("fields") or {}
            for label, key in COMM_LABELS:
                fname = f"{prefix} {label}"
                if key == FU_TIME:
                    ms = _date_ms(fu_fields.get(key))
                    out[fname] = ms if ms is not None else None
                else:
                    out[fname] = _cell_text(fu_fields.get(key))
        else:
            for label, key in COMM_LABELS:
                fname = f"{prefix} {label}"
                out[fname] = None if key == FU_TIME else ""

    return out


def _batch_create(token: str, records: list[dict[str, Any]]) -> int:
    headers = _headers(token)
    ok = 0
    for i in range(0, len(records), 100):
        chunk = records[i : i + 100]
        r = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{MEMO_TABLE}/records/batch_create",
            headers=headers,
            json={"records": [{"fields": f} for f in chunk]},
            timeout=120,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"batch_create failed: {data}")
        ok += len(data.get("data", {}).get("records") or chunk)
        print(f"  已创建 {ok}/{len(records)}", flush=True)
        time.sleep(0.2)
    return ok


def _batch_update(token: str, items: list[tuple[str, dict[str, Any]]]) -> int:
    headers = _headers(token)
    ok = 0
    for i in range(0, len(items), 100):
        chunk = items[i : i + 100]
        r = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{MEMO_TABLE}/records/batch_update",
            headers=headers,
            json={
                "records": [
                    {"record_id": rid, "fields": fields} for rid, fields in chunk
                ]
            },
            timeout=120,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"batch_update failed: {data}")
        ok += len(data.get("data", {}).get("records") or chunk)
        print(f"  已更新 {ok}/{len(items)}", flush=True)
        time.sleep(0.2)
    return ok


def _fingerprint(desired: dict[str, dict[str, Any]]) -> str:
    # 稳定序列化：按线索ID排序
    items = sorted(desired.items(), key=lambda x: x[0])
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _load_fp() -> str:
    try:
        with open(FP_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _save_fp(fp: str) -> None:
    path = os.path.abspath(FP_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(fp)


def sync_once(
    token: str,
    salesperson: str | None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    print("拉取线索…", flush=True)
    leads = _list_all(token, CASE_TABLE)
    print("拉取跟进…", flush=True)
    followups_raw = _list_all(token, FOLLOWUP_TABLE)
    by_lead: dict[str, list[dict]] = defaultdict(list)
    for fu in followups_raw:
        fields = fu.get("fields") or {}
        if fields.get(FU_DELETED) is True:
            continue
        for lid in _related_lead_ids(fields.get(FU_RELATED)):
            by_lead[lid].append(fu)
    print(f"跟进关联到 {len(by_lead)} 个线索", flush=True)

    candidates: list[tuple[str, dict, list[dict]]] = []
    for lead in leads:
        fields = lead.get("fields") or {}
        final = _cell_text(fields.get(FIELD_FINAL))
        if not final or final in ("未命中规则",):
            continue
        if salesperson and salesperson not in final and final not in salesperson:
            continue
        lead_id = _cell_text(fields.get(FIELD_LEAD_ID))
        if not lead_id:
            continue
        candidates.append((lead_id, lead, by_lead.get(lead["record_id"], [])))

    candidates.sort(key=lambda x: _lead_sort_key(x[0]))

    desired: dict[str, dict[str, Any]] = {}
    for seq, (lead_id, lead, fus) in enumerate(candidates, 1):
        memo = build_memo_fields(lead, fus, seq)
        if memo:
            desired[lead_id] = memo

    print(f"待同步客户 {len(desired)} 条（按线索ID降序）", flush=True)
    if dry_run:
        for s in list(desired.values())[:3]:
            print(
                " sample:",
                s.get(MEMO_SYNC_KEY),
                s.get(MEMO_SALES),
                s.get(MEMO_COMPANY),
                s.get(MEMO_CONTACT),
            )
        return False

    fp = _fingerprint(desired)
    if not force and fp == _load_fp():
        print("源数据无变化，跳过写入", flush=True)
        return False

    print("拉取备忘表现有记录…", flush=True)
    existing = _list_all(token, MEMO_TABLE)
    by_clue: dict[str, str] = {}
    for rec in existing:
        fields = rec.get("fields") or {}
        clue = (
            _cell_text(fields.get(MEMO_SYNC_KEY))
            or _cell_text(fields.get("线索ID Clue ID"))
            or _cell_text(fields.get("_同步键线索ID"))
        )
        if clue:
            by_clue[clue] = rec["record_id"]

    to_create: list[dict[str, Any]] = []
    to_update: list[tuple[str, dict[str, Any]]] = []
    for clue, fields in desired.items():
        rid = by_clue.get(clue)
        if rid:
            to_update.append((rid, fields))
        else:
            to_create.append(fields)

    # 删除源中已不存在的备忘行（可选：仅当全量且无 salesperson 过滤时）
    orphan_ids = []
    if not salesperson:
        for clue, rid in by_clue.items():
            if clue not in desired:
                orphan_ids.append(rid)

    print(
        f"新建 {len(to_create)} / 更新 {len(to_update)} / 源外残留 {len(orphan_ids)}",
        flush=True,
    )
    created = _batch_create(token, to_create) if to_create else 0
    updated = _batch_update(token, to_update) if to_update else 0
    _save_fp(fp)
    print(f"完成：创建 {created}，更新 {updated}")
    print(
        f"打开表格：https://rcn1z5q6iyyc.feishu.cn/base/{APP_TOKEN}?table={MEMO_TABLE}&view=vewzrqZQgY"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="同步客户推进备忘到多维表格")
    parser.add_argument("--salesperson", help="只同步指定最终分配业务员（模糊匹配）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    if not args.watch:
        sync_once(
            _token(),
            args.salesperson,
            dry_run=args.dry_run,
            force=args.force,
        )
        return 0

    print(f"进入近实时监听：每 {args.interval}s 检查并回填多维表…", flush=True)
    while True:
        try:
            sync_once(
                _token(),
                args.salesperson,
                dry_run=args.dry_run,
                force=args.force,
            )
            args.force = False
        except Exception as e:
            print(f"[watch] 同步失败: {e}", flush=True)
        time.sleep(max(15, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyError as e:
        print(f"缺少环境变量 {e}，请 source .env", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\n已停止监听", flush=True)
        raise SystemExit(0)
