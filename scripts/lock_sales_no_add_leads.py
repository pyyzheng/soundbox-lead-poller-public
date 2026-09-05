#!/usr/bin/env python3
"""禁止负责人/业务员新增主表线索，并把询盘录入字段降为只读。

营销部、仪表盘查看角色不改。Follow-up Records 仍可新增跟进。
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from typing import Any

BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
MAIN_TABLE = "线索总池 Case Database"
SKIP_ROLES = {"营销部", "数据仪表盘查看"}

# 构成「录一条新线索」的字段：一律只读
INTAKE_READONLY = {
    "Enquiry details（询盘内容）",
    "Customer Name（客户名称）",
    "Country（国家）",
    "Channels（渠道）",
    "Channel segmentation (细分渠道)",
    "Email（客户邮箱）",
    "Phone（客户电话）",
    "Wechat（微信）",
    "Product Categories（产品大类）",
    "Product model（具体型号）",
    "Enquiry attachments（询盘附件）",
    "Entry Time（录入时间）",
    "Data Entry Clerk（录入人员）",
    "Allocation Method（分配方式）",
    "Allocation Status（是否成功分配）",
    "Gmail_Msg_ID",
    "阿里ID",
}

# 跟进/改派类：若字段存在则保持或打开可编辑
FOLLOWUP_EDIT = {
    "Case handler",
    "Next Step",
    "Next follow-up date",
    "🌟Follow-up record",
    "🌟First Contact Completed（是否已首联）",
    "🌟Case Level / 线索分级",
    "🌟线索分级/Case Level",
    "🌟value of the lead（线索预估价值）",
    "Customer segmentation（客户分级）",
    "Customer Type（US）",
    "Customer type",
    "Customer Grade",
    "City",
    "States",
    "Deal Record Entry",
    "Deal Amount / 成交金额",
    "Final Deal Amount",
    "Contract No. / 合同号",
    "Contract No.",
    "Transaction Date / 成交时间",
    "Date of transaction",
    "Currency / 币种",
    "Urge Follow-up / 催跟进",
    "Last urge time / 最近催办时间",
    "Urge note / 催办备注",
    "History follow-up details（历史数据参考）",
    "Quick Follow-up / 快捷跟进",
    "Company Research",
    "Clue level（线索等级）",
    "Lead Grading Criteria（分级依据）",
    "Manually reassigned sales representatives（人工改派的业务员）",
    "客户是否回复",
}


def _cli(args: list[str]) -> dict[str, Any]:
    raw = subprocess.check_output(args, text=True)
    return json.loads(raw[raw.find("{") :])


def list_roles() -> list[dict[str, Any]]:
    d = _cli(
        [
            "lark-cli",
            "api",
            "GET",
            f"/open-apis/bitable/v1/apps/{BASE}/roles",
            "--as",
            "user",
            "--format",
            "json",
        ]
    )
    return d["data"]["items"]


def get_role(role_id: str) -> dict[str, Any]:
    d = _cli(
        [
            "lark-cli",
            "base",
            "+role-get",
            "--base-token",
            BASE,
            "--role-id",
            role_id,
            "--as",
            "user",
            "--format",
            "json",
        ]
    )
    inner = d["data"]["data"]
    if isinstance(inner, str):
        inner = json.loads(inner)
    return inner


def patch_main_table(main: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(main)
    rr = dict(out.get("record_rule") or {})
    rr["record_operations"] = []
    out["record_rule"] = rr

    fr = dict(out.get("field_rule") or {})
    perms = dict(fr.get("field_perms") or {})
    for name, perm in list(perms.items()):
        if name in INTAKE_READONLY and perm in ("edit", "create"):
            perms[name] = "read"
        if name in FOLLOWUP_EDIT and perm in ("read", "create"):
            # 不把 no_perm 抬成 edit；只把只读/可新增改成可编辑
            if perm != "no_perm":
                perms[name] = "edit"
    if "Case handler" in perms and perms["Case handler"] != "no_perm":
        perms["Case handler"] = "edit"
    fr["field_perms"] = perms
    out["field_rule"] = fr
    return out


def update_role(cfg: dict[str, Any], main: dict[str, Any]) -> None:
    payload = {
        "role_name": cfg["role_name"],
        "role_type": cfg.get("role_type") or "custom_role",
        "base_rule_map": cfg.get("base_rule_map")
        or {"copy": False, "download": False},
        "table_rule_map": {MAIN_TABLE: main},
    }
    _cli(
        [
            "lark-cli",
            "base",
            "+role-update",
            "--base-token",
            BASE,
            "--role-id",
            cfg["role_id"],
            "--json",
            json.dumps(payload, ensure_ascii=False),
            "--as",
            "user",
            "--format",
            "json",
            "--yes",
        ]
    )


def main() -> int:
    roles = list_roles()
    failed: list[str] = []
    for r in roles:
        name = r["role_name"]
        if name in SKIP_ROLES:
            print(f"SKIP {name}")
            continue
        cfg = get_role(r["role_id"])
        trm = cfg.get("table_rule_map") or {}
        if MAIN_TABLE not in trm:
            print(f"SKIP {name} (no main table)")
            continue
        patched = patch_main_table(trm[MAIN_TABLE])
        try:
            update_role(cfg, patched)
        except subprocess.CalledProcessError as e:
            failed.append(name)
            print(f"FAIL {name}: {(e.output or str(e))[:400]}")
            continue
        after = get_role(r["role_id"])
        main = after["table_rule_map"][MAIN_TABLE]
        ops = (main.get("record_rule") or {}).get("record_operations")
        perms = (main.get("field_rule") or {}).get("field_perms") or {}
        enquiry = perms.get("Enquiry details（询盘内容）")
        handler = perms.get("Case handler")
        print(f"OK {name}: ops={ops} enquiry={enquiry} case_handler={handler}")
    if failed:
        print("FAILED", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
