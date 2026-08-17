#!/usr/bin/env python3
"""Apply 2026-08 org allocation cutover (new leads only; no historical reassignment)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import defaultdict
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
AS = ["--as", "user"]

# Tables
T_MAP = "tblakShBJnVZiU2Y"
T_QUEUE = "tblav9GLrm8Vnf1j"
T_PTR = "tblGWSsPla3eRfuY"
T_SUB = "tblYQpLxEBYjFN0T"
T_EMP = "tbl1VLoTjKb3YIu8"
T_MAIN = "tbluuuXn9WexH8LV"

CHANNELS = ["Facebook", "谷歌", "阿里国际站", "国内渠道", "Outbound渠道", "LinkedIn"]

NEW_QUEUES: dict[str, list[str]] = {
    "中东区队列": ["Gigi", "Sue", "Cathy"],
    "亚洲区队列": ["Stephanie", "Kevin", "Rita"],
    "南美非洲公区队列": ["Gigi", "Sue", "Cathy", "Stephanie", "Kevin", "Rita"],
    "欧洲公区队列": ["Hanny", "Sherry", "Kaka", "Snow"],
    "英国子办队列": ["Lindsey", "James"],  # 张婉璐=Lindsey
    "德国子办队列": ["Hanny", "Sherry"],
}

OLD_QUEUES_DISABLE = [
    "欧洲区队列",
    "亚洲/中亚区队列",
    "中东/非洲区队列",
    "拉丁美洲/中南美洲区队列",
]

EURO_SPECIAL_SUB = {
    "意大利": "Kaka",
    "法国": "Kaka",
    "比利时": "Snow",
    "荷兰": "Snow",
}

EURO_QUEUE_UK = "英国"
EURO_QUEUE_DE = "德国"

HUES = ["Blue", "Orange", "Wathet", "Yellow", "Turquoise", "Red", "Purple", "Green", "Carmine", "Lime", "Gray"]


def run(args: list[str], check: bool = True) -> dict[str, Any]:
    r = subprocess.run(["lark-cli", *args], capture_output=True, text=True)
    if r.returncode != 0 and check:
        print("CMD FAIL", " ".join(args[:12]), file=sys.stderr)
        print(r.stderr[:2000] or r.stdout[:2000], file=sys.stderr)
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
        if isinstance(v[0], list):
            return ",".join("" if x is None else str(x) for x in v[0])
        if isinstance(v[0], dict):
            return ",".join(str(x.get("text") or x.get("name") or x) for x in v)
        return ",".join(str(x) for x in v)
    return str(v)


def list_all(table_id: str, fields: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        cmd = [
            "base",
            "+record-list",
            "--base-token",
            BASE,
            "--table-id",
            table_id,
            *AS,
            "--format",
            "json",
            "--limit",
            "200",
            "--offset",
            str(offset),
        ]
        for f in fields:
            cmd += ["--field-id", f]
        d = run(cmd)["data"]
        names = d["fields"]
        for rid, row in zip(d["record_id_list"], d["data"]):
            rec = {"_id": rid}
            for i, n in enumerate(names):
                rec[n] = cell(row[i])
            rows.append(rec)
        if not d.get("has_more"):
            break
        offset += len(d["data"])
        time.sleep(0.08)
    return rows


def field_get(table_id: str, field_id: str) -> dict[str, Any]:
    return run(
        [
            "base",
            "+field-get",
            "--base-token",
            BASE,
            "--table-id",
            table_id,
            "--field-id",
            field_id,
            *AS,
            "--format",
            "json",
        ]
    )["data"]["field"]


def field_update(table_id: str, field_id: str, payload: dict[str, Any]) -> None:
    path = f"/tmp/field_upd_{field_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    # CLI requires relative path — copy into cwd-relative later via @ file from script dir
    out = run(
        [
            "base",
            "+field-update",
            "--base-token",
            BASE,
            "--table-id",
            table_id,
            "--field-id",
            field_id,
            "--json",
            json.dumps(payload, ensure_ascii=False),
            "--yes",
            *AS,
            "--format",
            "json",
        ]
    )
    if not out.get("ok", True) and out.get("ok") is False:
        print(out)
        raise SystemExit(1)
    print("  field-update ok", table_id, field_id, payload.get("name"))


def ensure_select_options(table_id: str, field_id: str, extra_names: list[str]) -> None:
    f = field_get(table_id, field_id)
    opts = list(f.get("options") or [])
    existing = {o["name"] for o in opts}
    add = [n for n in extra_names if n not in existing]
    if not add:
        print(f"  options already ok {f['name']}: +0")
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
    print(f"  added to {f['name']}: {add}")


def batch_create(table_id: str, fields: list[str], rows: list[list[Any]]) -> None:
    for i in range(0, len(rows), 100):
        chunk = rows[i : i + 100]
        body = {"fields": fields, "rows": chunk}
        out = run(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                BASE,
                "--table-id",
                table_id,
                "--json",
                json.dumps(body, ensure_ascii=False),
                *AS,
                "--format",
                "json",
            ]
        )
        if out.get("ok") is False:
            print(out)
            raise SystemExit(1)
        print(f"  batch-create {table_id} +{len(chunk)}")
        time.sleep(0.2)


def batch_update_same_patch(table_id: str, record_ids: list[str], patch: dict[str, Any]) -> None:
    for i in range(0, len(record_ids), 200):
        chunk = record_ids[i : i + 200]
        body = {"record_id_list": chunk, "patch": patch}
        out = run(
            [
                "base",
                "+record-batch-update",
                "--base-token",
                BASE,
                "--table-id",
                table_id,
                "--json",
                json.dumps(body, ensure_ascii=False),
                *AS,
                "--format",
                "json",
            ]
        )
        if out.get("ok") is False:
            print(out)
            raise SystemExit(1)
        print(f"  batch-update {table_id} x{len(chunk)} {patch}")
        time.sleep(0.15)


def batch_update(table_id: str, fields: list[str], rows: list[list[Any]]) -> None:
    """Each row [record_id, val...] -> group identical patches for batch-update API."""
    groups: dict[str, list[str]] = defaultdict(list)
    patches: dict[str, dict[str, Any]] = {}
    for r in rows:
        patch = {fields[j]: r[j + 1] for j in range(len(fields))}
        key = json.dumps(patch, ensure_ascii=False, sort_keys=True)
        patches[key] = patch
        groups[key].append(r[0])
    for key, ids in groups.items():
        batch_update_same_patch(table_id, ids, patches[key])


def step_options() -> None:
    print("\n== Step1: ensure select options ==")
    ensure_select_options(T_MAP, "所属区域组", list(NEW_QUEUES.keys()))
    ensure_select_options(T_MAP, "线索分配部门", ["中东区", "亚洲区", "欧洲大区", "南美非洲公区"])
    ensure_select_options(T_QUEUE, "队列名称", list(NEW_QUEUES.keys()))
    # 队列表业务员是动态选项，禁止 field-update；在主表源字段加 Sherry
    fmain = field_get(T_MAIN, "fld4Uk8KfA")
    print("  main salesperson:", fmain["name"])
    ensure_select_options(T_MAIN, "fld4Uk8KfA", ["Sherry"])
    ensure_select_options(T_EMP, "所属部门", ["中东区", "亚洲区", "欧洲大区", "南美非洲公区"])


def step_queues_and_pointers() -> None:
    print("\n== Step2: create new queues + pointers ==")
    existing = list_all(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用"])
    have = {(r["队列名称"], r["渠道"], str(r["业务员"]), str(r["顺位"])) for r in existing}

    rows: list[list[Any]] = []
    for qname, people in NEW_QUEUES.items():
        for ch in CHANNELS:
            for i, person in enumerate(people, start=1):
                key = (qname, ch, person, str(i))
                if key in have:
                    continue
                rows.append([qname, ch, person, i, "启用"])
    if rows:
        batch_create(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用"], rows)
    else:
        print("  queue rows already exist")

    # disable conflicting/enabled old queue rows for same channels if any new already active later
    # pointers
    ptrs = list_all(T_PTR, ["队列Key", "当前顺序号", "是否启用"])
    phave = {r["队列Key"] for r in ptrs}
    prows = []
    for qname, people in NEW_QUEUES.items():
        for ch in CHANNELS:
            key = f"{ch}|{qname}"
            if key in phave:
                continue
            prows.append([key, 1, "启用"])
    if prows:
        batch_create(T_PTR, ["队列Key", "当前顺序号", "是否启用"], prows)
    else:
        print("  pointer rows already exist")


def step_disable_old_queues() -> None:
    print("\n== Step3: disable old regional queues ==")
    existing = list_all(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用"])
    upd = []
    for r in existing:
        if r["队列名称"] in OLD_QUEUES_DISABLE and r.get("是否启用") == "启用":
            upd.append([r["_id"], "停用"])
    if upd:
        batch_update(T_QUEUE, ["是否启用"], upd)
    else:
        print("  no old queue rows to disable")

    ptrs = list_all(T_PTR, ["队列Key", "是否启用"])
    pupd = []
    for r in ptrs:
        key = r.get("队列Key") or ""
        if any(key.endswith(f"|{q}") or f"|{q}" in key for q in OLD_QUEUES_DISABLE):
            if r.get("是否启用") == "启用":
                pupd.append([r["_id"], "停用"])
    if pupd:
        batch_update(T_PTR, ["是否启用"], pupd)
    print(f"  disabled queue={len(upd)} ptr={len(pupd)}")


def step_suboffice() -> None:
    print("\n== Step4: suboffice rules ==")
    subs = list_all(T_SUB, ["国家", "负责人", "是否启用", "规则ID", "备注"])
    by_country = {r["国家"]: r for r in subs}

    # Disable Germany fixed Hanny so DE uses 德国子办队列
    if "德国" in by_country and by_country["德国"].get("是否启用") == "启用":
        batch_update(T_SUB, ["是否启用", "备注"], [[by_country["德国"]["_id"], "停用", "2026-08改为德国子办双人顺序轮循(Hanny/Sherry)，停用单人子办"]])
        print("  disabled 德国 fixed Hanny")

    # Ensure IT/FR/BE/NL
    create_rows = []
    max_id = 0
    for r in subs:
        try:
            max_id = max(max_id, int(str(r.get("规则ID") or "0")))
        except ValueError:
            pass
    for country, owner in EURO_SPECIAL_SUB.items():
        if country in by_country:
            r = by_country[country]
            fields_upd = []
            # update to owner+enable
            batch_update(
                T_SUB,
                ["负责人", "是否启用", "备注"],
                [[r["_id"], owner, "启用", f"2026-08欧洲专国固定 {owner}"]],
            )
            print(f"  updated {country} -> {owner}")
        else:
            max_id += 1
            create_rows.append([country, owner, "启用", str(max_id).zfill(3), f"2026-08欧洲专国固定 {owner}"])
    if create_rows:
        batch_create(T_SUB, ["国家", "负责人", "是否启用", "规则ID", "备注"], create_rows)


def classify_country(m: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return (所属区域组, 线索分配部门, 是否子办区域, 是否代理区域|None keep)."""
    c = m["国家"]
    region = m.get("区域") or ""
    agency = m.get("是否代理区域") or "否"

    # Keep Americas/JP/HK/AU/Oceania offices etc. if already 子办区队列 (except Europe specials handled below)
    if c in ("美国", "加拿大", "墨西哥", "日本", "香港", "澳大利亚", "新西兰", "俄罗斯", "白俄罗斯", "印度"):
        return None  # no change this round
    # Pacific islands under AU office — keep
    if m.get("线索分配部门") == "澳洲办事处" and m.get("所属区域组") == "子办区队列":
        return None

    if c == "英国":
        return ("英国子办队列", "英国办事处", "否", agency)
    if c == "德国":
        return ("德国子办队列", "德国办事处", "否", agency)
    if c in EURO_SPECIAL_SUB:
        return ("子办区队列", "欧洲大区", "是", agency if c != "荷兰" else "是")
    if c in ("根西岛", "法罗群岛", "马恩岛") or region == "欧洲":
        if c in ("俄罗斯", "白俄罗斯"):
            return None
        return ("欧洲公区队列", "欧洲大区", "否", agency)
    if region == "中东" or c == "中东":
        return ("中东区队列", "中东区", "否", agency)
    if region == "亚洲" or c in ("中亚", "中国"):
        if c == "印度":
            return None
        return ("亚洲区队列", "亚洲区", "否", agency)
    if region == "非洲" or c == "非洲":
        return ("南美非洲公区队列", "南美非洲公区", "否", agency)
    if region == "中南美洲其他" or c == "南美":
        return ("南美非洲公区队列", "南美非洲公区", "否", agency)

    return None


