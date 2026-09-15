#!/usr/bin/env python3
"""将线索总池 + Follow-up 同步到飞书电子表格「客戶推進備忘記錄」。

目标：与 Memo Excel 模板对齐，并扩展到最多 5 次交流（超出写入「更多跟进摘要」）。
表格：https://rcn1z5q6iyyc.feishu.cn/sheets/PDT2seQIDhuCoxt95zycirSKnFe
子表默认：客户推荐记录（sheet_id=0xLmtT）
数据区从第 4 行起；第 1–3 行保留标题与表头。
默认按线索ID 从大到小排序。

用法：
  source .env && python3 scripts/sync_customer_followup_memo_sheet.py
  source .env && python3 scripts/sync_customer_followup_memo_sheet.py --salesperson Gigi
  source .env && python3 scripts/sync_customer_followup_memo_sheet.py --dry-run
  source .env && python3 scripts/sync_customer_followup_memo_sheet.py --watch --interval 60
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

SPREADSHEET_TOKEN = os.environ.get(
    "MEMO_SHEET_TOKEN", "PDT2seQIDhuCoxt95zycirSKnFe"
)
SHEET_ID = os.environ.get("MEMO_SHEET_ID", "0xLmtT")
HEADER_ROWS = 3
MAX_COMMS = 5  # 第1–5次交流；超过写入「更多跟进摘要」
COLS = 60  # A..BH = 9 客户区 + 50 交流 + 1 摘要
CONTACT_COL = 1  # A = Salesperson 业务员（下拉筛选）
TZ = ZoneInfo("Asia/Shanghai")
FP_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".cache", "memo_sheet_fingerprint.txt"
)

FIELD_LEAD_ID = "Clue ID"
FIELD_CUSTOMER = "Customer Name（客户名称）"
FIELD_UNIT = "Customer Name 客戶單位"
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

COMM_KEYS = [
    FU_TIME,
    FU_METHOD,
    FU_LOCATION,
    FU_ATTENDEES,
    FU_PRODUCTS,
    FU_REGION,
    FU_QUOTE,
    FU_OTHER,
    FU_GIFTS,
    FU_NEXT,
]


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


def _fmt_date(v: Any) -> str:
    dt = _parse_time(v)
    return dt.strftime("%Y/%m/%d") if dt else _cell_text(v)


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


def _fu_val(fu: dict | None, key: str) -> str:
    if not fu:
        return ""
    fields = fu.get("fields") or {}
    if key == FU_TIME:
        return _fmt_date(fields.get(key))
    return _cell_text(fields.get(key))


def _overflow_summary(fus: list[dict]) -> str:
    """第 6 次及以后跟进压缩摘要。"""
    if len(fus) <= MAX_COMMS:
        return ""
    lines = []
    for i, fu in enumerate(fus[MAX_COMMS:], MAX_COMMS + 1):
        bits = [
            f"#{i}",
            _fu_val(fu, FU_TIME),
            _fu_val(fu, FU_METHOD),
            (_fu_val(fu, FU_OTHER) or "")[:80],
            (_fu_val(fu, FU_NEXT) or "")[:60],
        ]
        lines.append(" | ".join(b for b in bits if b))
    return "\n".join(lines)


def build_sheet_rows(
    leads: list[dict],
    by_lead: dict[str, list[dict]],
    salesperson: str | None,
) -> list[list[Any]]:
    prepared: list[tuple[str, list[Any]]] = []
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
        fus = sorted(
            by_lead.get(lead["record_id"], []),
            key=lambda x: _parse_time((x.get("fields") or {}).get(FU_TIME))
            or datetime.min.replace(tzinfo=TZ),
        )
        company = _cell_text(fields.get(FIELD_UNIT))
        person = _cell_text(fields.get(FIELD_CUSTOMER))
        row: list[Any] = [
            final,  # A: 业务员（原序号）
            lead_id,
            company,
            _cell_text(fields.get(FIELD_ADDRESS)),
            person,  # E: 对接人 = 客户姓名
            _contact_info(fields),
            _lead_source(fields),
            _fmt_date(fields.get(FIELD_ENTRY)),
            _customer_type(fields),
        ]
        for i in range(MAX_COMMS):
            fu = fus[i] if i < len(fus) else None
            for key in COMM_KEYS:
                row.append(_fu_val(fu, key))
        row.append(_overflow_summary(fus))
        prepared.append((lead_id, row))

    # 线索ID 从大到小（零填充数字按数值；否则按字符串）
    def _lead_sort_key(lid: str) -> tuple:
        s = str(lid).strip()
        if s.isdigit():
            return (0, -int(s))
        return (1, s)

    prepared.sort(key=lambda x: _lead_sort_key(x[0]))
    out: list[list[Any]] = []
    for _lid, row in prepared:
        out.append(row)
    return out


def _rows_fingerprint(rows: list[list[Any]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
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


def _sheet_meta(token: str) -> dict:
    r = requests.get(
        f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{SPREADSHEET_TOKEN}/sheets/{SHEET_ID}",
        headers=_headers(token),
        timeout=30,
    )
    data = r.json()
    if data.get("code") != 0:
        # fallback list
        r2 = requests.get(
            f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{SPREADSHEET_TOKEN}/sheets/query",
            headers=_headers(token),
            timeout=30,
        )
        data2 = r2.json()
        if data2.get("code") != 0:
            raise RuntimeError(data2)
        for s in data2.get("data", {}).get("sheets") or []:
            if s.get("sheet_id") == SHEET_ID:
                return s
        raise RuntimeError(data)
    return data["data"]["sheet"]


def _ensure_rows(token: str, needed_rows: int) -> None:
    """确保子表至少有 needed_rows 行（含表头）。"""
    meta = _sheet_meta(token)
    grid = meta.get("grid_properties") or {}
    cur = int(grid.get("row_count") or 0)
    if cur >= needed_rows:
        return
    add = needed_rows - cur
    print(f"扩展行数 +{add}（{cur} → {needed_rows}）", flush=True)
    r = requests.post(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/dimension_range",
        headers=_headers(token),
        json={
            "dimension": {
                "sheetId": SHEET_ID,
                "majorDimension": "ROWS",
                "length": add,
            }
        },
        timeout=60,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"expand rows failed: {data}")
    time.sleep(0.3)


def _col_letter(n: int) -> str:
    """1-based column index → Excel letter."""
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _put_values(token: str, start_row: int, rows: list[list[Any]], label: str) -> None:
    """从 start_row 起批量写入（每批最多 100 行；遇限流自动退避重试）。"""
    if not rows:
        return
    batch = 100
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        r0 = start_row + i
        r1 = r0 + len(chunk) - 1
        rng = f"{SHEET_ID}!A{r0}:{_col_letter(COLS)}{r1}"
        for attempt in range(8):
            r = requests.put(
                f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/values",
                headers=_headers(token),
                json={"valueRange": {"range": rng, "values": chunk}},
                timeout=120,
            )
            data = r.json()
            code = data.get("code")
            if code == 0:
                break
            # 90217 too many request / 常见限流码
            if code in (90217, 99991429, 99991400) and attempt < 7:
                wait = min(2 ** attempt, 60)
                print(f"  限流 {code}，{wait}s 后重试 {rng}…", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{label} failed @ {rng}: {data}")
        print(f"  {label} {min(i + len(chunk), len(rows))}/{len(rows)}", flush=True)
        time.sleep(0.6)


def _blank_tail(token: str, first_blank_row: int, last_row: int) -> None:
    """用空值覆盖旧数据尾巴，避免残留。"""
    if first_blank_row > last_row:
        return
    n = last_row - first_blank_row + 1
    print(f"清空旧数据尾巴 {first_blank_row}–{last_row}（{n} 行）", flush=True)
    empty = [[""] * COLS for _ in range(n)]
    _put_values(token, first_blank_row, empty, "清空")


def _write_values(token: str, rows: list[list[Any]]) -> None:
    if not rows:
        print("无数据行可写", flush=True)
        return
    print(f"写入数据 {len(rows)} 行…", flush=True)
    _put_values(token, HEADER_ROWS + 1, rows, "写入")


def _apply_data_borders(token: str, nrows: int) -> None:
    """模板仅自带约前 10 行边框；同步后给全部数据行补全边框与居中。"""
    if nrows <= 0:
        return
    start = HEADER_ROWS + 1
    end = HEADER_ROWS + nrows
    style = {
        "borderType": "FULL_BORDER",
        "border": {"style": "FULL", "color": "#000000"},
        "hAlign": 1,
        "vAlign": 1,
    }
    batch = 400
    print(f"补全边框 A{start}:{_col_letter(COLS)}{end}…", flush=True)
    for r0 in range(start, end + 1, batch):
        r1 = min(r0 + batch - 1, end)
        rng = f"{SHEET_ID}!A{r0}:{_col_letter(COLS)}{r1}"
        for attempt in range(8):
            try:
                r = requests.put(
                    f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/style",
                    headers=_headers(token),
                    json={"appendStyle": {"range": rng, "style": style}},
                    timeout=180,
                )
                data = r.json()
            except requests.exceptions.RequestException as e:
                if attempt < 7:
                    wait = min(2 ** attempt, 60)
                    print(f"  边框网络异常，{wait}s 后重试 {rng}: {e}", flush=True)
                    time.sleep(wait)
                    continue
                print(f"  边框跳过（超时）{rng}: {e}", flush=True)
                break
            code = data.get("code")
            if code == 0:
                print(f"  边框 {r1 - HEADER_ROWS}/{nrows}", flush=True)
                break
            if code in (90217, 99991429, 99991400) and attempt < 7:
                wait = min(2 ** attempt, 60)
                print(f"  限流 {code}，{wait}s 后重试边框 {rng}…", flush=True)
                time.sleep(wait)
                continue
            print(f"  边框跳过 {rng}: {data}", flush=True)
            break
        time.sleep(0.5)


def _apply_contact_dropdown(token: str, rows: list[list[Any]]) -> None:
    """Salesperson 业务员（A 列）设为下拉。"""
    if not rows:
        return
    options = sorted(
        {
            str(r[0]).strip()
            for r in rows
            if len(r) > 0 and r[0] not in (None, "")
        }
    )
    if not options:
        return
    start = HEADER_ROWS + 1
    end = HEADER_ROWS + len(rows)
    col = _col_letter(CONTACT_COL)
    rng = f"{SHEET_ID}!{col}{start}:{col}{end}"
    print(f"设置對接人下拉（{len(options)} 个选项）…", flush=True)
    body = {
        "range": rng,
        "dataValidationType": "list",
        "conditionValues": options,
        "options": {
            "multipleValues": False,
            "highlightValidData": False,
            "colors": [],
        },
    }
    r = requests.post(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/dataValidation",
        headers=_headers(token),
        json=body,
        timeout=60,
    )
    # 部分租户对 bot 写 dataValidation 不稳定；失败不阻断主同步
    try:
        data = r.json()
    except Exception:
        print(f"  警告：下拉接口响应异常 status={r.status_code} body={r.text[:120]}", flush=True)
        return
    if data.get("code") != 0:
        print(f"  警告：下拉未更新 code={data.get('code')} msg={data.get('msg')}", flush=True)
        return
    print(f"  下拉已应用到 {col}{start}:{col}{end}", flush=True)


def sync_once(
    token: str,
    salesperson: str | None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """执行一次同步。返回是否实际写入了表格。"""
    print("拉取线索…", flush=True)
    leads = _list_all(token, CASE_TABLE)
    print("拉取跟进…", flush=True)
    followups = _list_all(token, FOLLOWUP_TABLE)
    by_lead: dict[str, list[dict]] = defaultdict(list)
    for fu in followups:
        fields = fu.get("fields") or {}
        if fields.get(FU_DELETED) is True:
            continue
        for lid in _related_lead_ids(fields.get(FU_RELATED)):
            by_lead[lid].append(fu)

    rows = build_sheet_rows(leads, by_lead, salesperson)
    print(f"待写入 {len(rows)} 行（按线索ID降序）", flush=True)
    if dry_run:
        for sample in rows[:3]:
            print(" sample:", sample[:9], "…")
        return False

    fp = _rows_fingerprint(rows)
    if not force and fp == _load_fp():
        print("源数据无变化，跳过写入", flush=True)
        return False

    needed = HEADER_ROWS + max(len(rows), 1) + 50
    meta_before = _sheet_meta(token)
    old_rows = int((meta_before.get("grid_properties") or {}).get("row_count") or 0)
    _ensure_rows(token, needed)
    _write_values(token, rows)
    _apply_data_borders(token, len(rows))
    _apply_contact_dropdown(token, rows)
    first_blank = HEADER_ROWS + len(rows) + 1
    last_old = max(old_rows, needed)
    _blank_tail(token, first_blank, last_old)
    _save_fp(fp)
    print(
        f"完成。打开："
        f"https://rcn1z5q6iyyc.feishu.cn/sheets/{SPREADSHEET_TOKEN}?sheet={SHEET_ID}"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="同步客户推进备忘到飞书电子表格")
    parser.add_argument("--salesperson", help="只同步指定业务员（模糊匹配最终分配）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="忽略指纹强制重写")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="持续监听：源表有变化则立即回填（近实时）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="--watch 轮询间隔秒数，默认 60",
    )
    args = parser.parse_args()

    token = _token()
    if not args.watch:
        sync_once(token, args.salesperson, dry_run=args.dry_run, force=args.force)
        return 0

    print(
        f"进入近实时监听：每 {args.interval}s 检查线索/跟进变化并回填…",
        flush=True,
    )
    while True:
        try:
            token = _token()  # token 可能过期，每轮刷新
            sync_once(
                token,
                args.salesperson,
                dry_run=args.dry_run,
                force=args.force,
            )
            args.force = False  # 仅首轮 force
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
