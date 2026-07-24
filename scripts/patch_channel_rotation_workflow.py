#!/usr/bin/env python3
"""从飞书拉取渠道轮转工作流，打最小补丁后写回。

修复点：
1. 触发条件增加「渠道顺序队列匹配业务员 isEmpty」，避免重复触发
2. 队列指针未找到时继续执行（should_proceed_when_no_results=true）
3. **成功分支写回「渠道顺序队列匹配业务员」**：队列表业务员已挂主表动态选项，
   用 FindRecord.firstfieldsRecord 跨表 ref；指针推进仍由 unblock 兜底同步
4. 去掉 Duplicate 公式条件；监听队列Key；strip option id
5. unblock 仅作兜底（写业务员失败 / 指针未推进时补齐）
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

WORKFLOW_ID = "wkf2Hopgt3bWuoOH"
BASE_TOKEN_ENV = "FEISHU_APP_TOKEN"
# 渠道顺序队列表.业务员；与主表「渠道顺序队列匹配业务员」动态选项同源
CHANNEL_QUEUE_ASSIGNEE_FIELD_ID = "fldJSP0l6d"
ASSIGNEE_REF = f"$.acteml359jG.firstfieldsRecord.{CHANNEL_QUEUE_ASSIGNEE_FIELD_ID}"
FIELD_QUEUE_ASSIGNEE = "渠道顺序队列匹配业务员"
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


def _queue_assignee_field_value() -> dict:
    return {
        "field_name": FIELD_QUEUE_ASSIGNEE,
        "value": [{"value": ASSIGNEE_REF, "value_type": "ref"}],
    }


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    steps = {s["id"]: s for s in out["steps"]}

    trigger = steps["triggzwCHjB9"]
    conds = trigger["data"]["condition_list"][0]["conditions"]
    conds = [
        c
        for c in conds
        if c.get("field_name") not in ("Duplicate（重复）", "分配来源")
    ]
    if not any(c.get("field_name") == FIELD_QUEUE_ASSIGNEE for c in conds):
        conds.append({"field_name": FIELD_QUEUE_ASSIGNEE, "operator": "isEmpty", "value": []})
    trigger["data"]["condition_list"][0]["conditions"] = conds

    watch = trigger["data"]["field_watch_info"]
    for name in (FIELD_QUEUE_ASSIGNEE, "队列Key", "是否命中代理国家"):
        if not any(w.get("field_name") == name for w in watch):
            watch.append({"field_name": name})

    steps["actJShk3sEn"]["data"]["should_proceed_when_no_results"] = True
    steps["acteml359jG"]["data"]["should_proceed_when_no_results"] = True
    steps["acteml359jG"]["data"]["field_names"] = ["业务员"]

    success_step = steps["actnfeoNaFo"]
    # 先写业务员，再写是否成功分配=是
    other = [
        fv
        for fv in success_step["data"]["field_values"]
        if fv.get("field_name") not in (FIELD_QUEUE_ASSIGNEE, FIELD_SUCCESS)
    ]
    success_step["data"]["field_values"] = [
        _queue_assignee_field_value(),
        {"field_name": FIELD_SUCCESS, "value": [_option("Yes（是）")]},
        *other,
    ]

    _strip_option_ids(out["steps"])
    body = migrate_workflow_document({"title": out["title"], "steps": out["steps"]})
    return fix_duplicate_formula_in_workflow(body)


def main() -> int:
    import os

    base_token = os.environ.get(BASE_TOKEN_ENV)
    if not base_token:
        print(f"缺少 {BASE_TOKEN_ENV}", file=sys.stderr)
        return 1

    live = _fetch_live(base_token)
    body = patch_workflow(live)
    out_path = Path(__file__).resolve().parents[1] / "workflows" / f"{WORKFLOW_ID}-渠道轮转自动化.json"
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
    print("patched channel rotation: FindRecord → write 渠道顺序队列匹配业务员 + Success=Yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
