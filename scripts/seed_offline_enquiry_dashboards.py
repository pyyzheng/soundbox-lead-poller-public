#!/usr/bin/env python3
"""Seed offline enquiry/follow-up test data and build daily/weekly/monthly dashboards."""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
ENQ_TABLE = "tblzrNcFHAISzi5a"
FU_TABLE = "tblgqC1lbgFazdUB"
ENQ_NAME = "线下询盘记录 Offline Enquiry"
FU_NAME = "线下跟进记录 Offline Follow-up"

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)
RNG = random.Random(20260922)

DEPTS = [
    "外贸一部（欧洲）",
    "外贸二部（亚洲/中亚）",
    "外贸三部（中东/非洲）",
    "北美业务中心",
    "澳洲办事处",
    "英国办事处",
    "声博士（香港）公司",
    "声博士（日本）公司",
    "德国办事处",
]

COUNTRIES = [
    "United States（美国）",
    "Canada（加拿大）",
    "Australia（澳大利亚）",
    "Germany（德国）",
    "United Kingdom（英国）",
    "Japan（日本）",
    "Hong Kong（香港）",
    "Singapore（新加坡）",
    "United Arab Emirates（阿联酋）",
    "India（印度）",
]

LEVELS = [
    "A0-Invalid / Spam Inquiry / 无效/垃圾询盘",
    "A1-Initial Contact / 初次沟通",
    "A2-Requirement Clarification / 细节沟通",
    "A3-High-Intent Lead / 高意向客户（真实需求)",
    "A4-Won Customer / 成单客户 ",
    "A5-Repeat Purchase Customer / 复购客户 ",
]

TYPES = [
    "A·直接客户（自用）",
    "B·渠道客户（转售）",
    "C·承包商（工程）",
    "D·配套商（代工）",
    "E·居间商（转介绍）",
]

SOURCES = [
    "展会-ISE",
    "展会-InfoComm",
    "代理推荐",
    "官网留资",
    "老客户转介绍",
    "线下拜访",
    "社媒询盘",
]

METHODS = ["Meeting", "Phone Call", "Whatsapp", "Wechat", "Email", "Online Meeting", "Other"]

PRODUCTS = [
    "吸音板 Acoustic Panel",
    "隔音门 Acoustic Door",
    "天花吸音 Ceiling Tile",
    "扩散体 Diffuser",
    "录音棚套装 Studio Kit",
]

CUSTOMERS = [
    ("Nordic Audio Lab", "Erik Johansson", "erik@nordicaudio.se"),
    ("Pacific Sound Works", "Mia Chen", "mia@pacificsound.au"),
    ("Berlin Studio GmbH", "Hans Mueller", "hans@berlinstudio.de"),
    ("Tokyo Acoustic Co", "Yuki Tanaka", "yuki@tokyoacoustic.jp"),
    ("HK AV Partners", "Grace Wong", "grace@hkav.hk"),
    ("London Theatre Fit", "James Clark", "james@londontf.uk"),
    ("Dubai Pro Audio", "Omar Al-Hassan", "omar@dubaipro.ae"),
    ("Seattle Rooms LLC", "Amy Brooks", "amy@seattlerooms.com"),
    ("Toronto QuietSpace", "Liam Patel", "liam@quietspace.ca"),
    ("Singapore SoundHub", "Wei Ling", "weiling@soundhub.sg"),
    ("Mumbai Acoustic Hub", "Raj Sharma", "raj@mumbaiacoustic.in"),
    ("Paris Atelier Son", "Camille Dupont", "camille@atelierson.fr"),
    ("Sydney Media Rooms", "Noah Wright", "noah@sydneymr.au"),
    ("Osaka Live House", "Kenji Sato", "kenji@osakalh.jp"),
    ("Manchester AV Ltd", "Sophie Green", "sophie@manchesterav.uk"),
    ("LA Broadcast Fit", "Carlos Rivera", "carlos@labroadcast.com"),
    ("Shenzhen OEM Audio", "Liu Fang", "liufang@sz-oem.cn"),
    ("Riyadh Quiet Design", "Fatima Al-Rashid", "fatima@riyadhqd.sa"),
    ("Amsterdam Studio BV", "Pieter de Vries", "pieter@amsstudio.nl"),
    ("Vancouver WoodRooms", "Emma Scott", "emma@vanwood.ca"),
    ("Taipei Sound Craft", "Chen Wei", "chenwei@tpsc.tw"),
    ("Bangkok AV Trade", "Narin Suk", "narin@bangkokav.th"),
    ("Melbourne Stage Co", "Oliver King", "oliver@melbstage.au"),
    ("Zurich Quiet Labs", "Anna Meier", "anna@zurichql.ch"),
    ("Chicago Pro Rooms", "Mike Johnson", "mike@chicagopr.com"),
    ("Busan Acoustic Mart", "Park Min", "parkmin@busanam.kr"),
    ("Jakarta Sound Pro", "Andi Wijaya", "andi@jktsp.id"),
    ("Cairo Media Fit", "Youssef Hassan", "youssef@cairofit.eg"),
    ("Lisbon Studio PT", "Ana Costa", "ana@lisbonstudio.pt"),
    ("Warsaw QuietTech", "Piotr Nowak", "piotr@warsawqt.pl"),
    ("Milan Acoustic SRL", "Giulia Romano", "giulia@milanac.it"),
    ("Cape Town Rooms", "Thabo Nkosi", "thabo@ctrooms.za"),
    ("Buenos Aires AV", "Lucia Gomez", "lucia@baav.ar"),
    ("Helsinki SoftWall", "Aino Virtanen", "aino@helsinkisw.fi"),
    ("Dublin Stage Works", "Connor Murphy", "connor@dublinsw.ie"),
    ("KL Acoustic Hub", "Aisha Rahman", "aisha@klah.my"),
]


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
        transient = any(x in msg.lower() for x in ("timeout", "rate", "内部错误", "800008006"))
        if not check:
            return last
        if transient and attempt < retries - 1:
            wait = 2.0 * (attempt + 1)
            print(f"  retry {attempt + 1}: {msg[:120]}")
            time.sleep(wait)
            continue
        raise RuntimeError(f"cmd failed: {' '.join(args[:6])} -> {raw[:800]}")
    raise RuntimeError(f"cmd failed after retries: {last}")


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def date_range_filter(field: str, start: datetime, end: datetime) -> dict[str, Any]:
    # isGreater / isLess with ExactDate; end exclusive-ish via +1 day boundary
    return {
        "conjunction": "and",
        "conditions": [
            {"field_name": field, "operator": "isGreater", "value": ["ExactDate", ms(start - timedelta(seconds=1))]},
            {"field_name": field, "operator": "isLess", "value": ["ExactDate", ms(end)]},
        ],
    }


