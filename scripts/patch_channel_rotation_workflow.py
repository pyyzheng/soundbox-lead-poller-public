#!/usr/bin/env python3
"""从飞书拉取渠道轮转工作流，打最小补丁后写回。

修复点（2026-07-22 再次固化）：
1. 触发条件增加「渠道顺序队列匹配业务员 isEmpty」，避免重复触发
2. 队列指针未找到时继续执行（should_proceed_when_no_results=true），走 Switch 保持「否」
3. **禁止跨表 ref 写「渠道顺序队列匹配业务员」**：即便队列表已挂动态选项，
   FindRecord→SetRecord 的单选 ref 仍会间歇报「字段类型不匹配」。
   业务员 + 指针推进一律由 cloud-assignment-unblock.py 按名称写入。
4. 去掉 Duplicate 公式条件；监听队列Key；strip option id（飞书 UI 会回写 id）
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
    """递归移除 option value 中的 id，避免选项重建后 id 漂移导致类型错误。"""
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


def patch_workflow(data: dict) -> dict:
    out = deepcopy(data)
    steps = {s["id"]: s for s in out["steps"]}

    trigger = steps["triggzwCHjB9"]
    conds = trigger["data"]["condition_list"][0]["conditions"]
    # Duplicate 条件与公式「是否满足渠道轮转」重复，且公式字段条件易触发类型不匹配告警；直接去掉。
    conds = [
        c
        for c in conds
        if c.get("field_name") not in ("Duplicate（重复）", "分配来源")
    ]
    if not any(c.get("field_name") == "渠道顺序队列匹配业务员" for c in conds):
        conds.append({"field_name": "渠道顺序队列匹配业务员", "operator": "isEmpty", "value": []})
    trigger["data"]["condition_list"][0]["conditions"] = conds

    watch = trigger["data"]["field_watch_info"]
    if not any(w.get("field_name") == "渠道顺序队列匹配业务员" for w in watch):
        watch.append({"field_name": "渠道顺序队列匹配业务员"})
    # 队列Key 由公式生成，监听以便公式就绪后再次触发
    if not any(w.get("field_name") == "队列Key" for w in watch):
        watch.append({"field_name": "队列Key"})

    steps["actJShk3sEn"]["data"]["should_proceed_when_no_results"] = True
    steps["acteml359jG"]["data"]["should_proceed_when_no_results"] = True
    # 字段重建后 field_id 变更，查找步骤改回字段名
    steps["acteml359jG"]["data"]["field_names"] = ["业务员"]

    success_step = steps["actnfeoNaFo"]
    # 不跨表 ref 写「渠道顺序队列匹配业务员」：FindRecord 单选 ref 易报字段类型不匹配。
    # 业务员 + 指针推进由 cloud-assignment-unblock.py 负责。
    # 本步仅标记 Allocation Status=Yes；若业务员仍空，unblock 的 _is_stuck_success 会重置后再分配。
    success_step["data"]["field_values"] = [
        fv
        for fv in success_step["data"]["field_values"]
        if fv.get("field_name") not in ("渠道顺序队列匹配业务员",)
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
    print("patched workflow deployed (no cross-table assignee write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
