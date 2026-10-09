"""分配状态 / 系统匹配公式：刚录入应显示正在匹配规则。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from assignment_formulas import (  # noqa: E402
    build_assignment_status_expression,
    build_system_assignee_expression,
)
from option_field_match import (  # noqa: E402
    is_assignment_pending,
    needs_assignment_unblock,
)


class TestAssignmentFormulas(unittest.TestCase):
    def test_system_assignee_waits_instead_of_exception(self):
        expr = build_system_assignee_expression()
        self.assertIn('IF([Duplicate（重复）]="查重中","",', expr)
        self.assertIn("[是否满足渠道轮转]=\"是\"", expr)
        self.assertIn("ISBLANK([队列Key])", expr)
        self.assertIn("\"\",\"未命中规则\"", expr)
        self.assertNotIn(
            'IF(NOT(ISBLANK([渠道顺序队列匹配业务员])),[渠道顺序队列匹配业务员],"未命中规则")',
            expr,
        )

    def test_status_shows_assigning_when_final_blank(self):
        expr = build_assignment_status_expression()
        self.assertIn("🔄 正在匹配规则", expr)
        self.assertIn("❌ 分配异常", expr)
        self.assertIn("⏳ 分配中/阻塞", expr)
        self.assertIn("✅ 已分配", expr)
        self.assertIn(
            'IF(ISBLANK([Owner（最终负责人）]),"🔄 正在匹配规则"',
            expr,
        )


class TestPendingStatusMatch(unittest.TestCase):
    def test_assigning_is_pending_and_needs_unblock(self):
        self.assertTrue(is_assignment_pending("🔄 正在匹配规则"))
        self.assertTrue(is_assignment_pending("正在分配"))
        self.assertTrue(is_assignment_pending("⏳ 分配中/阻塞"))
        self.assertTrue(needs_assignment_unblock("🔄 正在匹配规则"))
        self.assertTrue(needs_assignment_unblock("正在分配"))
        self.assertTrue(needs_assignment_unblock("❌ 分配异常"))
        self.assertFalse(is_assignment_pending("❌ 分配异常"))
        self.assertFalse(needs_assignment_unblock("✅ 已分配"))


if __name__ == "__main__":
    unittest.main()
