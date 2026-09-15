#!/usr/bin/env python3
"""把询盘 Follow-up Records 同步到商机侧「客户推进记录」。

仅同步：Related Lead 已在商机录入表（源线索ID）存在的跟进。
幂等键：源Follow-up ID ← Follow-up ID

用法:
  source .env && python3 scripts/sync_followup_to_opportunity.py --full
  source .env && python3 scripts/sync_followup_to_opportunity.py --record-id recXXXX
  source .env && python3 scripts/sync_followup_to_opportunity.py --incremental
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("fu-opp-sync")
TZ = ZoneInfo("Asia/Shanghai")

SOURCE_BASE = os.environ.get("FEISHU_APP_TOKEN") or os.environ.get("FEISHU_BITABLE_APP") or "ZpbUb7SP7azsNasniFjc0bWSnHg"
CASE_TABLE = os.environ.get("FEISHU_TABLE_ID") or "tbluuuXn9WexH8LV"
FOLLOWUP_TABLE = os.environ.get("FEISHU_FOLLOWUP_TABLE") or "tbl3n8TTJYXHG12q"
ROSTER_TABLE = os.environ.get("FEISHU_SALES_NOTIFY_TABLE") or "tblXq1rE7OQCrSgJ"

OPP_BASE = os.environ.get("OPP_BASE_TOKEN") or "Ktddb4mhtaixgYs9CIkcNnXFngh"
OPP_TABLE = os.environ.get("OPP_TABLE_ID") or "tblz7klQPWxg9H15"
TARGET_TABLE = os.environ.get("OPP_FOLLOWUP_TABLE") or "tbl5cYBxiF57LhbX"

FU_ID = "Follow-up ID"
FU_RELATED = "Related Lead"
FU_TIME = "Follow-up Time"
FU_METHOD = "Contact Method"
FU_LOCATION = "地点 Location"
FU_ATTENDEES = "参会人 Attendees"
FU_PRODUCTS = "意向产品 Products"
FU_REGION = "合作区域 Region"
FU_QUOTE = "报价折扣 Quote/Disc"
FU_DETAILS = "Follow-up Details"
FU_GIFTS = "赠送礼品 Gifts"
FU_NEXT = "Next Step"
FU_DELETED = "已删除"
FU_SALES = "Salesperson"
FU_CUSTOMER = "Customer Name"

METHOD_OPTIONS = {
    "Email",
    "Phone Call",
    "Whatsapp",
    "Wechat",
    "Online Meeting",
    "Email / Meeting",
    "阿里在线",
    "电商社媒（TK, B2C的FB,INS等）",
    "商城 （B2C谷歌）",
    "Other",
    "Meeting",
    "線上 Online",
    "線下 Offline",
    "電話 Phone",
    "視頻會議 Video Meeting",
    "郵件 Email",
}

METHOD_FALLBACK = {
    "Email,Meeting": "Email / Meeting",
    "Email, Meeting": "Email / Meeting",
    "Whatsapp, Email": "Whatsapp",
    "Whatsapp,Email": "Whatsapp",
    "Email, Whatsapp": "Email",
    "Email,Whatsapp": "Email",
    "Phone Call, Email": "Phone Call",
}


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
    if data.get("code") != 0:
        raise RuntimeError(data)
    return data["tenant_access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api(method: str, url: str, token: str, **kwargs) -> dict:
    last = None
    for attempt in range(5):
        resp = requests.request(method, url, headers=_headers(token), timeout=90, **kwargs)
        data = resp.json()
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


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else ""
    if isinstance(v, str):
        s = v.strip()
        m = re.match(r"^\[([^\]]+)\]\([^)]+\)$", s)
        return (m.group(1) if m else s).strip()
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v > 1e11:
            return datetime.fromtimestamp(v / 1000, tz=TZ).strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, float) and v == int(v):
            return str(int(v))
        return str(v)
    if isinstance(v, list):
        parts = []
        for item in v:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("name") or item.get("email") or "")
            else:
                parts.append(_cell_text(item))
        return " / ".join(p for p in parts if p)
    if isinstance(v, dict):
        if "value" in v:
            return _cell_text(v.get("value"))
        return v.get("text") or v.get("name") or _cell_text(v.get("value")) or ""
    return str(v).strip()


def _option_name(v: Any) -> str:
    if isinstance(v, list) and v:
        first = v[0]
        if isinstance(first, dict):
            return (first.get("text") or first.get("name") or "").strip()
        return _cell_text(first)
    return _cell_text(v)


def _checkbox_true(v: Any) -> bool:
    if v is True:
        return True
    if isinstance(v, str) and v.lower() in {"true", "1", "yes"}:
        return True
    if isinstance(v, (int, float)) and v == 1:
        return True
    return False


def _datetime_ms(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = _cell_text(v)
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(s[:26].replace("+08:00", "+0800"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _link_ids(v: Any) -> list[str]:
    out: list[str] = []

    def walk(obj: Any) -> None:
        if not obj:
            return
        if isinstance(obj, str):
            if obj.startswith("rec"):
                out.append(obj)
            return
        if isinstance(obj, dict):
            for key in ("link_record_ids", "record_ids"):
                vals = obj.get(key)
                if isinstance(vals, list):
                    out.extend(str(x) for x in vals if x)
            for key in ("record_id", "id"):
                if obj.get(key):
                    out.append(str(obj[key]))
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(v)
    # dedupe preserve order
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _user_ids(v: Any) -> list[str]:
    """兼容人员字段 / Lookup 人员：list[{id}]、{id}、{users:[{id}]}。"""
    ids: list[str] = []

    def take(obj: Any) -> None:
        if not obj:
            return
        if isinstance(obj, list):
            for item in obj:
                take(item)
            return
        if isinstance(obj, dict):
            if obj.get("id") and str(obj["id"]).startswith("ou_"):
                ids.append(obj["id"])
            if isinstance(obj.get("users"), list):
                take(obj["users"])
            if "value" in obj:
                take(obj.get("value"))

    take(v)
    # dedupe
    seen: set[str] = set()
    out: list[str] = []
    for uid in ids:
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def _list_all(token: str, base: str, table: str) -> list[dict]:
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
        log.info("…%s 已拉 %s", table, len(items))
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return items


def _get_record(token: str, base: str, table: str, record_id: str) -> dict:
    data = _api(
        "GET",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/{record_id}",
        token,
    )
    return data["data"]["record"]


def load_roster(token: str) -> dict[str, str]:
    """业务名单 / 中文名 / en_name → open_id（参会人与业务员共用）。"""
    mapping: dict[str, str] = {}

    def put(alias: str, uid: str) -> None:
        a = (alias or "").strip()
        if a and uid:
            mapping[a] = uid
            mapping[a.lower()] = uid

    for rec in _list_all(token, SOURCE_BASE, ROSTER_TABLE):
        fields = rec.get("fields") or {}
        name = _cell_text(fields.get("业务名单"))
        users = fields.get("对应业务") or []
        uid = None
        user0: dict[str, Any] = {}
        if isinstance(users, list) and users and isinstance(users[0], dict):
            user0 = users[0]
            uid = user0.get("id")
        if not uid:
            continue
        put(name, uid)
        put(user0.get("name") or "", uid)
        put(user0.get("en_name") or "", uid)
        if name == "Rita":
            put("Rita_USA", uid)
        if name == "Jessica_US":
            put("Jessica", uid)
    return mapping


def resolve_user_ids(raw: str, roster: dict[str, str]) -> list[str]:
    """把逗号/斜杠/顿号分隔的人名解析成 open_id 列表。"""
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,，、/;；|/]+", raw) if p.strip()]
    ids: list[str] = []
    seen: set[str] = set()
    for part in parts:
        uid = roster.get(part) or roster.get(part.lower())
        if uid and uid not in seen:
            seen.add(uid)
            ids.append(uid)
    return ids


def load_opp_index(token: str) -> dict[str, str]:
    """源线索ID → 商机 record_id"""
    index: dict[str, str] = {}
    for rec in _list_all(token, OPP_BASE, OPP_TABLE):
        fields = rec.get("fields") or {}
        clue = _cell_text(fields.get("源线索ID"))
        rid = rec.get("record_id") or rec.get("id")
        if clue and rid:
            index[clue] = rid
    log.info("商机索引 %s", len(index))
    return index


def load_case_clue_map(token: str) -> dict[str, str]:
    """case record_id → Clue ID"""
    mapping: dict[str, str] = {}
    for rec in _list_all(token, SOURCE_BASE, CASE_TABLE):
        fields = rec.get("fields") or {}
        clue = _cell_text(fields.get("Clue ID"))
        rid = rec.get("record_id") or rec.get("id")
        if clue and rid:
            mapping[rid] = clue
    log.info("线索ID映射 %s", len(mapping))
    return mapping


def load_target_index(token: str) -> dict[str, str]:
    """源Follow-up ID → target record_id"""
    index: dict[str, str] = {}
    for rec in _list_all(token, OPP_BASE, TARGET_TABLE):
        fields = rec.get("fields") or {}
        fid = _cell_text(fields.get("源Follow-up ID"))
        rid = rec.get("record_id") or rec.get("id")
        if fid and rid:
            index[fid] = rid
    log.info("目标跟进索引 %s", len(index))
    return index


def map_method(raw: str) -> str | None:
    if not raw:
        return None
    if raw in METHOD_OPTIONS:
        return raw
    if raw in METHOD_FALLBACK:
        return METHOD_FALLBACK[raw]
    # first token
    first = re.split(r"[,/]", raw)[0].strip()
    if first in METHOD_OPTIONS:
        return first
    return None


def build_fields(
    src: dict[str, Any],
    *,
    clue_map: dict[str, str],
    opp_index: dict[str, str],
    roster: dict[str, str],
) -> dict[str, Any] | None:
    if _checkbox_true(src.get(FU_DELETED)):
        return None
    fu_id = _cell_text(src.get(FU_ID))
    if not fu_id:
        return None

    related_ids = _link_ids(src.get(FU_RELATED))
    if not related_ids:
        return None
    clue_id = None
    for rid in related_ids:
        clue_id = clue_map.get(rid)
        if clue_id:
            break
    if not clue_id:
        return None
    opp_id = opp_index.get(clue_id)
    if not opp_id:
        return None  # 尚未进入商机表，不同步

    details = _cell_text(src.get(FU_DETAILS))
    attendees = _cell_text(src.get(FU_ATTENDEES))
    attendee_ids = resolve_user_ids(attendees, roster)
    # 无法解析的人名仍写入跟进正文，避免信息丢失
    unresolved = []
    if attendees:
        for part in re.split(r"[,，、/;；|/]+", attendees):
            p = part.strip()
            if not p:
                continue
            if not (roster.get(p) or roster.get(p.lower())):
                unresolved.append(p)
        if unresolved:
            tip = "、".join(unresolved)
            details = (f"【未识别参会人】{tip}\n{details}" if details else f"【未识别参会人】{tip}")

    next_step = _option_name(src.get(FU_NEXT))
    method = map_method(_option_name(src.get(FU_METHOD)))
    ts = _datetime_ms(src.get(FU_TIME))
    customer = _cell_text(src.get(FU_CUSTOMER))

    # 关联芯片展示用标题（主键目前是时间，标题便于人工识别）
    title_parts: list[str] = []
    if ts is not None:
        title_parts.append(datetime.fromtimestamp(ts / 1000, tz=TZ).strftime("%m-%d"))
    if next_step:
        title_parts.append(next_step[:40])
    if customer:
        title_parts.append(customer[:20])
    follow_title = " · ".join(title_parts) or fu_id

    out: dict[str, Any] = {
        "源Follow-up ID": fu_id,
        "源线索ID": clue_id,
        "跟进标题": follow_title,
        "关联商机": [opp_id],
        "客户名称": customer,
        "地點 Location": _cell_text(src.get(FU_LOCATION)),
        "意向产品": _cell_text(src.get(FU_PRODUCTS)),
        "合作區域 Region": _cell_text(src.get(FU_REGION)),
        "報價折扣 Quote/Disc.": _cell_text(src.get(FU_QUOTE)),
        "Follow-up Records/跟进记录.文本": details,
        "贈送禮品 Gifts": _cell_text(src.get(FU_GIFTS)),
        "推進計劃 Next Step": next_step,
        "One-line Progress/一句话进展": next_step,
    }
    if attendees:
        out["参会人文本 Attendees"] = attendees
    if attendee_ids:
        out["參會人 Attendees"] = [{"id": uid} for uid in attendee_ids]
    if method:
        out["方式 Method"] = method
    if ts is not None:
        out["時間 Date"] = ts

    # salesperson: lookup may already be user objects or names
    sales_users = _user_ids(src.get(FU_SALES))
    if sales_users:
        out["业务员"] = [{"id": sales_users[0]}]
    else:
        sales_name = _cell_text(src.get(FU_SALES))
        uid = roster.get(sales_name) or roster.get(sales_name.lower()) if sales_name else None
        if uid:
            out["业务员"] = [{"id": uid}]

    return {k: v for k, v in out.items() if v not in (None, "", [])}


def upsert(token: str, fields: dict[str, Any], index: dict[str, str]) -> str:
    fu_id = fields["源Follow-up ID"]
    rid = index.get(fu_id)
    if rid:
        _api(
            "PUT",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{OPP_BASE}/tables/{TARGET_TABLE}/records/{rid}",
            token,
            json={"fields": fields},
        )
        return "updated"
    data = _api(
        "POST",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{OPP_BASE}/tables/{TARGET_TABLE}/records",
        token,
        json={"fields": fields},
    )
    index[fu_id] = data["data"]["record"]["record_id"]
    return "created"


def sync_one(
    token: str,
    rec: dict,
    *,
    clue_map: dict[str, str],
    opp_index: dict[str, str],
    roster: dict[str, str],
    target_index: dict[str, str],
) -> str:
    fields = build_fields(
        rec.get("fields") or {},
        clue_map=clue_map,
        opp_index=opp_index,
        roster=roster,
    )
    if not fields:
        return "skipped"
    return upsert(token, fields, target_index)


def sync_full(token: str) -> dict[str, int]:
    roster = load_roster(token)
    opp_index = load_opp_index(token)
    clue_map = load_case_clue_map(token)
    target_index = load_target_index(token)
    stats = {"created": 0, "updated": 0, "skipped": 0, "error": 0}
    records = _list_all(token, SOURCE_BASE, FOLLOWUP_TABLE)
    log.info("源跟进共 %s 条；商机 %s", len(records), len(opp_index))
    for i, rec in enumerate(records, 1):
        try:
            action = sync_one(
                token,
                rec,
                clue_map=clue_map,
                opp_index=opp_index,
                roster=roster,
                target_index=target_index,
            )
            stats[action] = stats.get(action, 0) + 1
        except Exception as exc:
            stats["error"] += 1
            log.exception("同步失败 %s: %s", rec.get("record_id"), exc)
        if i % 100 == 0:
            log.info("进度 %s/%s %s", i, len(records), stats)
        time.sleep(0.03)
    return stats


def sync_record_id(token: str, record_id: str) -> str:
    roster = load_roster(token)
    opp_index = load_opp_index(token)
    clue_map = load_case_clue_map(token)
    target_index = load_target_index(token)
    rec = _get_record(token, SOURCE_BASE, FOLLOWUP_TABLE, record_id)
    return sync_one(
        token,
        rec,
        clue_map=clue_map,
        opp_index=opp_index,
        roster=roster,
        target_index=target_index,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true")
    g.add_argument("--incremental", action="store_true")
    g.add_argument("--record-id")
    args = parser.parse_args()
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not os.environ.get(key):
            log.error("缺少 %s", key)
            return 2
    token = _token()
    if args.full or args.incremental:
        stats = sync_full(token)
        log.info("完成 %s", stats)
        print(stats)
        return 0 if stats.get("error", 0) == 0 else 1
    action = sync_record_id(token, args.record_id)
    log.info("%s → %s", args.record_id, action)
    print(action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
