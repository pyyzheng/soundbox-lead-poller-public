#!/usr/bin/env python3
"""修复并恢复子办规则分配自动化工作流 wkfaNTuMd6vAE5E0。

策略（2026-07-24）：
- Switch(是否是子办国家) → FindRecord(子办分配规则表) → 写「子办规则命中负责人」+ Success=Yes
- 子办表「国家」「负责人」已挂主表动态选项；用 firstfieldsRecord 跨表 ref
- Lookup「是否是子办国家」条件用 option（飞书不允许 text）
- unblock / cloud-suboffice-assignee-fix 仅作兜底
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

WORKFLOW_ID = "wkfaNTuMd6vAE5E0"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
FIELD_ASSIGN_METHOD = "Allocation Method（分配方式）"
FIELD_SUCCESS = "Allocation Status（是否成功分配）"
FIELD_SUBOFFICE_OWNER = "子办规则命中负责人"
FIELD_SUBOFFICE_FLAG = "是否是子办国家"
OPT_ASSIGN_AUTO = "Automatic（自动）"
OPT_SUCCESS_YES = "Yes（是）"
OPT_SUCCESS_NO = "No（否）"
OPT_YES_CN = "是"
OPT_NO_CN = "否"
# 子办分配规则表.负责人
SUBOFFICE_OWNER_FIELD_ID = "fldATnmAXs"
# 主表 Country
MAIN_COUNTRY_FIELD_ID = "fldAEhwYJU"
# 是否是子办国家 Lookup
SUBOFFICE_FLAG_FIELD_ID = "fld9kCu7o6"


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


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    title = out.get("title") or "子办规则分配自动化"

    trigger = {
        "id": "trigpJUkcCnQ",
        "type": "SetRecordTrigger",
        "title": "修改记录时",
        "next": "actPIQDoV",
        "children": {"links": []},
        "data": {
            "table_name": "线索总池 Case Database",
            "condition_list": [
                {
                    "conjunction": "and",
                    "conditions": [
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
            ],
            "field_watch_info": [
                {"field_name": "Channels（渠道）"},
                {"field_name": "Country（国家）"},
                {"field_name": FIELD_ASSIGN_METHOD},
                {"field_name": FIELD_SUBOFFICE_FLAG},
                {"field_name": FIELD_SUBOFFICE_OWNER},
            ],
            "trigger_control_list": [
                "pasteUpdate",
                "automationBatchUpdate",
                "appendImport",
                "openAPIBatchUpdate",
            ],
        },
    }
    live_steps = {s["id"]: s for s in out.get("steps", [])}
    if "trigpJUkcCnQ" in live_steps:
        live_data = live_steps["trigpJUkcCnQ"].get("data") or {}
        if live_data.get("trigger_control_list"):
            trigger["data"]["trigger_control_list"] = live_data["trigger_control_list"]
        watched = {w["field_name"] for w in trigger["data"]["field_watch_info"]}
        for w in live_data.get("field_watch_info", []):
            name = w.get("field_name")
            if name and name not in watched:
                trigger["data"]["field_watch_info"].append({"field_name": name})

    country_switch = {
        "id": "actPIQDoV",
        "type": "SwitchBranch",
        "title": "多分支（Switch）",
        "next": None,
        "children": {
            "links": [
                {"desc": "分支 1", "kind": "case", "label": "branch_1", "to": "actVWM1Z5"},
                {"desc": "分支 2", "kind": "case", "label": "branch_2", "to": "acteIacr4"},
                {"desc": "默认分支", "kind": "case", "label": "default", "to": ""},
            ]
        },
        "data": {
            "child_branch_list": [
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
                                            "value": f"$.trigpJUkcCnQ.{SUBOFFICE_FLAG_FIELD_ID}",
                                            "value_type": "ref",
                                        },
                                        "operator": "is",
                                        "right_value": [_option(OPT_YES_CN)],
                                    }
                                ],
                            }
                        ],
                    },
                },
                {
                    "name": "分支 2",
                    "condition": {
                        "conjunction": "or",
                        "conditions": [
                            {
                                "conjunction": "and",
                                "conditions": [
                                    {
                                        "left_value": {
                                            "value": f"$.trigpJUkcCnQ.{SUBOFFICE_FLAG_FIELD_ID}",
                                            "value_type": "ref",
                                        },
                                        "operator": "is",
                                        "right_value": [_option(OPT_NO_CN)],
                                    }
                                ],
                            }
                        ],
                    },
                },
            ]
        },
    }

    find_record = {
        "id": "actVWM1Z5",
        "type": "FindRecordAction",
        "title": "查找记录",
        "next": "actDMTfWM",
        "children": {"links": []},
        "data": {
            "field_names": ["负责人"],
            "filter_info": {
                "conditions": [
                    {
                        "field_name": "国家",
                        "operator": "is",
                        "value": [
                            {
                                "value": f"$.trigpJUkcCnQ.{MAIN_COUNTRY_FIELD_ID}",
                                "value_type": "ref",
                            }
                        ],
                    },
                    {
                        "field_name": "是否启用",
                        "operator": "is",
                        "value": [_option("启用")],
                    },
                ],
                "conjunction": "and",
            },
            "ref_info": None,
            "should_proceed_when_no_results": True,
            "table_name": "子办分配规则表",
        },
    }

    # 有结果才写负责人；无结果不写 Success（避免空成功）
    found_switch = {
        "id": "actDMTfWM",
        "type": "SwitchBranch",
        "title": "多分支（Switch）",
        "next": None,
        "children": {
            "links": [
                {"desc": "未找到", "kind": "case", "label": "branch_1", "to": ""},
                {"desc": "默认分支", "kind": "case", "label": "default", "to": "act1jaIFY"},
            ]
        },
        "data": {
            "child_branch_list": [
                {
                    "name": "未找到",
                    "condition": {
                        "conjunction": "or",
                        "conditions": [
                            {
                                "conjunction": "and",
                                "conditions": [
                                    {
                                        "left_value": {
                                            "value": "$.actVWM1Z5.recordNum",
                                            "value_type": "ref",
                                        },
                                        "operator": "is",
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
            "mode": "exclusive",
            "no_match_action": "classifyToOther",
        },
    }

    mark_success = _set_record_step(
        "act1jaIFY",
        next_step=None,
        field_values=[
            {
                "field_name": FIELD_SUBOFFICE_OWNER,
                "value": [
                    {
                        "value": f"$.actVWM1Z5.firstfieldsRecord.{SUBOFFICE_OWNER_FIELD_ID}",
                        "value_type": "ref",
                    }
                ],
            },
            {
                "field_name": FIELD_SUCCESS,
                "value": [_option(OPT_SUCCESS_YES)],
            },
        ],
    )

    mark_no = _set_record_step(
        "acteIacr4",
        next_step=None,
        field_values=[
            {
                "field_name": FIELD_SUCCESS,
                "value": [_option(OPT_SUCCESS_NO)],
            }
        ],
    )

    steps = [trigger, country_switch, find_record, found_switch, mark_success, mark_no]
    _strip_option_ids(steps)
    body = migrate_workflow_document({"title": title, "steps": steps})
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
    print("restored suboffice workflow: FindRecord → write 子办规则命中负责人 + Success=Yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
