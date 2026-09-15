#!/usr/bin/env python3
"""谨慎清理 Facebook 补录误导入：仅处理 7/1 13:55 之后录入的 Facebook 渠道记录。

保留规则：
1. 绝不删除 Entry Time < 2026-07-01 13:55:00 的任何记录
2. 仅处理 Channels=Facebook（不含 Messenger 等其他渠道）
3. 保留 Meta 线索 created_time 在 2026-06-30 ~ 2026-07-01 且邮箱未在 13:55 前存在的记录
4. 删除：13:55 后录入 + (线索日期不在 6/30~7/1 OR 邮箱与 13:55 前记录重复 OR 批次内重复)
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from assignment_fields import FIELD_LEAD_ID  # noqa: E402
from feishu_utils import (  # noqa: E402
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)

CUTOFF_CST = "2026-07-01 13:55:00"  # 北京时间，13:55 前不动
# 飞书 Entry Time 为 UTC 毫秒；13:55 CST = 05:55 UTC
CST_CUTOFF_MS = int(datetime(2026, 7, 1, 5, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)

META_SINCE = "2026-06-30T00:00:00+0000"
META_UNTIL = "2026-07-02T00:00:00+0000"
META_SINCE_TS = int(datetime.fromisoformat("2026-06-30T00:00:00+00:00").timestamp())
META_UNTIL_TS = int(datetime.fromisoformat("2026-07-02T00:00:00+00:00").timestamp())

FIELD_ENTRY = "Entry Time（录入时间）"
FIELD_CHANNELS = "Channels（渠道）"
FIELD_EMAIL = "Email（客户邮箱）"
CHANNEL_FB = "Facebook"

DRY_RUN = os.environ.get("CLEANUP_DRY_RUN", "true").lower() != "false"


def _normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value or value in {"n/a", "na", "none", "-"}:
        return ""
    if "," in value:
        for part in value.split(","):
            p = part.strip()
            if p and p not in {"n/a", "na"}:
                return p
        return ""
    return value


def _search_all(token: str, body: dict) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/search?page_size=500"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = feishu_api("POST", url, token=token, json=body, max_retries=3)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书查询失败: {data}")
        chunk = data.get("data", {})
        items.extend(chunk.get("items", []))
        if not chunk.get("has_more"):
            break
        page_token = chunk.get("page_token", "")
        if not page_token:
            break
    return items


def _get_page_token() -> str:
    user_token = os.environ.get("META_PAGE_ACCESS_TOKEN", "")
    if not user_token:
        raise RuntimeError("META_PAGE_ACCESS_TOKEN 未设置")
    dbg = requests.get(
        "https://graph.facebook.com/v21.0/debug_token",
        params={"input_token": user_token, "access_token": user_token},
        timeout=15,
    ).json()
    if dbg.get("data", {}).get("type") == "PAGE" and dbg.get("data", {}).get("profile_id") == "504816599699127":
        return user_token
    acct = requests.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={"fields": "id,access_token", "access_token": user_token},
        timeout=30,
    ).json()
    if "error" in acct:
        raise RuntimeError(f"无法换取 Page Token: {acct['error']}")
    for page in acct.get("data", []):
        if page.get("id") == "504816599699127":
            return page["access_token"]
    raise RuntimeError("未找到 Soundbox Acoustic Page Token")


def _fetch_meta_leads() -> dict[str, dict]:
    """lead_id -> {email, created_ts} for Jun30-Jul1 only."""
    page_token = _get_page_token()
    forms = requests.get(
        f"https://graph.facebook.com/v21.0/504816599699127/leadgen_forms",
        params={"access_token": page_token, "limit": 100},
        timeout=30,
    ).json().get("data", [])

    valid: dict[str, dict] = {}
    for form in forms:
        url = f"https://graph.facebook.com/v21.0/{form['id']}/leads"
        params = {"access_token": page_token, "fields": "id,created_time,field_data", "limit": 500}
        pages = 0
        while url and pages < 50:
            resp = requests.get(url, params=params if pages == 0 else None, timeout=60)
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"Meta API: {data['error']}")
            for lead in data.get("data", []):
                created = lead.get("created_time", "")
                try:
                    ts = int(datetime.fromisoformat(created.replace("+0000", "+00:00")).timestamp())
                except (ValueError, TypeError):
                    continue
                if ts < META_SINCE_TS or ts >= META_UNTIL_TS:
                    continue
                email = ""
                for fd in lead.get("field_data", []):
                    if fd.get("name", "").lower() in ("email", "work_email", "business_email"):
                        vals = fd.get("values") or []
                        email = (vals[0] if vals else "").strip().lower()
                        break
                valid[lead["id"]] = {"email": _normalize_email(email), "created_ts": ts}
            url = data.get("paging", {}).get("next")
            pages += 1
    return valid


def main() -> int:
    token = get_feishu_token()
    print(f"模式: {'DRY-RUN' if DRY_RUN else 'DELETE'}")
    print(f"保护: Entry Time < {CUTOFF_CST} CST (ms={CST_CUTOFF_MS})")
    print("渠道: 仅 Facebook")

    all_fb = _search_all(
        token,
        {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FIELD_CHANNELS, "operator": "is", "value": [CHANNEL_FB]},
                ],
            },
            "field_names": [FIELD_ENTRY, FIELD_LEAD_ID, FIELD_EMAIL, FIELD_CHANNELS],
            "sort": [{"field_name": FIELD_ENTRY, "desc": True}],
        },
    )
    print(f"Facebook 总记录: {len(all_fb)}")

    protected_emails: set[str] = set()
    candidates: list[dict] = []
    for item in all_fb:
        fields = item.get("fields", {})
        entry_ms = fields.get(FIELD_ENTRY, 0) or 0
        email = _normalize_email(extract_text(fields.get(FIELD_EMAIL, "")))
        row = {
            "record_id": item["record_id"],
            "lead_id": extract_text(fields.get(FIELD_LEAD_ID, "")),
            "email": email,
            "entry_ms": entry_ms,
        }
        if entry_ms < CST_CUTOFF_MS:
            if email:
                protected_emails.add(email)
        else:
            candidates.append(row)

    print(f"13:55 前保护邮箱数: {len(protected_emails)}")
    print(f"13:55 后候选记录 (Facebook): {len(candidates)}")

    meta_valid = _fetch_meta_leads()
    valid_emails = {v["email"] for v in meta_valid.values() if v["email"]}
    print(f"Meta 6/30~7/1 有效线索: {len(meta_valid)} 条, 邮箱 {len(valid_emails)} 个")

    to_delete: list[dict] = []
    to_keep: list[dict] = []
    for row in candidates:
        reasons = []
        if row["email"] and row["email"] in protected_emails:
            reasons.append("dup_protected_email")
        if row["email"] and row["email"] not in valid_emails:
            reasons.append("not_in_meta_jun30_jul1")
        if not row["email"]:
            reasons.append("no_email")
        if reasons:
            to_delete.append({**row, "reasons": reasons})
        else:
            to_keep.append(row)

    # 13:55 后批次内同邮箱只保留线索ID最小的一条
    by_email: dict[str, dict] = {}
    for row in sorted(to_keep, key=lambda r: int(re.sub(r"\D", "", r["lead_id"] or "0") or "0")):
        if not row["email"]:
            continue
        if row["email"] in by_email:
            to_delete.append({**row, "reasons": ["dup_batch_email"]})
        else:
            by_email[row["email"]] = row
    to_keep = list(by_email.values())

    print(f"\n保留 (13:55后且有效): {len(to_keep)}")
    for r in to_keep[:10]:
        print(f"  KEEP {r['lead_id']} {r['email']}")
    if len(to_keep) > 10:
        print(f"  ... 还有 {len(to_keep)-10} 条")

    print(f"\n待删除: {len(to_delete)}")
    by_reason: dict[str, int] = {}
    for r in to_delete:
        for reason in r["reasons"]:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    print("  原因统计:", by_reason)
    for r in to_delete[:15]:
        print(f"  DEL {r['lead_id']} {r['email']} | {','.join(r['reasons'])}")
    if len(to_delete) > 15:
        print(f"  ... 还有 {len(to_delete)-15} 条")

    if DRY_RUN:
        print("\nDRY-RUN 完成，未删除。设置 CLEANUP_DRY_RUN=false 执行删除。")
        return 0

    deleted = 0
    for r in to_delete:
        resp = feishu_api(
            "DELETE",
            (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
                f"/tables/{FEISHU_TABLE_ID}/records/{r['record_id']}"
            ),
            token=token,
            max_retries=3,
        )
        data = resp.json()
        if data.get("code") == 0:
            deleted += 1
        else:
            print(f"删除失败 {r['lead_id']}: {data}")
    print(f"\n完成: 删除 {deleted}/{len(to_delete)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
