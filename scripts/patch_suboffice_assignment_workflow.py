#!/usr/bin/env python3
"""修复子办规则分配自动化工作流 wkfaNTuMd6vAE5E0。

根因（2026-07-22 全面排查固化）：
1. 跨表 FindRecord / 写「子办规则命中负责人」会报字段类型不匹配（即使动态选项已对齐）。
2. Lookup「是否是子办国家」在条件里只允许 option/ref（不允许 text）；
   飞书部署后会把 name-only option 回写为带 id 的 option。
3. 真正稳定的分配由 cloud-suboffice-assignee-fix.py 完成（assignment-unblock 每 5 分钟）。

固化策略：
- 2 步：触发（含子办国家=是）→ 只写 Allocation Status=Yes。
- 不 Switch、不 FindRecord、不写负责人。
"""

from __future__ import annotations

import json
import subprocess
import sys
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


def patch_workflow(data: dict) -> dict:
    title = data.get("title") or "子办规则分配自动化"

    trigger = {
        "id": "trigpJUkcCnQ",
        "type": "SetRecordTrigger",
        "title": "修改记录时",
        "next": "act1jaIFY",
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
                        # Lookup 条件只允许 option/ref；用 name-only，部署后飞书可能回写 id
                        {
                            "field_name": FIELD_SUBOFFICE_FLAG,
                            "operator": "is",
                            "value": [_option("是")],
                        },
                    ],
                }
            ],
            "field_watch_info": [
                {"field_name": "Channels（渠道）"},
                {"field_name": "Country（国家）"},
                {"field_name": FIELD_ASSIGN_METHOD},
                {"field_name": FIELD_SUBOFFICE_FLAG},
            ],
            "trigger_control_list": [
                "pasteUpdate",
                "automationBatchUpdate",
                "appendImport",
                "openAPIBatchUpdate",
            ],
        },
    }
    live_steps = {s["id"]: s for s in data.get("steps", [])}
    if "trigpJUkcCnQ" in live_steps:
        live_data = (live_steps["trigpJUkcCnQ"].get("data") or {})
        if live_data.get("trigger_control_list"):
            trigger["data"]["trigger_control_list"] = live_data["trigger_control_list"]
        watched = {w["field_name"] for w in trigger["data"]["field_watch_info"]}
        for w in live_data.get("field_watch_info", []):
            name = w.get("field_name")
            if name and name not in watched:
                trigger["data"]["field_watch_info"].append({"field_name": name})

    mark_success = {
        "id": "act1jaIFY",
        "type": "SetRecordAction",
        "title": "修改记录",
        "next": None,
        "children": {"links": []},
        "data": {
            "field_values": [
                {
                    "field_name": FIELD_SUCCESS,
                    "value": [_option(OPT_SUCCESS_YES)],
                }
            ],
            "filter_info": None,
            "max_set_record_num": 100,
            "ref_info": {"step_id": "trigpJUkcCnQ"},
            "table_name": "线索总池 Case Database",
        },
    }

    steps = [trigger, mark_success]
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
    print("deployed minimal suboffice workflow: trigger → mark Yes only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
