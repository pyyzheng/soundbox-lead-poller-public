"""渠道顺序队列分配：纯逻辑 + 飞书表数据解析。

与飞书公式 G（是否满足渠道轮转）保持一致的判定口径，便于在 Python 侧兜底分配。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assignment_fields import (
    ERROR_ASSIGNEES,
    FIELD_AGENT_COUNTRY,
    FIELD_AGENT_PRODUCT,
    FIELD_ASSIGN_METHOD,
    FIELD_ASSIGN_SOURCE,
    FIELD_DUP_READY,
    FIELD_QUEUE_ASSIGNEE,
    FIELD_QUEUE_KEY,
    FIELD_ROTATION,
    FIELD_SUBOFFICE,
    FIELD_SYSTEM,
    get_field,
    normalize_queue_key,
)
from feishu_utils import extract_text
from option_field_match import (
    is_agent_country,
    is_agent_product_empty,
    is_agent_product_no,
    is_agent_product_pending,
    is_agent_product_yes,
    is_assign_auto,
    is_assign_source_blocked,
    is_assign_source_eligible,
    is_dup_ready,
    is_not_agent_country,
    is_rotation_eligible,
    is_suboffice_country,
)


def _extract_int_field(field_val: object, default: int = 1) -> int:
    """解析数字字段，兼容 API 返回的 Lookup 结构（如 {\"type\":2,\"value\":[3]}）。"""
    if field_val in (None, ""):
        return default
    if isinstance(field_val, bool):
        return int(field_val)
    if isinstance(field_val, (int, float)):
        return int(field_val)
    if isinstance(field_val, dict):
        inner = field_val.get("value", field_val)
        if inner is field_val:
            return default
        return _extract_int_field(inner, default)
    if isinstance(field_val, list):
        for item in field_val:
            try:
                return _extract_int_field(item, default)
            except (TypeError, ValueError):
                continue
        return default
    try:
        return int(str(field_val).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class QueuePointer:
    record_id: str
    current: int
    max_rank: int


@dataclass(frozen=True)
class QueuePickResult:
    assignee: str
    pointer_record_id: str
    used_rank: int
    next_rank: int
    max_rank: int
    resolved_queue_key: str = ""


def advance_pointer(current: int, max_rank: int) -> int:
    if max_rank <= 0:
        return 1
    return 1 if current >= max_rank else current + 1


def eligible_for_channel_queue(fields: dict[str, Any]) -> bool:
    """判断记录是否应走渠道顺序队列（对齐公式 G + 分配链路前置条件）。"""
    if not is_assign_auto(get_field(fields, FIELD_ASSIGN_METHOD, "")):
        return False
    if not is_dup_ready(get_field(fields, FIELD_DUP_READY, "")):
        return False

    assign_source = get_field(fields, FIELD_ASSIGN_SOURCE, "")
    if is_assign_source_blocked(assign_source):
        return False
    if not is_assign_source_eligible(assign_source):
        return False

    if is_suboffice_country(get_field(fields, FIELD_SUBOFFICE, "")):
        return False
    if extract_text(get_field(fields, FIELD_QUEUE_ASSIGNEE, "")):
        return False
    if not extract_text(get_field(fields, FIELD_QUEUE_KEY, "")):
        return False

    system = extract_text(get_field(fields, FIELD_SYSTEM, ""))
    if system and system not in ERROR_ASSIGNEES:
        return False

    agent_country_val = get_field(fields, FIELD_AGENT_COUNTRY, "")
    agent_product_val = get_field(fields, FIELD_AGENT_PRODUCT, "")
    if is_agent_country(agent_country_val):
        if is_agent_product_yes(agent_product_val) or is_agent_product_pending(agent_product_val):
            return False
        if is_agent_product_empty(agent_product_val):
            return False

    if is_rotation_eligible(get_field(fields, FIELD_ROTATION, "")):
        return True
    if is_not_agent_country(agent_country_val):
        return True
    if is_agent_country(agent_country_val) and is_agent_product_no(agent_product_val):
        return True
    return False


def pick_queue_assignee(
    queue_key: str,
    pointers: dict[str, QueuePointer],
    queue_map: dict[tuple[str, int], str],
) -> QueuePickResult | None:
    """按当前指针从队列表选出业务员，并计算推进后的顺位。"""
    candidates = []
    for key in (queue_key, normalize_queue_key(queue_key)):
        if key and key not in candidates:
            candidates.append(key)

    for key in candidates:
        ptr = pointers.get(key)
        if not ptr or not ptr.record_id:
            continue

        max_rank = max(ptr.max_rank, 1)
        start = ptr.current if ptr.current > 0 else 1
        for offset in range(max_rank):
            rank = ((start - 1 + offset) % max_rank) + 1
            assignee = queue_map.get((key, rank))
            if assignee:
                return QueuePickResult(
                    assignee=assignee,
                    pointer_record_id=ptr.record_id,
                    used_rank=rank,
                    next_rank=advance_pointer(rank, max_rank),
                    max_rank=max_rank,
                    resolved_queue_key=key,
                )
    return None


def parse_queue_pointers(records: list[dict]) -> dict[str, QueuePointer]:
    pointers: dict[str, QueuePointer] = {}
    for record in records:
        fields = record.get("fields", {})
        queue_key = extract_text(fields.get("队列Key", "")).strip()
        if not queue_key:
            continue
        current_rank = _extract_int_field(fields.get("当前顺序号"), 1)
        max_rank_val = _extract_int_field(fields.get("最大顺序号"), current_rank)
        pointers[queue_key] = QueuePointer(
            record_id=record.get("record_id", ""),
            current=current_rank,
            max_rank=max_rank_val,
        )
    return pointers


def parse_channel_queue_map(records: list[dict]) -> dict[tuple[str, int], str]:
    mapping: dict[tuple[str, int], str] = {}
    for record in records:
        fields = record.get("fields", {})
        queue_key = extract_text(fields.get("队列Key", "")).strip()
        rank = fields.get("顺位")
        assignee = extract_text(fields.get("业务员", "")).strip()
        if not queue_key or not assignee:
            continue
        try:
            rank_val = int(rank)
        except (TypeError, ValueError):
            continue
        mapping[(queue_key, rank_val)] = assignee
    return mapping
