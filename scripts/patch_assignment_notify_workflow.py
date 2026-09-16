#!/usr/bin/env python3
"""修复线索分配通知工作流 wkfQSAjKdDouJULK。

历史：
- 2026-07-11：收件人经「业务通知名单」取原生 user 字段「对应业务」。
- 2026-09-16：改 Case handler 时飞书常拆成「先清空再写入」。清空瞬间
  「最终分配」公式回落到系统匹配（如 Kevin），工作流立刻按瞬时值通知；
  数秒后写成新人（如 Gigi），Kevin 点开却因权限看不到线索。

修复（保留原文「新线索分配提醒」模板）：
1. Delay 1 分钟（平台最小单位）避开秒级空窗；
2. 按线索 ID 重读当前「最终分配的业务员」；
3. 用重读后的最终分配去查业务通知名单；
4. 仅当重读值仍等于触发快照（变更已稳定）才发送 → 空窗回落的 Kevin 通知被丢掉，
   稳定后的 Gigi 仍会收到一封。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from assignment_fields import FIELD_ASSIGNEE, FIELD_LEAD_ID  # noqa: E402
from workflow_bilingual import migrate_workflow_document  # noqa: E402

WORKFLOW_ID = "wkfQSAjKdDouJULK"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")
FIELD_MATCHED_ACCOUNT = "Assigned Salesperson（匹配的业务员账号）"
FIELD_ID_CLUE = "flde0LY8qQ"
FIELD_ID_ASSIGNEE = "fldOMcCv5Y"
FIELD_ID_CUSTOMER = "flddqTlnEm"
FIELD_ID_COUNTRY = "fldAEhwYJU"
DELAY_MINUTES = 1


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


def _delay_step() -> dict:
    return {
        "id": "actNotifyDelay",
        "type": "Delay",
        "title": "延迟1分钟（过滤Case handler清空空窗）",
        "next": "actNotifyFresh",
        "children": {"links": []},
        "data": {"duration": DELAY_MINUTES},
    }


def _fresh_lead_step() -> dict:
    """延迟后按线索ID重读，取发送时刻的最终分配。"""
    return {
        "id": "actNotifyFresh",
        "type": "FindRecordAction",
        "title": "重读线索最终分配",
        "next": "actNotifyLookup",
        "children": {"links": []},
        "data": {
            "table_name": "线索总池 Case Database",
            "field_names": [
                FIELD_LEAD_ID,
                FIELD_ASSIGNEE,
                "Customer Name（客户名称）",
                "Country（国家）",
            ],
            "filter_info": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": FIELD_LEAD_ID,
                        "operator": "is",
                        "value": [
                            {
                                "value": f"$.trig6WiWjH.{FIELD_ID_CLUE}",
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


def _lookup_step() -> dict:
    """按重读后的最终分配查通知名单（不要用触发瞬间的 Kevin）。"""
    return {
        "id": "actNotifyLookup",
        "type": "FindRecordAction",
        "title": "查找业务通知名单",
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
                                "value": (
                                    f"$.actNotifyFresh.firstfieldsRecord."
                                    f"{FIELD_ID_ASSIGNEE}"
                                ),
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
    """有收件人 + 重读成功 + 最终分配仍等于触发快照（变更已稳定）才发。"""
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
                                    },
                                    {
                                        "operator": "isGreater",
                                        "left_value": {
                                            "value": "$.actNotifyFresh.recordNum",
                                            "value_type": "ref",
                                        },
                                        "right_value": [
                                            {"value": 0, "value_type": "number"}
                                        ],
                                    },
                                    {
                                        "operator": "is",
                                        "left_value": {
                                            "value": (
                                                f"$.actNotifyFresh.firstfieldsRecord."
                                                f"{FIELD_ID_ASSIGNEE}"
                                            ),
                                            "value_type": "ref",
                                        },
                                        "right_value": [
                                            {
                                                "value": (
                                                    f"$.trig6WiWjH.{FIELD_ID_ASSIGNEE}"
                                                ),
                                                "value_type": "ref",
                                            }
                                        ],
                                    },
                                ],
                            }
                        ],
                    },
                }
            ],
        },
    }


def _message_content() -> list[dict]:
    """保留原文模板；线索/客户/国家取延迟后重读值。"""
    return [
        {"value": "线索 ", "value_type": "text"},
        {
            "value": f"$.actNotifyFresh.firstfieldsRecord.{FIELD_ID_CLUE}",
            "value_type": "ref",
        },
        {"value": " 已分配给您，请及时跟进。\n客户：", "value_type": "text"},
        {
            "value": f"$.actNotifyFresh.firstfieldsRecord.{FIELD_ID_CUSTOMER}",
            "value_type": "ref",
        },
        {"value": "\n国家：", "value_type": "text"},
        {
            "value": f"$.actNotifyFresh.firstfieldsRecord.{FIELD_ID_COUNTRY}",
            "value_type": "ref",
        },
    ]


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    steps = {s["id"]: s for s in out["steps"]}

    trigger = steps["trig6WiWjH"]
    trigger["next"] = "actNotifyDelay"

    # 只监听最终分配：Case handler 空窗的本质是该公式值抖动。
    # 不再单独监听匹配账号，减少一次派生字段重算带来的误触发。
    trigger["data"]["field_watch_info"] = [{"field_name": FIELD_ASSIGNEE}]

    trigger["data"]["condition_list"] = [
        {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": FIELD_ASSIGNEE,
                    "operator": "isNotEmpty",
                    "value": [],
                },
                {
                    "field_name": FIELD_MATCHED_ACCOUNT,
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

    msg = steps["actiMAP8ADF"]
    msg["data"]["receiver"] = [
        {"value": "$.actNotifyLookup.firstfieldsRecord.fldEVPOdP6", "value_type": "ref"},
    ]
    msg["data"]["title"] = [
        {"value": "🚀 新线索分配提醒", "value_type": "text"},
    ]
    msg["data"]["content"] = _message_content()
    msg["data"]["btn_list"] = [
        {
            "text": "打开线索",
            "btn_action": "openLink",
            "link": [{"value": "$.trig6WiWjH.recordLink", "value_type": "ref"}],
        }
    ]

    out["steps"] = [
        trigger,
        _delay_step(),
        _fresh_lead_step(),
        _lookup_step(),
        _switch_step(),
        msg,
    ]

    _strip_option_ids(out["steps"])
    return migrate_workflow_document({"title": out["title"], "steps": out["steps"]})


def main() -> int:
    base_token = os.environ.get(BASE_TOKEN_ENV, "ZpbUb7SP7azsNasniFjc0bWSnHg")
    live = _fetch_live(base_token)
    body = patch_workflow(live)
    root = Path(__file__).resolve().parents[1]
    out_path = root / "workflows" / f"{WORKFLOW_ID}-线索分配通知.json"
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

    print("patched assignment notify workflow deployed and enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
