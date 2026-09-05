#!/usr/bin/env python3
"""把渠道队列 / 子办 / 代理规则的业务员单选，统一引用「实际跟进人名单.业务名称」。

已于 2026-09-02 执行。静态列：备份→重命名→新建动态选项→回填。
已是动态选项的子表列不能 field-update 改源，改为删除后重建同名列。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

BASE_TOKEN = "ZpbUb7SP7azsNasniFjc0bWSnHg"
MAIN_TABLE = "tbluuuXn9WexH8LV"
SOURCE_TABLE_ID = "tbl4nwPw8h8swFj2"
SOURCE_FIELD_ID = "fldU0sUS42"  # 实际跟进人名单.业务名称

NAME_ALIASES = {
    "Linsley": "Lindsey",
    "Rita1": "Rita",
}

DESCRIPTION = "动态选项来自「实际跟进人名单.业务名称」；新员工只需维护该名单表。"


@dataclass(frozen=True)
class FieldSpec:
    table_id: str
    field_name: str
    old_field_id: str
    legacy_field_name: str


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    old_field_id: str
    json_name: str


FIELDS = (
    FieldSpec("tbluuuXn9WexH8LV", "子办规则命中负责人", "fldBBzmesf", "子办规则命中负责人_待删除"),
    FieldSpec("tbluuuXn9WexH8LV", "代理规则命中业务员", "fld7jnKAvi", "代理规则命中业务员_待删除"),
    FieldSpec("tbluuuXn9WexH8LV", "渠道顺序队列匹配业务员", "fld4Uk8KfA", "渠道顺序队列匹配业务员_待删除"),
    FieldSpec("tblYQpLxEBYjFN0T", "负责人", "fldATnmAXs", "负责人_待删除"),
    FieldSpec("tblk9x487yPMJGZr", "业务员", "fldcmDUWhH", "业务员_待删除"),
    FieldSpec("tblav9GLrm8Vnf1j", "业务员", "fldJSP0l6d", "业务员_待删除"),
)

WORKFLOWS = (
    WorkflowSpec("wkfaNTuMd6vAE5E0", "fldATnmAXs", "wkfaNTuMd6vAE5E0-子办规则分配自动化.json"),
    WorkflowSpec("wkfKWPVBWT0NisJV", "fldcmDUWhH", "wkfKWPVBWT0NisJV-代理区域分配自动化.json"),
    WorkflowSpec("wkf2Hopgt3bWuoOH", "fldJSP0l6d", "wkf2Hopgt3bWuoOH-渠道轮转自动化.json"),
)

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
        "id": "fldpx236fT",
        "name": "Matched Sales Rep（系统匹配业务员）",
        "expression": (
            'IFERROR(IF([Duplicate（重复）]="查重中","",'
            'IF([Duplicate（重复）]="查重冲突","匹配错误请检查",'
            'IF(AND([Duplicate（重复）]="查重命中",NOT(ISBLANK([Dup_Match_Owner]))),[Dup_Match_Owner],'
            'IF(AND([是否是子办国家]="是",NOT(ISBLANK([子办规则命中负责人]))),[子办规则命中负责人],'
            'IF(NOT(ISBLANK([代理规则命中业务员])),[代理规则命中业务员],'
            'IF(NOT(ISBLANK([渠道顺序队列匹配业务员])),[渠道顺序队列匹配业务员],"未命中规则")))))),"公式计算异常")'
        ),
    },
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("align-assignee-roster")


def _lark(args: list[str]) -> dict:
    cmd = ["lark-cli", "base", *args, "--base-token", BASE_TOKEN, "--format", "json", "--as", "user"]
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


def _normalize(value: str) -> str:
    return NAME_ALIASES.get(value, value)


def _fields(table_id: str) -> dict[str, dict]:
    return {f["name"]: f for f in _lark(["+field-list", "--table-id", table_id])["data"]["fields"]}


def _backup(spec: FieldSpec) -> list[tuple[str, str]]:
    backup: list[tuple[str, str]] = []
    offset = 0
    while True:
        payload = _lark(
            [
                "+record-list",
                "--table-id",
                spec.table_id,
                "--field-id",
                spec.field_name,
                "--limit",
                "200",
                "--offset",
                str(offset),
                "--filter-json",
                json.dumps({"logic": "and", "conditions": [[spec.field_name, "non_empty"]]}, ensure_ascii=False),
            ]
        )["data"]
        rows = payload.get("data") or []
        ids = payload.get("record_id_list") or []
        for record_id, row in zip(ids, rows):
            value = _normalize(_cell_text(row[0] if row else None))
            if value:
                backup.append((record_id, value))
        offset += len(ids)
        log.info("[%s.%s] 备份 %d（本页 %d）", spec.table_id, spec.field_name, len(backup), len(ids))
        if not payload.get("has_more") or not ids:
            break
        time.sleep(0.12)
    return backup


def _rename_legacy(spec: FieldSpec) -> None:
    fields = _fields(spec.table_id)
    if spec.legacy_field_name in fields:
        log.info("[%s] 跳过重命名（%s 已存在）", spec.table_id, spec.legacy_field_name)
        return
    if spec.field_name not in fields:
        raise RuntimeError(f"{spec.table_id} 找不到 {spec.field_name}")
    field = _lark(["+field-get", "--table-id", spec.table_id, "--field-id", spec.old_field_id])["data"]["field"]
    body = {
        "name": spec.legacy_field_name,
        "type": "select",
        "multiple": False,
        "options": field.get("options") or [],
    }
    _lark(
        [
            "+field-update",
            "--table-id",
            spec.table_id,
            "--field-id",
            spec.old_field_id,
            "--json",
            json.dumps(body, ensure_ascii=False),
            "--yes",
        ]
    )
    log.info("[%s] %s → %s", spec.table_id, spec.field_name, spec.legacy_field_name)


def _create_dynamic(spec: FieldSpec) -> str:
    fields = _fields(spec.table_id)
    if spec.field_name in fields:
        existing = fields[spec.field_name]["id"]
        log.info("[%s] 复用 %s id=%s", spec.table_id, spec.field_name, existing)
        return existing
    body = {
        "name": spec.field_name,
        "type": "select",
        "multiple": False,
        "description": DESCRIPTION,
        "dynamic_options_source": {"table_id": SOURCE_TABLE_ID, "field_id": SOURCE_FIELD_ID},
    }
    resp = _lark(
        ["+field-create", "--table-id", spec.table_id, "--json", json.dumps(body, ensure_ascii=False)]
    )
    new_id = resp["data"]["field"]["id"]
    log.info("[%s] 新建 %s id=%s", spec.table_id, spec.field_name, new_id)
    return new_id


def _restore(spec: FieldSpec, backup: list[tuple[str, str]]) -> None:
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
                    spec.table_id,
                    "--json",
                    json.dumps({"record_id_list": chunk, "patch": {spec.field_name: value}}, ensure_ascii=False),
                ]
            )
            restored += len(chunk)
            log.info(
                "[%s.%s] 回填 %s %d/%d（累计 %d/%d）",
                spec.table_id,
                spec.field_name,
                value,
                min(i + 200, len(ids)),
                len(ids),
                restored,
                len(backup),
            )
            time.sleep(0.18)
    log.info("[%s.%s] 回填完成 %d", spec.table_id, spec.field_name, restored)


def _rebind_formulas() -> None:
    for spec in FORMULAS:
        body = {"type": "formula", "name": spec["name"], "expression": spec["expression"]}
        _lark(
            [
                "+field-update",
                "--table-id",
                MAIN_TABLE,
                "--field-id",
                spec["id"],
                "--json",
                json.dumps(body, ensure_ascii=False),
                "--i-have-read-guide",
                "--yes",
            ]
        )
        expr = _lark(["+field-get", "--table-id", MAIN_TABLE, "--field-id", spec["id"]])["data"]["field"]["expression"]
        if "待删除" in expr:
            raise RuntimeError(f"公式仍指向旧字段: {spec['name']}")
        log.info("已重绑公式 %s", spec["name"])
        time.sleep(0.2)


def _walk_replace(node, old_id: str, new_id: str) -> int:
    count = 0
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str) and old_id in value:
                node[key] = value.replace(old_id, new_id)
                count += 1
            else:
                count += _walk_replace(value, old_id, new_id)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, str) and old_id in item:
                node[i] = item.replace(old_id, new_id)
                count += 1
            else:
                count += _walk_replace(item, old_id, new_id)
    return count


def _patch_workflows(new_ids: dict[str, str]) -> None:
    out_dir = Path(__file__).resolve().parents[1] / "workflows"
    for spec in WORKFLOWS:
        new_id = new_ids[spec.old_field_id]
        live = _lark(["+workflow-get", "--workflow-id", spec.workflow_id])["data"]
        body = {"title": live["title"], "status": live.get("status"), "steps": live["steps"]}
        replaced = _walk_replace(body["steps"], spec.old_field_id, new_id)
        out_path = out_dir / spec.json_name
        out_path.write_text(json.dumps({"title": body["title"], "steps": body["steps"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _lark(
            [
                "+workflow-update",
                "--workflow-id",
                spec.workflow_id,
                "--json",
                json.dumps({"title": body["title"], "steps": body["steps"]}, ensure_ascii=False),
            ]
        )
        log.info("工作流 %s 已替换 %s → %s（%d 处）", spec.workflow_id, spec.old_field_id, new_id, replaced)


def _verify(spec: FieldSpec) -> None:
    field = _lark(["+field-get", "--table-id", spec.table_id, "--field-id", spec.field_name])["data"]["field"]
    source = field.get("dynamic_options_source") or {}
    names = {o["name"] for o in field.get("options") or []}
    if source.get("table_id") != SOURCE_TABLE_ID or source.get("field_id") != SOURCE_FIELD_ID:
        raise RuntimeError(f"{spec.field_name} 动态源不符合预期: {source}")
    if "Sherry" not in names:
        raise RuntimeError(f"{spec.field_name} 选项中没有 Sherry")
    log.info("[%s.%s] 动态源正确，Sherry 在列，共 %d 项", spec.table_id, spec.field_name, len(names))


def _delete_legacy(spec: FieldSpec) -> None:
    fields = _fields(spec.table_id)
    if spec.legacy_field_name not in fields:
        return
    _lark(["+field-delete", "--table-id", spec.table_id, "--field-id", spec.legacy_field_name, "--yes"])
    log.info("[%s] 已删除 %s", spec.table_id, spec.legacy_field_name)


def _set_workflow_enabled(enabled: bool) -> None:
    cmd = "+workflow-enable" if enabled else "+workflow-disable"
    for spec in WORKFLOWS:
        _lark([cmd, "--workflow-id", spec.workflow_id])
        log.info("%s %s", "启用" if enabled else "停用", spec.workflow_id)


def _already_aligned() -> bool:
    for spec in FIELDS:
        try:
            field = _lark(["+field-get", "--table-id", spec.table_id, "--field-id", spec.field_name])["data"]["field"]
        except Exception:
            return False
        source = field.get("dynamic_options_source") or {}
        if source.get("table_id") != SOURCE_TABLE_ID or source.get("field_id") != SOURCE_FIELD_ID:
            return False
    return True


def main() -> int:
    if _already_aligned():
        log.info("六列已引用实际跟进人名单.业务名称，跳过")
        return 0
    backups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for spec in FIELDS:
        backups[(spec.table_id, spec.field_name)] = _backup(spec)

    new_ids: dict[str, str] = {}
    disabled = False
    try:
        _set_workflow_enabled(False)
        disabled = True
        for spec in FIELDS:
            _rename_legacy(spec)
            new_id = _create_dynamic(spec)
            new_ids[spec.old_field_id] = new_id
            _restore(spec, backups[(spec.table_id, spec.field_name)])
        _rebind_formulas()
        _patch_workflows(new_ids)
        for spec in FIELDS:
            _verify(spec)
        for spec in FIELDS:
            _delete_legacy(spec)
    finally:
        if disabled:
            _set_workflow_enabled(True)
    log.info("完成：渠道/子办/代理业务员字段已统一引用实际跟进人名单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
