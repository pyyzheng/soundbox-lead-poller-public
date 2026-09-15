#!/usr/bin/env python3
"""刷新「周花费统计」表单日期默认值，以及看板「上周」筛选。

花费一般在周六录入上一自然周（周一~周日）的数据，看板因此展示上周而非本周。
表单开始/结束日期默认值仍用本周，方便周六补录当前周。

优先使用本机已登录的 lark-cli user 身份（--as user）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

APP_TOKEN = "ZpbUb7SP7azsNasniFjc0bWSnHg"
TABLE_ID = "tblMKntqOhwPmIkM"
FORM_ID = "vewly7GcRD"
FIELD_START = "fldKYEcj7E"
FIELD_END = "flde68Yeqk"
SHARE_TOKEN = "shrcnOEVTjlIOk0i4NrlA6mk3Nd"
DASHBOARD_ID = "blkOSyIMStY6AbUs"
# 广告花费周看板「上周*」组件：筛选上一自然周（周六统计口径）
WEEKLY_DASH_BLOCKS = {
    "chtcnKRQGdxm2o4kSI3hd9Hr0Te": {  # 上周广告花费
        "name": "上周广告花费",
        "type": "statistics",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "本周花费", "rollup": "SUM"}],
            "number_format": {"formatName": "digital"},
        },
    },
    "chtcn3ccAbYLmUlrOwUMTD4xlNe": {  # 上周线索数
        "name": "上周线索数",
        "type": "statistics",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "线索数量", "rollup": "SUM"}],
            "number_format": {"formatName": "digital"},
        },
    },
    "chtcnzrIMVsWXpFyQuK3gutfZie": {  # 上周平均CPA
        "name": "上周平均CPA",
        "type": "statistics",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "周CPA", "rollup": "AVERAGE"}],
            "number_format": {"formatName": "digital"},
        },
    },
    "chtcnjjghPFbhMTu777iwpgPlQc": {  # 上周渠道花费
        "name": "上周渠道花费",
        "type": "column",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "本周花费", "rollup": "SUM"}],
            "group_by": [
                {
                    "field_name": "渠道",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
    },
    "chtcnazpU9urHjZA70PqkTWKkhf": {  # 上周渠道线索
        "name": "上周渠道线索",
        "type": "column",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "线索数量", "rollup": "SUM"}],
            "group_by": [
                {
                    "field_name": "渠道",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
    },
    "chtcnaFPBK1UhUQuPgLqVJ9L3qf": {  # 上周细分渠道花费
        "name": "上周细分渠道花费",
        "type": "column",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "本周花费", "rollup": "SUM"}],
            "group_by": [
                {
                    "field_name": "细分渠道",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
    },
    "chtcnCEnpPhClfESgKWPKHIC9gd": {  # 上周细分渠道线索
        "name": "上周细分渠道线索",
        "type": "column",
        "base": {
            "table_name": "周花费统计",
            "series": [{"field_name": "线索数量", "rollup": "SUM"}],
            "group_by": [
                {
                    "field_name": "细分渠道",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
    },
}
TZ = ZoneInfo("Asia/Shanghai")


def current_week_bounds() -> tuple[str, str]:
    today = datetime.now(TZ).date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def last_week_bounds() -> tuple[str, str]:
    today = datetime.now(TZ).date()
    this_monday = today - timedelta(days=today.weekday())
    monday = this_monday - timedelta(days=7)
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def lark(*args: str) -> dict:
    raw = subprocess.check_output(
        ["lark-cli", "base", *args, "--base-token", APP_TOKEN, "--as", "user", "--format", "json"],
        text=True,
        timeout=90,
    )
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:800])
    return data


def set_date_default(field_id: str, name: str, day: str) -> None:
    lark(
        "+field-update",
        "--table-id",
        TABLE_ID,
        "--field-id",
        field_id,
        "--yes",
        "--json",
        json.dumps(
            {
                "type": "datetime",
                "name": name,
                "style": {"format": "yyyy/MM/dd"},
                "default_value": day,
                "description": "",
            },
            ensure_ascii=False,
        ),
    )


def prefill_url(monday: str, sunday: str) -> str:
    # Feishu form prefill uses yyyy/MM/dd
    m = monday.replace("-", "/")
    s = sunday.replace("-", "/")
    base = f"https://rcn1z5q6iyyc.feishu.cn/share/base/{SHARE_TOKEN}"
    return (
        f"{base}?prefill_{quote('开始日期')}={quote(m)}"
        f"&prefill_{quote('结束日期')}={quote(s)}"
    )


def week_label(monday: str, sunday: str) -> str:
    return f"{monday.replace('-', '/')} ~ {sunday.replace('-', '/')}"


def compact_range(monday: str, sunday: str) -> str:
    """8.31～9.6；跨年时带年份。"""
    start = datetime.strptime(monday, "%Y-%m-%d")
    end = datetime.strptime(sunday, "%Y-%m-%d")
    if start.year != end.year:
        return f"{start.year}.{start.month}.{start.day}～{end.year}.{end.month}.{end.day}"
    return f"{start.month}.{start.day}～{end.month}.{end.day}"


def dated_block_name(base_name: str, monday: str, sunday: str) -> str:
    return f"{base_name}（{compact_range(monday, sunday)}）"


def refresh_dashboard_last_week(monday: str, sunday: str) -> None:
    label = week_label(monday, sunday)
    week_filter = {
        "conjunction": "and",
        "conditions": [{"field_name": "统计周", "operator": "is", "value": [label]}],
    }
    for block_id, meta in WEEKLY_DASH_BLOCKS.items():
        cfg = dict(meta["base"])
        cfg["filter"] = week_filter
        name = dated_block_name(meta["name"], monday, sunday)
        lark(
            "+dashboard-block-update",
            "--dashboard-id",
            DASHBOARD_ID,
            "--block-id",
            block_id,
            "--name",
            name,
            "--data-config",
            json.dumps(cfg, ensure_ascii=False),
        )
        print(f"dashboard block refreshed: {name} -> {label}")


def main() -> int:
    monday, sunday = current_week_bounds()
    last_monday, last_sunday = last_week_bounds()
    print(f"refresh form defaults -> {monday} ~ {sunday}")
    print(f"refresh dashboard last week -> {last_monday} ~ {last_sunday}")
    set_date_default(FIELD_START, "开始日期", monday)
    set_date_default(FIELD_END, "结束日期", sunday)
    # Keep form question titles aligned with field names (for defaults/prefill)
    lark(
        "+form-questions-update",
        "--table-id",
        TABLE_ID,
        "--form-id",
        FORM_ID,
        "--questions",
        json.dumps(
            [
                {"id": FIELD_START, "title": "开始日期", "required": True, "description": ""},
                {"id": FIELD_END, "title": "结束日期", "required": True, "description": ""},
            ],
            ensure_ascii=False,
        ),
    )
    refresh_dashboard_last_week(last_monday, last_sunday)
    print("prefill:", prefill_url(monday, sunday))
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
