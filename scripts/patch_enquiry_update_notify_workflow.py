#!/usr/bin/env python3
"""启用并修复询盘内容更新通知工作流 wkfOCCVMcXBjbp4F。

历史修复：
- 2026-07-11：收件人必须经「业务通知名单」取原生 user 字段「对应业务」。
- 2026-09-16：004906 通知错乱 —— 非字段绑错，而是人为把 004908 SEO 询盘
  误粘进 004906 后约 5 秒改回；工作流按触发瞬间快照发出全文，业务员打开时
  已是改回后的内容。保留原文「线索ID + 询盘全文」模板，增加：
  1) Delay 1 分钟（平台最小单位）挡住秒级误操作；
  2) 按线索ID 重读当前 Enquiry details，消息正文嵌入重读后的全文
     （与业务员打开记录一致，不再用易过期的触发瞬间快照）。
- 2026-11-21：人工指派可能落到不在「业务通知名单」里的跟进人。
  业务员仍走业务通知名单；名单未命中时改查「实际跟进人名单」补发同一张卡片。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from assignment_fields import FIELD_ASSIGNEE, FIELD_ENQUIRY, FIELD_LEAD_ID  # noqa: E402
from workflow_bilingual import migrate_workflow_document  # noqa: E402

WORKFLOW_ID = "wkfOCCVMcXBjbp4F"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")
FIELD_MATCHED_ACCOUNT = "Assigned Salesperson（匹配的业务员账号）"
FIELD_FOLLOWER_ACCOUNT = "Follower（实际跟进人账号）"
FIELD_ID_CLUE = "flde0LY8qQ"
FIELD_ID_ENQUIRY = "fldNLj6Btg"
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
        "id": "actEnquiryDelay",
        "type": "Delay",
        "title": "延迟1分钟（过滤秒级误粘贴回滚）",
        "next": "actEnquiryFresh",
        "children": {"links": []},
        "data": {"duration": DELAY_MINUTES},
    }


def _fresh_enquiry_step() -> dict:
    """延迟后按线索ID重读，取发送时刻的询盘全文。"""
    return {
        "id": "actEnquiryFresh",
        "type": "FindRecordAction",
        "title": "重读线索询盘",
        "next": "actEnquiryLookup",
        "children": {"links": []},
        "data": {
            "table_name": "线索总池 Case Database",
            "field_names": [FIELD_LEAD_ID, FIELD_ENQUIRY],
            "filter_info": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": FIELD_LEAD_ID,
                        "operator": "is",
                        "value": [
                            {
                                "value": f"$.trigEvBreo.{FIELD_ID_CLUE}",
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


def _owner_ref() -> dict:
    return {"value": "$.trigEvBreo.fldOMcCv5Y", "value_type": "ref"}


def _lookup_step() -> dict:
    return {
        "id": "actEnquiryLookup",
        "type": "FindRecordAction",
        "title": "查找业务通知名单",
        "next": "actEnquiryFollower",
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
                        "value": [_owner_ref()],
                    }
                ],
            },
            "ref_info": None,
            "should_proceed_when_no_results": True,
        },
    }


def _follower_lookup_step() -> dict:
    """人工指派的跟进人不在业务通知名单时，用实际跟进人名单取原生 user。"""
    return {
        "id": "actEnquiryFollower",
        "type": "FindRecordAction",
        "title": "查找实际跟进人",
        "next": "actEnquirySwitch",
        "children": {"links": []},
        "data": {
            "table_name": "实际跟进人名单",
            "field_names": ["对应业务账号", "业务名称"],
            "filter_info": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "业务名称",
                        "operator": "is",
                        "value": [_owner_ref()],
                    }
                ],
            },
            "ref_info": None,
            "should_proceed_when_no_results": True,
        },
    }


def _hit_and_fresh(record_num_ref: str) -> dict:
    return {
        "conjunction": "and",
        "conditions": [
            {
                "operator": "isGreater",
                "left_value": {"value": record_num_ref, "value_type": "ref"},
                "right_value": [{"value": 0, "value_type": "number"}],
            },
            {
                "operator": "isGreater",
                "left_value": {
                    "value": "$.actEnquiryFresh.recordNum",
                    "value_type": "ref",
                },
                "right_value": [{"value": 0, "value_type": "number"}],
            },
        ],
    }


def _switch_step() -> dict:
    """业务通知名单命中优先；未命中则发给实际跟进人。

    不在此比较询盘全文是否等于触发快照：长文本 ref==ref 会在工作流校验/
    migrate 时被拍扁导致部署失败。秒级误粘贴回滚靠 Delay(1min)+重读消化：
    发出去的是发送时刻表内全文，与打开记录一致。
    """
    return {
        "id": "actEnquirySwitch",
        "type": "SwitchBranch",
        "title": "多分支（Switch）",
        "children": {
            "links": [
                {
                    "desc": "业务通知名单",
                    "kind": "case",
                    "label": "branch_1",
                    "to": "act36Vyyk",
                },
                {
                    "desc": "实际跟进人",
                    "kind": "case",
                    "label": "branch_2",
                    "to": "actEnquiryMsgFollower",
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
                    "name": "业务通知名单",
                    "condition": {
                        "conjunction": "or",
                        "conditions": [
                            _hit_and_fresh("$.actEnquiryLookup.recordNum"),
                        ],
                    },
                },
                {
                    "name": "实际跟进人",
                    "condition": {
                        "conjunction": "or",
                        "conditions": [
                            _hit_and_fresh("$.actEnquiryFollower.recordNum"),
                        ],
                    },
                },
            ],
        },
    }


def _message_content() -> list[dict]:
    """恢复原文模板：线索ID + 询盘全文（取延迟后重读值）。"""
    return [
        {
            "value": "您好，您负责的线索询盘内容已更新，请及时查看。\n线索ID：",
            "value_type": "text",
        },
        {
            "value": f"$.actEnquiryFresh.firstfieldsRecord.{FIELD_ID_CLUE}",
            "value_type": "ref",
        },
        {"value": "\n询盘内容：", "value_type": "text"},
        {
            "value": f"$.actEnquiryFresh.firstfieldsRecord.{FIELD_ID_ENQUIRY}",
            "value_type": "ref",
        },
    ]


def _owner_guards() -> list[dict]:
    return [
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
    ]


def _apply_message(step: dict, receiver_ref: str) -> dict:
    step["data"]["receiver"] = [{"value": receiver_ref, "value_type": "ref"}]
    step["data"]["content"] = _message_content()
    step["data"]["title"] = [{"value": "询盘内容更新提醒", "value_type": "text"}]
    step["data"]["btn_list"] = [
        {
            "text": "查看详情",
            "btn_action": "openLink",
            "link": [{"value": "$.trigEvBreo.recordLink", "value_type": "ref"}],
        }
    ]
    step["data"]["send_to_everyone"] = False
    return step


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    steps = {s["id"]: s for s in out["steps"]}

    trigger = steps["trigEvBreo"]
    trigger["next"] = "actEnquiryDelay"
    # 条件组之间是 OR：业务员账号或实际跟进人账号有值都触发。
    trigger["data"]["condition_list"] = [
        {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": FIELD_MATCHED_ACCOUNT,
                    "operator": "isNotEmpty",
                    "value": [],
                },
                *_owner_guards(),
            ],
        },
        {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": FIELD_FOLLOWER_ACCOUNT,
                    "operator": "isNotEmpty",
                    "value": [],
                },
                *_owner_guards(),
            ],
        },
    ]

    msg = _apply_message(
        steps["act36Vyyk"],
        "$.actEnquiryLookup.firstfieldsRecord.fldEVPOdP6",
    )
    follower_msg = _apply_message(deepcopy(msg), "$.actEnquiryFollower.firstfieldsRecord.fldEVPOdP6")
    follower_msg["id"] = "actEnquiryMsgFollower"
    follower_msg["title"] = "发送飞书消息（实际跟进人）"

    out["steps"] = [
        trigger,
        _delay_step(),
        _fresh_enquiry_step(),
        _lookup_step(),
        _follower_lookup_step(),
        _switch_step(),
        msg,
        follower_msg,
    ]

    _strip_option_ids(out["steps"])
    return migrate_workflow_document({"title": out["title"], "steps": out["steps"]})


def main() -> int:
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
