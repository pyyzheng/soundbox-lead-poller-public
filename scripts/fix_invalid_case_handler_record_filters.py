#!/usr/bin/env python3
"""修复子办负责人角色主表记录权限中的无效 Case handler 条件。

Case handler 已改为动态单选（选项源=实际跟进人名单），高级权限里
「Case handler contains []」会触发「字段内容已被修改，请设置其他条件」。

负责人角色改回仅按 Country（国家）过滤；转接线索仍通过「转接的线索」视图可见。

用法：
  python3 scripts/fix_invalid_case_handler_record_filters.py
  python3 scripts/fix_invalid_case_handler_record_filters.py --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
MAIN = "线索总池 Case Database"
COUNTRY_FIELD = "Country（国家）"

ROLES: dict[str, list[str]] = {
    "rolck1e4bB": ["美国"],  # US_加州负责人
    "rolFieisEP": ["德国"],  # 德国子办负责人
    "rolsrnwOfQb": ["香港"],  # 香港子办负责人
    "rolXVcTk5lZ": ["俄罗斯", "白俄罗斯"],  # 俄白子办负责人
}


def _run(cmd: list[str], *, yes: bool = False) -> dict[str, Any]:
    full = ["lark-cli", *cmd]
    if yes and "--yes" not in full:
        full.append("--yes")
    proc = subprocess.run(full, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(full)}\n{proc.stderr or proc.stdout}")
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def _base(args: list[str], *, yes: bool = False) -> dict[str, Any]:
    return _run(["base", *args, "--base-token", BASE, "--as", "user", "--format", "json"], yes=yes)


def get_role(role_id: str) -> dict[str, Any]:
    raw = _base(["+role-get", "--role-id", role_id])
    data = raw["data"]["data"]
    return json.loads(data) if isinstance(data, str) else data


def country_record_filters(countries: list[str]) -> dict[str, Any]:
    return {
        "conjunction": "or",
        "filter_rules": [
            {
                "conjunction": "and",
                "filters": [
                    {
                        "field_name": COUNTRY_FIELD,
                        "operator": "contains",
                        "field_type": "SingleSelect",
                        "field_ui_type": "",
                        "reference_type": "SingleSelect",
                        "filter_values": countries,
                        "is_invalid": False,
                    }
                ],
            }
        ],
    }


def strip_case_handler_filters(node: Any) -> int:
    """Remove Case handler filter rules; return count removed."""
    removed = 0
    if not isinstance(node, dict):
        return 0
    for key in ("read_filter_rule_group", "edit_filter_rule_group"):
        group = node.get(key)
        if not isinstance(group, dict):
            continue
        rules = group.get("filter_rules") or []
        kept = []
        for rule in rules:
            filters = rule.get("filters") or []
            if any(f.get("field_name") == "Case handler" for f in filters):
                removed += 1
                continue
            kept.append(rule)
        group["filter_rules"] = kept
    return removed


def patch_role(role_id: str, countries: list[str], *, dry_run: bool) -> None:
    role = get_role(role_id)
    name = role.get("role_name", role_id)
    print(f"\n== {name} ({role_id})")

    trm = copy.deepcopy(role["table_rule_map"])
    main = trm[MAIN]
    rr = main.setdefault("record_rule", {})

    removed = strip_case_handler_filters(rr)
    new_filter = country_record_filters(countries)
    rr["read_filter_rule_group"] = copy.deepcopy(new_filter)
    rr["edit_filter_rule_group"] = copy.deepcopy(new_filter)
    rr["other_record_all_read"] = False

    print(f"  removed Case handler rules: {removed}")
    print(f"  new country filter: {countries}")

    payload = {
        "role_name": name,
        "role_type": "custom_role",
        "table_rule_map": trm,
    }
    if dry_run:
        print("  [dry-run] skip update")
        return
    _base(
        ["+role-update", "--role-id", role_id, "--json", json.dumps(payload, ensure_ascii=False)],
        yes=True,
    )
    print("  updated")


def verify() -> None:
    print("\n== verify")
    for role_id, countries in ROLES.items():
        role = get_role(role_id)
        rr = role["table_rule_map"][MAIN]["record_rule"]
        fields: list[str] = []

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("field_name"):
                    fields.append(node["field_name"])
                for v in node.values():
                    collect(v)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect(rr.get("read_filter_rule_group"))
        collect(rr.get("edit_filter_rule_group"))
        print(f"{role['role_name']}: fields={fields}, expect={countries}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for role_id, countries in ROLES.items():
        patch_role(role_id, countries, dry_run=args.dry_run)

    if not args.dry_run:
        verify()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
