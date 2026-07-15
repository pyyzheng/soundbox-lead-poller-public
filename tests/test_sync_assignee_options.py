"""sync_assignee_select_options 单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sync_assignee_select_options import _merge_options  # noqa: E402


class TestMergeOptions(unittest.TestCase):
    def test_realigns_ids_by_name(self):
        canonical = [
            {"id": "opt_main_sue", "name": "Sue", "color": 0},
            {"id": "opt_main_kaka", "name": "Kaka", "color": 2},
        ]
        target = [
            {"id": "opt_queue_sue", "name": "Sue", "color": 0},
            {"id": "opt_queue_kaka", "name": "Kaka", "color": 2},
        ]
        merged, count = _merge_options(canonical, target)
        self.assertEqual(count, 2)
        by_name = {o["name"]: o["id"] for o in merged}
        self.assertEqual(by_name["Sue"], "opt_main_sue")
        self.assertEqual(by_name["Kaka"], "opt_main_kaka")

    def test_keeps_extra_target_options(self):
        canonical = [{"id": "opt_main_sue", "name": "Sue", "color": 0}]
        target = [
            {"id": "opt_queue_sue", "name": "Sue", "color": 0},
            {"id": "opt_extra", "name": "Legacy", "color": 1},
        ]
        merged, count = _merge_options(canonical, target)
        self.assertEqual(count, 1)
        self.assertEqual(len(merged), 2)
        self.assertEqual({o["name"] for o in merged}, {"Sue", "Legacy"})


if __name__ == "__main__":
    unittest.main()
