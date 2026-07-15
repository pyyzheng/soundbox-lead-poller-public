#!/usr/bin/env python3
"""启用并修复询盘内容更新通知工作流 wkfOCCVMcXBjbp4F。

根因（2026-07-11）：收件人直接引用 lookup 字段「匹配的业务员账号」会在
LarkMessageAction 报「字段类型不匹配」；必须通过业务通知名单二次查找，
取原生 user 字段「对应业务」作为收件人。

保留：监听 Enquiry details 变更，且最终分配业务员有效时再通知。
"""

from __future__ import annotations

import json
import subprocess
import sys
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from assignment_fields import FIELD_ASSIGNEE  # noqa: E402
from workflow_bilingual import migrate_workflow_document  # noqa: E402

WORKFLOW_ID = "wkfOCCVMcXBjbp4F"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")


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


def _lookup_step() -> dict:
    return {
        "id": "actEnquiryLookup",
        "type": "FindRecordAction",
        "title": "查找记录",
        "next": "actEnquirySwitch",
        "children": {"links": []},
        "data": {
            "table_name": "业务通知名单",
            "field_names": ["对应业务", "业务名单"],
            "filter_info": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "业务名单",
                        "operator": "is",
                        "value": [
                            {
                                "value": "$.trigEvBreo.fldOMcCv5Y",
                                "value_type": "ref",
                            }
                        ],
                    }
                ],
            },
            "ref_info": None,
            "should_proceed_when_no_results": True,
        },
    }


def _switch_step() -> dict:
    return {
        "id": "actEnquirySwitch",
        "type": "SwitchBranch",
        "title": "多分支（Switch）",
        "children": {
            "links": [
                {
                    "desc": "分支 1",
                    "kind": "case",
                    "label": "branch_1",
                    "to": "act36Vyyk",
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
                    "name": "分支 1",
                    "condition": {
                        "conjunction": "or",
                        "conditions": [
                            {
                                "conjunction": "and",
                                "conditions": [
                                    {
                                        "operator": "isGreater",
                                        "left_value": {
                                            "value": "$.actEnquiryLookup.recordNum",
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

    trigger = steps["trigEvBreo"]
    trigger["next"] = "actEnquiryLookup"
    trigger["data"]["condition_list"] = [
        {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": "匹配的业务员账号",
                    "operator": "isNotEmpty",
                    "value": [],
                },
                {
                    "field_name": FIELD_ASSIGNEE,
                    "operator": "isNotEmpty",
                    "value": [],
                },
                *[
                    {
                        "field_name": FIELD_ASSIGNEE,
                        "operator": "isNot",
                        "value": [{"value": err, "value_type": "text"}],
                    }
                    for err in ERROR_ASSIGNEES
                ],
            ],
        }
    ]

    msg = steps["act36Vyyk"]
    msg["data"]["receiver"] = [
        {"value": "$.actEnquiryLookup.firstfieldsRecord.fldEVPOdP6", "value_type": "ref"},
    ]
    msg["data"]["btn_list"] = [
        {
            "text": "查看详情",
            "btn_action": "openLink",
            "link": [{"value": "$.trigEvBreo.recordLink", "value_type": "ref"}],
        }
    ]

    out["steps"] = [trigger, _lookup_step(), _switch_step(), msg]

    _strip_option_ids(out["steps"])
    return migrate_workflow_document({"title": out["title"], "steps": out["steps"]})


def main() -> int:
    import os

    base_token = os.environ.get(BASE_TOKEN_ENV, "ZpbUb7SP7azsNasniFjc0bWSnHg")
    live = _fetch_live(base_token)
    body = patch_workflow(live)
    root = Path(__file__).resolve().parents[1]
    out_path = root / "workflows" / f"{WORKFLOW_ID}-询盘更新通知业务员.json"
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    update_cmd = [
        "lark-cli", "base", "+workflow-update",
        "--base-token", base_token,
        "--workflow-id", WORKFLOW_ID,
        "--json", f"@{out_path.relative_to(root)}",
        "--as", "user",
    ]
    result = subprocess.run(update_cmd, cwd=root, capture_output=True, text=True)
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        return result.returncode

    enable_cmd = [
        "lark-cli", "base", "+workflow-enable",
        "--base-token", base_token,
        "--workflow-id", WORKFLOW_ID,
        "--as", "user",
    ]
    result = subprocess.run(enable_cmd, cwd=root, capture_output=True, text=True)
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        return result.returncode

    print("patched enquiry update notify workflow deployed and enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