def step_mapping() -> None:
    print("\n== Step5: country mapping remaps ==")
    maps = list_all(
        T_MAP,
        ["国家", "所属区域组", "线索分配部门", "是否子办区域", "是否代理区域", "区域"],
    )
    updates = []
    for m in maps:
        res = classify_country(m)
        if not res:
            continue
        q, dept, sub, agency = res
        if (
            m.get("所属区域组") == q
            and m.get("线索分配部门") == dept
            and m.get("是否子办区域") == sub
            and (agency is None or m.get("是否代理区域") == agency)
        ):
            continue
        row = [m["_id"], q, dept, sub]
        fields = ["所属区域组", "线索分配部门", "是否子办区域"]
        if agency is not None and m.get("是否代理区域") != agency:
            fields.append("是否代理区域")
            row.append(agency)
        # pad: batch_update needs consistent field set — do two passes
        updates.append((m["国家"], m["_id"], q, dept, sub, agency))

    # group by whether agency changes
    no_ag = []
    with_ag = []
    for country, rid, q, dept, sub, agency in updates:
        cur = next(x for x in maps if x["_id"] == rid)
        if agency is not None and cur.get("是否代理区域") != agency:
            with_ag.append([rid, q, dept, sub, agency])
        else:
            no_ag.append([rid, q, dept, sub])
    print(f"  will update {len(updates)} countries (agency tweaks {len(with_ag)})")
    if no_ag:
        batch_update(T_MAP, ["所属区域组", "线索分配部门", "是否子办区域"], no_ag)
    if with_ag:
        batch_update(
            T_MAP,
            ["所属区域组", "线索分配部门", "是否子办区域", "是否代理区域"],
            with_ag,
        )
    # sample print
    for country, rid, q, dept, sub, agency in updates[:15]:
        print(f"   {country} -> {q} / {dept} / 子办={sub}")
    if len(updates) > 15:
        print(f"   ... +{len(updates)-15} more")