def seed_enquiries(n: int = 36) -> list[str]:
    records: list[dict[str, Any]] = []
    for i in range(n):
        cust, contact, email = CUSTOMERS[i % len(CUSTOMERS)]
        # denser recent days for daily board
        if i < 8:
            day_offset = RNG.randint(0, 1)  # today/yesterday
        elif i < 18:
            day_offset = RNG.randint(0, 6)
        elif i < 28:
            day_offset = RNG.randint(7, 27)
        else:
            day_offset = RNG.randint(28, 75)
        hour = RNG.randint(9, 18)
        minute = RNG.choice([0, 15, 30, 45])
        dt = (NOW - timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        level = RNG.choices(LEVELS, weights=[1, 4, 5, 3, 2, 1])[0]
        records.append(
            {
                "Customer Name 客戶單位": f"[TEST] {cust}",
                "Contact 对接人": contact,
                "Contact Info 联系方式": email,
                "Lead Source 商机来源": RNG.choice(SOURCES),
                "Country（国家）": [RNG.choice(COUNTRIES)],
                "Customer Type 客户类型": [RNG.choice(TYPES)],
                "所属部门 Department": [RNG.choice(DEPTS)],
                "🌟Case Level / 线索分级": [level],
                "Address 单位地址": f"{cust} HQ",
                "Reg. Date 备案时间": dt.strftime("%Y-%m-%d %H:%M"),
            }
        )

    # batch in chunks of 40
    ids: list[str] = []
    for start in range(0, len(records), 40):
        chunk = records[start : start + 40]
        resp = run(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                BASE,
                "--table-id",
                ENQ_TABLE,
                "--json",
                json.dumps({"create_records": chunk}, ensure_ascii=False),
            ]
        )
        data = resp.get("data") or {}
        id_list = data.get("record_id_list") or []
        created = data.get("records") or data.get("create_records") or []
        for rid in id_list:
            if rid:
                ids.append(rid)
        for item in created:
            if isinstance(item, str):
                ids.append(item)
                continue
            rid = item.get("record_id") or item.get("id")
            if rid:
                ids.append(rid)
        print(
            f"  enquiry batch {start // 40 + 1}: "
            f"ids={len(id_list) or len(created)} sample={json.dumps(id_list[:2] or created[:1], ensure_ascii=False)[:200]}"
        )
        time.sleep(0.4)

    if len(ids) < n:
        # fallback: list recent test records
        listed = run(
            [
                "base",
                "+record-list",
                "--base-token",
                BASE,
                "--table-id",
                ENQ_TABLE,
                "--page-size",
                "100",
            ]
        )
        items = listed.get("data", {}).get("items") or listed.get("data", {}).get("records") or []
        ids = []
        for it in items:
            fields = it.get("fields") or {}
            name = fields.get("Customer Name 客戶單位") or ""
            if isinstance(name, list):
                name = "".join(str(x.get("text", x) if isinstance(x, dict) else x) for x in name)
            if str(name).startswith("[TEST]"):
                ids.append(it.get("record_id") or it.get("id"))
        ids = [x for x in ids if x]
        print(f"  fallback listed test enquiry ids: {len(ids)}")
    return ids[:n]


