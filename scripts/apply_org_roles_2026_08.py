#!/usr/bin/env python3
"""Phase 3：询盘分配新区组织 — Base 高级权限角色改名 / 过滤 / 成员对齐。

用法：
  python3 scripts/apply_org_roles_2026_08.py
  python3 scripts/apply_org_roles_2026_08.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from copy import deepcopy
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
MAIN = "线索总池 Case Database"

OID = {
    "彭桑": "ou_6d5a02a6249b5ab94b955009807d0df0",
    "Jannice": "ou_cb84f4105dbfea96eccf8b3e0dd3507e",  # 曾宪玲
    "Sue": "ou_ac366adb0bd8a89e23c312de4b03ce53",  # 黄淑仪
    "Leepy": "ou_4c6b35b85a4f957ffdaf6eaa5fe08c01",  # 李一萍
    "Gigi": "ou_fd50a6c4c235d33c77e1456e92a118a3",  # 黄嘉琪
    "Sherry": "ou_1617c9d9b13de76df2084db15d77417d",  # 芮圣美
    "Hanny": "ou_264ef2e445ee9df57810bf178dcbaa02",  # 温涵
    "Lindsey": "ou_348a7234d78945237fd14efb61708a5a",  # 张婉璐
    "James": "ou_bf6418cc8e7fdb36fc880c0d1909f3ee",
}

ROLES = {
    "europe_mgr": "rol1YnCUVM",  # → 欧洲大区负责人
    "asia_mgr": "rolzFS28cZ",  # → 亚洲区负责人
    "me_mgr": "rolnScxe1Y",  # → 中东区负责人
    "latam_mgr": "rolYtTHNUz",  # → 归档-旧四部负责人
    "latam_sp": "rolfLnHD1tO",  # → 中东区_业务员
    "intl_sp": "rolxWooJUFN",  # 国际部_业务员（补 Gigi）
    "de_mgr": "rolFieisEP",
    "de_sp": "rolww797uyO",
    "uk_mgr": "rolQPj4NHP4",
    "uk_sp": "rol1XZhoKy",
}

# 精确替换：单值旧列表 → 新多值列表；同时把 operator 统一成 contains（兼容多选）
FILTER_REPLACEMENTS: dict[tuple[str, ...], list[str]] = {
    ("外贸一部（欧洲）",): [
        "欧洲大区",
        "英国办事处",
        "德国办事处",
        "外贸一部（欧洲）",
    ],
    ("外贸二部（亚洲/中亚）",): [
        "亚洲区",
        "南美非洲公区",
        "外贸二部（亚洲/中亚）",
        "外贸四部（拉丁美洲/中南美洲）",
    ],
    ("外贸三部（中东/非洲）",): [
        "中东区",
        "南美非洲公区",
        "外贸三部（中东/非洲）",
        "外贸四部（拉丁美洲/中南美洲）",
    ],
    # 四部归档角色：仍只看旧四部部门值（历史）
    ("外贸四部（拉丁美洲/中南美洲）",): ["外贸四部（拉丁美洲/中南美洲）"],
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
    return _run(
        ["base", *args, "--base-token", BASE, "--as", "user", "--format", "json"],
        yes=yes,
    )


def _api(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    cmd = ["api", method, path, "--as", "user", "--format", "json"]
    if body is not None:
        cmd += ["--data", json.dumps(body, ensure_ascii=False)]
    return _run(cmd)


def get_role(role_id: str) -> dict[str, Any]:
    raw = _base(["+role-get", "--role-id", role_id])
    data = raw["data"]["data"]
    return json.loads(data) if isinstance(data, str) else data


def update_role(role_id: str, patch: dict[str, Any], *, dry_run: bool) -> None:
    # CLI 要求显式带 role_type，否则会报 invalid role type ''
    if "role_type" not in patch:
        patch = {**patch, "role_type": "custom_role"}
    payload = json.dumps(patch, ensure_ascii=False)
    if dry_run:
        print(f"  [dry-run] role-update {role_id} keys={list(patch.keys())}")
        if "role_name" in patch:
            print(f"    role_name -> {patch['role_name']}")
        return
    _base(["+role-update", "--role-id", role_id, "--json", payload], yes=True)


def list_members(role_id: str) -> list[dict[str, Any]]:
    out = _api("GET", f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members")
    return out.get("data", {}).get("items") or []


def add_members(role_id: str, open_ids: list[str], *, dry_run: bool) -> None:
    existing = {m["open_id"] for m in list_members(role_id)}
    to_add = [oid for oid in open_ids if oid not in existing]
    if not to_add:
        print(f"  members add skip (already present): {open_ids}")
        return
    print(f"  members add: {to_add}")
    if dry_run:
        return
    if len(to_add) == 1:
        _api(
            "POST",
            f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members?member_id_type=open_id",
            {"member_id": to_add[0]},
        )
        return
    _api(
        "POST",
        f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members/batch_create",
        {"member_list": [{"type": "open_id", "id": oid} for oid in to_add]},
    )


def remove_members(role_id: str, open_ids: list[str], *, dry_run: bool) -> None:
    existing = {m["open_id"] for m in list_members(role_id)}
    to_del = [oid for oid in open_ids if oid in existing]
    if not to_del:
        print(f"  members remove skip (not present): {open_ids}")
        return
    print(f"  members remove: {to_del}")
    if dry_run:
        return
    try:
        _api(
            "POST",
            f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members/batch_delete",
            {"member_id_list": to_del},
        )
        return
    except RuntimeError:
        pass
    for oid in to_del:
        _api(
            "DELETE",
            f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members/{oid}",
        )


def walk_replace_dept_filters(node: Any, allowed_old: set[tuple[str, ...]] | None = None) -> int:
    """Replace 分配部门 filter_values by FILTER_REPLACEMENTS. Return count."""
    changed = 0
    if isinstance(node, dict):
        if (
            node.get("field_name") == "分配部门"
            and isinstance(node.get("filter_values"), list)
            and node["filter_values"]
        ):
            key = tuple(node["filter_values"])
            if key in FILTER_REPLACEMENTS and (allowed_old is None or key in allowed_old):
                new_vals = FILTER_REPLACEMENTS[key]
                if node["filter_values"] != new_vals or (
                    len(new_vals) > 1 and node.get("operator") == "is"
                ):
                    node["filter_values"] = list(new_vals)
                    if len(new_vals) > 1:
                        node["operator"] = "contains"
                    changed += 1
        for v in node.values():
            changed += walk_replace_dept_filters(v, allowed_old)
    elif isinstance(node, list):
        for item in node:
            changed += walk_replace_dept_filters(item, allowed_old)
    return changed


def set_main_visible_views(role: dict[str, Any], views: list[str]) -> None:
    main = role["table_rule_map"][MAIN]
    vis = main.setdefault("view_rule", {}).setdefault("visibility", {})
    vis["all_visible"] = False
    vis["visible_views"] = views


def patch_manager_role(
    role_id: str,
    new_name: str,
    old_filter_key: tuple[str, ...],
    *,
    dry_run: bool,
    visible_views: list[str] | None = None,
) -> None:
    role = get_role(role_id)
    print(f"\n== {role.get('role_name')} ({role_id}) → {new_name}")
    trm = deepcopy(role["table_rule_map"])
    n = walk_replace_dept_filters(trm, allowed_old={old_filter_key})
    if visible_views is not None:
        set_main_visible_views({"table_rule_map": trm}, visible_views)
    print(f"  filter patches: {n}")
    update_role(
        role_id,
        {"role_name": new_name, "table_rule_map": trm},
        dry_run=dry_run,
    )


def patch_archive_latam_mgr(*, dry_run: bool) -> None:
    role_id = ROLES["latam_mgr"]
    role = get_role(role_id)
    new_name = "归档-旧四部负责人"
    print(f"\n== {role.get('role_name')} ({role_id}) → {new_name}")
    # 过滤保持旧四部；视图已是归档视图
    update_role(role_id, {"role_name": new_name}, dry_run=dry_run)
    remove_members(role_id, [OID["Gigi"]], dry_run=dry_run)


def patch_me_salesperson(*, dry_run: bool) -> None:
    role_id = ROLES["latam_sp"]
    role = get_role(role_id)
    new_name = "中东区_业务员"
    print(f"\n== {role.get('role_name')} ({role_id}) → {new_name}")
    trm = deepcopy(role["table_rule_map"])
    set_main_visible_views({"table_rule_map": trm}, ["中东区", "转接的线索", "Sales Representative View"])
    update_role(
        role_id,
        {"role_name": new_name, "table_rule_map": trm},
        dry_run=dry_run,
    )
    add_members(role_id, [OID["Gigi"]], dry_run=dry_run)


def align_members(*, dry_run: bool) -> None:
    print("\n== members align")
    # 中东负责人：仅 Jannice；去掉 Sue
    remove_members(ROLES["me_mgr"], [OID["Sue"]], dry_run=dry_run)
    add_members(ROLES["me_mgr"], [OID["Jannice"]], dry_run=dry_run)
    # 亚洲：仅 Leepy
    add_members(ROLES["asia_mgr"], [OID["Leepy"]], dry_run=dry_run)
    # 欧洲：保留彭桑
    add_members(ROLES["europe_mgr"], [OID["彭桑"]], dry_run=dry_run)
    # Gigi 补进国际部业务员（跟进人视角，与同级一致）
    add_members(ROLES["intl_sp"], [OID["Gigi"]], dry_run=dry_run)
    # 德/英校验补齐
    add_members(ROLES["de_sp"], [OID["Sherry"]], dry_run=dry_run)
    add_members(ROLES["de_mgr"], [OID["Hanny"]], dry_run=dry_run)
    add_members(ROLES["uk_mgr"], [OID["Lindsey"]], dry_run=dry_run)
    add_members(ROLES["uk_sp"], [OID["James"]], dry_run=dry_run)


def verify() -> None:
    print("\n== verify")
    for key, rid in ROLES.items():
        if key in ("de_mgr", "de_sp", "uk_mgr", "uk_sp", "intl_sp"):
            # light check
            pass
        role = get_role(rid)
        main = role["table_rule_map"][MAIN]
        vis = (main.get("view_rule") or {}).get("visibility")
        ms = [(m.get("member_name"), m.get("open_id")) for m in list_members(rid)]
        # collect 分配部门 values in main filters
        found: list[list[str]] = []

        def collect(n: Any) -> None:
            if isinstance(n, dict):
                if n.get("field_name") == "分配部门" and isinstance(n.get("filter_values"), list):
                    found.append(list(n["filter_values"]))
                for v in n.values():
                    collect(v)
            elif isinstance(n, list):
                for i in n:
                    collect(i)

        collect(main.get("record_rule"))
        uniq = []
        for f in found:
            if f not in uniq:
                uniq.append(f)
        print(f"{rid} | {role['role_name']}")
        print(f"  views={json.dumps(vis, ensure_ascii=False)}")
        print(f"  dept_filters={json.dumps(uniq, ensure_ascii=False)}")
        print(f"  members={ms}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    patch_manager_role(
        ROLES["europe_mgr"],
        "欧洲大区负责人",
        ("外贸一部（欧洲）",),
        dry_run=dry,
        visible_views=["欧洲大区", "Sales Representative View"],
    )
    patch_manager_role(
        ROLES["asia_mgr"],
        "亚洲区负责人",
        ("外贸二部（亚洲/中亚）",),
        dry_run=dry,
        visible_views=["亚洲区", "Sales Representative View"],
    )
    patch_manager_role(
        ROLES["me_mgr"],
        "中东区负责人",
        ("外贸三部（中东/非洲）",),
        dry_run=dry,
        visible_views=["中东区", "Sales Representative View"],
    )
    patch_archive_latam_mgr(dry_run=dry)
    patch_me_salesperson(dry_run=dry)
    align_members(dry_run=dry)

    if not dry:
        verify()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