def step_employment() -> None:
    print("\n== Step6: employment departments ==")
    emp = list_all(T_EMP, ["业务员姓名", "是否在职", "所属部门"])
    mapping = {
        "Jannice": "中东区",
        "Gigi": "中东区",
        "Sue": "中东区",
        "Cathy": "中东区",
        "Leepy": "亚洲区",
        "Stephanie": "亚洲区",
        "Kevin": "亚洲区",
        "Rita": "亚洲区",
        "Kaka": "欧洲大区",
        "Snow": "欧洲大区",
        "Hanny": "德国办事处",  # keep office
        "Sherry": "德国办事处",
        "James": "英国办事处",
        "Lindsey": "英国办事处",
    }
    # Ensure Sherry/Lindsey rows exist if missing
    names = {r.get("业务员姓名") for r in emp}
    create = []
    for person, dept in (("Sherry", "德国办事处"), ("Lindsey", "英国办事处")):
        if person not in names:
            create.append([person, "是", dept])
    if create:
        batch_create(T_EMP, ["业务员姓名", "是否在职", "所属部门"], create)

    emp = list_all(T_EMP, ["业务员姓名", "是否在职", "所属部门"])
    upd = []
    for r in emp:
        name = r.get("业务员姓名")
        if name in mapping and r.get("所属部门") != mapping[name]:
            upd.append([r["_id"], mapping[name]])
            print(f"  {name}: {r.get('所属部门')} -> {mapping[name]}")
    if upd:
        batch_update(T_EMP, ["所属部门"], upd)


