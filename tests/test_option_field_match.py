"""飞书单选/公式字段 option 匹配单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from feishu_utils import option_tokens
from option_field_match import (
    is_agent_country,
    is_agent_product_no,
    is_agent_product_pending,
    is_assign_auto,
    is_assign_source_eligible,
    is_assignment_assigned,
    is_assignment_blocked,
    is_assignment_exception,
    is_dup_ready,
    is_not_agent_country,
    is_rotation_eligible,
    is_suboffice_country,
    is_success_assigned,
)


class TestOptionTokens(unittest.TestCase):
    def test_bare_option_id_list(self):
        self.assertEqual(option_tokens(["opthA5jqMG"]), {"opthA5jqMG"})

    def test_lookup_wrapper_with_label(self):
        self.assertEqual(option_tokens({"type": 3, "value": ["是"]}), {"是"})

    def test_lookup_wrapper_with_option_id(self):
        self.assertEqual(option_tokens({"type": 3, "value": ["opt6XJowhl"]}), {"opt6XJowhl"})


class TestAssignmentOptionMatch(unittest.TestCase):
    def test_suboffice_country_option_id(self):
        self.assertTrue(is_suboffice_country(["opthA5jqMG"]))
        self.assertFalse(is_suboffice_country({"type": 3, "value": ["否"]}))

    def test_agent_country_option_ids(self):
        self.assertTrue(is_agent_country(["optstg0Zdp"]))
        self.assertTrue(is_not_agent_country(["opt6XJowhl"]))

    def test_agent_product_pending_option_id(self):
        self.assertTrue(is_agent_product_pending(["optJ7X2CIx"]))
        self.assertTrue(is_agent_product_no(["optWdtyujk"]))

    def test_success_and_assign_method_option_ids(self):
        self.assertTrue(is_success_assigned(["optBhNG4cY"]))
        self.assertTrue(is_assign_auto(["opt8r8I1Re"]))

    def test_bilingual_assign_method_labels(self):
        self.assertTrue(is_assign_auto("Automatic（自动）"))
        self.assertTrue(is_success_assigned("Yes（是）"))

    def test_formula_yes_labels(self):
        self.assertTrue(is_dup_ready("是"))
        self.assertTrue(is_rotation_eligible({"type": 3, "value": ["是"]}))

    def test_assign_source_option_id(self):
        self.assertTrue(is_assign_source_eligible(["optGRVFdR1"]))
        self.assertTrue(is_assign_source_eligible("无重复"))

    def test_assignment_status_option_ids(self):
        self.assertTrue(is_assignment_exception(["optqgb587m"]))
        self.assertTrue(is_assignment_exception("❌ 分配异常"))
        self.assertTrue(is_assignment_assigned(["optpspV6LA"]))
        self.assertTrue(is_assignment_blocked(["optIZkcgkB"]))
        self.assertFalse(is_assignment_exception(["optpspV6LA"]))


if __name__ == "__main__":
    unittest.main()
