#!/usr/bin/env python3
"""渠道顺序队列分配逻辑单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from channel_queue_assign import (  # noqa: E402
    QueuePointer,
    advance_pointer,
    eligible_for_channel_queue,
    parse_channel_queue_map,
    parse_queue_pointers,
    pick_queue_assignee,
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
    normalize_queue_key,
)


def _base_fields(**overrides):
    fields = {
        FIELD_ASSIGN_METHOD: "自动",
        FIELD_DUP_READY: "是",
        FIELD_ASSIGN_SOURCE: "无重复",
        FIELD_SUBOFFICE: "否",
        FIELD_QUEUE_ASSIGNEE: "",
        FIELD_QUEUE_KEY: "Facebook|欧洲区队列",
        FIELD_SYSTEM: "未命中规则",
        FIELD_AGENT_COUNTRY: "否",
        FIELD_AGENT_PRODUCT: "",
        FIELD_ROTATION: "是",
    }
    fields.update(overrides)
    return fields


class TestAdvancePointer(unittest.TestCase):
    def test_wraps_at_max(self):
        self.assertEqual(advance_pointer(3, 3), 1)
        self.assertEqual(advance_pointer(2, 3), 3)

    def test_zero_max_defaults_to_one(self):
        self.assertEqual(advance_pointer(1, 0), 1)


class TestEligibleForChannelQueue(unittest.TestCase):
    def test_happy_path_non_agent_country(self):
        self.assertTrue(eligible_for_channel_queue(_base_fields()))

    def test_rejects_manual_assignment(self):
        self.assertFalse(eligible_for_channel_queue(_base_fields(**{FIELD_ASSIGN_METHOD: "人工"})))

    def test_rejects_dup_in_progress(self):
        self.assertFalse(eligible_for_channel_queue(_base_fields(**{FIELD_ASSIGN_SOURCE: "查重中"})))

    def test_accepts_formula_assign_source_option_id_when_dup_ready(self):
        self.assertTrue(
            eligible_for_channel_queue(
                _base_fields(**{FIELD_ASSIGN_SOURCE: ["optGRVFdR1"]})
            )
        )

    def test_accepts_agent_country_option_ids(self):
        self.assertFalse(
            eligible_for_channel_queue(
                _base_fields(
                    **{
                        FIELD_SUBOFFICE: ["opteBbb8vv"],
                        FIELD_AGENT_COUNTRY: ["optstg0Zdp"],
                        FIELD_AGENT_PRODUCT: ["optJ7X2CIx"],
                        FIELD_ROTATION: "否",
                    }
                )
            )
        )
        self.assertTrue(
            eligible_for_channel_queue(
                _base_fields(
                    **{
                        FIELD_AGENT_COUNTRY: ["optstg0Zdp"],
                        FIELD_AGENT_PRODUCT: ["optWdtyujk"],
                        FIELD_ROTATION: "否",
                    }
                )
            )
        )

    def test_accepts_dup_ready_wrapper_format(self):
        self.assertTrue(
            eligible_for_channel_queue(
                _base_fields(**{FIELD_DUP_READY: {"type": 3, "value": ["是"]}})
            )
        )

    def test_rejects_suboffice_country_option_id(self):
        self.assertFalse(
            eligible_for_channel_queue(_base_fields(**{FIELD_SUBOFFICE: ["opthA5jqMG"]}))
        )

    def test_rejects_suboffice_country(self):
        self.assertFalse(eligible_for_channel_queue(_base_fields(**{FIELD_SUBOFFICE: "是"})))

    def test_rejects_when_queue_assignee_exists(self):
        self.assertFalse(
            eligible_for_channel_queue(_base_fields(**{FIELD_QUEUE_ASSIGNEE: "Sue"}))
        )

    def test_agent_country_allows_when_product_miss_is_no(self):
        self.assertTrue(
            eligible_for_channel_queue(
                _base_fields(
                    **{
                        FIELD_AGENT_COUNTRY: "是",
                        FIELD_AGENT_PRODUCT: "否",
                        FIELD_ROTATION: "否",
                    }
                )
            )
        )

    def test_agent_country_blocks_when_product_pending(self):
        self.assertFalse(
            eligible_for_channel_queue(
                _base_fields(
                    **{
                        FIELD_AGENT_COUNTRY: "是",
                        FIELD_AGENT_PRODUCT: "待确认",
                        FIELD_ROTATION: "否",
                    }
                )
            )
        )

    def test_agent_country_blocks_when_product_empty(self):
        self.assertFalse(
            eligible_for_channel_queue(
                _base_fields(**{FIELD_AGENT_COUNTRY: "是", FIELD_AGENT_PRODUCT: "", FIELD_ROTATION: "否"})
            )
        )


class TestPickQueueAssignee(unittest.TestCase):
    def setUp(self):
        self.queue_key = "Facebook|欧洲区队列"
        self.pointers = {
            self.queue_key: QueuePointer(record_id="ptr1", current=1, max_rank=3),
        }
        self.queue_map = {
            (self.queue_key, 1): "Sue",
            (self.queue_key, 2): "Kaka",
            (self.queue_key, 3): "Snow",
        }

    def test_picks_current_rank(self):
        result = pick_queue_assignee(self.queue_key, self.pointers, self.queue_map)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.assignee, "Sue")
        self.assertEqual(result.used_rank, 1)
        self.assertEqual(result.next_rank, 2)

    def test_skips_missing_rank_slot(self):
        pointers = {self.queue_key: QueuePointer(record_id="ptr1", current=2, max_rank=3)}
        queue_map = {(self.queue_key, 1): "Sue", (self.queue_key, 3): "Snow"}
        result = pick_queue_assignee(self.queue_key, pointers, queue_map)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.assignee, "Snow")
        self.assertEqual(result.used_rank, 3)

    def test_returns_none_when_queue_missing(self):
        self.assertIsNone(pick_queue_assignee("missing", self.pointers, self.queue_map))


class TestParseHelpers(unittest.TestCase):
    def test_parse_queue_pointers(self):
        pointers = parse_queue_pointers(
            [
                {
                    "record_id": "rec1",
                    "fields": {"队列Key": "Facebook|欧洲区队列", "当前顺序号": 2, "最大顺序号": 3},
                }
            ]
        )
        self.assertEqual(pointers["Facebook|欧洲区队列"].current, 2)
        self.assertEqual(pointers["Facebook|欧洲区队列"].max_rank, 3)

    def test_parse_queue_pointers_lookup_max_rank(self):
        pointers = parse_queue_pointers(
            [
                {
                    "record_id": "rec1",
                    "fields": {
                        "队列Key": "谷歌|欧洲区队列",
                        "当前顺序号": 2,
                        "最大顺序号": {"type": 2, "value": [3]},
                    },
                }
            ]
        )
        self.assertEqual(pointers["谷歌|欧洲区队列"].current, 2)
        self.assertEqual(pointers["谷歌|欧洲区队列"].max_rank, 3)

    def test_parse_channel_queue_map(self):
        mapping = parse_channel_queue_map(
            [
                {
                    "fields": {
                        "队列Key": "Facebook|欧洲区队列",
                        "顺位": 1,
                        "业务员": "Sue",
                    }
                }
            ]
        )
        self.assertEqual(mapping[("Facebook|欧洲区队列", 1)], "Sue")

    def test_normalize_queue_key_google_alias(self):
        self.assertEqual(normalize_queue_key("Google|欧洲区队列"), "谷歌|欧洲区队列")
        self.assertEqual(normalize_queue_key("谷歌|欧洲区队列"), "谷歌|欧洲区队列")
        self.assertEqual(normalize_queue_key("阿里国际站|欧洲区队列"), "阿里国际站|欧洲区队列")

    def test_pick_queue_assignee_accepts_google_prefix(self):
        pointers = {
            "谷歌|欧洲区队列": QueuePointer(record_id="rec-p", current=1, max_rank=2),
        }
        queue_map = {
            ("谷歌|欧洲区队列", 1): "Sue",
            ("谷歌|欧洲区队列", 2): "Snow",
        }
        pick = pick_queue_assignee("Google|欧洲区队列", pointers, queue_map)
        self.assertIsNotNone(pick)
        self.assertEqual(pick.assignee, "Sue")
        self.assertEqual(pick.resolved_queue_key, "谷歌|欧洲区队列")


if __name__ == "__main__":
    unittest.main()
