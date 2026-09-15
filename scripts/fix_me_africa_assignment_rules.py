#!/usr/bin/env python3
"""修正中东/非洲分配规则，并回修 2026-07 以来错分线索。

业务口径：
1. 阿联酋 + 静音舱 → Cathy；产品大类=无法识别/非静音舱 → 中东非洲轮循（Cathy/Jannice）
2. 沙特阿拉伯、以色列 → 一律 Jannice
3. 其他中东/非洲 → Cathy/Jannice 轮循
4. 系统匹配业务员：仅当「是否是子办国家=是」时才采用「子办规则命中负责人」
   （修复 003659 阿联酋脏子办字段盖过队列、误分给 Rita_USA）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

BASE = os.environ.get("FEISHU_APP_TOKEN", "ZpbUb7SP7azsNasniFjc0bWSnHg")
MAIN = "tbluuuXn9WexH8LV"
AGENT_RULES = "tblk9x487yPMJGZr"
JULY_CLUE_MIN = 3500  # 约 2026-07 起

SILENCE = "Silence Booth 静音舱"
ACOUSTIC = "Acoustic products 声学产品"
HOMEPOD = "Homepod 家居舱"
MODELS = ["SR", "VR", "VRT", "ART", "尖顶", "全系列"]
SAUDI = "沙特阿拉伯"
ISRAEL = "以色列"
UAE = "阿联酋"
FIXED_COUNTRIES = {SAUDI, ISRAEL}


def _cli(*args: str, check: bool = True) -> dict:
    cmd = ["lark-cli", "base", *args, "--as", "user", "--format", "json"]
    raw = subprocess.check_output(cmd, text=True)
    payload = json.loads(raw)
    if check and not payload.get("ok", True) and "data" not in payload:
        raise RuntimeError(payload)
    return payload


def _cell(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def update_matched_sales_rep_formula() -> None:
    """子办字段仅在「是否是子办国家=是」时生效。"""
    expression = """IFERROR(
  IF(
    bitable::$table[tbluuuXn9WexH8LV].$field[fldUa1OwhQ]="查重中",
    "",

    IF(
      bitable::$table[tbluuuXn9WexH8LV].$field[fldUa1OwhQ]="查重冲突",
      "匹配错误请检查",

      IF(
        AND(
          bitable::$table[tbluuuXn9WexH8LV].$field[fldUa1OwhQ]="查重命中",
          NOT(ISBLANK(bitable::$table[tbluuuXn9WexH8LV].$field[fldQzU8NBM]))
        ),
        bitable::$table[tbluuuXn9WexH8LV].$field[fldQzU8NBM],

        IF(
          AND(
            bitable::$table[tbluuuXn9WexH8LV].$field[fld9kCu7o6]="是",
            NOT(ISBLANK(bitable::$table[tbluuuXn9WexH8LV].$field[fldBBzmesf]))
          ),
          bitable::$table[tbluuuXn9WexH8LV].$field[fldBBzmesf],

          IF(
            NOT(ISBLANK(bitable::$table[tbluuuXn9WexH8LV].$field[fld7jnKAvi])),
            bitable::$table[tbluuuXn9WexH8LV].$field[fld7jnKAvi],

            IF(
              NOT(ISBLANK(bitable::$table[tbluuuXn9WexH8LV].$field[fld4Uk8KfA])),
              bitable::$table[tbluuuXn9WexH8LV].$field[fld4Uk8KfA],
              "未命中规则"
            )
          )
        )
      )
    )
  ),
  "公式计算异常"
)"""
    field_json = {
        "name": "Matched Sales Rep（系统匹配业务员）",
        "type": "formula",
        "expression": expression,
    }
    path = ROOT / "scripts" / "_tmp_matched_sales_rep_formula.json"
    path.write_text(json.dumps(field_json, ensure_ascii=False), encoding="utf-8")
    cmd = [
        "lark-cli",
        "base",
        "+field-update",
        "--base-token",
        BASE,
        "--table-id",
        MAIN,
        "--field-id",
        "fldpx236fT",
        "--json",
        f"@{path.relative_to(ROOT)}",
        "--as",
        "user",
        "--i-have-read-guide",
        "--yes",
        "--format",
        "json",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        raise RuntimeError("formula update failed")
    try:
        path.unlink()
    except OSError:
        pass
    print("OK: Matched Sales Rep formula gated on 是否是子办国家=是")


def _list_agent_rules() -> set[tuple[str, str, str, str]]:
    payload = _cli(
        "+record-search",
        "--base-token",
        BASE,
        "--table-id",
        AGENT_RULES,
        "--keyword",
        "启用",
        "--search-field",
        "是否启用",
        "--field-id",
        "国家",
        "--field-id",
        "产品大类",
        "--field-id",
        "具体型号",
        "--field-id",
        "业务员",
        "--limit",
        "200",
    )
    out: set[tuple[str, str, str, str]] = set()
    for row in payload["data"]["data"]:
        out.add(
            (
                _cell(row[0]) or "",
                _cell(row[1]) or "",
                _cell(row[2]) or "",
                _cell(row[3]) or "",
            )
        )
    return out


def ensure_agent_rules() -> list[str]:
    """补齐沙特/以色列静音舱全型号 → Jannice（非静音舱走轮转，不建代理规则）。"""
    existing = _list_agent_rules()
    wanted: list[tuple[str, str, str, str, str]] = []
    for country in (SAUDI, ISRAEL):
        for model in MODELS:
            wanted.append(
                (
                    country,
                    SILENCE,
                    model,
                    "Jannice",
                    f"Jannice-{country}-静音舱-{model}",
                )
            )

    created: list[str] = []
    for country, category, model, assignee, name in wanted:
        key = (country, category, model, assignee)
        if key in existing or (country, category, model, "Jannice") in {
            (a, b, c, d) for a, b, c, d in existing
        }:
            # already have same country/category/model (any assignee)
            if any(a == country and b == category and c == model for a, b, c, d in existing):
                continue
        body = {
            "规则名称": name,
            "国家": country,
            "产品大类": category,
            "具体型号": model,
            "业务员": assignee,
            "是否启用": "启用",
        }
        cmd = [
            "lark-cli",
            "base",
            "+record-upsert",
            "--base-token",
            BASE,
            "--table-id",
            AGENT_RULES,
            "--json",
            json.dumps(body, ensure_ascii=False),
            "--as",
            "user",
            "--format",
            "json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("FAIL create rule", name, result.stdout or result.stderr)
            continue
        created.append(name)
        print("created", name)
    print(f"OK: agent rules created={len(created)}")
    return created


def patch_agent_workflow() -> None:
    """1) 去掉产品大类=无法识别 的触发排除，让沙特/以色列无法识别也能进代理流
    2) 声学产品短路改为：仅非沙特/以色列才写「否」
    3) 触发后优先：沙特/以色列 → 直接写 Jannice
    """
    from copy import deepcopy

    from workflow_bilingual import fix_duplicate_formula_in_workflow, migrate_workflow_document

    live = _cli(
        "+workflow-get",
        "--base-token",
        BASE,
        "--workflow-id",
        "wkfKWPVBWT0NisJV",
    )
    # workflow-get may nest under data
    data = live.get("data", live)
    if "title" not in data and "workflow" in data:
        data = data["workflow"]
    # some responses wrap steps differently
    if "steps" not in data:
        # try nested
        for k in ("document", "body", "workflow"):
            if isinstance(data.get(k), dict) and "steps" in data[k]:
                data = data[k]
                break
    if "steps" not in data:
        raise RuntimeError(f"unexpected workflow payload keys: {list(live.keys())}")

    out = deepcopy({"title": data.get("title") or "代理区域分配自动化", "steps": data["steps"]})
    steps = {s["id"]: s for s in out["steps"]}
    trigger = steps["trigYl0y5W"]

    # Remove Product Categories doesNotContainAny 无法识别 watch filter
    watch = trigger["data"].get("field_watch_info") or []
    new_watch = []
    for w in watch:
        if w.get("field_name") == "Product Categories（产品大类）" and w.get("operator") == "doesNotContainAny":
            # keep watching the field, but without excluding 无法识别
            new_watch.append({"field_name": "Product Categories（产品大类）"})
            continue
        new_watch.append(w)
    trigger["data"]["field_watch_info"] = new_watch

    # Insert early country branch after trigger
    country_branch_id = "branchMEFixedCN"
    set_jannice_id = "actMEFixedJannice"
    acoustic_branch = steps["branchhWywIAFT"]

    # Retarget trigger next → country branch → (true: set Jannice) / (false: old acoustic branch)
    trigger["next"] = country_branch_id

    country_branch = {
        "id": country_branch_id,
        "title": "沙特/以色列固定分给 Jannice",
        "type": "IfElseBranch",
        "children": {
            "links": [
                {"kind": "if_true", "to": set_jannice_id},
                {"kind": "if_false", "to": "branchhWywIAFT"},
            ]
        },
        "data": {
            "condition": {
                "conjunction": "or",
                "conditions": [
                    {
                        "conjunction": "and",
                        "conditions": [
                            {
                                "left_value": {
                                    "value": "$.trigYl0y5W.fldAEhwYJU",
                                    "value_type": "ref",
                                },
                                "operator": "is",
                                "right_value": [
                                    {
                                        "value": {"name": SAUDI},
                                        "value_type": "option",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "conjunction": "and",
                        "conditions": [
                            {
                                "left_value": {
                                    "value": "$.trigYl0y5W.fldAEhwYJU",
                                    "value_type": "ref",
                                },
                                "operator": "is",
                                "right_value": [
                                    {
                                        "value": {"name": ISRAEL},
                                        "value_type": "option",
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        },
    }

    set_jannice = {
        "id": set_jannice_id,
        "title": "写代理业务员=Jannice",
        "type": "SetRecordAction",
        "children": {"links": []},
        "data": {
            "table_name": "线索总池 Case Database",
            "ref_info": {"step_id": "trigYl0y5W"},
            "filter_info": None,
            "max_set_record_num": 100,
            "field_values": [
                {
                    "field_name": "是否命中代理产品",
                    "value": [{"value": {"name": "是"}, "value_type": "option"}],
                },
                {
                    "field_name": "代理规则命中业务员",
                    "value": [{"value": {"name": "Jannice"}, "value_type": "option"}],
                },
                {
                    "field_name": "Allocation Status（是否成功分配）",
                    "value": [{"value": {"name": "Yes（是）"}, "value_type": "option"}],
                },
            ],
        },
    }

    # Acoustic short-circuit: keep for non-fixed countries only
    # Current acoustic branch only checks category; leave as-is because
    # Saudi/Israel never reach it (early branch catches them).

    # Rebuild steps list: keep order but inject new nodes after trigger
    new_steps = []
    for s in out["steps"]:
        new_steps.append(s)
        if s["id"] == "trigYl0y5W":
            new_steps.append(country_branch)
            new_steps.append(set_jannice)
    out["steps"] = new_steps

    # Strip option ids for update safety
    def _strip(node):
        if isinstance(node, list):
            for i in node:
                _strip(i)
            return
        if not isinstance(node, dict):
            return
        if node.get("value_type") == "option":
            val = node.get("value")
            if isinstance(val, dict) and "id" in val and "name" in val:
                del val["id"]
        for child in node.values():
            _strip(child)

    _strip(out["steps"])
    body = migrate_workflow_document(out)
    body = fix_duplicate_formula_in_workflow(body)

    out_path = ROOT / "workflows" / "wkfKWPVBWT0NisJV-代理区域分配自动化.json"
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cmd = [
        "lark-cli",
        "base",
        "+workflow-update",
        "--base-token",
        BASE,
        "--workflow-id",
        "wkfKWPVBWT0NisJV",
        "--json",
        f"@{out_path.relative_to(ROOT)}",
        "--as",
        "user",
        "--format",
        "json",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        raise RuntimeError("workflow update failed")
    print("OK: agent workflow patched (Saudi/Israel → Jannice; allow 无法识别 trigger)")


def _search_country(kw: str) -> list[dict]:
    payload = _cli(
        "+record-search",
        "--base-token",
        BASE,
        "--table-id",
        MAIN,
        "--keyword",
        kw,
        "--search-field",
        "Country（国家）",
        "--field-id",
        "Clue ID",
        "--field-id",
        "Country（国家）",
        "--field-id",
        "The final assigned salesperson（最终分配的业务员）",
        "--field-id",
        "子办规则命中负责人",
        "--field-id",
        "代理规则命中业务员",
        "--field-id",
        "渠道顺序队列匹配业务员",
        "--field-id",
        "是否是子办国家",
        "--field-id",
        "是否命中代理产品",
        "--field-id",
        "Product Categories（产品大类）",
        "--field-id",
        "Product model（具体型号）",
        "--field-id",
        "Allocation Status（是否成功分配）",
        "--limit",
        "200",
    )
    rows = []
    for row, rid in zip(payload["data"]["data"], payload["data"]["record_id_list"]):
        while len(row) < 11:
            row.append(None)
        rows.append(
            {
                "rid": rid,
                "cid": str(row[0] or ""),
                "country": _cell(row[1]) or "",
                "final": _cell(row[2]),
                "sub": _cell(row[3]),
                "agent": _cell(row[4]),
                "queue": _cell(row[5]),
                "is_sub": _cell(row[6]),
                "hit_p": _cell(row[7]),
                "cat": _cell(row[8]) or "",
                "model": _cell(row[9]) or "",
                "status": _cell(row[10]),
            }
        )
    return rows


def _is_july(cid: str) -> bool:
    try:
        return int(cid) >= JULY_CLUE_MIN
    except ValueError:
        return False


def expected_assignee(country: str, cat: str) -> str | None:
    silence = "静音舱" in cat or "Silence" in cat
    if country == UAE:
        return "Cathy" if silence else "ROTATE"
    if country in FIXED_COUNTRIES:
        return "Jannice" if silence else "ROTATE"
    return None


def fix_july_records() -> list[str]:
    fixed: list[str] = []
    all_rows: list[dict] = []
    for kw in (UAE, "沙特", "以色列"):
        all_rows.extend(_search_country(kw))

    # Deduplicate by rid
    by_rid = {r["rid"]: r for r in all_rows}

    for r in by_rid.values():
        if not _is_july(r["cid"]):
            continue
        country = r["country"]
        cat = r["cat"]
        expect = expected_assignee(country, cat)
        if expect is None:
            continue

        updates: dict = {}
        need = False

        # Clear dirty suboffice owner on non-suboffice countries
        if r["sub"] and r["is_sub"] == "否":
            updates["子办规则命中负责人"] = None
            need = True

        if expect == "Jannice" and r["final"] != "Jannice":
            updates["代理规则命中业务员"] = "Jannice"
            updates["是否命中代理产品"] = "是"
            updates["Allocation Status（是否成功分配）"] = "Yes（是）"
            need = True
        elif expect == "Cathy" and r["final"] != "Cathy":
            updates["代理规则命中业务员"] = "Cathy"
            updates["是否命中代理产品"] = "是"
            updates["Allocation Status（是否成功分配）"] = "Yes（是）"
            need = True
        elif expect == "ROTATE":
            # Must be Cathy or Jannice; if Rita_USA or other → assign via ME rotate preference Cathy then Jannice
            if r["final"] not in ("Cathy", "Jannice"):
                # Prefer existing queue assignee if valid, else Cathy
                pick = r["queue"] if r["queue"] in ("Cathy", "Jannice") else "Cathy"
                updates["渠道顺序队列匹配业务员"] = pick
                updates["是否命中代理产品"] = "否"
                updates["Allocation Status（是否成功分配）"] = "Yes（是）"
                # Clear wrong agent if any
                if r["agent"] and r["agent"] not in ("Cathy", "Jannice"):
                    updates["代理规则命中业务员"] = None
                need = True

        if not need:
            continue

        cmd = [
            "lark-cli",
            "base",
            "+record-upsert",
            "--base-token",
            BASE,
            "--table-id",
            MAIN,
            "--record-id",
            r["rid"],
            "--json",
            json.dumps(updates, ensure_ascii=False),
            "--as",
            "user",
            "--format",
            "json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("FAIL fix", r["cid"], result.stdout or result.stderr)
            continue
        msg = f"{r['cid']} {country} final={r['final']} → {updates}"
        print("fixed", msg)
        fixed.append(msg)
    print(f"OK: july fixed={len(fixed)}")
    return fixed


def main() -> int:
    print("=== 1. formula ===")
    update_matched_sales_rep_formula()
    print("=== 2. agent rules ===")
    ensure_agent_rules()
    print("=== 3. agent workflow ===")
    patch_agent_workflow()
    print("=== 4. july backfill ===")
    fix_july_records()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
