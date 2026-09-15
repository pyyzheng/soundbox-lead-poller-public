#!/usr/bin/env python3
"""从「Enquiry details（询盘内容）」用 AI 提取公司/客户单位，写入线索表字段
「Customer Name 客戶單位」。

说明：飞书原生「AI 字段捷径」无法通过 OpenAPI 创建；本脚本使用项目同款智谱 GLM
做信息提取，效果等价于「从询盘内容提取公司名」。也可在飞书 UI 将该列改配为
AI 信息提取捷径（源字段=询盘内容）。

用法：
  source .env && python3 scripts/backfill_customer_unit_from_enquiry.py
  source .env && python3 scripts/backfill_customer_unit_from_enquiry.py --limit 50
  source .env && python3 scripts/backfill_customer_unit_from_enquiry.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

APP_TOKEN = (
    os.environ.get("FEISHU_BITABLE_APP")
    or os.environ.get("FEISHU_APP_TOKEN")
    or "ZpbUb7SP7azsNasniFjc0bWSnHg"
)
CASE_TABLE = "tbluuuXn9WexH8LV"
FIELD_ENQUIRY = "Enquiry details（询盘内容）"
FIELD_PERSON = "Customer Name（客户名称）"
FIELD_UNIT = "Customer Name 客戶單位"
FIELD_LEAD_ID = "Clue ID"

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")
ZHIPU_BASE_URL = os.environ.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

PROMPT = """你是 B2B 线索解析助手。从询盘正文中提取「客户单位/公司名称」。

规则：
1. 只输出 JSON：{"company":"..."} 
2. company 优先取：公司名、Company、Organization、Firm、单位、工作室、学校/医院/政府机构全称
3. 不要用个人姓名冒充公司；若正文只有人名且无公司信息，company 置空字符串
4. 邮箱域名一般公司名不可靠时不要臆造；署名档里的公司名可用
5. 保持原文语言，不要翻译
6. 若无法确定，返回 {"company":""}

询盘内容：
"""


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


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        parts = []
        for item in v:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("name") or "")
            else:
                parts.append(_cell_text(item))
        return "\n".join(p for p in parts if p).strip()
    if isinstance(v, dict):
        if "value" in v:
            return _cell_text(v.get("value"))
        return (v.get("text") or v.get("name") or "").strip()
    return str(v).strip()


def _list_all(token: str) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{CASE_TABLE}/records",
            headers=_headers(token),
            params=params,
            timeout=90,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        items.extend(data["data"].get("items") or [])
        print(f"  …已拉取 {len(items)}", flush=True)
        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return items


def _extract_company(enquiry: str, person: str) -> str:
    if not ZHIPU_API_KEY:
        raise RuntimeError("缺少 ZHIPU_API_KEY")
    text = (enquiry or "").strip()
    if not text:
        return ""
    # 截断过长正文
    if len(text) > 6000:
        text = text[:6000]
    payload = {
        "model": ZHIPU_MODEL,
        "messages": [
            {"role": "system", "content": "只输出 JSON。"},
            {
                "role": "user",
                "content": PROMPT + text + (f"\n\n已知联系人姓名：{person}" if person else ""),
            },
        ],
        "temperature": 0.1,
    }
    r = requests.post(
        f"{ZHIPU_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {ZHIPU_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        # 尝试截取花括号
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(content[start : end + 1])
        else:
            return ""
    company = str(obj.get("company") or "").strip()
    # 避免把人名原样当公司
    if company and person and company.lower() == person.lower():
        return ""
    return company


def _batch_update(token: str, items: list[tuple[str, str]]) -> int:
    ok = 0
    for i in range(0, len(items), 100):
        chunk = items[i : i + 100]
        r = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{CASE_TABLE}/records/batch_update",
            headers=_headers(token),
            json={
                "records": [
                    {"record_id": rid, "fields": {FIELD_UNIT: val}}
                    for rid, val in chunk
                ]
            },
            timeout=120,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        ok += len(chunk)
        print(f"  已写回 {ok}/{len(items)}", flush=True)
        time.sleep(0.2)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 条（0=全部）")
    parser.add_argument("--force", action="store_true", help="覆盖已有值")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not ZHIPU_API_KEY:
        print("缺少 ZHIPU_API_KEY", file=sys.stderr)
        return 2

    token = _token()
    print("拉取线索…", flush=True)
    records = _list_all(token)
    todo: list[tuple[str, str, str, str]] = []  # rid, lead_id, enquiry, person
    for rec in records:
        fields = rec.get("fields") or {}
        existing = _cell_text(fields.get(FIELD_UNIT))
        if existing and not args.force:
            continue
        enquiry = _cell_text(fields.get(FIELD_ENQUIRY))
        if not enquiry:
            continue
        todo.append(
            (
                rec["record_id"],
                _cell_text(fields.get(FIELD_LEAD_ID)),
                enquiry,
                _cell_text(fields.get(FIELD_PERSON)),
            )
        )
    if args.limit:
        todo = todo[: args.limit]
    print(f"待提取 {len(todo)} 条", flush=True)

    # 边提取边写回，避免中断后进度全丢（空串也写入，便于下次跳过）
    FLUSH_EVERY = 50
    updates: list[tuple[str, str]] = []
    written = 0
    filled = 0
    for i, (rid, lead_id, enquiry, person) in enumerate(todo, 1):
        try:
            company = _extract_company(enquiry, person)
        except Exception as e:
            print(f"  ! {lead_id} 提取失败: {e}", flush=True)
            company = ""
        if args.dry_run:
            print(f"  [{i}] {lead_id} -> {company!r}", flush=True)
        else:
            updates.append((rid, company))
            if company:
                filled += 1
            if i % 20 == 0:
                print(f"  …已提取 {i}/{len(todo)}（非空累计 {filled}）", flush=True)
            if len(updates) >= FLUSH_EVERY:
                written += _batch_update(token, updates)
                updates.clear()
        time.sleep(0.15)  # 控速

    if args.dry_run:
        return 0
    if updates:
        written += _batch_update(token, updates)
    print(f"完成：写回 {written}，其中非空公司 {filled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
