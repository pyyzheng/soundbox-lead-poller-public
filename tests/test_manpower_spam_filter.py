"""劳务中介冷推销应被硬拦截（004713 漏网回归）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lead_filter_common import (  # noqa: E402
    check_promotional_content,
    check_supplier_outreach,
    load_lead_rules,
)

TRADESMEN_MSG = """
Kind Attn:
Respected Sir/Madam,
Greetings from India!!
I hope this email finds you well. I am writing to introduce Tradesmen Jobs,
a manpower recruitment company based in India that specializes in deploying
skilled professionals to various industries across Europe, GCC Region, North
America, and Northern African countries.
Our forte lies in providing manpower across various sectors, and we take
pride in offering a comprehensive range of positions, including:
All types of Masons
Bricklayers
Electrician
Plumber
"""


class ManpowerSpamFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_supplier_outreach_hard_blocks_manpower_pitch(self):
        hit, reason = check_supplier_outreach(TRADESMEN_MSG, rules=self.rules)
        self.assertTrue(hit, reason)
        self.assertIn("supplier_outreach", reason)

    def test_promotional_patterns_match_recruitment(self):
        hit, reason = check_promotional_content(
            "Sanoj Mahapatra", "", TRADESMEN_MSG, "", self.rules
        )
        self.assertTrue(hit, reason)

    def test_real_product_inquiry_not_blocked(self):
        msg = "We need a quote for SR-M silence booths for our office in Berlin."
        hit, reason = check_supplier_outreach(msg, rules=self.rules)
        self.assertFalse(hit, reason)


if __name__ == "__main__":
    unittest.main()
