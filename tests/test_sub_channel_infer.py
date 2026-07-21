#!/usr/bin/env python3
"""细分渠道推断单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from assignment_fields import (  # noqa: E402
    heal_invalid_sub_channel,
    infer_sub_channel_from_content,
    infer_sub_channel_from_signals,
)
from tagline_fields import build_feishu_fields_from_content, is_valid_tag_line  # noqa: E402


class TestInferSubChannel(unittest.TestCase):
    def test_wp_form_select_country(self):
        content = (
            "Name: Anna\nEmail: a@b.com\nInquiry:\nHello price?\n\n"
            "Select your country *:\nGreece"
        )
        self.assertEqual(infer_sub_channel_from_content(content), "谷歌1")

    def test_elementor_form(self):
        content = (
            "Name: Isha\nEmail: isha@pensive.com\n"
            "Telephone Number: 4128025332\nMessage: I need to rent Phone booth"
        )
        self.assertEqual(infer_sub_channel_from_content(content), "谷歌1")

    def test_new_site_notification(self):
        content = "新官网询价通知（https://www.soundbox-sys.com/）\nName: A"
        self.assertEqual(infer_sub_channel_from_content(content), "新官网")

    def test_loose_tag_line(self):
        content = "Name: A\nEmail: a@b.com\n\n美国-谷歌1-静音舱-无法识别"
        self.assertEqual(infer_sub_channel_from_content(content), "谷歌1")

    def test_gmail_fallback_when_channels_google(self):
        healed = infer_sub_channel_from_signals(
            enquiry="Name: A\nMessage: quote",
            channels="谷歌",
            gmail_msg_id="msg-1",
        )
        self.assertEqual(healed, "谷歌1")

    def test_build_fields_without_tag_line(self):
        content = (
            "Name: Sean\nEmail: thomsentrees@gmail.com\nInquiry:\nHow much?\n\n"
            "Select your country *:\nUnited Kingdom"
        )
        fields = build_feishu_fields_from_content(
            content, channels="谷歌", gmail_msg_id="msg-2",
        )
        self.assertEqual(fields["Channel segmentation (细分渠道)"], "谷歌1")
        self.assertEqual(fields["Channels（渠道）"], "谷歌")

    def test_rejects_message_line_with_dashes(self):
        tag = "Message: Hi - I am interested in single booths for my office (2-3)."
        self.assertFalse(is_valid_tag_line(tag))
        fields = build_feishu_fields_from_content(
            "Name: Layla\nEmail: a@b.com\nTelephone Number: 1\nMessage: Hi - I am interested in single booths for my office (2-3).",
            channels="谷歌",
            gmail_msg_id="msg-1",
        )
        self.assertEqual(fields["Channel segmentation (细分渠道)"], "谷歌1")

    def test_valid_tag_line_still_parses(self):
        self.assertTrue(is_valid_tag_line("美国-谷歌1-静音舱-VRT"))

    def test_heal_invalid_sub_channel(self):
        self.assertEqual(
            heal_invalid_sub_channel(
                "无法识别",
                enquiry="Telephone Number: 1\nMessage: quote",
                channels="谷歌",
                gmail_msg_id="x",
            ),
            "谷歌1",
        )


if __name__ == "__main__":
    unittest.main()
