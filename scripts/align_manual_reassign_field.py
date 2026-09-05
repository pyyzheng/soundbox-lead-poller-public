#!/usr/bin/env python3
"""将主表「人工改派的业务员」改为动态单选，选项源=实际跟进人名单.业务名称。

飞书 OpenAPI 不能把静态单选原地改成动态选项；做法与 Case handler 对齐：
备份 → 重命名旧字段 → 新建 dynamic_options_source → 按名称回填 → 重绑公式 → 删旧字段。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_TOKEN = "ZpbUb7SP7azsNasniFjc0bWSnHg"
TABLE_ID = "tbluuuXn9WexH8LV"
SOURCE_TABLE_ID = "tbl4nwPw8h8swFj2"
SOURCE_FIELD_ID = "fldU0sUS42"  # 实际跟进人名单.业务名称

FIELD_NAME = "Manually reassigned sales representatives（人工改派的业务员）"
OLD_FIELD_ID = "fldtayX7TI"
LEGACY_FIELD_NAME = "Manually reassigned sales representatives（人工改派的业务员）_待删除"
BACKUP_PATH = Path(__file__).with_name("align_manual_reassign_backup.json")

FORMULAS = (
    {
        "id": "fldIgfZT45",
        "name": "分配依据",
        "expression": (
            "IF(NOT(ISBLANK([Manually reassigned sales representatives（人工改派的业务员）])),\"人工改派\","
            "IF(AND(NOT(ISBLANK([Dup_Match_Owner])),[Dup_Match_Owner_是否可接单]=\"是\","
            "OR([分配部门]=\"本部（公共）\",[Dup_Match_Owner_所属部门]=[分配部门])),\"查重复用\","
            "IF([Dup_Match_Result]=\"匹配错误请检查\",\"待人工确认\","
            "IF(AND([是否命中代理国家]=\"是\",OR([是否命中代理产品]=\"待确认\","
            "[是否命中代理产品]=\"无法识别\",ISBLANK([是否命中代理产品]))),\"待人工确认\","
            "IF(NOT(ISBLANK([子办规则命中负责人])),\"子办优先\","
            "IF(NOT(ISBLANK([代理规则命中业务员])),\"代理优先\","
            "IF(NOT(ISBLANK([渠道顺序队列匹配业务员])),\"渠道顺序队列\",\"\")))))))"
        ),
    },
    {
        "id": "fldOMcCv5Y",
        "name": "The final assigned salesperson（最终分配的业务员）",
        "expression": (
            "IFBLANK([Manually reassigned sales representatives（人工改派的业务员）],"
            "[Matched Sales Rep（系统匹配业务员）])"
        ),
    },
    {
        "id": "fldorF3W8M",
        "name": "Urge recipient / 催办对象",
        "description": "Priority: Case handler → Manual reassign → final assigned → 实际线索跟进人",
        "expression": (
            "IF(NOT(ISBLANK([Case handler])),[Case handler],"
            "IF(NOT(ISBLANK([Manually reassigned sales representatives（人工改派的业务员）])),"
            "[Manually reassigned sales representatives（人工改派的业务员）],"
            "IF(NOT(ISBLANK([The final assigned salesperson（最终分配的业务员）])),"
            "[The final assigned salesperson（最终分配的业务员）],[实际线索跟进人])))"
        ),
    },
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("align-manual-reassign")


def _lark(args: list[str]) -> dict:
    cmd = [
        "lark-cli",
        "base",
        *args,
        "--base-token",
        BASE_TOKEN,
        "--format",
        "json",
        "--as",
        "user",
    ]
    raw = subprocess.check_output(cmd, text=True)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload


def _cell_text(value) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, list):
        return _cell_text(value[0]) if value else ""
    return str(value)


def _field_by_name() -> dict[str, dict]:
    fields = _lark(["+field-list", "--table-id", TABLE_ID])["data"]["fields"]
    return {f["name"]: f for f in fields}


def _backup_records() -> list[tuple[str, str]]:
    if BACKUP_PATH.exists():
        saved = json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
        log.info("复用本地备份 %d 条 %s", len(saved), BACKUP_PATH.name)
        return [(row["record_id"], row["value"]) for row in saved]

    backup: list[tuple[str, str]] = []
    offset = 0
    while True:
        payload = _lark(
            [
                "+record-list",
                "--table-id",
                TABLE_ID,
                "--field-id",
                FIELD_NAME,
                "--limit",
                "200",
                "--offset",
                str(offset),
                "--filter-json",
                json.dumps(
                    {"logic": "and", "conditions": [[FIELD_NAME, "non_empty"]]},
                    ensure_ascii=False,
                ),
            ]
        )["data"]
        rows = payload.get("data") or []
        ids = payload.get("record_id_list") or []
        for record_id, row in zip(ids, rows):
            value = _cell_text(row[0] if row else None)
            if value:
                backup.append((record_id, value))
        offset += len(ids)
        log.info("备份进度 %d（本页 %d）", len(backup), len(ids))
        if not payload.get("has_more"):
            break
        time.sleep(0.15)

    BACKUP_PATH.write_text(
        json.dumps(
            [{"record_id": rid, "value": value} for rid, value in backup],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("已备份 %d 条到 %s", len(backup), BACKUP_PATH.name)
    return backup


def _rename_legacy() -> None:
    fields = _field_by_name()
    if LEGACY_FIELD_NAME in fields:
        log.info("跳过重命名（%s 已存在）", LEGACY_FIELD_NAME)
        return
    if FIELD_NAME not in fields:
        raise RuntimeError("原字段不存在，无法重命名")
    field = _lark(["+field-get", "--table-id", TABLE_ID, "--field-id", OLD_FIELD_ID])["data"]["field"]
    body = {
        "name": LEGACY_FIELD_NAME,
        "type": "select",
        "multiple": False,
        "options": field.get("options") or [],
    }
    _lark(
        [
            "+field-update",
            "--table-id",
            TABLE_ID,
            "--field-id",
            OLD_FIELD_ID,
            "--json",
            json.dumps(body, ensure_ascii=False),
            "--yes",
        ]
    )
    log.info("已重命名 → %s", LEGACY_FIELD_NAME)


def _create_dynamic() -> str:
    fields = _field_by_name()
    if FIELD_NAME in fields:
        existing = fields[FIELD_NAME]["id"]
        log.info("复用已有字段 %s id=%s", FIELD_NAME, existing)
        return existing
    body = {
        "name": FIELD_NAME,
        "type": "select",
        "multiple": False,
        "description": "动态选项来自「实际跟进人名单.业务名称」；与 Case handler 同源，新员工只需维护该名单表。",
        "dynamic_options_source": {
            "table_id": SOURCE_TABLE_ID,
            "field_id": SOURCE_FIELD_ID,
        },
    }
    resp = _lark(
        [
            "+field-create",
            "--table-id",
            TABLE_ID,
            "--json",
            json.dumps(body, ensure_ascii=False),
        ]
    )
    new_id = resp["data"]["field"]["id"]
    log.info("已创建动态选项字段 id=%s", new_id)
    return new_id


def _restore_records(backup: list[tuple[str, str]]) -> None:
    grouped: dict[str, list[str]] = defaultdict(list)
    for record_id, value in backup:
        grouped[value].append(record_id)

    restored = 0
    for value, ids in grouped.items():
        for i in range(0, len(ids), 200):
            chunk = ids[i : i + 200]
            _lark(
                [
                    "+record-batch-update",
                    "--table-id",
                    TABLE_ID,
                    "--json",
                    json.dumps(
                        {"record_id_list": chunk, "patch": {FIELD_NAME: value}},
                        ensure_ascii=False,
                    ),
                ]
            )
            restored += len(chunk)
            log.info("回填 %s %d/%d（累计 %d/%d）", value, min(i + 200, len(ids)), len(ids), restored, len(backup))
            time.sleep(0.2)
    log.info("回填完成 %d 条", restored)


def _rebind_formulas() -> None:
    for spec in FORMULAS:
        body = {
            "type": "formula",
            "name": spec["name"],
            "expression": spec["expression"],
        }
        if spec.get("description"):
            body["description"] = spec["description"]
        _lark(
            [
                "+field-update",
                "--table-id",
                TABLE_ID,
                "--field-id",
                spec["id"],
                "--json",
                json.dumps(body, ensure_ascii=False),
                "--i-have-read-guide",
                "--yes",
            ]
        )
        log.info("已重绑公式 %s", spec["name"])
        time.sleep(0.2)


def _verify() -> None:
    field = _lark(["+field-get", "--table-id", TABLE_ID, "--field-id", FIELD_NAME])["data"]["field"]
    source = field.get("dynamic_options_source") or {}
    names = {o["name"] for o in field.get("options") or []}
    if source.get("table_id") != SOURCE_TABLE_ID or source.get("field_id") != SOURCE_FIELD_ID:
        raise RuntimeError(f"动态选项源不符合预期: {source}")
    if "Sherry" not in names:
        raise RuntimeError(f"选项中没有 Sherry，当前 {sorted(names)}")
    log.info("动态选项源正确，含 Sherry，共 %d 项", len(names))

    for spec in FORMULAS:
        expr = _lark(["+field-get", "--table-id", TABLE_ID, "--field-id", spec["id"]])["data"]["field"]["expression"]
        if "待删除" in expr:
            raise RuntimeError(f"公式仍指向旧字段: {spec['name']}")
    log.info("公式已指向新字段")

    sample = _lark(
        [
            "+record-list",
            "--table-id",
            TABLE_ID,
            "--field-id",
            FIELD_NAME,
            "--field-id",
            "The final assigned salesperson（最终分配的业务员）",
            "--limit",
            "5",
            "--filter-json",
            json.dumps({"logic": "and", "conditions": [[FIELD_NAME, "non_empty"]]}, ensure_ascii=False),
        ]
    )["data"]
    if not sample.get("record_id_list"):
        raise RuntimeError("回填后抽查为空")
    log.info("抽查回填 %s", list(zip(sample.get("record_id_list") or [], sample.get("data") or [])))


def _delete_legacy() -> None:
    fields = _field_by_name()
    if LEGACY_FIELD_NAME not in fields:
        log.info("无待删除字段")
        return
    _lark(
        [
            "+field-delete",
            "--table-id",
            TABLE_ID,
            "--field-id",
            LEGACY_FIELD_NAME,
            "--yes",
        ]
    )
    log.info("已删除 %s", LEGACY_FIELD_NAME)


def main() -> int:
    backup = _backup_records()
    _rename_legacy()
    _create_dynamic()
    _restore_records(backup)
    _rebind_formulas()
    _verify()
    _delete_legacy()
    log.info("完成：人工改派已引用实际跟进人名单.业务名称")
    return 0


if __name__ == "__main__":
    sys.exit(main())
