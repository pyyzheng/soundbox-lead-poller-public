#!/usr/bin/env python3
"""Near-real-time sync: Offline Enquiry/Follow-up from leads Base → Opportunity Base.

Idempotent keys (业务主键，不依赖「源 *」技术字段):
  enquiry  -> Clue ID 线索ID
  followup -> Follow-up ID
"""
from __future__ import annotations

import logging
import os
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

CLUE_FIELD = "Clue ID 线索ID"
FU_ID_FIELD = "Follow-up ID"

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
        json={"app_id": os.environ["FEISHU_APP_ID"], "app_secret": os.environ["FEISHU_APP_SECRET"]},
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
            delay = 2 ** attempt
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
        items.extend(data.get("data", {}).get("items") or [])
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
    if v is None:
        return None
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
        if all(isinstance(x, dict) for x in v) and any("id" in x for x in v):
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


def _batch_create(token: str, base: str, table: str, records: list[dict]) -> None:
    for i in range(0, len(records), 100):
        chunk = records[i : i + 100]
        _api(
            "POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/batch_create",
            token,
            json={"records": [{"fields": r} for r in chunk]},
        )
        time.sleep(0.2)


def _batch_update(token: str, base: str, table: str, records: list[dict]) -> None:
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
    """Return source_enquiry_record_id -> dest_enquiry_record_id."""
    src = _list_records(token, SRC_BASE, SRC_ENQ)
    dst = _list_records(token, DST_BASE, DST_ENQ)
    existing_by_clue = {
        _text((r.get("fields") or {}).get(CLUE_FIELD)): r["record_id"]
        for r in dst
        if _text((r.get("fields") or {}).get(CLUE_FIELD))
    }
    log.info("enquiry src=%s dst_by_clue=%s", len(src), len(existing_by_clue))

    creates: list[dict] = []
    updates: list[dict] = []
    mapping: dict[str, str] = {}

    for rec in src:
        sid = rec["record_id"]
        fields = rec.get("fields") or {}
        clue = _text(fields.get(CLUE_FIELD))
        if not clue:
            log.warning("skip enquiry without Clue ID: %s", sid)
            continue
        payload: dict[str, Any] = {CLUE_FIELD: clue}
        for name in ENQ_COPY_FIELDS:
            if name not in fields:
                continue
            cv = _copy_value(fields.get(name), field_name=name)
            if cv is not None:
                payload[name] = cv
        dest_id = existing_by_clue.get(clue)
        if dest_id:
            # 更新时不写主键 Clue ID，避免主字段写权限问题；主键仅创建时写入
            updates.append({"record_id": dest_id, "fields": {k: v for k, v in payload.items() if k != CLUE_FIELD}})
            mapping[sid] = dest_id
        else:
            creates.append({"_src_id": sid, **payload})

    if creates:
        for i in range(0, len(creates), 100):
            chunk = creates[i : i + 100]
            write_chunk = [{k: v for k, v in row.items() if k != "_src_id"} for row in chunk]
            data = _api(
                "POST",
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{DST_BASE}/tables/{DST_ENQ}/records/batch_create",
                token,
                json={"records": [{"fields": r} for r in write_chunk]},
            )
            for src_payload, item in zip(chunk, data.get("data", {}).get("records") or []):
                rid = item.get("record_id")
                if rid:
                    mapping[src_payload["_src_id"]] = rid
                    existing_by_clue[src_payload[CLUE_FIELD]] = rid
            log.info("enquiry create +%s", len(chunk))
            time.sleep(0.2)

    if updates:
        _batch_update(token, DST_BASE, DST_ENQ, updates)
        log.info("enquiry update %s", len(updates))

    for rec in src:
        sid = rec["record_id"]
        if sid in mapping:
            continue
        clue = _text((rec.get("fields") or {}).get(CLUE_FIELD))
        if clue and clue in existing_by_clue:
            mapping[sid] = existing_by_clue[clue]
    return mapping


def sync_followups(token: str, enq_map: dict[str, str]) -> None:
    src = _list_records(token, SRC_BASE, SRC_FU)
    dst = _list_records(token, DST_BASE, DST_FU)
    existing_by_fu = {
        _text((r.get("fields") or {}).get(FU_ID_FIELD)): r["record_id"]
        for r in dst
        if _text((r.get("fields") or {}).get(FU_ID_FIELD))
    }
    log.info("followup src=%s dst_by_fu_id=%s", len(src), len(existing_by_fu))

    creates: list[dict] = []
    updates: list[dict] = []
    for rec in src:
        fields = rec.get("fields") or {}
        fu_id = _text(fields.get(FU_ID_FIELD))
        payload: dict[str, Any] = {}
        if fu_id:
            payload[FU_ID_FIELD] = fu_id
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
        remapped = [enq_map[item] for item in link if isinstance(item, str) and item in enq_map]
        if remapped:
            payload["Related Offline Enquiry 关联线下询盘"] = remapped

        dest_id = existing_by_fu.get(fu_id) if fu_id else None
        if dest_id:
            updates.append({"record_id": dest_id, "fields": payload})
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
