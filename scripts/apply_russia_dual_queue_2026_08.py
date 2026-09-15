#!/usr/bin/env python3
"""俄罗斯/白俄罗斯：Wendy → Mia 双人顺序轮循（无负责人）。

对齐英/德子办做法：
1. 停用子办规则表固定 Wendy
2. 国家映射改为「俄罗斯子办队列」且 是否子办区域=否（才能走渠道轮转）
3. 渠道顺序队列表 + 指针表：5 渠道 × Wendy(1)/Mia(2)
4. 主表业务员选项补 Mia；在职表补 Mia
5. 角色：俄白子办业务员含 Wendy+Mia；俄白子办负责人清空成员

仅影响新线索；不改历史跟进人。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
AS = ["--as", "user"]

T_MAP = "tblakShBJnVZiU2Y"
T_QUEUE = "tblav9GLrm8Vnf1j"
T_PTR = "tblGWSsPla3eRfuY"
T_SUB = "tblYQpLxEBYjFN0T"
T_EMP = "tbl1VLoTjKb3YIu8"
T_MAIN = "tbluuuXn9WexH8LV"

QUEUE_NAME = "俄罗斯子办队列"
PEOPLE = ["Wendy", "Mia"]  # 顺位 1→2
CHANNELS = ["Facebook", "谷歌", "阿里国际站", "国内渠道", "Outbound渠道", "LinkedIn"]
COUNTRIES = ["俄罗斯", "白俄罗斯"]

ROLE_SP = "rol0oxX294m"  # 俄白子办业务员
ROLE_MGR = "rolXVcTk5lZ"  # 俄白子办负责人（应无成员）

OID = {
    "Wendy": "ou_49d34d8a0ac045f194b112f6f31327ab",  # 古绮雯
    "Mia": "ou_ca2bb3664599dc44cb15d284d4e02ef2",  # 穆艳莎·阿不来海提
}

HUES = ["Blue", "Orange", "Wathet", "Yellow", "Turquoise", "Red", "Purple", "Green", "Carmine", "Lime", "Gray"]
DRY_RUN = "--dry-run" in sys.argv


def run(args: list[str], check: bool = True) -> dict[str, Any]:
    r = subprocess.run(["lark-cli", *args], capture_output=True, text=True)
    if r.returncode != 0 and check:
        print("CMD FAIL", " ".join(args[:14]), file=sys.stderr)
        print((r.stderr or r.stdout)[:3000], file=sys.stderr)
        raise SystemExit(r.returncode)
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": r.stdout, "stderr": r.stderr, "code": r.returncode}


def cell(v: Any) -> str:
    if v is None or v == []:
        return ""
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        if not v:
            return ""
        if isinstance(v[0], dict):
            return ",".join(str(x.get("text") or x.get("name") or x) for x in v)
        return str(v[0])
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v)
    return str(v)


def field_get(table_id: str, field_id: str) -> dict[str, Any]:
    out = run(
        [
            "base", "+field-get",
            "--base-token", BASE, "--table-id", table_id,
            "--field-id", field_id, *AS, "--format", "json",
        ]
    )
    return out["data"]["field"]


def field_update(table_id: str, field_id: str, payload: dict[str, Any]) -> None:
    if DRY_RUN:
        print(f"  [dry] field-update {table_id} {field_id} +options")
        return
    out = run(
        [
            "base", "+field-update",
            "--base-token", BASE, "--table-id", table_id,
            "--field-id", field_id,
            "--json", json.dumps(payload, ensure_ascii=False),
            "--yes",
            *AS, "--format", "json",
        ]
    )
    if out.get("ok") is False:
        print(out)
        raise SystemExit(1)


def ensure_select_options(table_id: str, field_id: str, extra_names: list[str]) -> None:
    f = field_get(table_id, field_id)
    opts = list(f.get("options") or [])
    existing = {o["name"] for o in opts}
    add = [n for n in extra_names if n not in existing]
    if not add:
        print(f"  options ok {f.get('name')}: +0")
        return
    i0 = len(opts)
    for j, name in enumerate(add):
        opts.append({"name": name, "hue": HUES[(i0 + j) % len(HUES)], "lightness": "Lighter"})
    payload: dict[str, Any] = {
        "name": f["name"],
        "type": "select",
        "multiple": bool(f.get("multiple")),
        "options": opts,
    }
    if f.get("dynamic_options_source"):
        payload["dynamic_options_source"] = f["dynamic_options_source"]
    if f.get("description"):
        payload["description"] = f["description"]
    field_update(table_id, field_id, payload)
    print(f"  added to {f.get('name')}: {add}")


def list_all(table_id: str, fields: list[str]) -> list[dict[str, Any]]:
    """Paginate via record-list markdown/json — use search with high limit + keyword fallback."""
    out = run(
        [
            "base", "+record-list",
            "--base-token", BASE, "--table-id", table_id,
            "--limit", "200", *AS, "--format", "json",
        ],
        check=False,
    )
    rows: list[dict[str, Any]] = []
    data = out.get("data") or {}
    # shape A: data.data rows + fields + record_id_list
    if isinstance(data, dict) and "fields" in data and "data" in data:
        colnames = data["fields"]
        for row, rid in zip(data["data"], data.get("record_id_list") or []):
            m = dict(zip(colnames, row))
            m["_id"] = rid
            for k in list(m):
                if k != "_id":
                    m[k] = cell(m[k])
            rows.append(m)
        return rows
    # shape B: items
    items = data.get("items") if isinstance(data, dict) else None
    if items is None and isinstance(data, list):
        items = data
    for it in items or []:
        if not isinstance(it, dict):
            continue
        f = it.get("fields") or {}
        m = {k: cell(f.get(k)) for k in fields}
        m["_id"] = it.get("record_id") or it.get("id") or ""
        rows.append(m)
    return rows


def batch_create(table_id: str, fields: list[str], rows: list[list[Any]]) -> None:
    if not rows:
        return
    if DRY_RUN:
        print(f"  [dry] batch-create {table_id} +{len(rows)}")
        return
    for i in range(0, len(rows), 100):
        chunk = rows[i : i + 100]
        body = {"fields": fields, "rows": chunk}
        out = run(
            [
                "base", "+record-batch-create",
                "--base-token", BASE, "--table-id", table_id,
                "--json", json.dumps(body, ensure_ascii=False),
                *AS, "--format", "json",
            ]
        )
        if out.get("ok") is False:
            print(out)
            raise SystemExit(1)
        print(f"  batch-create {table_id} +{len(chunk)}")
        time.sleep(0.25)


def upsert_record(table_id: str, record_id: str, patch: dict[str, Any]) -> None:
    if DRY_RUN:
        print(f"  [dry] upsert {table_id} {record_id} {patch}")
        return
    out = run(
        [
            "base", "+record-upsert",
            "--base-token", BASE, "--table-id", table_id,
            "--record-id", record_id,
            "--json", json.dumps(patch, ensure_ascii=False),
            *AS, "--format", "json",
        ]
    )
    if out.get("ok") is False:
        print(out)
        raise SystemExit(1)
    time.sleep(0.15)


def api(method: str, path: str, data: dict | None = None) -> dict[str, Any]:
    args = ["api", method, path, "--as", "user", "--format", "json"]
    if data is not None:
        args += ["--data", json.dumps(data, ensure_ascii=False)]
    return run(args)


def list_role_members(role_id: str) -> list[dict[str, Any]]:
    out = api("GET", f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members")
    # unwrap
    payload = out.get("data") or out
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if isinstance(payload, dict):
        return payload.get("items") or payload.get("members") or []
    return []


def add_role_members(role_id: str, open_ids: list[str]) -> None:
    existing = {m.get("open_id") for m in list_role_members(role_id)}
    to_add = [oid for oid in open_ids if oid not in existing]
    if not to_add:
        print(f"  role {role_id} members already ok")
        return
    print(f"  role {role_id} add {to_add}")
    if DRY_RUN:
        return
    for oid in to_add:
        out = api(
            "POST",
            f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members?member_id_type=open_id",
            {"member_id": oid},
        )
        if out.get("ok") is False:
            print(out)
            raise SystemExit(1)


def clear_role_members(role_id: str) -> None:
    members = list_role_members(role_id)
    if not members:
        print(f"  role {role_id} already empty")
        return
    oids = [m.get("open_id") for m in members if m.get("open_id")]
    print(f"  role {role_id} clear {[(m.get('member_name'), m.get('open_id')) for m in members]}")
    if DRY_RUN or not oids:
        return
    for oid in oids:
        out = api(
            "DELETE",
            f"/open-apis/bitable/v1/apps/{BASE}/roles/{role_id}/members/{oid}?member_id_type=open_id",
        )
        if out.get("ok") is False:
            print(out)
            raise SystemExit(1)


def step_options() -> None:
    print("\n== 1. select options ==")
    ensure_select_options(T_MAP, "所属区域组", [QUEUE_NAME])
    ensure_select_options(T_QUEUE, "队列名称", [QUEUE_NAME])
    # 主表渠道顺序队列匹配业务员 + Case handler
    ensure_select_options(T_MAIN, "fld4Uk8KfA", PEOPLE)
    ensure_select_options(T_MAIN, "fldJi4Y57A", PEOPLE)


def step_queues_and_pointers() -> None:
    print("\n== 2. queues + pointers ==")
    existing = list_all(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用", "队列Key"])
    have = set()
    for r in existing:
        if r.get("队列名称") == QUEUE_NAME or (r.get("队列Key") or "").endswith(f"|{QUEUE_NAME}"):
            have.add((r.get("渠道") or "", r.get("业务员") or "", str(r.get("顺位") or "")))
    rows: list[list[Any]] = []
    for ch in CHANNELS:
        for i, person in enumerate(PEOPLE, start=1):
            if (ch, person, str(i)) in have:
                continue
            rows.append([QUEUE_NAME, ch, person, i, "启用"])
    if rows:
        batch_create(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用"], rows)
    else:
        print("  queue rows already exist")

    ptrs = list_all(T_PTR, ["队列Key", "当前顺序号", "是否启用"])
    phave = {r.get("队列Key") for r in ptrs}
    prows = []
    for ch in CHANNELS:
        key = f"{ch}|{QUEUE_NAME}"
        if key in phave:
            # ensure enabled
            for r in ptrs:
                if r.get("队列Key") == key and r.get("是否启用") != "启用":
                    upsert_record(T_PTR, r["_id"], {"是否启用": "启用"})
            continue
        prows.append([key, 1, "启用"])
    if prows:
        batch_create(T_PTR, ["队列Key", "当前顺序号", "是否启用"], prows)
    else:
        print("  pointer rows already exist / enabled")


def step_country_map() -> None:
    print("\n== 3. country map → 俄罗斯子办队列 ==")
    for country in COUNTRIES:
        out = run(
            [
                "base", "+record-search",
                "--base-token", BASE, "--table-id", T_MAP,
                "--keyword", country, "--search-field", "国家",
                "--limit", "5", *AS, "--format", "json",
            ]
        )
        data = out["data"]
        fields = data["fields"]
        found = False
        for row, rid in zip(data["data"], data["record_id_list"]):
            m = dict(zip(fields, row))
            if cell(m.get("国家")) != country:
                continue
            found = True
            patch = {
                "所属区域组": QUEUE_NAME,
                "是否子办区域": "否",
                "线索分配部门": "俄罗斯办事处",
                "备注": "2026-08-14 Wendy/Mia 双人顺序轮循（无负责人）",
            }
            print(f"  {country} {rid} -> {patch}")
            upsert_record(T_MAP, rid, patch)
        if not found:
            print(f"  WARN: country not found: {country}")


def step_disable_suboffice_fixed() -> None:
    print("\n== 4. disable fixed Wendy suboffice rules ==")
    for country in COUNTRIES:
        out = run(
            [
                "base", "+record-search",
                "--base-token", BASE, "--table-id", T_SUB,
                "--keyword", country, "--search-field", "国家",
                "--limit", "5", *AS, "--format", "json",
            ]
        )
        data = out["data"]
        fields = data["fields"]
        for row, rid in zip(data["data"], data["record_id_list"]):
            m = dict(zip(fields, row))
            if cell(m.get("国家")) != country:
                continue
            if cell(m.get("是否启用")) != "启用":
                print(f"  {country} already disabled")
                continue
            patch = {
                "是否启用": "停用",
                "备注": "2026-08-14 改为俄罗斯子办双人顺序轮循(Wendy/Mia)，停用单人子办",
            }
            print(f"  disable {country} {rid} owner={cell(m.get('负责人'))}")
            upsert_record(T_SUB, rid, patch)


def step_employee() -> None:
    print("\n== 5. employee roster ==")
    existing = list_all(T_EMP, ["业务员姓名", "是否在职", "所属部门"])
    by_name = {r.get("业务员姓名"): r for r in existing}
    for person in PEOPLE:
        if person in by_name:
            r = by_name[person]
            patch: dict[str, Any] = {}
            if r.get("是否在职") != "是":
                patch["是否在职"] = "是"
            if r.get("所属部门") != "俄罗斯办事处":
                patch["所属部门"] = "俄罗斯办事处"
            if patch:
                print(f"  update {person} {patch}")
                upsert_record(T_EMP, r["_id"], patch)
            else:
                print(f"  employee ok {person}")
        else:
            print(f"  create employee {person}")
            batch_create(T_EMP, ["业务员姓名", "是否在职", "所属部门"], [[person, "是", "俄罗斯办事处"]])


def step_roles() -> None:
    print("\n== 6. roles (业务员 Wendy+Mia；负责人清空) ==")
    add_role_members(ROLE_SP, [OID["Wendy"], OID["Mia"]])
    clear_role_members(ROLE_MGR)
    print("  SP members now:", [(m.get("member_name"), m.get("open_id")) for m in list_role_members(ROLE_SP)])
    print("  MGR members now:", [(m.get("member_name"), m.get("open_id")) for m in list_role_members(ROLE_MGR)])


def verify() -> None:
    print("\n== 7. verify ==")
    for country in COUNTRIES:
        out = run(
            [
                "base", "+record-search",
                "--base-token", BASE, "--table-id", T_MAP,
                "--keyword", country, "--search-field", "国家",
                "--limit", "3", *AS, "--format", "json",
            ]
        )
        data = out["data"]
        fields = data["fields"]
        for row in data["data"]:
            m = {k: cell(v) for k, v in zip(fields, row)}
            if m.get("国家") != country:
                continue
            print(
                f"  map {country}: 区域组={m.get('所属区域组')} 子办={m.get('是否子办区域')} 部门={m.get('线索分配部门')}"
            )
    q = list_all(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用", "队列Key"])
    ru = [r for r in q if r.get("队列名称") == QUEUE_NAME or (r.get("队列Key") or "").endswith(f"|{QUEUE_NAME}")]
    print(f"  queue rows for {QUEUE_NAME}: {len(ru)}")
    for r in sorted(ru, key=lambda x: (x.get("渠道") or "", x.get("顺位") or "")):
        print(f"    {r.get('队列Key') or r.get('渠道')} #{r.get('顺位')} {r.get('业务员')} {r.get('是否启用')}")
    ptrs = list_all(T_PTR, ["队列Key", "当前顺序号", "是否启用"])
    for r in ptrs:
        if (r.get("队列Key") or "").endswith(f"|{QUEUE_NAME}"):
            print(f"  ptr {r.get('队列Key')} current={r.get('当前顺序号')} {r.get('是否启用')}")


def main() -> int:
    print(f"Russia dual queue apply dry_run={DRY_RUN}")
    step_options()
    step_queues_and_pointers()
    step_country_map()
    step_disable_suboffice_fixed()
    step_employee()
    step_roles()
    verify()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
