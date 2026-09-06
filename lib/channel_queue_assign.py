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
    expand_queue_key_candidates,
    get_field,
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


def _is_queue_member_enabled(fields: dict[str, Any]) -> bool:
    """未投影「是否启用」时视为启用（调用方通常已用 filter 筛过启用行）。"""
    if "是否启用" not in fields:
        return True
    status = fields.get("是否启用")
    if isinstance(status, list):
        status = status[0] if status else ""
    return extract_text(status).strip() == "启用"


def _enabled_entries_for_key(
    queue_map: dict[tuple[str, int], str],
    queue_key: str,
) -> list[tuple[int, str]]:
    return sorted(
        ((rank, assignee) for (key, rank), assignee in queue_map.items() if key == queue_key and assignee),
        key=lambda item: item[0],
    )


def _start_index_for_current(ranks: list[int], current: int) -> int:
    """当指针落在已停用/已删除顺位时，从下一个可用顺位继续轮转。"""
    if not ranks:
        return 0
    if current in ranks:
        return ranks.index(current)
    for idx, rank in enumerate(ranks):
        if rank >= current:
            return idx
    return 0


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
    """按当前指针从队列表选出业务员，并计算推进后的顺位。

    当队列Key 前缀无效（如「无法识别|拉丁美洲/中南美洲区队列」）时，
    按同区域后缀依次尝试 谷歌/Facebook/阿里 等已有队列。
    """
    for key in expand_queue_key_candidates(queue_key):
        ptr = pointers.get(key)
        if not ptr or not ptr.record_id:
            continue

        entries = _enabled_entries_for_key(queue_map, key)
        if not entries:
            continue

        ranks = [rank for rank, _ in entries]
        max_rank = max(ranks)
        current = ptr.current if ptr.current > 0 else ranks[0]
        start_idx = _start_index_for_current(ranks, current)

        for offset in range(len(entries)):
            idx = (start_idx + offset) % len(entries)
            rank, assignee = entries[idx]
            next_idx = (idx + 1) % len(entries)
            next_rank = ranks[next_idx]
            return QueuePickResult(
                assignee=assignee,
                pointer_record_id=ptr.record_id,
                used_rank=rank,
                next_rank=next_rank,
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
        if not _is_queue_member_enabled(fields):
            continue
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


def reconcile_pointer_fields(
    queue_map: dict[tuple[str, int], str],
    pointer: QueuePointer,
    queue_key: str,
) -> dict[str, int]:
    """根据启用顺位重算指针，避免停用后 max/current 仍指向旧顺位。"""
    entries = _enabled_entries_for_key(queue_map, queue_key)
    if not entries:
        return {}
    ranks = [rank for rank, _ in entries]
    max_rank = max(ranks)
    current = pointer.current if pointer.current in ranks else ranks[0]
    return {"当前顺序号": current, "最大顺序号": max_rank}
