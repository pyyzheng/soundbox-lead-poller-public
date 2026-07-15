"""询盘意图覆写：防止真实询盘被 L3 non_inquiry 误拦。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lead_filter_common import (  # noqa: E402
    check_inquiry_keywords,
    load_lead_rules,
    should_force_inquiry_intent,
)


class TestInquiryIntentOverride(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_quiet_pods_enquiry_subject(self):
        self.assertTrue(
            should_force_inquiry_intent("quiet pods enquiry", "", rules=self.rules)
        )
        has_kw, _ = check_inquiry_keywords("", "", "", self.rules, subject="quiet pods enquiry")
        self.assertTrue(has_kw)

    def test_loose_furniture_request_subject(self):
        self.assertTrue(
            should_force_inquiry_intent("Loose Furniture request", "", rules=self.rules)
        )

    def test_dear_sir_madam_with_product_body(self):
        body = (
            "Dear Sir/Madam,\n\nWe are looking for acoustic meeting pods for our showroom. "
            "Please send catalogue and pricing.\n\nBest regards"
        )
        self.assertTrue(
            should_force_inquiry_intent("Dear Sir/Madam", body, rules=self.rules, body=body)
        )

    def test_seo_spam_still_blocked(self):
        msg = (
            "We make it easy for your business to be seen first and chosen first. "
            "You pick the keywords, and we put your banner at the top of search results."
        )
        self.assertFalse(
            should_force_inquiry_intent("Message from SoundBox", msg, rules=self.rules)
        )

    def test_quote_request_seo_spam_still_blocked(self):
        msg = (
            "After a brief review of your website, I developed a quote request that "
            "contained recommendations for enhancing its search assessment."
        )
        self.assertFalse(
            should_force_inquiry_intent("Quote request", msg, rules=self.rules)
        )

    def test_walmart_seller_support_blocked(self):
        subj = "RE: 我们收到了您的 CN Case #15318594!"
        body = "感谢您联系沃尔玛卖家支持。please provide reference documents."
        from lead_filter_common import check_platform_marketplace_notification
        self.assertTrue(check_platform_marketplace_notification(
            "sellersupport@walmart.com", subj, body)[0])
        self.assertFalse(should_force_inquiry_intent(subj, body, rules=self.rules, body=body))


if __name__ == "__main__":
    unittest.main()
