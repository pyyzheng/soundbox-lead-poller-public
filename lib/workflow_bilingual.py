"""飞书工作流 JSON：旧中文字段名/选项值 → 2026-07 双语字段迁移。"""

from __future__ import annotations

from assignment_fields import (
    FIELD_ASSIGNEE,
    FIELD_ASSIGN_METHOD,
    FIELD_ASSIGN_SOURCE,
    FIELD_LEAD_ID,
    FIELD_STATUS,
    FIELD_SUCCESS,
    FIELD_SYSTEM,
    WRITE_ASSIGN_AUTO,
    WRITE_ASSIGN_MANUAL,
    WRITE_SUCCESS_NO,
    WRITE_SUCCESS_YES,
)

# 主表已重命名的字段（旧名 → 当前 API 名）
FIELD_RENAMES: dict[str, str] = {
    "分配方式": FIELD_ASSIGN_METHOD,
    "是否成功分配": FIELD_SUCCESS,
    "分配状态": FIELD_STATUS,
    "最终分配的业务员": FIELD_ASSIGNEE,
    "系统匹配业务员": FIELD_SYSTEM,
    "线索ID": FIELD_LEAD_ID,
    "分配来源": FIELD_ASSIGN_SOURCE,
}

# 仅以下字段的单选 option 改为双语展示名
OPTION_RENAMES_BY_FIELD: dict[str, dict[str, str]] = {
    FIELD_ASSIGN_METHOD: {
        "自动": WRITE_ASSIGN_AUTO,
        "人工": WRITE_ASSIGN_MANUAL,
    },
    FIELD_SUCCESS: {
        "是": WRITE_SUCCESS_YES,
        "否": WRITE_SUCCESS_NO,
    },
}


def _rename_field_name(name: str) -> str:
    return FIELD_RENAMES.get(name, name)


def _rename_option(field_name: str, option_name: str) -> str:
    mapping = OPTION_RENAMES_BY_FIELD.get(field_name, {})
    return mapping.get(option_name, option_name)


def migrate_workflow_node(node: object, active_field: str | None = None) -> None:
    """原地递归迁移 workflow steps / conditions / field_values。"""
    if isinstance(node, list):
        for item in node:
            migrate_workflow_node(item, active_field)
        return
    if not isinstance(node, dict):
        return

    field_name = node.get("field_name")
    if isinstance(field_name, str):
        active_field = _rename_field_name(field_name)
        if active_field != field_name:
            node["field_name"] = active_field

    if node.get("value_type") == "option" and active_field:
        value = node.get("value")
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            value["name"] = _rename_option(active_field, value["name"])

    for key, child in node.items():
        if key == "field_name":
            continue
        migrate_workflow_node(child, active_field)


def migrate_workflow_document(body: dict) -> dict:
    migrate_workflow_node(body)
    return body


# Duplicate（重复）公式字段在工作流校验里仍按单选（fieldType 3）处理：
# 不能用多选算子 doesNotContainAny；应使用 isNot + option（仅 name，不写 id）。
ASSIGN_SOURCE_FORMULA_FIELD = FIELD_ASSIGN_SOURCE
ASSIGN_SOURCE_FORMULA_ALIASES = frozenset({FIELD_ASSIGN_SOURCE, "分配来源"})
ASSIGN_SOURCE_BLOCKED_TEXT = ("查重命中", "查重冲突")


def _option_name_only(name: str) -> dict:
    return {"value": {"name": name}, "value_type": "option"}


def _option_or_text_name(item: dict) -> str:
    val = item.get("value")
    if isinstance(val, dict):
        return str(val.get("name") or val.get("text") or "")
    if isinstance(val, str):
        return val
    return ""


def rewrite_duplicate_option_filters(conditions: list) -> list:
    """把 Duplicate 上的 doesNotContainAny 改成多个 isNot(option name)。"""
    out: list = []
    for cond in conditions:
        field = cond.get("field_name")
        if (
            field in ASSIGN_SOURCE_FORMULA_ALIASES
            and cond.get("operator") == "doesNotContainAny"
        ):
            names = [
                name
                for name in (_option_or_text_name(v) for v in (cond.get("value") or []))
                if name
            ] or list(ASSIGN_SOURCE_BLOCKED_TEXT)
            for name in names:
                out.append(
                    {
                        "field_name": ASSIGN_SOURCE_FORMULA_FIELD,
                        "operator": "isNot",
                        "value": [_option_name_only(name)],
                    }
                )
            continue
        # 已是 isNot 但仍带 option id 的，统一清掉 id（在 strip 之外再保一次）
        if (
            field in ASSIGN_SOURCE_FORMULA_ALIASES
            and cond.get("operator") == "isNot"
            and cond.get("value")
        ):
            cleaned = []
            for v in cond["value"]:
                name = _option_or_text_name(v)
                if name:
                    cleaned.append(_option_name_only(name))
                else:
                    cleaned.append(v)
            cond = {**cond, "value": cleaned or cond["value"]}
        out.append(cond)
    return out


def fix_duplicate_formula_in_workflow(body: dict) -> dict:
    """扫描所有 SetRecordTrigger 条件，修复 Duplicate 公式/单选算子不匹配。"""
    for step in body.get("steps") or []:
        if step.get("type") != "SetRecordTrigger":
            continue
        data = step.get("data") or {}
        for group in data.get("condition_list") or []:
            conds = group.get("conditions")
            if isinstance(conds, list):
                group["conditions"] = rewrite_duplicate_option_filters(conds)
    return body
