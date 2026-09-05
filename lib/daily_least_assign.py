"""按天累计最少优先 + 公区中东/亚洲区轮（2026-09-05 冻结规则）。

仅替代：中东区队列 / 亚洲区队列 / 南美非洲公区队列。
欧洲及子办等仍走 channel_queue_assign。
代理强制给人（含 Jannice 沙特/以色列）仍由代理规则/工作流处理；公区代理不占区指针。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 区内花名册（并列最少时按此顺序取第一个）
ME_ROSTER: tuple[str, ...] = ("Gigi", "Cathy")
ASIA_ROSTER: tuple[str, ...] = ("Kevin", "Rita")

# 人级累计统计对象（Jannice 代理不进最少池，可不计入竞争；仍可统计但不参与选人）
TRACKED_ASSIGNEES: frozenset[str] = frozenset(ME_ROSTER + ASIA_ROSTER)

POOL_ME = "ME"
POOL_ASIA = "ASIA"
POOL_PUBLIC = "PUBLIC"

QUEUE_SUFFIX_ME = frozenset({"中东区队列"})
QUEUE_SUFFIX_ASIA = frozenset({"亚洲区队列", "亚洲/中亚区队列"})
QUEUE_SUFFIX_PUBLIC = frozenset({"南美非洲公区队列", "拉丁美洲/中南美洲区队列"})

# 旧「中东/非洲」混合队列：非洲已迁公区；若仍出现则按中东池处理（国家映射应已切到新区）
QUEUE_SUFFIX_ME_LEGACY = frozenset({"中东/非洲区队列"})

DAILY_LEAST_POOLS = QUEUE_SUFFIX_ME | QUEUE_SUFFIX_ASIA | QUEUE_SUFFIX_PUBLIC | QUEUE_SUFFIX_ME_LEGACY

# 公区区指针存在队列指针表：当前顺序号 1=中东 2=亚洲
PUBLIC_REGION_POINTER_KEY = "__DAILY_LEAST__|公区区指针"
PUBLIC_REGION_ME = 1
PUBLIC_REGION_ASIA = 2


@dataclass(frozen=True)
class DailyLeastPickResult:
    assignee: str
    pool: str
    advance_public_region: bool
    next_public_region: int | None = None
    resolved_queue_suffix: str = ""


def queue_key_suffix(queue_key: str) -> str:
    key = (queue_key or "").strip()
    if "|" not in key:
        return key
    return key.split("|", 1)[1].strip()


def resolve_daily_least_pool(queue_key: str) -> str | None:
    """队列Key → ME / ASIA / PUBLIC；非本算法池返回 None。"""
    suffix = queue_key_suffix(queue_key)
    if suffix in QUEUE_SUFFIX_ME or suffix in QUEUE_SUFFIX_ME_LEGACY:
        return POOL_ME
    if suffix in QUEUE_SUFFIX_ASIA:
        return POOL_ASIA
    if suffix in QUEUE_SUFFIX_PUBLIC:
        return POOL_PUBLIC
    return None


def is_daily_least_queue(queue_key: str) -> bool:
    return resolve_daily_least_pool(queue_key) is not None


def shanghai_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """返回 (昨日0点, 今日0点, 明日0点) Asia/Shanghai。"""
    now_sh = (now or datetime.now(TZ_SHANGHAI)).astimezone(TZ_SHANGHAI)
    today_start = now_sh.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)
    return yesterday_start, today_start, tomorrow_start


def to_utc_ms(dt: datetime) -> int:
    return int(dt.astimezone(ZoneInfo("UTC")).timestamp() * 1000)


def eligible_for_daily_least(fields: dict[str, Any]) -> bool:
    """与渠道轮转资格对齐，但仅当队列属于中东/亚洲/公区。"""
    queue_key = extract_text(get_field(fields, FIELD_QUEUE_KEY, "")).strip()
    if not is_daily_least_queue(queue_key):
        return False
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


def pick_least_assignee(roster: Iterable[str], counts: dict[str, int]) -> str:
    """花名册顺序稳定：累计最小者；并列取花名册更靠前。"""
    best: str | None = None
    best_count: int | None = None
    for name in roster:
        c = int(counts.get(name, 0))
        if best is None or c < best_count:
            best = name
            best_count = c
    if best is None:
        raise ValueError("empty roster")
    return best


def counts_should_include(
    *,
    final_assignee: str,
    manual_assignee: str = "",
) -> bool:
    """人工改派不计入；仅统计跟踪名单内自动分配。"""
    if (manual_assignee or "").strip():
        return False
    name = (final_assignee or "").strip()
    return name in TRACKED_ASSIGNEES


def bump_count(counts: dict[str, int], assignee: str) -> None:
    name = (assignee or "").strip()
    if name not in TRACKED_ASSIGNEES:
        return
    counts[name] = int(counts.get(name, 0)) + 1


def align_newcomer_to_max(counts: dict[str, int], name: str, roster: Iterable[str]) -> None:
    """新人/调入：累计对齐当前花名册 max。"""
    peers = [int(counts.get(p, 0)) for p in roster]
    peak = max(peers) if peers else 0
    counts[name] = peak


def next_public_region(current: int) -> int:
    if current == PUBLIC_REGION_ME:
        return PUBLIC_REGION_ASIA
    return PUBLIC_REGION_ME


def roster_for_public_region(region: int) -> tuple[str, ...]:
    if region == PUBLIC_REGION_ASIA:
        return ASIA_ROSTER
    return ME_ROSTER


def pick_daily_least_assignee(
    queue_key: str,
    counts: dict[str, int],
    public_region: int,
) -> DailyLeastPickResult | None:
    """按池选人；公区非代理路径会要求调用方推进区指针。"""
    pool = resolve_daily_least_pool(queue_key)
    if pool is None:
        return None
    suffix = queue_key_suffix(queue_key)

    if pool == POOL_ME:
        assignee = pick_least_assignee(ME_ROSTER, counts)
        return DailyLeastPickResult(
            assignee=assignee,
            pool=pool,
            advance_public_region=False,
            resolved_queue_suffix=suffix,
        )

    if pool == POOL_ASIA:
        assignee = pick_least_assignee(ASIA_ROSTER, counts)
        return DailyLeastPickResult(
            assignee=assignee,
            pool=pool,
            advance_public_region=False,
            resolved_queue_suffix=suffix,
        )

    # PUBLIC
    region = public_region if public_region in (PUBLIC_REGION_ME, PUBLIC_REGION_ASIA) else PUBLIC_REGION_ME
    roster = roster_for_public_region(region)
    assignee = pick_least_assignee(roster, counts)
    nxt = next_public_region(region)
    return DailyLeastPickResult(
        assignee=assignee,
        pool=pool,
        advance_public_region=True,
        next_public_region=nxt,
        resolved_queue_suffix=suffix,
    )


def normalize_public_region(value: int | None) -> int:
    if value == PUBLIC_REGION_ASIA:
        return PUBLIC_REGION_ASIA
    return PUBLIC_REGION_ME


@dataclass(frozen=True)
class PersonCountDetail:
    name: str
    region: str  # 中东区 / 亚洲区
    yesterday: int
    today: int

    @property
    def total(self) -> int:
        return int(self.yesterday) + int(self.today)


def region_for_assignee(name: str) -> str:
    if name in ME_ROSTER:
        return "中东区"
    if name in ASIA_ROSTER:
        return "亚洲区"
    return ""


def empty_split_counts() -> dict[str, dict[str, int]]:
    return {name: {"yesterday": 0, "today": 0} for name in TRACKED_ASSIGNEES}


def accumulate_split_count(
    split: dict[str, dict[str, int]],
    *,
    assignee: str,
    entry_ms: int,
    manual_assignee: str,
    yesterday_start_ms: int,
    today_start_ms: int,
    tomorrow_start_ms: int,
) -> None:
    """按上海昨/今窗口累计；人工改派不计。"""
    if not counts_should_include(final_assignee=assignee, manual_assignee=manual_assignee):
        return
    if entry_ms < yesterday_start_ms or entry_ms >= tomorrow_start_ms:
        return
    bucket = "today" if entry_ms >= today_start_ms else "yesterday"
    split.setdefault(assignee, {"yesterday": 0, "today": 0})
    split[assignee][bucket] = int(split[assignee].get(bucket, 0)) + 1


def totals_from_split(split: dict[str, dict[str, int]]) -> dict[str, int]:
    return {
        name: int(split.get(name, {}).get("yesterday", 0)) + int(split.get(name, {}).get("today", 0))
        for name in TRACKED_ASSIGNEES
    }


def person_details_from_split(split: dict[str, dict[str, int]]) -> list[PersonCountDetail]:
    out: list[PersonCountDetail] = []
    for name in ME_ROSTER + ASIA_ROSTER:
        row = split.get(name, {"yesterday": 0, "today": 0})
        out.append(
            PersonCountDetail(
                name=name,
                region=region_for_assignee(name),
                yesterday=int(row.get("yesterday", 0)),
                today=int(row.get("today", 0)),
            )
        )
    return out


def debt_within_roster(name: str, totals: dict[str, int], roster: Iterable[str]) -> int:
    peers = [int(totals.get(p, 0)) for p in roster]
    peak = max(peers) if peers else 0
    return max(0, peak - int(totals.get(name, 0)))


def public_region_label(region: int) -> str:
    return "亚洲" if region == PUBLIC_REGION_ASIA else "中东"