def step_verify() -> None:
    print("\n== Step7: verify ==")
    qs = list_all(T_QUEUE, ["队列名称", "渠道", "业务员", "顺位", "是否启用"])
    enb = [r for r in qs if r.get("是否启用") == "启用"]
    by = defaultdict(list)
    for r in enb:
        by[r["队列名称"]].append(r)
    for q in NEW_QUEUES:
        rows = by.get(q, [])
        chans = sorted({r["渠道"] for r in rows})
        people = sorted({(int(r["顺位"]) if str(r["顺位"]).isdigit() else 999, r["业务员"]) for r in rows if r["渠道"] == "Facebook"})
        print(f"  EN {q}: channels={len(chans)} fb_people={people}")
    for q in OLD_QUEUES_DISABLE:
        still = [r for r in enb if r["队列名称"] == q]
        print(f"  OLD {q} still enabled: {len(still)}")

    maps = list_all(T_MAP, ["国家", "所属区域组", "线索分配部门", "是否子办区域"])
    samples = ["阿联酋", "巴西", "尼日利亚", "西班牙", "英国", "德国", "意大利", "法国", "比利时", "荷兰", "泰国", "以色列", "沙特阿拉伯"]
    byc = {m["国家"]: m for m in maps}
    for c in samples:
        m = byc.get(c)
        if m:
            print(f"  MAP {c}: {m['所属区域组']} | {m['线索分配部门']} | 子办={m['是否子办区域']}")


def main() -> None:
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {
        "options": step_options,
        "queues": step_queues_and_pointers,
        "disable_old": step_disable_old_queues,
        "suboffice": step_suboffice,
        "mapping": step_mapping,
        "employment": step_employment,
        "verify": step_verify,
    }
    if step == "all":
        for name in ["options", "queues", "suboffice", "mapping", "disable_old", "employment", "verify"]:
            steps[name]()
    else:
        steps[step]()
    print("\nDONE", step)


if __name__ == "__main__":
    main()
