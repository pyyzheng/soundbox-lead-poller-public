"""按天最少优先 + 公区区轮：单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("FEISHU_APP_TOKEN", "test_app_token")
os.environ.setdefault("FEISHU_TABLE_ID", "test_table_id")
os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from daily_least_assign import (  # noqa: E402
    ASIA_ROSTER,
    ME_ROSTER,
    POOL_ASIA,
    POOL_ME,
    POOL_PUBLIC,
    PUBLIC_REGION_ASIA,
    PUBLIC_REGION_ME,
    align_newcomer_to_max,
    bump_count,
    counts_should_include,
    eligible_for_daily_least,
    is_daily_least_queue,
    next_public_region,
    pick_daily_least_assignee,
    pick_least_assignee,
    resolve_daily_least_pool,
)
from assignment_fields import (  # noqa: E402
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
    WRITE_ASSIGN_AUTO,
)


def _fields(**kwargs):
    base = {
        FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
        FIELD_DUP_READY: "是",
        FIELD_ASSIGN_SOURCE: "无重复",
        FIELD_SUBOFFICE: "否",
        FIELD_QUEUE_ASSIGNEE: "",
        FIELD_QUEUE_KEY: "谷歌|中东区队列",
        FIELD_SYSTEM: "未命中规则",
        FIELD_AGENT_COUNTRY: "否",
        FIELD_AGENT_PRODUCT: "",
        FIELD_ROTATION: "是",
    }
    base.update(kwargs)
    return base


class DailyLeastPoolTests(unittest.TestCase):
    def test_resolve_pools(self):
        self.assertEqual(resolve_daily_least_pool("谷歌|中东区队列"), POOL_ME)
        self.assertEqual(resolve_daily_least_pool("Facebook|亚洲区队列"), POOL_ASIA)
        self.assertEqual(resolve_daily_least_pool("谷歌|南美非洲公区队列"), POOL_PUBLIC)
        self.assertIsNone(resolve_daily_least_pool("谷歌|欧洲公区队列"))
        self.assertTrue(is_daily_least_queue("阿里国际站|中东区队列"))
        self.assertFalse(is_daily_least_queue("谷歌|欧洲公区队列"))

    def test_pick_least_tie_break_roster_order(self):
        counts = {"Gigi": 3, "Cathy": 3}
        self.assertEqual(pick_least_assignee(ME_ROSTER, counts), "Gigi")
        counts = {"Kevin": 5, "Rita": 4}
        self.assertEqual(pick_least_assignee(ASIA_ROSTER, counts), "Rita")

    def test_me_picks_lower_count(self):
        counts = {"Gigi": 10, "Cathy": 7, "Kevin": 0, "Rita": 0}
        pick = pick_daily_least_assignee("谷歌|中东区队列", counts, PUBLIC_REGION_ME)
        self.assertIsNotNone(pick)
        assert pick is not None
        self.assertEqual(pick.assignee, "Cathy")
        self.assertFalse(pick.advance_public_region)

    def test_public_alternates_region(self):
        counts = {"Gigi": 1, "Cathy": 2, "Kevin": 0, "Rita": 3}
        pick1 = pick_daily_least_assignee("谷歌|南美非洲公区队列", counts, PUBLIC_REGION_ME)
        assert pick1 is not None
        self.assertEqual(pick1.assignee, "Gigi")
        self.assertTrue(pick1.advance_public_region)
        self.assertEqual(pick1.next_public_region, PUBLIC_REGION_ASIA)

        bump_count(counts, pick1.assignee)
        pick2 = pick_daily_least_assignee(
            "谷歌|南美非洲公区队列", counts, pick1.next_public_region or PUBLIC_REGION_ASIA
        )
        assert pick2 is not None
        self.assertEqual(pick2.assignee, "Kevin")
        self.assertEqual(pick2.next_public_region, PUBLIC_REGION_ME)

    def test_agency_gap_then_least_prefers_behind(self):
        """Cathy 因代理累计升高后，中东普通单应补给 Gigi。"""
        counts = {"Gigi": 5, "Cathy": 5, "Kevin": 0, "Rita": 0}
        bump_count(counts, "Cathy")  # 阿联酋代理
        bump_count(counts, "Cathy")
        pick = pick_daily_least_assignee("Facebook|中东区队列", counts, PUBLIC_REGION_ME)
        assert pick is not None
        self.assertEqual(pick.assignee, "Gigi")

    def test_counts_exclude_manual(self):
        self.assertTrue(counts_should_include(final_assignee="Cathy", manual_assignee=""))
        self.assertFalse(counts_should_include(final_assignee="Cathy", manual_assignee="Gigi"))
        self.assertFalse(counts_should_include(final_assignee="Jannice", manual_assignee=""))

    def test_newcomer_align(self):
        counts = {"Gigi": 12, "Cathy": 8}
        align_newcomer_to_max(counts, "Cathy", ME_ROSTER)
        # Cathy already in roster — align sets her to max of roster
        self.assertEqual(counts["Cathy"], 12)

    def test_eligible_daily_least(self):
        self.assertTrue(eligible_for_daily_least(_fields()))
        self.assertFalse(
            eligible_for_daily_least(_fields(**{FIELD_QUEUE_KEY: "谷歌|欧洲公区队列"}))
        )
        self.assertFalse(
            eligible_for_daily_least(_fields(**{FIELD_QUEUE_ASSIGNEE: "Cathy"}))
        )

    def test_next_public_region(self):
        self.assertEqual(next_public_region(PUBLIC_REGION_ME), PUBLIC_REGION_ASIA)
        self.assertEqual(next_public_region(PUBLIC_REGION_ASIA), PUBLIC_REGION_ME)


if __name__ == "__main__":
    unittest.main()