def seed_followups(enquiry_ids: list[str], per_min: int = 1, per_max: int = 3) -> int:
    records: list[dict[str, Any]] = []
    for eid in enquiry_ids:
        k = RNG.randint(per_min, per_max)
        for j in range(k):
            day_offset = RNG.randint(0, 45)
            dt = (NOW - timedelta(days=day_offset)).replace(
                hour=RNG.randint(9, 19),
                minute=RNG.choice([0, 10, 20, 30, 40, 50]),
                second=0,
                microsecond=0,
            )
            records.append(
                {
                    "Related Offline Enquiry 关联线下询盘": [{"id": eid}],
                    "Follow-up Time 跟进时间": dt.strftime("%Y-%m-%d %H:%M"),
                    "Method 方式": [RNG.choice(METHODS)],
                    "Products 意向产品": RNG.choice(PRODUCTS),
                    "Location 地点": RNG.choice(["客户现场", "展会展位", "线上", "公司会议室", "酒店咖啡厅"]),
                    "Region 合作区域": RNG.choice(["欧洲", "北美", "亚太", "中东", "拉美"]),
                    "Next Step 推进计划": RNG.choice(
                        [
                            "发送报价单",
                            "安排样品寄送",
                            "二次技术沟通",
                            "确认图纸尺寸",
                            "等待客户反馈",
                            "推进合同签署",
                        ]
                    ),
                    "Quote/Disc. 报价折扣": RNG.choice(["标准价", "95折", "9折", "项目特价待批"]),
                    "Gifts 赠送礼品": RNG.choice(["无", "样本册", "小礼品套装", "吸音样块"]),
                    "Attendees 参会人": RNG.choice(["业务员+客户", "业务+技术+客户", "代理商+客户"]),
                    "Other 其他事项": f"[TEST] follow-up note #{j + 1}",
                }
            )

    total = 0
    for start in range(0, len(records), 40):
        chunk = records[start : start + 40]
        resp = run(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                BASE,
                "--table-id",
                FU_TABLE,
                "--json",
                json.dumps({"create_records": chunk}, ensure_ascii=False),
            ]
        )
        data = resp.get("data") or {}
        id_list = data.get("record_id_list") or []
        created = data.get("records") or data.get("create_records") or []
        n = len(id_list) or len(created)
        total += n
        print(f"  followup batch {start // 40 + 1}: created={n}")
        time.sleep(0.4)
    return total


def create_dashboard(name: str) -> str:
    resp = run(["base", "+dashboard-create", "--base-token", BASE, "--name", name])
    data = resp.get("data") or {}
    dash = data.get("dashboard") if isinstance(data.get("dashboard"), dict) else {}
    did = (
        data.get("dashboard_id")
        or data.get("id")
        or dash.get("dashboard_id")
        or dash.get("id")
    )
    if not did:
        raise RuntimeError(f"no dashboard_id: {resp}")
    print(f"created dashboard {name}: {did}")
    return did


def add_block(dashboard_id: str, name: str, btype: str, data_config: dict[str, Any], position: dict[str, int] | None = None) -> None:
    args = [
        "base",
        "+dashboard-block-create",
        "--base-token",
        BASE,
        "--dashboard-id",
        dashboard_id,
        "--name",
        name,
        "--type",
        btype,
        "--data-config",
        json.dumps(data_config, ensure_ascii=False),
    ]
    if position:
        args.extend(["--position", json.dumps(position)])
    run(args)
    print(f"  + {btype}: {name}")
    time.sleep(0.35)


