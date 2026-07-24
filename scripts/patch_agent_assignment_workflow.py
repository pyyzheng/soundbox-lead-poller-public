#!/usr/bin/env python3
"""从飞书拉取代理区域分配工作流，打最小补丁后写回。

修复点（2026-07-03）：
1. 触发条件不再排除「Product model = 无法识别」，让代理工作流能处理迪拜等
   型号未识别但国家/产品已明确的静音舱线索。
2. 精确型号未命中时，仅「型号为空」走待确认；「无法识别」先查全系列规则，
   再决定是否待确认（与 cloud-assignment_unblock._match_agent_rule 口径一致）。

修复点（2026-07-20）：
3. 代理规则表「国家」重建为动态选项后，FindRecord 过滤里旧 field id
   fldqTJjAD7 失效，改为字段名「国家」（或当前 field id）。

修复点（2026-07-24）：
4. 监听「是否命中代理国家 / 是否命中代理产品」：后补国家后 Lookup 变「是」会重跑。
5. **工作流必须写回「代理规则命中业务员」**（不可只写命中产品=是）：
   规则表「业务员」已挂主表动态选项；用 FindRecord.firstfieldsRecord 跨表 ref。
   精确命中 / 全系列命中两路都写业务员，并标 Allocation Status=Yes。
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
STALE_COUNTRY_FIELD_IDS = ("fldqTJjAD7",)
COUNTRY_FIELD_NAME = "国家"
# 代理优先规则表.业务员（与主表「代理规则命中业务员」动态选项同源）
AGENT_RULE_ASSIGNEE_FIELD_ID = "fldcmDUWhH"
FIELD_AGENT_ASSIGNEE = "代理规则命中业务员"
FIELD_AGENT_PRODUCT = "是否命中代理产品"
FIELD_SUCCESS = "Allocation Status（是否成功分配）"


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


def _option(name: str) -> dict:
    return {"value": {"name": name}, "value_type": "option"}


def _option_name(value_item: dict) -> str:
    value = value_item.get("value", {})
    if isinstance(value, dict):
        return value.get("name", "")
    return ""


def _assignee_ref(find_step_id: str) -> dict:
    return {
        "field_name": FIELD_AGENT_ASSIGNEE,
        "value": [
            {
                "value": f"$.{find_step_id}.firstfieldsRecord.{AGENT_RULE_ASSIGNEE_FIELD_ID}",
                "value_type": "ref",
            }
        ],
    }


def _fix_stale_country_field_refs(node) -> None:
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


def _set_success_with_assignee(step: dict, *, find_step_id: str, product: str) -> None:
    """命中规则：写产品标记 + 业务员 + 成功分配。"""
    step["data"]["field_values"] = [
        {"field_name": FIELD_AGENT_PRODUCT, "value": [_option(product)]},
        _assignee_ref(find_step_id),
        {"field_name": FIELD_SUCCESS, "value": [_option("Yes（是）")]},
    ]


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

    # 精确型号命中（Find actlMXhJW 有结果）→ 写业务员
    _set_success_with_assignee(steps["actfxjmzT"], find_step_id="actlMXhJW", product="是")
    # 全系列命中（Find act3Nvoal 有结果）→ 写业务员
    _set_success_with_assignee(steps["actBTQP7f"], find_step_id="act3Nvoal", product="是")

    # FindRecord 无结果时继续往下走，才能落到全系列 / 否
    steps["actlMXhJW"]["data"]["should_proceed_when_no_results"] = True
    steps["act3Nvoal"]["data"]["should_proceed_when_no_results"] = True
    steps["actlMXhJW"]["data"]["field_names"] = ["业务员"]
    steps["act3Nvoal"]["data"]["field_names"] = ["业务员"]

    watch = trigger["data"].setdefault("field_watch_info", [])
    for name in (
        "是否命中代理国家",
        "是否命中代理产品",
        "Allocation Method（分配方式）",
        FIELD_AGENT_ASSIGNEE,
    ):
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
    print("patched agent workflow: FindRecord → write 代理规则命中业务员 + Success=Yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
