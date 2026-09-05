#!/usr/bin/env python3
"""按「跟进人所属部门」收紧区域负责人记录级权限。

中东区负责人：分配部门=中东区 OR (分配部门=南美非洲公区 AND 跟进人所属部门=中东区)
亚洲区负责人：分配部门=亚洲区 OR (分配部门=南美非洲公区 AND 跟进人所属部门=亚洲区)

用法：
  python3 scripts/apply_manager_follower_dept_filters.py
  python3 scripts/apply_manager_follower_dept_filters.py --dry-run
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

ROLES = {
    "rolnScxe1Y": {
        "role_name": "中东区负责人",
        "dept": ["中东区", "外贸三部（中东/非洲）"],
        "follower_dept": ["中东区"],
    },
    "rolzFS28cZ": {
        "role_name": "亚洲区负责人",
        "dept": ["亚洲区", "外贸二部（亚洲/中亚）"],
        "follower_dept": ["亚洲区"],
    },
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


def update_role(role_id: str, patch: dict[str, Any], *, dry_run: bool) -> None:
    payload = json.dumps({**patch, "role_type": "custom_role"}, ensure_ascii=False)
    if dry_run:
        print(f"  [dry-run] role-update {role_id}")
        return
    _base(["+role-update", "--role-id", role_id, "--json", payload], yes=True)


def dept_filter(values: list[str]) -> dict[str, Any]:
    return {
        "field_name": "分配部门",
        "operator": "contains",
        "field_type": "Lookup",
        "field_ui_type": "",
        "reference_type": "SingleSelect",
        "filter_values": values,
        "is_invalid": False,
    }


def follower_dept_filter(values: list[str]) -> dict[str, Any]:
    return {
        "field_name": "跟进人所属部门",
        "operator": "contains",
        "field_type": "Lookup",
        "field_ui_type": "",
        "reference_type": "SingleSelect",
        "filter_values": values,
        "is_invalid": False,
    }


def manager_filter(dept_values: list[str], follower_dept_values: list[str]) -> dict[str, Any]:
    return {
        "conjunction": "or",
        "filter_rules": [
            {"conjunction": "and", "filters": [dept_filter(dept_values)]},
            {
                "conjunction": "and",
                "filters": [
                    dept_filter(["南美非洲公区"]),
                    follower_dept_filter(follower_dept_values),
                ],
            },
        ],
    }


def patch_role(role_id: str, cfg: dict[str, Any], *, dry_run: bool) -> None:
    role = get_role(role_id)
    trm = copy.deepcopy(role["table_rule_map"])
    main = trm[MAIN]
    rr = main.setdefault("record_rule", {})
    new_filter = manager_filter(cfg["dept"], cfg["follower_dept"])
    rr["read_filter_rule_group"] = copy.deepcopy(new_filter)
    rr["edit_filter_rule_group"] = copy.deepcopy(new_filter)
    rr["other_record_all_read"] = False

    fr = main.setdefault("field_rule", {})
    if fr.get("field_perm_mode") == "specify":
        perms = dict(fr.get("field_perms") or {})
        perms["跟进人所属部门"] = "read"
        fr["field_perms"] = perms

    print(f"\n== {cfg['role_name']} ({role_id})")
    update_role(role_id, {"role_name": cfg["role_name"], "table_rule_map": trm}, dry_run=dry_run)


def verify() -> None:
    print("\n== verify")
    for role_id, cfg in ROLES.items():
        role = get_role(role_id)
        main = role["table_rule_map"][MAIN]
        rr = main["record_rule"]
        perms = (main.get("field_rule") or {}).get("field_perms") or {}
        print(f"{cfg['role_name']}: 跟进人所属部门={perms.get('跟进人所属部门')}")
        print(json.dumps(rr.get("read_filter_rule_group"), ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for role_id, cfg in ROLES.items():
        patch_role(role_id, cfg, dry_run=args.dry_run)

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
