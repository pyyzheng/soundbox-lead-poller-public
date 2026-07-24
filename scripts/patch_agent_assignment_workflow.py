#!/usr/bin/env python3
"""从飞书拉取代理区域分配工作流，打最小补丁后写回。

修复点（2026-07-03）：
1. 触发条件不再排除「Product model = 无法识别」，让代理工作流能处理迪拜等
   型号未识别但国家/产品已明确的静音舱线索。
2. 精确型号未命中时，仅「型号为空」走待确认；「无法识别」先查全系列规则，
   再决定是否待确认（与 cloud-assignment-unblock._match_agent_rule 口径一致）。

修复点（2026-07-20）：
3. 代理规则表「国家」重建为动态选项后，FindRecord 过滤里旧 field id
   fldqTJjAD7 失效，改为字段名「国家」（或当前 field id）。

修复点（2026-07-22）：
4. 禁止跨表 ref 写「代理规则命中业务员」：与渠道轮转/子办相同，
   FindRecord→SetRecord 单选 ref 会报字段类型不匹配。
   业务员由 cloud-assignment-unblock.py 按规则表名称写入。

修复点（2026-07-24）：
5. 监听「是否命中代理国家」：先国家=无法识别/空，后补国家时 Lookup 可能晚于
   Country 变更才变为「是」；若不监听该 Lookup，工作流不会重跑 → 一直分配异常。
6. 监听「是否命中代理产品」：工作流只写了命中=是、业务员由 unblock 补写时，
   产品字段变化也可再次进入分配链路。
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from workflow_bilingual import (  # noqa: E402
    fix_duplicate_formula_in_workflow,
    migrate_workflow_document,
)

WORKFLOW_ID = "wkfKWPVBWT0NisJV"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
UNRECOGNIZED_MODEL = "无法识别"
# 历史静态选项字段 id；动态选项重建后必须替换
STALE_COUNTRY_FIELD_IDS = ("fldqTJjAD7",)
COUNTRY_FIELD_NAME = "国家"


def _fetch_live(base_token: str) -> dict:
    cmd = [
        "lark-cli",
        "base",
        "+workflow-get",
        "--base-token",
        base_token,
        "--workflow-id",
        WORKFLOW_ID,
        "--as",
        "user",
    ]
    raw = subprocess.check_output(cmd, text=True)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["data"]


def _strip_option_ids(node):
    if isinstance(node, list):
        for item in node:
            _strip_option_ids(item)
        return
    if not isinstance(node, dict):
        return
    if node.get("value_type") == "option":
        value = node.get("value")
        if isinstance(value, dict) and "name" in value and "id" in value:
            del value["id"]
    for child in node.values():
        _strip_option_ids(child)


def _option_name(value_item: dict) -> str:
    value = value_item.get("value", {})
    if isinstance(value, dict):
        return value.get("name", "")
    return ""


def _fix_stale_country_field_refs(node) -> None:
    """FindRecord 等处若仍引用已删除的国家 field id，改为字段名「国家」。"""
    if isinstance(node, list):
        for item in node:
            _fix_stale_country_field_refs(item)
        return
    if not isinstance(node, dict):
        return
    if node.get("field_name") in STALE_COUNTRY_FIELD_IDS:
        node["field_name"] = COUNTRY_FIELD_NAME
    for child in node.values():
        _fix_stale_country_field_refs(child)


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    steps = {s["id"]: s for s in out["steps"]}

    trigger = steps["trigYl0y5W"]
    conds = trigger["data"]["condition_list"][0]["conditions"]
    trigger["data"]["condition_list"][0]["conditions"] = [
        c
        for c in conds
        if not (
            c.get("field_name") == "Product model（具体型号）"
            and c.get("operator") == "doesNotContainAny"
            and any(_option_name(v) == UNRECOGNIZED_MODEL for v in c.get("value", []))
        )
        and c.get("field_name") not in ("Duplicate（重复）", "分配来源")
    ]

    branch = steps["branch3AHWu3mS"]
    groups = branch["data"]["condition"]["conditions"]
    branch["data"]["condition"]["conditions"] = [
        g
        for g in groups
        if not any(
            c.get("left_value", {}).get("value") == "$.trigYl0y5W.fld1mYUXOF"
            and c.get("operator") == "is"
            and any(_option_name(v) == UNRECOGNIZED_MODEL for v in c.get("right_value", []))
            for c in g.get("conditions", [])
        )
    ]

    _fix_stale_country_field_refs(out["steps"])

    # 去掉所有跨表写「代理规则命中业务员」；保留是否命中代理产品 / Allocation Status
    for step in out["steps"]:
        if step.get("type") != "SetRecordAction":
            continue
        field_values = (step.get("data") or {}).get("field_values") or []
        step["data"]["field_values"] = [
            fv for fv in field_values if fv.get("field_name") != "代理规则命中业务员"
        ]

    # 后补国家后 Lookup「是否命中代理国家」才变为「是」——必须监听，否则永不重跑
    watch = trigger["data"].setdefault("field_watch_info", [])
    for name in ("是否命中代理国家", "是否命中代理产品", "Allocation Method（分配方式）"):
        if not any(w.get("field_name") == name for w in watch):
            watch.append({"field_name": name})

    _strip_option_ids(out["steps"])
    body = migrate_workflow_document({"title": out["title"], "steps": out["steps"]})
    return fix_duplicate_formula_in_workflow(body)


def main() -> int:
    import os

    base_token = os.environ.get(BASE_TOKEN_ENV, "ZpbUb7SP7azsNasniFjc0bWSnHg")
    live = _fetch_live(base_token)
    body = patch_workflow(live)
    out_path = Path(__file__).resolve().parents[1] / "workflows" / f"{WORKFLOW_ID}-代理区域分配自动化.json"
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cmd = [
        "lark-cli",
        "base",
        "+workflow-update",
        "--base-token",
        base_token,
        "--workflow-id",
        WORKFLOW_ID,
        "--json",
        f"@{out_path.relative_to(out_path.parents[1])}",
        "--as",
        "user",
    ]
    result = subprocess.run(cmd, cwd=out_path.parents[1], capture_output=True, text=True)
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        return result.returncode
    print("patched agent assignment workflow deployed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
