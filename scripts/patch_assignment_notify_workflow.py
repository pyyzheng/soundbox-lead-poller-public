#!/usr/bin/env python3
"""修复线索分配通知工作流 wkfQSAjKdDouJULK。

根因（2026-07-11）：收件人直接引用 lookup 字段「匹配的业务员账号」会在
LarkMessageAction 报「字段类型不匹配」；必须通过业务通知名单二次查找，
取原生 user 字段「对应业务」作为收件人。

保留改进：
1. 同时监听「最终分配的业务员」「匹配的业务员账号」。
2. 消息正文使用 Customer Name（flddqTlnEm）而非 Customer Type。
3. 名单查不到人时不发消息（Switch recordNum > 0）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from assignment_fields import FIELD_ASSIGNEE  # noqa: E402
from workflow_bilingual import migrate_workflow_document  # noqa: E402

WORKFLOW_ID = "wkfQSAjKdDouJULK"
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
        "id": "actNotifyLookup",
        "type": "FindRecordAction",
        "title": "查找记录",
        "next": "actNotifySwitch",
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
                                "value": "$.trig6WiWjH.fldOMcCv5Y",
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
        "id": "actNotifySwitch",
        "type": "SwitchBranch",
        "title": "多分支（Switch）",
        "children": {
            "links": [
                {
                    "desc": "分支 1",
                    "kind": "case",
                    "label": "branch_1",
                    "to": "actiMAP8ADF",
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
                                            "value": "$.actNotifyLookup.recordNum",
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

    trigger = steps["trig6WiWjH"]
    trigger["next"] = "actNotifyLookup"

    watched = {w["field_name"] for w in trigger["data"]["field_watch_info"]}
    for field_name in (FIELD_ASSIGNEE, "匹配的业务员账号"):
        if field_name not in watched:
            trigger["data"]["field_watch_info"].append({"field_name": field_name})

    conds = trigger["data"]["condition_list"][0]["conditions"]
    required = [
        (FIELD_ASSIGNEE, "isNotEmpty", []),
        ("匹配的业务员账号", "isNotEmpty", []),
    ]
    for field_name, operator, value in required:
        if not any(
            c.get("field_name") == field_name and c.get("operator") == operator
            for c in conds
        ):
            conds.append({
                "field_name": field_name,
                "operator": operator,
                "value": value,
            })
    for err in ERROR_ASSIGNEES:
        if not any(
            c.get("field_name") == FIELD_ASSIGNEE
            and c.get("operator") == "isNot"
            and (c.get("value") or [{}])[0].get("value") == err
            for c in conds
        ):
            conds.append({
                "field_name": FIELD_ASSIGNEE,
                "operator": "isNot",
                "value": [{"value": err, "value_type": "text"}],
            })

    msg = steps["actiMAP8ADF"]
    msg["data"]["receiver"] = [
        {"value": "$.actNotifyLookup.firstfieldsRecord.fldEVPOdP6", "value_type": "ref"},
    ]
    msg["data"]["title"] = [
        {"value": "🚀 新线索分配提醒", "value_type": "text"},
    ]
    msg["data"]["content"] = [
        {"value": "线索 ", "value_type": "text"},
        {"value": "$.trig6WiWjH.flde0LY8qQ", "value_type": "ref"},
        {"value": " 已分配给您，请及时跟进。\n客户：", "value_type": "text"},
        {"value": "$.trig6WiWjH.flddqTlnEm", "value_type": "ref"},
        {"value": "\n国家：", "value_type": "text"},
        {"value": "$.trig6WiWjH.fldAEhwYJU", "value_type": "ref"},
    ]
    msg["data"]["btn_list"] = [
        {
            "text": "打开线索",
            "btn_action": "openLink",
            "link": [{"value": "$.trig6WiWjH.recordLink", "value_type": "ref"}],
        }
    ]

    out["steps"] = [
        trigger,
        _lookup_step(),
        _switch_step(),
        msg,
    ]

    _strip_option_ids(out["steps"])
    return migrate_workflow_document({"title": out["title"], "steps": out["steps"]})


def main() -> int:
    import os

    base_token = os.environ.get(BASE_TOKEN_ENV, "ZpbUb7SP7azsNasniFjc0bWSnHg")
    live = _fetch_live(base_token)
    body = patch_workflow(live)
    root = Path(__file__).resolve().parents[1]
    out_path = root / "workflows" / f"{WORKFLOW_ID}-线索分配通知.json"
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
    print("patched assignment notify workflow deployed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
