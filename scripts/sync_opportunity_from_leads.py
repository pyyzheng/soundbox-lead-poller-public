#!/usr/bin/env python3
"""把询盘主表 A1–A5 线索同步到商机录入表。

源 Base: ZpbUb7SP7azsNasniFjc0bWSnHg / 线索总池 tbluuuXn9WexH8LV
目标 Base: Ktddb4mhtaixgYs9CIkcNnXFngh / 商机录入表 tblz7klQPWxg9H15
业务员映射: 业务通知名单 tblXq1rE7OQCrSgJ（业务名单 → 对应业务 User）

幂等键: Source Lead ID / 源线索ID ← Clue ID

用法:
  source .env && python3 scripts/sync_opportunity_from_leads.py --full
  source .env && python3 scripts/sync_opportunity_from_leads.py --record-id recXXXX
  source .env && python3 scripts/sync_opportunity_from_leads.py --clue-id 000314
  source .env && python3 scripts/sync_opportunity_from_leads.py --incremental
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from typing import Any

import requests

log = logging.getLogger("opp-sync")

SOURCE_BASE = os.environ.get("FEISHU_APP_TOKEN") or os.environ.get("FEISHU_BITABLE_APP") or "ZpbUb7SP7azsNasniFjc0bWSnHg"
SOURCE_TABLE = os.environ.get("FEISHU_TABLE_ID") or "tbluuuXn9WexH8LV"
ROSTER_TABLE = os.environ.get("FEISHU_SALES_NOTIFY_TABLE") or "tblXq1rE7OQCrSgJ"
TARGET_BASE = os.environ.get("OPP_BASE_TOKEN") or "Ktddb4mhtaixgYs9CIkcNnXFngh"
TARGET_TABLE = os.environ.get("OPP_TABLE_ID") or "tblz7klQPWxg9H15"

CASE_LEVEL_FIELD = "🌟Case Level / 线索分级"
SYNC_LEVELS = {
    "A1-Initial Contact / 初次沟通",
    "A2-Requirement Clarification / 细节沟通",
    "A3-High-Intent Lead / 高意向客户（真实需求)",
    "A4-Won Customer / 成单客户 ",
    "A5-Repeat Purchase Customer / 复购客户 ",
}
# tolerate trimmed variants
SYNC_LEVELS_NORM = {s.strip() for s in SYNC_LEVELS}

# 目标表「Opportunity Stage / 商机阶段」选项（双语全名）
STAGE_MAP = {
    "A1-Initial Contact / 初次沟通": "Initial Interest / 意向沟通",
    "A2-Requirement Clarification / 细节沟通": "Initial Interest / 意向沟通",
    "A3-High-Intent Lead / 高意向客户（真实需求)": "Proposal Discussion / 方案沟通",
    "A4-Won Customer / 成单客户 ": "Won / 已赢单",
    "A5-Repeat Purchase Customer / 复购客户 ": "Won / 已赢单",
}

# 目标表「Opportunity Source / 商机来源」选项（双语全名）
CHANNEL_TO_SOURCE = {
    "谷歌": "Website Inquiry / 官网询盘",
    "Google": "Website Inquiry / 官网询盘",
    "Google（谷歌）": "Website Inquiry / 官网询盘",
    "Facebook": "Social Media / 社交平台",
    "Facebook（脸书）": "Social Media / 社交平台",
    "Instagram": "Social Media / 社交平台",
    "LinkedIn": "Social Media / 社交平台",
    "LinkedIn（领英）": "Social Media / 社交平台",
    "Facebook-Messenger": "Social Media / 社交平台",
    "阿里国际站": "Other / 其他",
    "Alibaba International（阿里国际站）": "Other / 其他",
    "国内渠道": "Other / 其他",
    "Domestic Channel（国内渠道）": "Other / 其他",
    "Outbound渠道": "Other / 其他",
    "Outbound Channel（出站渠道）": "Other / 其他",
    "无法识别": "Other / 其他",
    "Unrecognized（无法识别）": "Other / 其他",
}

# 目标表写字段（双语名）
F_SOURCE_LEAD_ID = "Source Lead ID / 源线索ID"
F_SOURCE_CASE_LEVEL = "Source Case Level / 源Case Level"
F_CUSTOMER_NAME = "Customer Name / 客户名称"
F_CONTACT_NAME = "Contact Name / 联系人姓名"
F_CUSTOMER_EMAIL = "Customer Email / 客户邮箱"
F_PHONE = "Phone / 联系电话"
F_COMPANY_ADDR = "Company Address / 公司地址"
F_CUSTOMER_TYPE = "Customer Type / 客户类型"
F_OPP_DESC = "Opportunity Description / 商机描述"
F_ONE_LINE = "One-line Progress / 一句话进展"
F_OPP_STAGE = "Opportunity Stage / 商机阶段"
F_OPP_SOURCE = "Opportunity Source / 商机来源"
F_INTENDED_PRODUCT = "Intended Product / 意向产品"
F_EXPECTED_AMT = "Expected Amount (CNY) / 预计成交金额（元）"
F_ACTUAL_AMT = "Actual Amount (CNY) / 实际成交金额（元）"
F_SALESPERSON = "Salesperson / 业务员"

PRODUCT_CAT_ALLOWED = {
    "Homepod 家居舱",
    "Silence Booth 静音舱",
    "Acoustic products 声学产品",
}

SOURCE_FIELDS = [
    "Clue ID",
    "Customer Name（客户名称）",
    "Customer Name 客戶單位",
    "Email（客户邮箱）",
    "Phone（客户电话）",
    "单位地址 Address",
    "Customer type",
    "Channels（渠道）",
    "Product Categories（产品大类）",
    "Enquiry details（询盘内容）",
    CASE_LEVEL_FIELD,
    "🌟value of the lead（线索预估价值）",
    "Deal Amount / 成交金额",
    "The final assigned salesperson（最终分配的业务员）",
    "Next Step",
    "Quick Follow-up / 快捷跟进",
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
        resp = requests.request(
            method, url, headers=_headers(token), timeout=90, **kwargs
        )
        try:
            data = resp.json()
        except Exception:
            data = {"code": -1, "msg": resp.text[:300], "http": resp.status_code}
        code = data.get("code")
        if code == 0:
            return data
        if resp.status_code in (429, 500, 502, 503) or code in (1254291, 99991400):
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
    if isinstance(v, str):
        # strip markdown links like [Brook](mailto:...)
        s = v.strip()
        m = re.match(r"^\[([^\]]+)\]\([^)]+\)$", s)
        return (m.group(1) if m else s).strip()
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v == int(v):
            return str(int(v))
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
        return (
            v.get("text")
            or v.get("name")
            or _cell_text(v.get("value"))
            or ""
        )
    return str(v).strip()


def _cell_number(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _cell_text(v).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _option_name(v: Any) -> str:
    if isinstance(v, list) and v:
        first = v[0]
        if isinstance(first, dict):
            return (first.get("text") or first.get("name") or "").strip()
        return _cell_text(first)
    return _cell_text(v)


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
        log.info("…%s 已拉 %s", table, len(items))
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return items


def _search_records(
    token: str,
    base: str,
    table: str,
    filter_obj: dict,
    field_names: list[str] | None = None,
) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        body: dict[str, Any] = {
            "filter": filter_obj,
            "page_size": 200,
            "automatic_fields": False,
        }
        if field_names:
            body["field_names"] = field_names
        if page_token:
            body["page_token"] = page_token
        data = _api(
            "POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/search",
            token,
            json=body,
        )
        batch = data.get("data", {}).get("items") or []
        items.extend(batch)
        log.info("…search %s 已拉 %s", table, len(items))
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
    """业务名单(英文名) → open_id"""
    mapping: dict[str, str] = {}
    for rec in _list_records(token, SOURCE_BASE, ROSTER_TABLE):
        fields = rec.get("fields") or {}
        name = _cell_text(fields.get("业务名单"))
        users = fields.get("对应业务") or []
        if not name or not users:
            continue
        # Brook 行可能是 markdown
        name = _cell_text(name)
        uid = None
        if isinstance(users, list) and users:
            uid = users[0].get("id") if isinstance(users[0], dict) else None
        if name and uid:
            mapping[name] = uid
            # aliases
            if name == "Rita":
                mapping["Rita_USA"] = uid
            if name == "Jessica_US":
                mapping["Jessica"] = uid
    log.info("业务通知名单映射 %s 人", len(mapping))
    return mapping


def case_level_ok(level: str) -> bool:
    if not level:
        return False
    return level in SYNC_LEVELS or level.strip() in SYNC_LEVELS_NORM


def map_stage(level: str) -> str | None:
    if level in STAGE_MAP:
        return STAGE_MAP[level]
    for k, v in STAGE_MAP.items():
        if k.strip() == level.strip():
            return v
    return None


def map_channel_source(channel: str) -> str:
    return CHANNEL_TO_SOURCE.get(channel, "Other / 其他")


def build_fields(src: dict[str, Any], roster: dict[str, str]) -> dict[str, Any] | None:
    level = _option_name(src.get(CASE_LEVEL_FIELD))
    if not case_level_ok(level):
        return None

    clue_id = _cell_text(src.get("Clue ID"))
    if not clue_id:
        return None

    contact = _cell_text(src.get("Customer Name（客户名称）"))
    unit = _cell_text(src.get("Customer Name 客戶單位"))
    channel = _option_name(src.get("Channels（渠道）"))
    product = _option_name(src.get("Product Categories（产品大类）"))
    assignee = _cell_text(src.get("The final assigned salesperson（最终分配的业务员）"))
    next_step = _cell_text(src.get("Next Step")) or _cell_text(
        src.get("Quick Follow-up / 快捷跟进")
    )
    value = _cell_number(src.get("🌟value of the lead（线索预估价值）"))
    deal_amt = _cell_number(src.get("Deal Amount / 成交金额"))

    out: dict[str, Any] = {
        F_SOURCE_LEAD_ID: clue_id,
        F_SOURCE_CASE_LEVEL: level.strip(),
        F_CUSTOMER_NAME: unit or contact,
        F_CONTACT_NAME: contact,
        F_CUSTOMER_EMAIL: _cell_text(src.get("Email（客户邮箱）")),
        F_PHONE: _cell_text(src.get("Phone（客户电话）")),
        F_COMPANY_ADDR: _cell_text(src.get("单位地址 Address")),
        F_CUSTOMER_TYPE: _option_name(src.get("Customer type")),
        F_OPP_DESC: _cell_text(src.get("Enquiry details（询盘内容）")),
        F_ONE_LINE: next_step,
        F_OPP_STAGE: map_stage(level),
        F_OPP_SOURCE: map_channel_source(channel),
    }

    if product in PRODUCT_CAT_ALLOWED:
        out[F_INTENDED_PRODUCT] = [product]

    if value is not None:
        out[F_EXPECTED_AMT] = value
    if deal_amt is not None:
        out[F_ACTUAL_AMT] = str(deal_amt)

    if assignee:
        ou = roster.get(assignee)
        if ou:
            out[F_SALESPERSON] = [{"id": ou}]
        else:
            log.warning("Clue %s 业务员 %s 未在业务通知名单找到飞书账号", clue_id, assignee)

    # drop empties
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def load_target_index(token: str) -> dict[str, str]:
    """源线索ID → record_id"""
    index: dict[str, str] = {}
    for rec in _list_records(token, TARGET_BASE, TARGET_TABLE):
        fields = rec.get("fields") or {}
        clue = _cell_text(fields.get(F_SOURCE_LEAD_ID)) or _cell_text(
            fields.get("源线索ID")
        )
        rid = rec.get("record_id") or rec.get("id")
        if clue and rid:
            index[clue] = rid
    log.info("目标表已有同步键 %s", len(index))
    return index


def upsert(
    token: str, fields: dict[str, Any], index: dict[str, str]
) -> tuple[str, str]:
    clue = fields[F_SOURCE_LEAD_ID]
    rid = index.get(clue)
    if rid:
        _api(
            "PUT",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{TARGET_BASE}/tables/{TARGET_TABLE}/records/{rid}",
            token,
            json={"fields": fields},
        )
        return "updated", rid
    data = _api(
        "POST",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{TARGET_BASE}/tables/{TARGET_TABLE}/records",
        token,
        json={"fields": fields},
    )
    new_id = data["data"]["record"]["record_id"]
    index[clue] = new_id
    return "created", new_id


def iter_source_a2a5(token: str) -> list[dict]:
    """拉取主表全量后本地过滤 A1–A5。"""
    matched: list[dict] = []
    page_token = None
    total = 0
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        data = _api(
            "GET",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{SOURCE_BASE}/tables/{SOURCE_TABLE}/records",
            token,
            params=params,
        )
        batch = data.get("data", {}).get("items") or []
        total += len(batch)
        for rec in batch:
            level = _option_name((rec.get("fields") or {}).get(CASE_LEVEL_FIELD))
            if case_level_ok(level):
                matched.append(rec)
        log.info("…主表已扫 %s，命中 A1–A5 %s", total, len(matched))
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")
        if not page_token:
            break
    return matched


def sync_record_dict(
    token: str, rec: dict, roster: dict[str, str], index: dict[str, str]
) -> str:
    fields_in = rec.get("fields") or {}
    mapped = build_fields(fields_in, roster)
    if not mapped:
        return "skipped"
    action, _ = upsert(token, mapped, index)
    return action


def sync_full(token: str) -> dict[str, int]:
    roster = load_roster(token)
    index = load_target_index(token)
    stats = {"created": 0, "updated": 0, "skipped": 0, "error": 0}
    records = iter_source_a2a5(token)
    log.info("源 A1–A5 共 %s 条", len(records))
    for i, rec in enumerate(records, 1):
        try:
            action = sync_record_dict(token, rec, roster, index)
            stats[action] = stats.get(action, 0) + 1
        except Exception as exc:
            stats["error"] += 1
            rid = rec.get("record_id") or rec.get("id")
            log.exception("同步失败 record=%s: %s", rid, exc)
        if i % 50 == 0:
            log.info("进度 %s/%s %s", i, len(records), stats)
        time.sleep(0.05)
    return stats


def sync_one_record_id(token: str, record_id: str) -> str:
    roster = load_roster(token)
    index = load_target_index(token)
    rec = _get_record(token, SOURCE_BASE, SOURCE_TABLE, record_id)
    return sync_record_dict(token, rec, roster, index)


def sync_one_clue_id(token: str, clue_id: str) -> str:
    roster = load_roster(token)
    index = load_target_index(token)
    filter_obj = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "Clue ID", "operator": "is", "value": [clue_id]}
        ],
    }
    items = _search_records(token, SOURCE_BASE, SOURCE_TABLE, filter_obj, SOURCE_FIELDS)
    if not items:
        return "not_found"
    return sync_record_dict(token, items[0], roster, index)


def sync_incremental(token: str) -> dict[str, int]:
    """全量扫描 A1–A5 并 upsert（幂等，适合 cron）。"""
    return sync_full(token)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="同步询盘 A1–A5 → 商机录入表")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true", help="全量灌入 A1–A5")
    g.add_argument("--incremental", action="store_true", help="增量/cron 幂等同步")
    g.add_argument("--record-id", help="同步单条源 record_id")
    g.add_argument("--clue-id", help="同步单个 Clue ID")
    args = parser.parse_args()

    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not os.environ.get(key):
            log.error("缺少环境变量 %s", key)
            return 2

    token = _token()
    if args.full or args.incremental:
        stats = sync_full(token) if args.full else sync_incremental(token)
        log.info("完成 %s", stats)
        print(stats)
        return 0 if stats.get("error", 0) == 0 else 1
    if args.record_id:
        action = sync_one_record_id(token, args.record_id)
        log.info("record %s → %s", args.record_id, action)
        print(action)
        return 0 if action != "skipped" else 0
    if args.clue_id:
        action = sync_one_clue_id(token, args.clue_id)
        log.info("clue %s → %s", args.clue_id, action)
        print(action)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