def build_boards() -> dict[str, str]:
    today_start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())  # Monday
    week_end = week_start + timedelta(days=7)
    month_start = today_start.replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    # trend windows
    last7_start = today_start - timedelta(days=6)
    last8w_start = today_start - timedelta(days=55)
    last6m_start = today_start - timedelta(days=180)

    nf = {"formatName": "digital", "precision": 0}

    boards: dict[str, str] = {}

    # ---- Daily ----
    daily_id = create_dashboard("线下询盘_每日看板")
    boards["daily"] = daily_id
    day_f = date_range_filter("Reg. Date 备案时间", today_start, day_end)
    day_fu_f = date_range_filter("Follow-up Time 跟进时间", today_start, day_end)
    last7_f = date_range_filter("Reg. Date 备案时间", last7_start, day_end)
    last7_fu_f = date_range_filter("Follow-up Time 跟进时间", last7_start, day_end)

    add_block(
        daily_id,
        "说明",
        "text",
        {
            "text": (
                "# 线下询盘 · 每日看板\n"
                "基于 **线下询盘记录** 与 **线下跟进记录** 的测试数据看板。\n"
                f"- 今日范围：{today_start.strftime('%Y-%m-%d')}\n"
                "- 含近 7 日趋势对比"
            )
        },
        {"x": 0, "y": 0, "w": 12, "h": 2},
    )
    add_block(
        daily_id,
        "今日新增询盘",
        "statistics",
        {"table_name": ENQ_NAME, "count_all": True, "number_format": nf, "filter": day_f},
        {"x": 0, "y": 2, "w": 3, "h": 2},
    )
    add_block(
        daily_id,
        "今日跟进次数",
        "statistics",
        {"table_name": FU_NAME, "count_all": True, "number_format": nf, "filter": day_fu_f},
        {"x": 3, "y": 2, "w": 3, "h": 2},
    )
    add_block(
        daily_id,
        "今日高意向(A3+)",
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
        daily_id,
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
        daily_id,
        "近7日询盘趋势",
        "line",
        {
            "table_name": ENQ_NAME,
            "count_all": True,
            "filter": last7_f,
            "group_by": [
                {
                    "field_name": "Reg. Date 备案时间",
                    "mode": "integrated",
                    "sort": {"type": "group", "order": "asc"},
                }
            ],
        },
        {"x": 0, "y": 4, "w": 6, "h": 5},
    )
    add_block(
        daily_id,
        "近7日跟进趋势",
        "area",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": last7_fu_f,
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
        daily_id,
        "今日部门询盘分布",
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
        daily_id,
        "今日跟进方式占比",
        "ring",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": day_fu_f,
            "group_by": [
                {
                    "field_name": "Method 方式",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
        {"x": 6, "y": 9, "w": 6, "h": 5},
    )
    add_block(
        daily_id,
        "今日线索分级",
        "funnel",
        {
            "table_name": ENQ_NAME,
            "count_all": True,
            "filter": last7_f,
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
        daily_id,
        "近7日国家TOP",
        "ranking",
        {
            "table_name": ENQ_NAME,
            "count_all": True,
            "limit_size": 8,
            "filter": last7_f,
            "group_by": [
                {
                    "field_name": "Country（国家）",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
        {"x": 6, "y": 14, "w": 6, "h": 5},
    )

    # ---- Weekly ----
    weekly_id = create_dashboard("线下询盘_每周看板")
    boards["weekly"] = weekly_id
    week_f = date_range_filter("Reg. Date 备案时间", week_start, week_end)
    week_fu_f = date_range_filter("Follow-up Time 跟进时间", week_start, week_end)
    last8w_f = date_range_filter("Reg. Date 备案时间", last8w_start, day_end)
    last8w_fu_f = date_range_filter("Follow-up Time 跟进时间", last8w_start, day_end)

    add_block(
        weekly_id,
        "说明",
        "text",
        {
            "text": (
                "# 线下询盘 · 每周看板\n"
                f"- 本周范围：{week_start.strftime('%Y-%m-%d')} ~ {(week_end - timedelta(days=1)).strftime('%Y-%m-%d')}\n"
                "- 含近 8 周趋势与结构分析"
            )
        },
        {"x": 0, "y": 0, "w": 12, "h": 2},
    )
    add_block(
        weekly_id,
        "本周新增询盘",
        "statistics",
        {"table_name": ENQ_NAME, "count_all": True, "number_format": nf, "filter": week_f},
        {"x": 0, "y": 2, "w": 3, "h": 2},
    )
    add_block(
        weekly_id,
        "本周跟进次数",
        "statistics",
        {"table_name": FU_NAME, "count_all": True, "number_format": nf, "filter": week_fu_f},
        {"x": 3, "y": 2, "w": 3, "h": 2},
    )
    add_block(
        weekly_id,
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
        weekly_id,
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
        weekly_id,
        "近8周询盘趋势",
        "line",
        {
            "table_name": ENQ_NAME,
            "count_all": True,
            "filter": last8w_f,
            "group_by": [
                {
                    "field_name": "Reg. Date 备案时间",
                    "mode": "integrated",
                    "sort": {"type": "group", "order": "asc"},
                }
            ],
        },
        {"x": 0, "y": 4, "w": 6, "h": 5},
    )
    add_block(
        weekly_id,
        "近8周跟进趋势",
        "area",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": last8w_fu_f,
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
        weekly_id,
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
        weekly_id,
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
        weekly_id,
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
        weekly_id,
        "本周跟进方式",
        "ring",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": week_fu_f,
            "group_by": [
                {
                    "field_name": "Method 方式",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
        {"x": 6, "y": 14, "w": 6, "h": 5},
    )

    # ---- Monthly ----
    monthly_id = create_dashboard("线下询盘_每月看板")
    boards["monthly"] = monthly_id
    month_f = date_range_filter("Reg. Date 备案时间", month_start, month_end)
    month_fu_f = date_range_filter("Follow-up Time 跟进时间", month_start, month_end)
    last6m_f = date_range_filter("Reg. Date 备案时间", last6m_start, day_end)
    last6m_fu_f = date_range_filter("Follow-up Time 跟进时间", last6m_start, day_end)

    add_block(
        monthly_id,
        "说明",
        "text",
        {
            "text": (
                "# 线下询盘 · 每月看板\n"
                f"- 本月范围：{month_start.strftime('%Y-%m')}\n"
                "- 含近半年趋势、部门/国家结构与线索漏斗"
            )
        },
        {"x": 0, "y": 0, "w": 12, "h": 2},
    )
    add_block(
        monthly_id,
        "本月新增询盘",
        "statistics",
        {"table_name": ENQ_NAME, "count_all": True, "number_format": nf, "filter": month_f},
        {"x": 0, "y": 2, "w": 3, "h": 2},
    )
    add_block(
        monthly_id,
        "本月跟进次数",
        "statistics",
        {"table_name": FU_NAME, "count_all": True, "number_format": nf, "filter": month_fu_f},
        {"x": 3, "y": 2, "w": 3, "h": 2},
    )
    add_block(
        monthly_id,
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
        monthly_id,
        "本月成单(A4/A5)",
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
        monthly_id,
        "近半年询盘趋势",
        "line",
        {
            "table_name": ENQ_NAME,
            "count_all": True,
            "filter": last6m_f,
            "group_by": [
                {
                    "field_name": "Reg. Date 备案时间",
                    "mode": "integrated",
                    "sort": {"type": "group", "order": "asc"},
                }
            ],
        },
        {"x": 0, "y": 4, "w": 6, "h": 5},
    )
    add_block(
        monthly_id,
        "近半年跟进趋势",
        "area",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": last6m_fu_f,
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
        monthly_id,
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
        monthly_id,
        "本月国家分布",
        "pie",
        {
            "table_name": ENQ_NAME,
            "count_all": True,
            "filter": month_f,
            "group_by": [
                {
                    "field_name": "Country（国家）",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
        {"x": 6, "y": 9, "w": 6, "h": 5},
    )
    add_block(
        monthly_id,
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
        monthly_id,
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
        monthly_id,
        "本月意向产品词云",
        "wordCloud",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": month_fu_f,
            "group_by": [{"field_name": "Products 意向产品", "mode": "integrated"}],
        },
        {"x": 0, "y": 19, "w": 6, "h": 5},
    )
    add_block(
        monthly_id,
        "本月跟进方式",
        "ring",
        {
            "table_name": FU_NAME,
            "count_all": True,
            "filter": month_fu_f,
            "group_by": [
                {
                    "field_name": "Method 方式",
                    "mode": "integrated",
                    "sort": {"type": "value", "order": "desc"},
                }
            ],
        },
        {"x": 6, "y": 19, "w": 6, "h": 5},
    )

    for key, did in boards.items():
        run(["base", "+dashboard-arrange", "--base-token", BASE, "--dashboard-id", did])
        print(f"arranged {key}: {did}")
        time.sleep(0.5)

    return boards


def main() -> int:
    print("== seeding offline enquiry test data ==")
    enq_ids = seed_enquiries(36)
    print(f"enquiry ids: {len(enq_ids)}")
    if not enq_ids:
        print("ERROR: no enquiry records created", file=sys.stderr)
        return 1
    fu_n = seed_followups(enq_ids)
    print(f"followups created: {fu_n}")

    print("== building dashboards ==")
    boards = build_boards()
    print("DONE")
    print(json.dumps(boards, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
