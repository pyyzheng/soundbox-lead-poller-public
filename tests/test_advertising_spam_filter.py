"""客座文章/广告约稿应被硬拦截（004910 漏网回归）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lead_filter_common import (  # noqa: E402
    check_advertising_outreach,
    check_inquiry_keywords,
    check_promotional_content,
    check_skip_sender,
    load_lead_rules,
    should_force_inquiry_intent,
)


SOPHIE_MSG = """
Greetings,

Are you currently accepting guest article submissions? If so, I have a topic
I’d like to address for your readers. It’s on how individuals can future-proof
their minds and strengthen resilience in today’s unpredictable world.

The piece will explore strategies such as cultivating openness to change,
managing uncertainty with curiosity rather than fear, and embracing lifelong
learning as a growth mindset.

Would this topic interest your readers? If so, please let me know.

Thank you,
Sophie Letts

P.S. Feel free to suggest another topic if you have one in mind. I'm more than
happy to tailor my writing to suit your website. My goal is always to share
content that’s as enjoyable for people to read as it is easy for AI to recommend.
"""

FORM_BODY = f"""
Name: Sophie Letts
Email: sophiel@meditationhelp.net
Telephone Number: (203) 786-5156
Message: {SOPHIE_MSG}
Page URL: https://www.soundboxacoustic.com/soundbox-sr-m/
Page Title: SoundBox Acoustic SR-M Silence Booth
"""


class AdvertisingSpamFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_advertising_hard_blocks_guest_article_pitch(self):
        hit, reason = check_advertising_outreach(SOPHIE_MSG, rules=self.rules)
        self.assertTrue(hit, reason)
        self.assertIn("advertising_outreach", reason)

    def test_website_form_wrapper_still_hard_blocked(self):
        """官网主题/Page URL 含 soundbox 时，约稿仍应硬拦截，不能靠 T2 凑信号。"""
        hit, reason = check_advertising_outreach(
            SOPHIE_MSG,
            subject="Message from SoundBox",
            raw_body=FORM_BODY,
            rules=self.rules,
        )
        self.assertTrue(hit, reason)
        has_kw, _ = check_inquiry_keywords(
            "Sophie Letts", SOPHIE_MSG, "", self.rules, subject="Message from SoundBox"
        )
        self.assertTrue(has_kw, "回归场景：主题/品牌词会命中询盘关键词，T2 不够")

    def test_meditationhelp_domain_is_skip_sender(self):
        hit, reason = check_skip_sender("sophiel@meditationhelp.net", self.rules)
        self.assertTrue(hit, reason)

    def test_should_not_override_llm_non_inquiry(self):
        self.assertFalse(
            should_force_inquiry_intent(
                "Message from SoundBox", SOPHIE_MSG, rules=self.rules, body=FORM_BODY
            )
        )

    def test_promotional_patterns_still_match(self):
        hit, reason = check_promotional_content(
            "Sophie Letts", "", SOPHIE_MSG, "", self.rules
        )
        self.assertTrue(hit, reason)

    def test_real_product_inquiry_not_blocked(self):
        msg = (
            "I read a guest article about acoustic treatment and now need a quote "
            "for 4 SR-M silence booths for our Berlin office."
        )
        hit, reason = check_advertising_outreach(msg, rules=self.rules)
        self.assertFalse(hit, reason)
        has_kw, _ = check_inquiry_keywords("Anna Buyer", msg, "Berlin Office GmbH", self.rules)
        self.assertTrue(has_kw)


if __name__ == "__main__":
    unittest.main()
