"""健康检查漏网告警应带 advertising_outreach 原因和 skip_senders 域名。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lead_filter_common import check_advertising_outreach, load_lead_rules  # noqa: E402
from spam_leak_alert import (  # noqa: E402
    format_spam_leak_detail,
    leak_item,
    skip_sender_hint,
)


SOPHIE_MSG = (
    "Are you currently accepting guest article submissions? If so, I have a "
    "topic I'd like to address for your readers."
)


class HealthSpamLeakAlertTest(unittest.TestCase):
    def test_skip_sender_hint_skips_consumer_domains(self):
        self.assertEqual(skip_sender_hint("buyer@gmail.com"), "")
        self.assertEqual(
            skip_sender_hint("sophiel@meditationhelp.net"),
            '{"pattern": "*@meditationhelp.net"}',
        )

    def test_format_includes_advertising_reason_and_domain(self):
        line = format_spam_leak_detail({
            "record_id": "recvvlfEE7dfIK",
            "name": "Sophie Letts",
            "email": "sophiel@meditationhelp.net",
            "signals": ["advertising_outreach(pattern:guest\\s+article\\s+submissions?)"],
        })
        self.assertIn("reason=advertising_outreach", line)
        self.assertIn("domain=meditationhelp.net", line)
        self.assertIn('skip_senders={"pattern": "*@meditationhelp.net"}', line)

    def test_guest_article_leak_item_ready_for_alert(self):
        rules = load_lead_rules()
        hit, reason = check_advertising_outreach(SOPHIE_MSG, rules=rules)
        self.assertTrue(hit, reason)
        item = leak_item(
            {"record_id": "recvvlfEE7dfIK"},
            "Sophie Letts",
            "sophiel@meditationhelp.net",
            SOPHIE_MSG,
            SOPHIE_MSG,
            [reason],
        )
        detail = format_spam_leak_detail(item)
        self.assertIn("reason=advertising_outreach", detail)
        self.assertIn("meditationhelp.net", detail)
        self.assertIn("skip_senders=", detail)


if __name__ == "__main__":
    unittest.main()
