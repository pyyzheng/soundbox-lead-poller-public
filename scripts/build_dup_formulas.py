#!/usr/bin/env python3
"""Generate gated dup / assignment formula JSON for Feishu field-update."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DEFAULT_TABLE_ID = "tbluuuXn9WexH8LV"
TABLE_ID = os.environ.get("FEISHU_TABLE_ID", DEFAULT_TABLE_ID)


def table_ref() -> str:
    return f"bitable::$table[{TABLE_ID}].$field"

SRC = {
    "email": ("fldLlZgKLx", "fldjqObcql"),
    "phone": ("fldc822WUz", "fldlhPGN84"),
    "ali": ("fldRu0jqvn", "fldHCJrl1J"),
    "wx": ("fldhVsBiAq", "fldHeMzfE6"),
    "domain": ("fldLlZgKLx", "flduV0ywHy"),
}

READY = "fldeFLLrc1"
CONFLICT = "fldjytDaFk"
COUNT = "fldbSuvsML"
ASSIGN_SOURCE = "fldUa1OwhQ"

RAW_SRC_FIELDS = (
    "fldLlZgKLx",
    "fldc822WUz",
    "fldRu0jqvn",
    "fldhVsBiAq",
)

SUBOFFICE_ASSIGNEE = "fldBBzmesf"
AGENT_ASSIGNEE = "fld7jnKAvi"
QUEUE_ASSIGNEE = "fld4Uk8KfA"
DUP_OWNER = "fldQzU8NBM"


def ref(field_id: str) -> str:
    return f"{table_ref()}[{field_id}]"


def conflict_true() -> str:
    return f'AND({ref(COUNT)}>=2,EXACT(LOWER(TEXT({ref(CONFLICT)})),"true"))'


def valid_src(field_id: str) -> str:
    f = ref(field_id)
    return (
        f"AND(NOT(ISBLANK({f})),"
        f'NOT(OR({f}="N/A",{f}="无匹配类别",CONTAINTEXT({f}&"","匹配错误请检查"))))'
    )


def gated_owner(src_id: str, owner_id: str) -> str:
    return f"IF({valid_src(src_id)},{ref(owner_id)},\"\")"


def norm_owner(expr: str) -> str:
    return (
        f'IF(ISBLANK({expr}),"",'
        f'IF(FIND({expr},",")>0,'
        f"TRIM(LEFT({expr},FIND({expr},\",\")-1)),"
        f"TRIM({expr})))"
    )


def pair_conflict(a_src: str, a_owner: str, b_src: str, b_owner: str) -> str:
    ga = gated_owner(a_src, a_owner)
    gb = gated_owner(b_src, b_owner)
    return (
        f"AND(NOT(ISBLANK({ga})),NOT(ISBLANK({gb})),"
        f"NOT(OR({ga}={gb},CONTAINTEXT(\",\"&{ga}&\",\",\",\"&{gb}&\",\"),"
        f"CONTAINTEXT(\",\"&{gb}&\",\",\",\"&{ga}&\",\"))))"
    )


def build_ready() -> str:
    parts = ",".join(valid_src(fid) for fid in RAW_SRC_FIELDS)
    return f'IF(OR({parts}),"是","否")'


def build_conflict() -> str:
    # Strong matches (email/phone) take precedence. Lower-priority Ali/domain
    # matches should not create false conflicts when a strong owner exists.
    strong_pairs = [
        ("email", "phone"),
        ("email", "wx"),
        ("phone", "wx"),
    ]
    low_pairs = [
        ("ali", "wx"),
        ("ali", "domain"),
        ("wx", "domain"),
    ]
    strong_body = "OR(\n  " + ",\n  ".join(
        pair_conflict(*SRC[a], *SRC[b]) for a, b in strong_pairs
    ) + "\n)"
    low_body = "OR(\n  " + ",\n  ".join(
        pair_conflict(*SRC[a], *SRC[b]) for a, b in low_pairs
    ) + "\n)"
    has_strong = f"OR({valid_src(SRC['email'][0])},{valid_src(SRC['phone'][0])})"
    return f"IF({has_strong},{strong_body},{low_body})"


def build_assignment_source() -> str:
    r = ref
    return (
        f"IF(OR({r('fldqjZeH49')}=\"匹配错误请检查\",{r(DUP_OWNER)}=\"匹配错误请检查\"),\"查重冲突\","
        f"IF(OR(ISBLANK({r('fldqjZeH49')}),{r('fldqjZeH49')}=\"待查重\","
        f"AND({r('fldqjZeH49')}=\"查重中\",NOT({r(READY)}=\"是\"))),\"查重中\","
        f"IF(AND(NOT(ISBLANK({r(DUP_OWNER)})),NOT({r(DUP_OWNER)}=\"匹配错误请检查\"),"
        f"{r('fldyMGTE7A')}=\"是\",OR({r('flde1hdT3S')}=\"本部（公共）\","
        f"{r('fld6WkdNyf')}={r('flde1hdT3S')})),\"查重命中\","
        f"IF(AND(NOT(ISBLANK({r(DUP_OWNER)})),NOT({r(DUP_OWNER)}=\"匹配错误请检查\")),\"查重不继承\",\"无重复\"))))"
    )


def build_system_assignee() -> str:
    r = ref
    return (
        f"IFERROR(IF({r(ASSIGN_SOURCE)}=\"查重中\",\"\","
        f"IF({r(ASSIGN_SOURCE)}=\"查重冲突\",\"匹配错误请检查\","
        f"IF(AND({r(ASSIGN_SOURCE)}=\"查重命中\",NOT(ISBLANK({r(DUP_OWNER)}))),{r(DUP_OWNER)},"
        f"IF(NOT(ISBLANK({r(SUBOFFICE_ASSIGNEE)})),{r(SUBOFFICE_ASSIGNEE)},"
        f"IF(NOT(ISBLANK({r(AGENT_ASSIGNEE)})),{r(AGENT_ASSIGNEE)},"
        f"IF(NOT(ISBLANK({r(QUEUE_ASSIGNEE)})),{r(QUEUE_ASSIGNEE)},\"未命中规则\")))))),\"公式计算异常\")"
    )


def build_owner() -> str:
    chain = '""'
    for key in ("domain", "wx", "ali", "phone", "email"):
        src_id, owner_id = SRC[key]
        g = gated_owner(src_id, owner_id)
        chain = f"IF(NOT(ISBLANK({g})),{g},{chain})"
    return (
        f"IF({ref(CONFLICT)},\"匹配错误请检查\",{chain})"
    )


def build_count() -> str:
    parts = []
    for src_id, owner_id in SRC.values():
        g = gated_owner(src_id, owner_id)
        parts.append(f"IF(NOT(ISBLANK({g})),1,0)")
    return "+".join(parts)


def build_result() -> str:
    chain = '"无重复"'
    for key in ("domain", "wx", "ali", "phone"):
        src_id, owner_id = SRC[key]
        g = gated_owner(src_id, owner_id)
        labels = {
            "phone": "电话匹配",
            "ali": "阿里ID匹配",
            "wx": "微信匹配",
            "domain": "公司域名匹配",
        }
        chain = f"IF(NOT(ISBLANK({g})),\"{labels[key]}\",{chain})"
    email_g = gated_owner(*SRC["email"])
    chain = f'IF(NOT(ISBLANK({email_g})),"完整邮箱匹配",{chain})'
    return (
        f"IF(NOT({ref(READY)}=\"是\"),\"查重中\","
        f"IF({ref(CONFLICT)},\"匹配错误请检查\",{chain}))"
    )


FORMULA_SPECS = {
    "dup-formula-ready.json": ("Dup Formula Ready（公式查重就绪）", build_ready),
    "dup-match-conflict.json": ("Dup_Match_Conflict", build_conflict),
    "dup-match-owner.json": ("Dup_Match_Owner", build_owner),
    "dup-match-count.json": ("Dup_Match_Count", build_count),
    "dup-match-result.json": ("Dup_Match_Result", build_result),
    "assignment-source.json": ("分配来源", build_assignment_source),
    "system-assignee.json": ("系统匹配业务员", build_system_assignee),
}


def main() -> None:
    global TABLE_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table-id",
        default=TABLE_ID,
        help=f"Base table id used in formula refs. Default: FEISHU_TABLE_ID or {DEFAULT_TABLE_ID}",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "tmp-formulas",
    )
    args = parser.parse_args()
    TABLE_ID = args.table_id
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for name, (field_name, builder) in FORMULA_SPECS.items():
        payload = {"name": field_name, "type": "formula", "expression": builder()}
        path = args.output_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
