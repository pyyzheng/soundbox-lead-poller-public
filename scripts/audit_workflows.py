#!/usr/bin/env python3
"""扫描飞书 Base 工作流中的字段引用是否与当前表结构一致。

检查项：
- 工作流 JSON 中的 fld* 是否仍存在于 Base
- 已知过期 field_id（Case handler / 代理 / 子办 / 队列表旧业务员）
- 消息正文是否误用 fldCMXBRI2（Customer Type）作客户名

用法:
  python3 scripts/audit_workflows.py
  python3 scripts/audit_workflows.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from feishu_utils import FEISHU_APP_TOKEN  # noqa: E402

BASE_TOKEN = os.environ.get("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
FIELD_REF = re.compile(r"fld[A-Za-z0-9]+")

# 历史上因字段对齐/迁移而失效的 id（含误用说明）
KNOWN_STALE: dict[str, str] = {
    "fldkoE4cko": "旧 Case handler（现为 fldJi4Y57A）",
    "fld9vps8a6": "旧代理规则业务员字段（现为 fldcmDUWhH）",
    "fldxAsUa9t": "旧子办负责人字段（现为 fldATnmAXs）",
    "fldNAEpBXi": "旧渠道队列表「业务员」（已改动态选项 fldJSP0l6d）",
    "fldCMXBRI2": "Customer Type（US）单选，勿作客户名称（应用 flddqTlnEm）",
}

# 分配链路当前应使用的 field_id（仅用于快查输出）
CORE_FIELD_IDS: dict[str, str] = {
    "fldcmDUWhH": "代理规则命中业务员",
    "fldATnmAXs": "子办规则命中负责人",
    "fldJSP0l6d": "渠道顺序队列表.业务员",
    "fld4Uk8KfA": "渠道顺序队列匹配业务员",
    "fldphEUn67": "匹配的业务员账号",
    "fldyv3fLLI": "实际跟进人账号",
    "flddqTlnEm": "Customer Name（客户名称）",
    "fldJi4Y57A": "Case handler",
    "fldOMcCv5Y": "最终分配的业务员",
}


def _lark_json(args: list[str]) -> dict:
    cmd = ["lark-cli", "base", *args, "--base-token", BASE_TOKEN, "--format", "json", "--as", "user"]
    raw = subprocess.check_output(cmd, text=True, cwd=ROOT)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["data"]


def _load_field_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for table in _lark_json(["+table-list"])["tables"]:
        tid, tname = table["id"], table["name"]
        data = _lark_json(["+field-list", "--table-id", tid])
        for field in data.get("fields", []):
            fid = field["id"]
            index[fid] = {
                "name": field["name"],
                "ui_type": field.get("ui_type", field.get("type")),
                "table": tname,
            }
    return index


def _audit_workflow(wf: dict, field_index: dict[str, dict]) -> list[str]:
    issues: list[str] = []
    blob = json.dumps(wf, ensure_ascii=False)
    refs = sorted(set(FIELD_REF.findall(blob)))

    for fid in refs:
        if fid in KNOWN_STALE:
            issues.append(f"已知问题字段 {fid}: {KNOWN_STALE[fid]}")
        if fid not in field_index:
            issues.append(f"字段不存在 {fid}")

    if "fldCMXBRI2" in refs and "客户" in blob:
        issues.append("消息正文引用 fldCMXBRI2 作客户名，应改为 flddqTlnEm")

    for step in wf.get("steps", []):
        data = step.get("data", {})

        def walk(obj) -> None:
            if isinstance(obj, dict):
                fn = obj.get("field_name")
                if isinstance(fn, str) and fn not in ("CreatedUser", "ModifiedUser"):
                    if not any(meta["name"] == fn for meta in field_index.values()):
                        issues.append(f"步骤 {step.get('id')} 字段名不存在: {fn}")
                for val in obj.values():
                    walk(val)
            elif isinstance(obj, list):
                for val in obj:
                    walk(val)

        walk(data)

    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Feishu Base workflow field references")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    field_index = _load_field_index()
    workflows = _lark_json(["+workflow-list"])["items"]

    rows: list[dict] = []
    for item in workflows:
        wid = item["workflow_id"]
        wf = _lark_json(["+workflow-get", "--workflow-id", wid])
        issues = _audit_workflow(wf, field_index)
        core_hits = sorted(fid for fid in CORE_FIELD_IDS if fid in json.dumps(wf))
        rows.append(
            {
                "workflow_id": wid,
                "title": item["title"],
                "status": item["status"],
                "issue_count": len(issues),
                "issues": issues,
                "core_field_ids": core_hits,
            }
        )

    enabled_issues = sum(1 for r in rows if r["status"] == "enabled" and r["issue_count"])
    disabled_issues = sum(1 for r in rows if r["status"] != "enabled" and r["issue_count"])

    if args.json:
        print(
            json.dumps(
                {
                    "base_token": BASE_TOKEN,
                    "workflow_count": len(rows),
                    "enabled_with_issues": enabled_issues,
                    "disabled_with_issues": disabled_issues,
                    "workflows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if enabled_issues else 0

    print(f"Base {BASE_TOKEN} | 工作流 {len(rows)} | 字段 {len(field_index)}")
    print()
    for row in sorted(rows, key=lambda r: (-r["issue_count"], r["title"])):
        flag = "🔴" if row["issue_count"] else "✅"
        if row["status"] != "enabled" and row["issue_count"]:
            flag = "⚠️"
        print(f"{flag} [{row['status']}] {row['title']} ({row['workflow_id']}) issues={row['issue_count']}")
        for issue in row["issues"]:
            print(f"    - {issue}")

    print()
    if enabled_issues:
        print(f"❌ {enabled_issues} 个已启用工作流存在问题")
        return 1
    if disabled_issues:
        print(f"ℹ️ {disabled_issues} 个已停用工作流有过期引用（建议飞书 UI 手动删除副本）")
    print("✅ 所有已启用工作流字段引用正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
