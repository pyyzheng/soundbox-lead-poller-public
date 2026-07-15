"""分配链路常用的飞书单选/公式字段取值判断。"""

from __future__ import annotations

from assignment_fields import (
    AGENT_COUNTRY_NO,
    AGENT_COUNTRY_YES,
    AGENT_PRODUCT_NO,
    AGENT_PRODUCT_PENDING,
    AGENT_PRODUCT_YES,
    ASSIGN_METHOD_AUTO,
    ASSIGN_METHOD_MANUAL,
    ASSIGN_SOURCE_BLOCKED,
    ASSIGN_SOURCE_ELIGIBLE,
    ASSIGN_STATUS_ASSIGNED,
    ASSIGN_STATUS_BLOCKED,
    ASSIGN_STATUS_EXCEPTION,
    FORMULA_NO,
    FORMULA_YES,
    SUBOFFICE_COUNTRY_NO,
    SUBOFFICE_COUNTRY_YES,
    SUCCESS_NO,
    SUCCESS_YES,
)
from feishu_utils import is_option_no, is_option_yes, matches_option, option_tokens


def is_suboffice_country(field_val) -> bool:
    return is_option_yes(field_val, SUBOFFICE_COUNTRY_YES)


def is_not_suboffice_country(field_val) -> bool:
    return is_option_no(field_val, SUBOFFICE_COUNTRY_NO)


def is_agent_country(field_val) -> bool:
    return is_option_yes(field_val, AGENT_COUNTRY_YES)


def is_not_agent_country(field_val) -> bool:
    return is_option_no(field_val, AGENT_COUNTRY_NO)


def is_agent_product_yes(field_val) -> bool:
    return is_option_yes(field_val, AGENT_PRODUCT_YES)


def is_agent_product_no(field_val) -> bool:
    return is_option_no(field_val, AGENT_PRODUCT_NO)


def is_agent_product_pending(field_val) -> bool:
    return matches_option(field_val, AGENT_PRODUCT_PENDING)


def is_agent_product_empty(field_val) -> bool:
    return not option_tokens(field_val)


def is_assign_auto(field_val) -> bool:
    return matches_option(field_val, ASSIGN_METHOD_AUTO)


def is_assign_manual(field_val) -> bool:
    return matches_option(field_val, ASSIGN_METHOD_MANUAL)


def is_success_assigned(field_val) -> bool:
    return is_option_yes(field_val, SUCCESS_YES)


def is_not_success_assigned(field_val) -> bool:
    return is_option_no(field_val, SUCCESS_NO)


def is_formula_yes(field_val) -> bool:
    return is_option_yes(field_val, FORMULA_YES)


def is_formula_no(field_val) -> bool:
    return is_option_no(field_val, FORMULA_NO)


def is_dup_ready(field_val) -> bool:
    return is_formula_yes(field_val)


def is_rotation_eligible(field_val) -> bool:
    return is_formula_yes(field_val)


def is_assign_source_blocked(field_val) -> bool:
    return bool(option_tokens(field_val) & set(ASSIGN_SOURCE_BLOCKED))


def is_assign_source_eligible(field_val) -> bool:
    tokens = option_tokens(field_val)
    if tokens & set(ASSIGN_SOURCE_BLOCKED):
        return False
    if tokens & set(ASSIGN_SOURCE_ELIGIBLE):
        return True
    # 兼容尚未收录 option id 的分配来源（保持与旧逻辑一致）
    return any(token.startswith("opt") for token in tokens)


def is_assign_source_ok_for_queue(field_val, *, dup_ready: bool) -> bool:
    if is_assign_source_blocked(field_val):
        return False
    if is_assign_source_eligible(field_val):
        return True
    return dup_ready and any(token.startswith("opt") for token in option_tokens(field_val))


def is_assignment_exception(field_val) -> bool:
    return matches_option(field_val, ASSIGN_STATUS_EXCEPTION)


def is_assignment_assigned(field_val) -> bool:
    return matches_option(field_val, ASSIGN_STATUS_ASSIGNED)


def is_assignment_blocked(field_val) -> bool:
    return matches_option(field_val, ASSIGN_STATUS_BLOCKED)
