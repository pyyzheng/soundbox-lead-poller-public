#!/usr/bin/env python3
"""子办高级权限改造：区域仪表盘 + 负责人/业务员角色 + 成员调整。

按已确认方案执行。用法：
  python3 scripts/restructure_suboffice_roles.py --phase all
  python3 scripts/restructure_suboffice_roles.py --phase dashboards|roles|members|views
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from copy import deepcopy
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
MAIN_TABLE = "线索总池 Case Database"
MAIN_TABLE_ID = "tbluuuXn9WexH8LV"
COUNTRY_FIELD = "Country（国家）"

SOURCE_NA = {
    "daily": "blkZ47J5R0gENlSR",
    "weekly": "blkI62HiVeZMhBUZ",
    "monthly": "blkC3sMG6yvt8L5F",
}
PERIOD_SUFFIX = {
    "daily": "每日数据_仪表盘",
    "weekly": "周度数据_仪表盘",
    "monthly": "月度数据_仪表盘",
}

FEONEY_COUNTRIES = [
    "澳大利亚",
    "新西兰",
    "阿鲁巴",
    "图瓦卢",
    "基里巴斯",
    "密克罗尼西亚",
    "巴布亚新几内亚",
    "帕劳",
    "所罗门群岛",
    "斐济",
    "汤加",
    "瑙鲁",
    "瓦努阿图",
    "萨摩亚",
]

# open_id
OID = {
    "邱俊珊": "ou_a9ddb263e855c7074e14ed4f760ae72a",
    "刘玲": "ou_7e75b358908db2c6ccac7ed533c810f1",
    "温涵": "ou_264ef2e445ee9df57810bf178dcbaa02",
    "Shirley": "ou_1190e91e91ef254be135d14acbe9a73e",
    "肖冠": "ou_853948fd5cbad1b95ccb81459efa9a7f",
    "古绮雯": "ou_49d34d8a0ac045f194b112f6f31327ab",
    "Alex": "ou_f0fad9465e148c4f4bdd671c1f34dcde",
    "Kay": "ou_7544b1ea9f5fd7debf298dba12728e21",
    "Burcu": "ou_516a521376c8c175b21f3dc45cca7aa8",
    "Jessica": "ou_5e8ab0f9e98cd414247f2e9fa170c613",
    "芮圣美": "ou_1617c9d9b13de76df2084db15d77417d",
    "王毅": "ou_b83197d396528480a61be115c5eb86b3",
    "王芷芹": "ou_4893eaa4832c359b82b0c3d5953f7b69",
}

EXISTING_ROLES = {
    "北美区域负责人": "rolMllUUqK",
    "区域负责人": "roluaSpNDh",
    "国际部_子办业务员": "rol2oHfSfn",
    "澳洲负责人": "rolgeEJY9G",
    "德国办事处": "rolFieisEP",
    "US_加州负责人": "rolck1e4bB",
    "us_加州业务员": "rolwzxs1rZ",
    "US_纽约业务员": "rolKpRGDj6",
    "加拿大业务员": "rolJibTyUx",
    "墨西哥业务员": "rolHiDx9UV",
    "香港子办业务员": "rolehmKZKY",
    "澳洲子办业务员": "rolvzGJzNR",
    "国际部_业务员": "rolxWooJUFN",
}


def _run(cmd: list[str], *, yes: bool = False) -> dict[str, Any]:
    full = ["lark-cli", *cmd]
    if yes and "--yes" not in full:
        full.append("--yes")
    proc = subprocess.run(full, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(full)}\n{proc.stderr or proc.stdout}")
    out = proc.stdout.strip()
    if not out:
        return {}
    return json.loads(out)


def _base(args: list[str], *, yes: bool = False) -> dict[str, Any]:
    return _run(["base", *args, "--base-token", BASE, "--as", "user", "--format", "json"], yes=yes)


def _api(method: str, path: str, data: dict | None = None) -> dict[str, Any]:
    cmd = ["api", method, path, "--as", "user", "--format", "json"]
    if data is not None:
        cmd += ["--data", json.dumps(data, ensure_ascii=False)]
    return _run(cmd)


def _parse_role(payload: dict[str, Any]) -> dict[str, Any]:
    inner = payload["data"]["data"]
    if isinstance(inner, str):
        inner = json.loads(inner)
    return inner.get("role") or inner


def _list_dashboards() -> dict[str, str]:
    raw = _base(["+dashboard-list"])
    items = raw.get("data", {}).get("items") or []
    if not items and isinstance(raw.get("data", {}).get("data"), (str, dict)):
        inner = raw["data"]["data"]
        if isinstance(inner, str):
            inner = json.loads(inner)
        items = inner.get("items") or []
    out: dict[str, str] = {}
    for it in items:
        if isinstance(it, str):
            it = json.loads(it)
        name = it.get("name")
        did = it.get("dashboard_id") or it.get("id")
        if name and did:
            out[name] = did
    return out


def _country_cond(countries: list[str]) -> dict[str, Any]:
    return {
        "field_name": COUNTRY_FIELD,
        "operator": "contains",
        "value": countries,
    }


def _replace_country_in_config(cfg: dict[str, Any] | None, countries: list[str]) -> dict[str, Any] | None:
    if not cfg:
        return cfg
    cfg = deepcopy(cfg)
    filt = cfg.get("filter")
    if not isinstance(filt, dict):
        return cfg
    conditions = filt.get("conditions") or []
    found = False
    for cond in conditions:
        if cond.get("field_name") == COUNTRY_FIELD:
            cond["operator"] = "contains"
            cond["value"] = countries
            found = True
    if not found:
        conditions.append(_country_cond(countries))
        filt["conditions"] = conditions
        filt.setdefault("conjunction", "and")
    return cfg


# ---------- Phase: views ----------


def phase_views() -> str:
    views = _base(["+view-list", "--table-id", MAIN_TABLE_ID])["data"]["views"]
    by_name = {v["name"]: v["id"] for v in views}
    target = "Russia Belarus Lead Region"
    if target in by_name:
        print(f"view exists: {target} -> {by_name[target]}")
        vid = by_name[target]
    else:
        created = _base(
            [
                "+view-create",
                "--table-id",
                MAIN_TABLE_ID,
                "--json",
                json.dumps({"name": target, "type": "grid"}, ensure_ascii=False),
            ]
        )
        vid = created["data"]["views"][0]["id"] if created["data"].get("views") else created["data"]["view"]["id"]
        print(f"created view: {target} -> {vid}")
    _base(
        [
            "+view-set-filter",
            "--table-id",
            MAIN_TABLE_ID,
            "--view-id",
            vid,
            "--json",
            json.dumps(
                {
                    "logic": "or",
                    "conditions": [
                        [COUNTRY_FIELD, "intersects", ["俄罗斯"]],
                        [COUNTRY_FIELD, "intersects", ["白俄罗斯"]],
                    ],
                },
                ensure_ascii=False,
            ),
        ]
    )
    print(f"filter set on {target}")
    return vid


# ---------- Phase: dashboards ----------


def _clone_dashboard_from_na(prefix: str, countries: list[str], period: str, existing: dict[str, str]) -> str:
    src_id = SOURCE_NA[period]
    name = f"{prefix}_{PERIOD_SUFFIX[period]}"
    if name in existing:
        target_id = existing[name]
        print(f"exists dashboard: {name} -> {target_id}")
    else:
        created = _base(["+dashboard-create", "--name", name])
        target_id = created["data"]["dashboard"]["dashboard_id"]
        existing[name] = target_id
        print(f"created dashboard: {name} -> {target_id}")

    src_blocks = _base(["+dashboard-get", "--dashboard-id", src_id])["data"]["dashboard"]["blocks"]
    dst_blocks = _base(["+dashboard-get", "--dashboard-id", target_id])["data"]["dashboard"]["blocks"]
    existing_names = {b["block_name"] for b in dst_blocks}

    for meta in src_blocks:
        bname = meta["block_name"]
        if bname in existing_names:
            continue
        detail = _base(
            ["+dashboard-block-get", "--dashboard-id", src_id, "--block-id", meta["block_id"]]
        )["data"]["block"]
        btype = detail["type"]
        cfg = _replace_country_in_config(detail.get("data_config"), countries)
        create_type = btype
        if btype in {"pivot_table", "text"}:
            print(f"  skip {bname} ({btype})")
            continue
        if btype == "unknown":
            if isinstance(cfg, dict) and cfg.get("count_all"):
                create_type = "statistics"
            else:
                print(f"  skip {bname} (unknown)")
                continue
        payload = json.dumps(cfg or {}, ensure_ascii=False)
        try:
            _base(
                [
                    "+dashboard-block-create",
                    "--dashboard-id",
                    target_id,
                    "--name",
                    bname,
                    "--type",
                    create_type,
                    "--data-config",
                    payload,
                    "--no-validate",
                ]
            )
            print(f"  + {bname}")
        except RuntimeError as exc:
            print(f"  ! {bname}: {exc}", file=sys.stderr)
        time.sleep(1.2)

    try:
        _base(["+dashboard-arrange", "--dashboard-id", target_id])
    except RuntimeError as exc:
        print(f"  arrange warn: {exc}", file=sys.stderr)
    return target_id


def phase_dashboards() -> dict[str, dict[str, str]]:
    existing = _list_dashboards()
    regions = {
        "澳洲/太平洋": FEONEY_COUNTRIES,
        "德国": ["德国"],
        "日本": ["日本"],
        "香港": ["香港"],
        "俄白": ["俄罗斯", "白俄罗斯"],
    }
    result: dict[str, dict[str, str]] = {}
    for prefix, countries in regions.items():
        result[prefix] = {}
        for period in ("daily", "weekly", "monthly"):
            did = _clone_dashboard_from_na(prefix, countries, period, existing)
            result[prefix][period] = did
    return result


# ---------- Role helpers ----------


def _own_record_filters(*, include_case_handler: bool = False) -> dict[str, Any]:
    """业务员只看自己的数据。

    Case handler 是姓名单选，高级权限无法用「当前用户」直接匹配；
    业务上 Case handler 转派后通常会反映到 Follower，故默认不加 Case handler 条件。
    """
    rules = [
        {
            "conjunction": "and",
            "filters": [
                {
                    "field_name": "CreatedUser",
                    "operator": "contains",
                    "field_type": "CreatedUser",
                    "reference_type": "CreatedUser",
                    "filter_values": None,
                    "is_invalid": False,
                }
            ],
        },
        {
            "conjunction": "and",
            "filters": [
                {
                    "field_name": "Assigned Salesperson（匹配的业务员账号）",
                    "operator": "contains",
                    "field_type": "Lookup",
                    "reference_type": "User",
                    "filter_values": None,
                    "is_invalid": False,
                }
            ],
        },
        {
            "conjunction": "and",
            "filters": [
                {
                    "field_name": "Follower（实际跟进人账号）",
                    "operator": "contains",
                    "field_type": "Lookup",
                    "reference_type": "User",
                    "filter_values": None,
                    "is_invalid": False,
                }
            ],
        },
    ]
    if include_case_handler:
        rules.append(
            {
                "conjunction": "and",
                "filters": [
                    {
                        "field_name": "Case handler",
                        "operator": "contains",
                        "field_type": "SingleSelect",
                        "reference_type": "SingleSelect",
                        "filter_values": None,
                        "is_invalid": False,
                    }
                ],
            }
        )
    return {"conjunction": "or", "filter_rules": rules}


def _country_record_filters(countries: list[str]) -> dict[str, Any]:
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
                        "reference_type": "SingleSelect",
                        "filter_values": countries,
                        "is_invalid": False,
                    }
                ],
            }
        ],
    }


def _global_dash_no_perm(extra_read: list[str] | None = None) -> dict[str, Any]:
    m = {
        "每日数据_仪表盘": {"perm": "no_perm"},
        "周度数据_仪表盘": {"perm": "no_perm"},
        "月度数据_仪表盘": {"perm": "no_perm"},
        "客户分级_仪表盘": {"perm": "no_perm"},
        "渠道ROI与线索转化分析": {"perm": "no_perm"},
        "狼群表数据": {"perm": "no_perm"},
        "北美_每日数据_仪表盘": {"perm": "no_perm"},
        "北美_周度数据_仪表盘": {"perm": "no_perm"},
        "北美_月度数据_仪表盘": {"perm": "no_perm"},
    }
    for name in extra_read or []:
        m[name] = {"perm": "read_only"}
    return m


def _manager_main_table(countries: list[str], views: list[str], field_perms: dict[str, str]) -> dict[str, Any]:
    return {
        "perm": "edit",
        "view_rule": {
            "allow_edit": False,
            "visibility": {"all_visible": False, "visible_views": views},
        },
        "record_rule": {
            "record_operations": ["add"],
            "edit_filter_rule_group": _country_record_filters(countries),
            "read_filter_rule_group": _country_record_filters(countries),
        },
        "field_rule": {
            "field_perm_mode": "specify",
            "field_perms": field_perms,
            "allow_edit_and_modify_option_fields": [],
        },
    }


def _sp_main_table(views: list[str], field_perms: dict[str, str]) -> dict[str, Any]:
    own = _own_record_filters(include_case_handler=False)
    return {
        "perm": "edit",
        "view_rule": {
            "allow_edit": False,
            "visibility": {"all_visible": False, "visible_views": views},
        },
        "record_rule": {
            "record_operations": ["add"],
            "edit_filter_rule_group": own,
            "read_filter_rule_group": own,
        },
        "field_rule": {
            "field_perm_mode": "specify",
            "field_perms": field_perms,
            "allow_edit_and_modify_option_fields": [],
        },
    }


def _get_role(role_id: str) -> dict[str, Any]:
    return _parse_role(_base(["+role-get", "--role-id", role_id]))


def _update_role(role_id: str, patch: dict[str, Any]) -> None:
    _base(
        ["+role-update", "--role-id", role_id, "--json", json.dumps(patch, ensure_ascii=False)],
        yes=True,
    )


def _create_role(cfg: dict[str, Any]) -> str:
    out = _base(["+role-create", "--json", json.dumps(cfg, ensure_ascii=False)])
    # shape may vary
    data = out.get("data") or {}
    role = data.get("role") or data
    if isinstance(role, str):
        role = json.loads(role)
    rid = role.get("role_id")
    if not rid and isinstance(data.get("data"), str):
        inner = json.loads(data["data"])
        rid = (inner.get("role") or inner).get("role_id")
    if not rid:
        # fallback list by name
        rid = _find_role_id(cfg["role_name"])
    if not rid:
        raise RuntimeError(f"role create missing id: {out}")
    return rid


def _find_role_id(name: str) -> str | None:
    raw = _base(["+role-list"])
    inner = raw["data"]["data"]
    if isinstance(inner, str):
        inner = json.loads(inner)
    for item in inner.get("base_roles", []):
        if isinstance(item, str):
            item = json.loads(item)
        if item.get("role_name") == name:
            return item.get("role_id")
    return None


def _list_members(role_id: str) -> list[dict[str, Any]]:
    out = _api("GET", f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members")
    return out.get("data", {}).get("items") or []


def _add_members(role_id: str, open_ids: list[str]) -> None:
    existing = {m["open_id"] for m in _list_members(role_id)}
    to_add = [oid for oid in open_ids if oid not in existing]
    if not to_add:
        return
    # 官方单条接口: body.member_id + query member_id_type
    # 批量: .../members/batch_create + member_list[{type,id}]
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


def _remove_members(role_id: str, open_ids: list[str]) -> None:
    existing = {m["open_id"] for m in _list_members(role_id)}
    to_del = [oid for oid in open_ids if oid in existing]
    if not to_del:
        return
    # batch_delete
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
        _api("DELETE", f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members/{oid}")


def _clear_members(role_id: str) -> None:
    oids = [m["open_id"] for m in _list_members(role_id)]
    if oids:
        _remove_members(role_id, oids)


# ---------- Phase: roles ----------


def phase_roles() -> dict[str, str]:
    na = _get_role(EXISTING_ROLES["北美区域负责人"])
    na_fields = na["table_rule_map"][MAIN_TABLE]["field_rule"]["field_perms"]
    sp = _get_role(EXISTING_ROLES["us_加州业务员"])
    sp_fields = sp["table_rule_map"][MAIN_TABLE]["field_rule"]["field_perms"]
    # Follow-up 用 all_edit，避免 Lookup/AutoNumber 在 specify 模式下校验失败
    follow_simple = {
        "perm": "edit",
        "view_rule": {"allow_edit": True, "visibility": {"all_visible": True}},
        "record_rule": {"record_operations": ["add"], "other_record_all_read": True},
        "field_rule": {"field_perm_mode": "all_edit", "allow_edit_and_modify_option_fields": []},
    }
    hist_simple = {"perm": "no_perm"}

    dash_names = _list_dashboards()
    role_ids: dict[str, str] = {}

    # A1 北美区域负责人 — 微调（只改主表，避免连带 Follow-up 字段权限校验失败）
    na_patch = {
        "role_name": "北美区域负责人",
        "role_type": "custom_role",
        "table_rule_map": {
            MAIN_TABLE: _manager_main_table(
                ["美国", "加拿大", "墨西哥"],
                ["US Lead Region", "CA Lead Region", "Mexico Lead Region", "转接的线索"],
                na_fields,
            ),
        },
    }
    _update_role(EXISTING_ROLES["北美区域负责人"], na_patch)
    role_ids["北美区域负责人"] = EXISTING_ROLES["北美区域负责人"]
    print("updated 北美区域负责人")

    managers = [
        (
            "澳洲/太平洋负责人",
            EXISTING_ROLES["澳洲负责人"],
            True,
            "澳洲负责人",
            FEONEY_COUNTRIES,
            ["Australia Lead Region", "转接的线索"],
            ["澳洲/太平洋_每日数据_仪表盘", "澳洲/太平洋_周度数据_仪表盘", "澳洲/太平洋_月度数据_仪表盘"],
        ),
        (
            "德国子办负责人",
            EXISTING_ROLES["德国办事处"],
            True,
            "德国办事处",
            ["德国"],
            ["German Lead Region", "转接的线索"],
            ["德国_每日数据_仪表盘", "德国_周度数据_仪表盘", "德国_月度数据_仪表盘"],
        ),
        (
            "日本子办负责人",
            None,
            False,
            None,
            ["日本"],
            ["Japan Lead Region", "转接的线索"],
            ["日本_每日数据_仪表盘", "日本_周度数据_仪表盘", "日本_月度数据_仪表盘"],
        ),
        (
            "香港子办负责人",
            None,
            False,
            None,
            ["香港"],
            ["HK Lead Region", "转接的线索"],
            ["香港_每日数据_仪表盘", "香港_周度数据_仪表盘", "香港_月度数据_仪表盘"],
        ),
        (
            "俄白子办负责人",
            None,
            False,
            None,
            ["俄罗斯", "白俄罗斯"],
            ["Russia Belarus Lead Region", "转接的线索"],
            ["俄白_每日数据_仪表盘", "俄白_周度数据_仪表盘", "俄白_月度数据_仪表盘"],
        ),
    ]

    for new_name, rid, rename, old_name, countries, views, dashes in managers:
        dash_map = _global_dash_no_perm([d for d in dashes if d in dash_names or True])
        # also no_perm other regional dashes except own
        for dn in list(dash_names):
            if dn not in dash_map:
                dash_map[dn] = {"perm": "no_perm"}
        for d in dashes:
            dash_map[d] = {"perm": "read_only"}

        table_map = {
            MAIN_TABLE: _manager_main_table(countries, views, na_fields),
            "Follow-up Records": deepcopy(follow_simple),
            "历史数据汇总 History Data": deepcopy(hist_simple),
            "实际跟进人名单": {"perm": "no_perm"},
        }
        cfg = {
            "role_name": new_name,
            "role_type": "custom_role",
            "base_rule_map": {"copy": True, "download": True},
            "dashboard_rule_map": dash_map,
            "table_rule_map": table_map,
        }
        if rid:
            # keep extra tables as no_perm from old if present
            old = _get_role(rid)
            for tname, tr in (old.get("table_rule_map") or {}).items():
                if tname not in table_map:
                    table_map[tname] = {"perm": "no_perm"} if tr.get("perm") != "no_perm" else tr
            cfg["table_rule_map"] = table_map
            if rename and old_name:
                cfg["role_name"] = new_name
            _update_role(rid, cfg)
            role_ids[new_name] = rid
            print(f"updated {old_name or rid} -> {new_name}")
        else:
            existing_id = _find_role_id(new_name)
            if existing_id:
                _update_role(existing_id, cfg)
                role_ids[new_name] = existing_id
                print(f"updated existing {new_name}")
            else:
                rid_new = _create_role(cfg)
                role_ids[new_name] = rid_new
                print(f"created {new_name} -> {rid_new}")

    # Salesperson roles
    sp_specs = [
        ("美国业务员", None, ["Sales Representative View", "US Lead Region", "转接的线索"]),
        ("德国业务员", None, ["Sales Representative View", "German Lead Region", "转接的线索"]),
        ("加拿大业务员", EXISTING_ROLES["加拿大业务员"], ["Sales Representative View", "CA Lead Region", "转接的线索"]),
        ("墨西哥业务员", EXISTING_ROLES["墨西哥业务员"], ["Sales Representative View", "Mexico Lead Region", "转接的线索"]),
        ("香港子办业务员", EXISTING_ROLES["香港子办业务员"], ["Sales Representative View", "HK Lead Region", "转接的线索"]),
        ("澳洲子办业务员", EXISTING_ROLES["澳洲子办业务员"], ["Sales Representative View", "Australia Lead Region", "转接的线索"]),
    ]
    for name, rid, views in sp_specs:
        table_map = {
            MAIN_TABLE: _sp_main_table(views, sp_fields),
            "Follow-up Records": deepcopy(follow_simple),
            "历史数据汇总 History Data": deepcopy(hist_simple),
        }
        # no_perm other tables from template
        for tname in sp.get("table_rule_map") or {}:
            if tname not in table_map:
                table_map[tname] = {"perm": "no_perm"}
        cfg = {
            "role_name": name,
            "role_type": "custom_role",
            "base_rule_map": {"copy": True, "download": True},
            "dashboard_rule_map": _global_dash_no_perm(),
            "table_rule_map": table_map,
        }
        if rid:
            _update_role(rid, cfg)
            role_ids[name] = rid
            print(f"updated {name}")
        else:
            existing_id = _find_role_id(name)
            if existing_id:
                _update_role(existing_id, cfg)
                role_ids[name] = existing_id
                print(f"updated existing {name}")
            else:
                # Prefer rename us_加州业务员 -> 美国业务员 for first create path
                if name == "美国业务员" and EXISTING_ROLES["us_加州业务员"]:
                    _update_role(EXISTING_ROLES["us_加州业务员"], cfg)
                    role_ids[name] = EXISTING_ROLES["us_加州业务员"]
                    print("renamed us_加州业务员 -> 美国业务员")
                else:
                    rid_new = _create_role(cfg)
                    role_ids[name] = rid_new
                    print(f"created {name} -> {rid_new}")

    return role_ids


# ---------- Phase: members ----------


def phase_members(role_ids: dict[str, str] | None = None) -> None:
    role_ids = role_ids or {}

    def rid(name: str, fallback_key: str | None = None) -> str:
        if name in role_ids:
            return role_ids[name]
        found = _find_role_id(name)
        if found:
            return found
        if fallback_key and fallback_key in EXISTING_ROLES:
            return EXISTING_ROLES[fallback_key]
        raise RuntimeError(f"role not found: {name}")

    # Clear legacy roles
    for key in ("区域负责人", "国际部_子办业务员", "US_加州负责人", "US_纽约业务员"):
        print(f"clear {key}")
        _clear_members(EXISTING_ROLES[key])

    # If 美国业务员 was renamed from us_加州, still clear US_纽约 etc already done
    # us_加州 may now be 美国业务员 — don't clear it

    # Remove managers from 国际部_业务员
    print("remove managers from 国际部_业务员")
    _remove_members(
        EXISTING_ROLES["国际部_业务员"],
        [OID["肖冠"], OID["温涵"], OID["古绮雯"]],
    )

    assignments = {
        "北美区域负责人": [OID["邱俊珊"]],
        "澳洲/太平洋负责人": [OID["刘玲"]],
        "德国子办负责人": [OID["温涵"]],
        "日本子办负责人": [OID["Shirley"]],
        "香港子办负责人": [OID["肖冠"]],
        "俄白子办负责人": [OID["古绮雯"]],
        "美国业务员": [OID["Alex"], OID["Kay"], OID["Burcu"], OID["Jessica"]],
        "德国业务员": [OID["芮圣美"]],
    }
    for name, oids in assignments.items():
        role = rid(name, None)
        print(f"set members {name}: {len(oids)}")
        # for US salesperson role ensure target members present; remove others if any leftover unintended?
        _add_members(role, oids)
        # For 美国业务员, ensure only these four (Alex was already there as us_加州)
        if name == "美国业务员":
            cur = [m["open_id"] for m in _list_members(role)]
            extra = [x for x in cur if x not in oids]
            if extra:
                _remove_members(role, extra)

    print("members done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "views", "dashboards", "roles", "members"])
    args = ap.parse_args()
    role_ids: dict[str, str] = {}
    if args.phase in ("all", "views"):
        phase_views()
    if args.phase in ("all", "dashboards"):
        phase_dashboards()
    if args.phase in ("all", "roles"):
        role_ids = phase_roles()
        print(json.dumps(role_ids, ensure_ascii=False, indent=2))
    if args.phase in ("all", "members"):
        phase_members(role_ids)


if __name__ == "__main__":
    main()
