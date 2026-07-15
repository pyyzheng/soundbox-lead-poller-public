#!/usr/bin/env python3
"""修复子办规则分配自动化工作流 wkfaNTuMd6vAE5E0。

根因：
1. （历史）跨表 ref 写入「子办规则命中负责人」会在 SetRecordAction 报字段类型不匹配。
2. （2026-07-14）Duplicate（重复）已是公式字段，触发器用 doesNotContainAny+option 报字段类型不匹配。
3. 飞书 UI/同步会把 option id 写回，需每次 strip。

修复策略（与渠道轮转一致）：
- 工作流仅负责：子办国家 + 命中规则时写「是否成功分配=是」。
- Duplicate 条件改为 isNot(text)。
- 「子办规则命中负责人」由 cloud-suboffice-assignee-fix.py 按名称回填。
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from assignment_fields import FIELD_ASSIGN_SOURCE  # noqa: E402
from workflow_bilingual import (  # noqa: E402
    fix_duplicate_formula_in_workflow,
    migrate_workflow_document,
)

WORKFLOW_ID = "wkfaNTuMd6vAE5E0"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
FIELD_ASSIGN_METHOD = "Allocation Method（分配方式）"
FIELD_SUCCESS = "Allocation Status（是否成功分配）"
FIELD_SUBOFFICE_OWNER = "子办规则命中负责人"
OPT_ASSIGN_AUTO = "Automatic（自动）"
OPT_SUCCESS_NO = "No（否）"
OPT_SUCCESS_YES = "Yes（是）"


def _fetch_live(base_token: str) -> dict:
    cmd = [
        "lark-cli", "base", "+workflow-get",
        "--base-token", base_token,
        "--workflow-id", WORKFLOW_ID,
        "--as", "user",
    ]
    raw = subprocess.check_output(cmd, text=True)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["data"]


def _strip_option_ids(node) -> None:
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


def _set_record_step(
    step_id: str,
    *,
    next_step: str | None,
    field_values: list[dict],
) -> dict:
    return {
        "id": step_id,
        "type": "SetRecordAction",
        "title": "修改记录",
        "next": next_step,
        "children": {"links": []},
        "data": {
            "field_values": field_values,
            "filter_info": None,
            "max_set_record_num": 100,
            "ref_info": {"step_id": "trigpJUkcCnQ"},
            "table_name": "线索总池 Case Database",
        },
    }


def _rule_found_switch() -> dict:
    return {
        "id": "actSubRuleSwitch",
        "type": "SwitchBranch",
        "title": "多分支（Switch）",
        "children": {
            "links": [
                {
                    "desc": "命中子办规则",
                    "kind": "case",
                    "label": "branch_1",
                    "to": "act1jaIFY",
                },
                {
                    "desc": "默认分支",
                    "kind": "case",
                    "label": "default",
                    "to": "",
                },
            ]
        },
        "data": {
            "mode": "exclusive",
            "no_match_action": "classifyToOther",
            "child_branch_list": [
                {
                    "name": "命中子办规则",
                    "condition": {
                        "conjunction": "or",
                        "conditions": [
                            {
                                "conjunction": "and",
                                "conditions": [
                                    {
                                        "operator": "isGreater",
                                        "left_value": {
                                            "value": "$.actVWM1Z5.recordNum",
                                            "value_type": "ref",
                                        },
                                        "right_value": [
                                            {"value": 0, "value_type": "number"}
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                }
            ],
        },
    }


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    steps = {s["id"]: s for s in out["steps"]}

    trigger = steps["trigpJUkcCnQ"]
    trigger["next"] = "actPIQDoV"

    # 触发条件：保持子办待分配口径，option 仅保留 name
    trigger["data"]["condition_list"] = [
        {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": FIELD_ASSIGN_SOURCE,
                    "operator": "isNot",
                    "value": [_option("查重命中")],
                },
                {
                    "field_name": FIELD_ASSIGN_SOURCE,
                    "operator": "isNot",
                    "value": [_option("查重冲突")],
                },
                {"field_name": "队列Key", "operator": "isNotEmpty", "value": []},
                {"field_name": "Country（国家）", "operator": "isNotEmpty", "value": []},
                {"field_name": "Channels（渠道）", "operator": "isNotEmpty", "value": []},
                {
                    "field_name": FIELD_ASSIGN_METHOD,
                    "operator": "is",
                    "value": [_option(OPT_ASSIGN_AUTO)],
                },
                {
                    "field_name": FIELD_SUCCESS,
                    "operator": "is",
                    "value": [_option(OPT_SUCCESS_NO)],
                },
                {
                    "field_name": FIELD_SUBOFFICE_OWNER,
                    "operator": "isEmpty",
                    "value": [],
                },
            ],
        }
    ]
    watched = {w["field_name"] for w in trigger["data"].get("field_watch_info", [])}
    for field_name in ("Channels（渠道）", "Country（国家）", FIELD_ASSIGN_METHOD, "是否是子办国家"):
        if field_name not in watched:
            trigger["data"]["field_watch_info"].append({"field_name": field_name})

    country_switch = steps["actPIQDoV"]
    country_switch["children"] = {
        "links": [
            {"desc": "分支 1", "kind": "case", "label": "branch_1", "to": "actVWM1Z5"},
            {"desc": "默认分支", "kind": "case", "label": "default", "to": ""},
        ]
    }
    country_switch["data"]["child_branch_list"] = [
        {
            "name": "分支 1",
            "condition": {
                "conjunction": "or",
                "conditions": [
                    {
                        "conjunction": "and",
                        "conditions": [
                            {
                                "left_value": {
                                    "value": "$.trigpJUkcCnQ.fld9kCu7o6",
                                    "value_type": "ref",
                                },
                                "operator": "is",
                                "right_value": [_option("是")],
                            }
                        ],
                    }
                ],
            },
        }
    ]

    find_rules = steps["actVWM1Z5"]
    find_rules["next"] = "actSubRuleSwitch"
    find_rules["data"]["field_names"] = ["负责人"]
    find_rules["data"]["should_proceed_when_no_results"] = True
    find_rules["data"]["filter_info"] = {
        "conjunction": "and",
        "conditions": [
            {
                "field_name": "国家",
                "operator": "is",
                "value": [
                    {"value": "$.trigpJUkcCnQ.fldAEhwYJU", "value_type": "ref"}
                ],
            },
            {
                "field_name": "是否启用",
                "operator": "is",
                "value": [_option("启用")],
            },
        ],
    }

    mark_success = _set_record_step(
        "act1jaIFY",
        next_step=None,
        field_values=[
            {
                "field_name": FIELD_SUCCESS,
                "value": [_option(OPT_SUCCESS_YES)],
            }
        ],
    )

    out["steps"] = [
        trigger,
        country_switch,
        find_rules,
        _rule_found_switch(),
        mark_success,
    ]

    _strip_option_ids(out["steps"])
    body = migrate_workflow_document({"title": out["title"], "steps": out["steps"]})
    return fix_duplicate_formula_in_workflow(body)


def main() -> int:
    import os

    base_token = os.environ.get(BASE_TOKEN_ENV, "ZpbUb7SP7azsNasniFjc0bWSnHg")
    live = _fetch_live(base_token)
    body = patch_workflow(live)
    root = Path(__file__).resolve().parents[1]
    out_path = root / "workflows" / f"{WORKFLOW_ID}-子办规则分配自动化.json"
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cmd = [
        "lark-cli", "base", "+workflow-update",
        "--base-token", base_token,
        "--workflow-id", WORKFLOW_ID,
        "--json", f"@{out_path.relative_to(root)}",
        "--as", "user",
    ]
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        return result.returncode
    print("patched suboffice assignment workflow deployed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
