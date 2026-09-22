#!/usr/bin/env python3
"""Near-real-time sync: Offline Enquiry/Follow-up from leads Base → Opportunity Base.

Source: ZpbUb7SP7azsNasniFjc0bWSnHg
  - tblzrNcFHAISzi5a 线下询盘记录
  - tblgqC1lbgFazdUB 线下跟进记录
Target: Ktddb4mhtaixgYs9CIkcNnXFngh
  - tblV75ehPDgd7CHV 线下询盘记录 Offline Enquiry
  - tblj2h1ffFxlh78i 线下跟进记录 Offline Follow-up

Idempotent key field: 「源 Record ID」

Usage:
  source .env && python3 scripts/sync_offline_enquiry_to_opp_base.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

import requests

log = logging.getLogger("offline-enq-sync")

SRC_BASE = os.environ.get("FEISHU_APP_TOKEN") or "ZpbUb7SP7azsNasniFjc0bWSnHg"
SRC_ENQ = os.environ.get("OFFLINE_SRC_ENQ_TABLE") or "tblzrNcFHAISzi5a"
SRC_FU = os.environ.get("OFFLINE_SRC_FU_TABLE") or "tblgqC1lbgFazdUB"
DST_BASE = os.environ.get("OPP_BASE_TOKEN") or "Ktddb4mhtaixgYs9CIkcNnXFngh"
DST_ENQ = os.environ.get("OFFLINE_DST_ENQ_TABLE") or "tblV75ehPDgd7CHV"
DST_FU = os.environ.get("OFFLINE_DST_FU_TABLE") or "tblj2h1ffFxlh78i"

SRC_REC = "源 Record ID"
SRC_CLUE = "源 Clue ID"
SRC_FU_ID = "源 Follow-up ID"

SELECT_FIELDS = {
    "🌟Case Level / 线索分级",
    "Country（国家）",
    "Customer Type 客户类型",
    "所属部门 Department",
    "Method 方式",
}

ENQ_COPY_FIELDS = [
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
    # Salesperson 人员字段在部分角色下批量写会 Permission denied，改由用户侧维护
]

FU_COPY_FIELDS = [
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
]


def _token() -> str:
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": os.environ["FEISHU_APP_ID"],
            "app_secret": os.environ["FEISHU_APP_SECRET"],
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise RuntimeError(f"token failed: {data}")
    return data["tenant_access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api(method: str, url: str, token: str, **kwargs) -> dict:
    last = None
    for attempt in range(5):
        resp = requests.request(method, url, headers=_headers(token), timeout=90, **kwargs)
        try:
            data = resp.json()
        except Exception:
            data = {"code": -1, "msg": resp.text[:300], "http": resp.status_code}
        if data.get("code") == 0:
            return data
        if resp.status_code in (429, 500, 502, 503) or data.get("code") in (1254291, 99991400):
            delay = 2**attempt
            log.warning("retry %s after %ss: %s", attempt + 1, delay, data.get("msg"))
            time.sleep(delay)
            last = data
            continue
        raise RuntimeError(data)
    raise RuntimeError(last or data)


def _list_records(token: str, base: str, table: str) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        data = _api(
            "GET",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records",
            token,
            params=params,
        )
        batch = data.get("data", {}).get("items") or []
        items.extend(batch)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return items


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        parts = []
        for item in v:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or "").strip()
    return str(v).strip()


def _copy_value(v: Any, *, field_name: str = "") -> Any:
    """Normalize bitable v1 cell value for write-back."""
    if v is None:
        return None
    # bitable v1: single-select write expects a plain string
    if field_name in SELECT_FIELDS:
        if isinstance(v, str):
            return v.strip() or None
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    t = str(x.get("text") or x.get("name") or "").strip()
                else:
                    t = str(x).strip()
                if t:
                    return t
            return None
        t = _text(v)
        return t or None
    # bitable v1: duplex/link write expects list[str] record_ids
    if field_name == "Related Offline Enquiry 关联线下询盘" or (
        isinstance(v, list) and v and isinstance(v[0], dict) and ("record_ids" in v[0] or "table_id" in v[0])
    ):
        ids: list[str] = []
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    if x.get("id"):
                        ids.append(x["id"])
                    for rid in x.get("record_ids") or []:
                        ids.append(rid)
                elif isinstance(x, str) and x.startswith("rec"):
                    ids.append(x)
        elif isinstance(v, dict):
            if v.get("id"):
                ids.append(v["id"])
            for rid in v.get("record_ids") or []:
                ids.append(rid)
        return ids or None
    if isinstance(v, list):
        if not v:
            return None
        if all(isinstance(x, dict) for x in v):
            if any("id" in x for x in v):
                # user field still uses [{id}]
                return [{"id": x["id"]} for x in v if x.get("id")]
        return [_text(x) for x in v]
    if isinstance(v, dict):
        if "id" in v:
            return [{"id": v["id"]}]
        t = _text(v)
        return t or None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    return s or None


def _batch_create(token: str, base: str, table: str, records: list[dict]) -> list[str]:
    ids: list[str] = []
    for i in range(0, len(records), 100):
        chunk = records[i : i + 100]
        data = _api(
            "POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/batch_create",
            token,
            json={"records": [{"fields": r} for r in chunk]},
        )
        for item in data.get("data", {}).get("records") or []:
            rid = item.get("record_id")
            if rid:
                ids.append(rid)
        time.sleep(0.2)
    return ids


def _batch_update(token: str, base: str, table: str, records: list[dict]) -> None:
    # smaller batches reduce partial-field permission failures
    for i in range(0, len(records), 20):
        chunk = records[i : i + 20]
        try:
            _api(
                "POST",
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/batch_update",
                token,
                json={"records": chunk},
            )
        except Exception as e:
            log.warning("batch update failed (%s), fallback single: %s", len(chunk), e)
            for rec in chunk:
                try:
                    _api(
                        "POST",
                        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/batch_update",
                        token,
                        json={"records": [rec]},
                    )
                except Exception as e2:
                    log.error("update %s failed: %s", rec.get("record_id"), e2)
        time.sleep(0.2)


def sync_enquiries(token: str) -> dict[str, str]:
    src = _list_records(token, SRC_BASE, SRC_ENQ)
    dst = _list_records(token, DST_BASE, DST_ENQ)
    existing = {_text((r.get("fields") or {}).get(SRC_REC)): r["record_id"] for r in dst if _text((r.get("fields") or {}).get(SRC_REC))}
    log.info("enquiry src=%s dst_mapped=%s", len(src), len(existing))

    creates: list[dict] = []
    updates: list[dict] = []
    mapping = dict(existing)

    for rec in src:
        sid = rec["record_id"]
        fields = rec.get("fields") or {}
        payload: dict[str, Any] = {SRC_REC: sid}
        clue = _text(fields.get("Clue ID 线索ID"))
        if clue:
            payload[SRC_CLUE] = clue
            payload["Clue ID 线索ID"] = clue
        for name in ENQ_COPY_FIELDS:
            if name not in fields:
                continue
            cv = _copy_value(fields.get(name), field_name=name)
            if cv is not None:
                payload[name] = cv
        if sid in existing:
            updates.append({"record_id": existing[sid], "fields": payload})
        else:
            creates.append(payload)

    if creates:
        # create returns records in order
        for i in range(0, len(creates), 100):
            chunk = creates[i : i + 100]
            data = _api(
                "POST",
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{DST_BASE}/tables/{DST_ENQ}/records/batch_create",
                token,
                json={"records": [{"fields": r} for r in chunk]},
            )
            for src_payload, item in zip(chunk, data.get("data", {}).get("records") or []):
                rid = item.get("record_id")
                if rid:
                    mapping[src_payload[SRC_REC]] = rid
            log.info("enquiry create +%s", len(chunk))
            time.sleep(0.2)

    if updates:
        _batch_update(token, DST_BASE, DST_ENQ, updates)
        log.info("enquiry update %s", len(updates))

    return mapping


def sync_followups(token: str, enq_map: dict[str, str]) -> None:
    src = _list_records(token, SRC_BASE, SRC_FU)
    dst = _list_records(token, DST_BASE, DST_FU)
    existing = {_text((r.get("fields") or {}).get(SRC_REC)): r["record_id"] for r in dst if _text((r.get("fields") or {}).get(SRC_REC))}
    log.info("followup src=%s dst_mapped=%s", len(src), len(existing))

    creates: list[dict] = []
    updates: list[dict] = []
    for rec in src:
        sid = rec["record_id"]
        fields = rec.get("fields") or {}
        payload: dict[str, Any] = {SRC_REC: sid}
        fu_id = _text(fields.get("Follow-up ID"))
        if fu_id:
            payload[SRC_FU_ID] = fu_id
            payload["Follow-up ID"] = fu_id
        for name in FU_COPY_FIELDS:
            if name not in fields:
                continue
            cv = _copy_value(fields.get(name), field_name=name)
            if cv is not None:
                payload[name] = cv
        link = _copy_value(
            fields.get("Related Offline Enquiry 关联线下询盘"),
            field_name="Related Offline Enquiry 关联线下询盘",
        ) or []
        remapped = []
        for item in link:
            src_link = item if isinstance(item, str) else None
            if src_link and src_link in enq_map:
                remapped.append(enq_map[src_link])
        if remapped:
            payload["Related Offline Enquiry 关联线下询盘"] = remapped
        if sid in existing:
            updates.append({"record_id": existing[sid], "fields": payload})
        else:
            creates.append(payload)

    if creates:
        _batch_create(token, DST_BASE, DST_FU, creates)
        log.info("followup create %s", len(creates))
    if updates:
        _batch_update(token, DST_BASE, DST_FU, updates)
        log.info("followup update %s", len(updates))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not os.environ.get(key):
            log.error("missing env %s", key)
            return 2
    token = _token()
    mapping = sync_enquiries(token)
    sync_followups(token, mapping)
    log.info("done mapping=%s", len(mapping))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
