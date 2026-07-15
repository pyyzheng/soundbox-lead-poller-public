#!/usr/bin/env python3
"""从飞书拉取代理区域分配工作流，打最小补丁后写回。

修复点（2026-07-03）：
1. 触发条件不再排除「Product model = 无法识别」，让代理工作流能处理迪拜等
   型号未识别但国家/产品已明确的静音舱线索。
2. 精确型号未命中时，仅「型号为空」走待确认；「无法识别」先查全系列规则，
   再决定是否待确认（与 cloud-assignment-unblock._match_agent_rule 口径一致）。
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
