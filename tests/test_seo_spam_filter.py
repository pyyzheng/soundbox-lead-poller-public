"""SEO/排名冷推销应被硬拦截（004908 漏网回归）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lead_filter_common import (  # noqa: E402
    check_inquiry_keywords,
    check_seo_outreach,
    load_lead_rules,
    should_force_inquiry_intent,
)


JONE_MSG = """
dear,

Hope everything is good with you

I was going through your website on behalf of this Email:
soundboxbooth@gmail.com. It has a good design and it looks great_, but it's
not ranking in top on Google .

I will help you to place your website. Although it looks fantastic and has
a nice design, on Google's 1st page (Yahoo, etc.)

Could I provide you with the cost and a quote?

Thanks

Fwd:-Email: soundboxbooth@gmail.com an it appears on Google's first page
[Provide Accurate Quotation]soundboxbooth@gmail.comsoundboxbooth@gmail.com
"""

JONE_SUBJECT = (
    "Fwd:-Email: soundboxbooth@gmail.com an it appears on Google's first page "
    "[Provide Accurate Quotation"
)


class SeoSpamFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_seo_hard_blocks_jonehenry_pitch(self):
        hit, reason = check_seo_outreach(JONE_MSG, subject=JONE_SUBJECT, rules=self.rules)
        self.assertTrue(hit, reason)
        self.assertIn("seo_outreach", reason)

    def test_quotation_subject_still_hits_keyword_but_seo_blocks(self):
        """主题带 Quotation 会命中询盘关键词，但 SEO 硬拦截必须先挡掉。"""
        has_kw, _ = check_inquiry_keywords(
            "", JONE_MSG, "", self.rules, subject=JONE_SUBJECT
        )
        self.assertTrue(has_kw, "回归：主题 Quotation 仍会命中询盘关键词")
        hit, reason = check_seo_outreach(JONE_MSG, subject=JONE_SUBJECT, rules=self.rules)
        self.assertTrue(hit, reason)

    def test_should_not_force_inquiry_intent(self):
        self.assertFalse(
            should_force_inquiry_intent(JONE_SUBJECT, JONE_MSG, rules=self.rules)
        )

    def test_real_product_inquiry_not_blocked(self):
        msg = (
            "Please send a quotation for 6 SR-M silence booths for our Singapore office. "
            "We also need the catalog and lead time."
        )
        hit, reason = check_seo_outreach(msg, subject="SR-M quotation request", rules=self.rules)
        self.assertFalse(hit, reason)


if __name__ == "__main__":
    unittest.main()
