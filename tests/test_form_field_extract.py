#!/usr/bin/env python3
"""新官网 HTML 询盘：邮箱/电话字段提取回归。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from lead_fallback_parser import (  # noqa: E402
    choose_email_body,
    extract_fields,
    overlay_form_fields,
    strip_html,
)
from tagline_fields import (  # noqa: E402
    FIELD_EMAIL,
    FIELD_PHONE,
    build_feishu_fields_from_content,
    parse_inquiry_fields,
)

NEW_SITE_HTML = """
<html><body>
<h2>新官网询价通知 (https://www.soundbox-sys.com/)</h2>
<p>A new product inquiry has been submitted on the Soundbox website.</p>
<table>
<tr><td><strong>Inquiry No:</strong></td><td>INQ20260819002</td></tr>
<tr><td><strong>Name:</strong></td><td>Samuel Robert</td></tr>
<tr><td><strong>Email:</strong></td>
    <td><a href="mailto:rbr.samuel@gmail.com">rbr.samuel@gmail.com</a></td></tr>
<tr><td><strong>Phone:</strong></td><td></td></tr>
<tr><td><strong>Country:</strong></td><td>Hong Kong</td></tr>
</table>
<p>你好，我係 Sam，同我嘅拍檔 Angie 一齊聯絡你。</p>
<p>我們正考慮租用堅尼地城一個地下舖位，計劃改裝成私人舞蹈工作室。</p>
<p>Hi, my name is Sam and I&rsquo;m reaching out together with my partner Angie.</p>
</body></html>
"""

EMPTY_FORM_PLAIN = (
    "Name: \nEmail: \nCompany: \nTelephone Number: \n"
    "Message: 你好，我係 Sam，同我嘅拍檔 Angie 一齊聯絡你。\n\n"
    "Hong KongInquiry:你好，我係 Sam，同我嘅拍檔 Angie 一齊聯絡你。"
    "-新官网-声学产品-无法识别"
)


class TestNewSiteHtmlExtract(unittest.TestCase):
    def test_phone_country_inference_bulgaria(self):
        from lead_fallback_parser import identify_country

        self.assertEqual(identify_country("", "+359233511", ""), "保加利亚")

    def test_build_fields_infer_country_from_phone(self):
        from tagline_fields import FIELD_COUNTRY

        content = (
            "Name: \nEmail: \nTelephone Number: +359233511\n"
            "Message: customer asked about certification\n"
        )
        fields = build_feishu_fields_from_content(content, channels="谷歌")
        self.assertEqual(fields.get(FIELD_COUNTRY), "保加利亚")

    def test_html_table_extracts_mailto_email_and_name(self):
        fields = extract_fields(NEW_SITE_HTML)
        self.assertEqual(fields["email"], "rbr.samuel@gmail.com")
        self.assertEqual(fields["name"], "Samuel Robert")
        self.assertEqual(fields["country"], "Hong Kong")
        self.assertEqual(fields["phone"], "")
        self.assertIn("我係 Sam", fields["message"])

    def test_html_prefers_form_over_empty_plain(self):
        empty_plain = (
            "Inquiry No: INQ20260819002\nName: Samuel Robert\n"
            "Email: \nPhone: \nCountry: Hong Kong\n"
        )
        body = choose_email_body(empty_plain, NEW_SITE_HTML)
        fields = extract_fields(body)
        self.assertEqual(fields["email"], "rbr.samuel@gmail.com")

    def test_strip_html_decodes_entities(self):
        text = strip_html("I&rsquo;m Sam &bull; item")
        self.assertIn("Sam", text)
        self.assertNotIn("&rsquo;", text)
        self.assertNotIn("&bull;", text)
        self.assertIn("•", text)

    def test_empty_phone_does_not_eat_message_line(self):
        fields = extract_fields(EMPTY_FORM_PLAIN)
        self.assertEqual(fields["email"], "")
        self.assertEqual(fields["phone"], "")
        self.assertNotIn("Message:", fields["phone"])
        parsed = parse_inquiry_fields(EMPTY_FORM_PLAIN)
        self.assertNotIn("phone", parsed)
        self.assertNotEqual(parsed.get("email", ""), "Company:")
        built = build_feishu_fields_from_content(EMPTY_FORM_PLAIN)
        self.assertEqual(built[FIELD_PHONE], "N/A")
        self.assertEqual(built[FIELD_EMAIL], "N/A")

    def test_same_line_phone_still_works(self):
        parsed = parse_inquiry_fields(
            "Name: Isha\nEmail: isha@pensive.com\n"
            "Telephone Number: 4128025332\nMessage: I need to rent Phone booth"
        )
        self.assertEqual(parsed["email"], "isha@pensive.com")
        self.assertEqual(parsed["phone"], "4128025332")
        self.assertEqual(parsed["name"], "Isha")

    def test_overlay_fills_llm_empty_email(self):
        pre = extract_fields(NEW_SITE_HTML)
        merged = overlay_form_fields(
            {"name": "", "email": "", "phone": "Message: 你好", "country": "Hong KongInquiry:你好"},
            pre,
        )
        self.assertEqual(merged["email"], "rbr.samuel@gmail.com")
        self.assertEqual(merged["name"], "Samuel Robert")
        self.assertEqual(merged["phone"], "")
        self.assertEqual(merged["country"], "Hong Kong")


if __name__ == "__main__":
    unittest.main()
