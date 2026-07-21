#!/usr/bin/env python3
"""无法识别渠道自愈单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from assignment_fields import (  # noqa: E402
    expand_queue_key_candidates,
    heal_invalid_channel,
    infer_channel_from_content,
    infer_channel_from_source_ids,
)


class TestHealInvalidChannel(unittest.TestCase):
    def test_no_heal_when_valid(self):
        self.assertIsNone(heal_invalid_channel("谷歌", sub_channel="谷歌1"))

    def test_heal_from_sub_channel(self):
        self.assertEqual(
            heal_invalid_channel("无法识别", sub_channel="Facebook"),
            "Facebook",
        )

    def test_heal_from_enquiry_tagline(self):
        content = "Name: A\nEmail: a@b.com\n\n秘鲁-Facebook-静音舱-VRT"
        self.assertEqual(infer_channel_from_content(content), "Facebook")
        self.assertEqual(
            heal_invalid_channel("无法识别", sub_channel="无法识别", enquiry=content),
            "Facebook",
        )

    def test_heal_from_gmail_msg_id(self):
        self.assertEqual(
            heal_invalid_channel(
                "无法识别",
                sub_channel="无法识别",
                gmail_msg_id="msg-123",
            ),
            "谷歌",
        )

    def test_heal_from_fb_leadgen(self):
        self.assertEqual(
            infer_channel_from_source_ids(fb_leadgen="12345"),
            "Facebook",
        )

    def test_expand_unrecognized_queue_key(self):
        cands = expand_queue_key_candidates("无法识别|拉丁美洲/中南美洲区队列")
        self.assertIn("无法识别|拉丁美洲/中南美洲区队列", cands)
        self.assertIn("谷歌|拉丁美洲/中南美洲区队列", cands)
        self.assertIn("Facebook|拉丁美洲/中南美洲区队列", cands)


if __name__ == "__main__":
    unittest.main()
