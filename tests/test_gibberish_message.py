#!/usr/bin/env python3
"""乱码 Message 过滤单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from lead_filter_common import (  # noqa: E402
    check_gibberish_message,
    check_short_message,
    is_gibberish_message,
    is_pure_numeric_message,
    load_lead_rules,
)


class TestGibberishMessage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_rejects_random_latin_blob(self):
        samples = [
            "iSOYNUuFvvqGKGUSahECXtj",
            "DhLENREjeAgMLHMZOtZ",
            "asdfghjklqwertyuiop",
            "GUYMaKqfADLmClzw",
        ]
        for msg in samples:
            with self.subTest(msg=msg):
                self.assertTrue(is_gibberish_message(msg, self.rules), msg)

    def test_accepts_real_inquiry(self):
        samples = [
            "Hello, We are interested in renting phone booths for our office.",
            "Pricing inquiry for VRT booth",
            "Hi, I need a quote for soundproof pods",
            "我们需要为办公室采购静音舱，请报价",
        ]
        for msg in samples:
            with self.subTest(msg=msg):
                self.assertFalse(is_gibberish_message(msg, self.rules), msg)

    def test_accepts_short_messages(self):
        for msg in ("Hi", "test", "quote", "VRT"):
            with self.subTest(msg=msg):
                self.assertFalse(is_gibberish_message(msg, self.rules))

    def test_check_returns_reason(self):
        ok, reason = check_gibberish_message("iSOYNUuFvvqGKGUSahECXtj", self.rules)
        self.assertTrue(ok)
        self.assertIn("gibberish_message", reason)

    def test_skips_when_no_message_field(self):
        self.assertFalse(
            is_gibberish_message("iSOYNUuFvvqGKGUSahECXtj", self.rules, has_message_field=False)
        )
        ok, reason = check_gibberish_message(
            "iSOYNUuFvvqGKGUSahECXtj", self.rules, has_message_field=False
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "")

    def test_rejects_gibberish_with_tagline_suffix(self):
        polluted = "sdsdfsdfsdfsdfsdf\n\n美国-美国舱网-静音舱-无法识别"
        self.assertTrue(is_gibberish_message(polluted, self.rules, has_message_field=True))

    def test_rejects_sdsf_pattern(self):
        self.assertTrue(is_gibberish_message("sdsdfsdfsdfsdfsdf", self.rules))

    def test_rejects_numeric_test_message(self):
        for msg in ("123", "12345", "12345678", "11111111"):
            with self.subTest(msg=msg):
                ok, reason = check_short_message(msg, self.rules)
                self.assertTrue(ok, reason)
                self.assertIn("numeric", reason)

    def test_rejects_short_keyboard_mash(self):
        self.assertTrue(is_gibberish_message("Hofutbgh", self.rules))


class TestShortMessage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_rejects_too_short(self):
        for msg in ("hi", "test", ""):
            with self.subTest(msg=msg):
                ok, reason = check_short_message(msg, self.rules)
                self.assertTrue(ok, reason)

    def test_accepts_five_char_message(self):
        ok, reason = check_short_message("hello", self.rules)
        self.assertFalse(ok, reason)

    def test_accepts_allowlisted_short(self):
        for msg in ("quote", "inquiry", "pricing", "rfq"):
            with self.subTest(msg=msg):
                ok, reason = check_short_message(msg, self.rules)
                self.assertFalse(ok, reason)

    def test_accepts_real_inquiry(self):
        msg = "Hello, We are interested in renting phone booths for our office."
        ok, reason = check_short_message(msg, self.rules)
        self.assertFalse(ok, reason)

    def test_accepts_chinese_inquiry(self):
        msg = "我们想为办公室采购静音舱，请提供报价和规格参数。"
        ok, reason = check_short_message(msg, self.rules)
        self.assertFalse(ok, reason)

    def test_pure_numeric_helper(self):
        self.assertTrue(is_pure_numeric_message("99"))
        self.assertFalse(is_pure_numeric_message("quote 2 pods"))


class TestFormSpamSubmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_rejects_003349_style(self):
        from lead_filter_common import check_form_spam_submission

        ok, reason = check_form_spam_submission(
            "Md Aftab ansari", "Majhar", "N/A", "", self.rules,
            has_message_field=True,
        )
        self.assertTrue(ok, reason)
        self.assertIn("form_spam_single_word", reason)

    def test_accepts_real_inquiry_with_phone(self):
        from lead_filter_common import check_form_spam_submission

        ok, reason = check_form_spam_submission(
            "John", "Majhar", "+1 214 717 7505", "", self.rules,
        )
        self.assertFalse(ok, reason)

    def test_accepts_allowlisted_short_word(self):
        from lead_filter_common import check_form_spam_submission

        ok, reason = check_form_spam_submission("A", "quote", "N/A", "", self.rules)
        self.assertFalse(ok, reason)

    def test_accepts_multi_word_message(self):
        from lead_filter_common import check_form_spam_submission

        msg = "We need a quote for office phone booths"
        ok, reason = check_form_spam_submission("A", msg, "N/A", "", self.rules)
        self.assertFalse(ok, reason)

    def test_accepts_product_model_single_word(self):
        from lead_filter_common import check_form_spam_submission

        for word in ("Homepod", "Contact", "Silent", "Meeting"):
            with self.subTest(word=word):
                ok, reason = check_form_spam_submission("A", word, "N/A", "", self.rules)
                self.assertFalse(ok, reason)

    def test_still_rejects_random_single_word(self):
        from lead_filter_common import check_form_spam_submission

        ok, reason = check_form_spam_submission("Test", "Majhar", "N/A", "", self.rules)
        self.assertTrue(ok, reason)


class TestTrivialContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_rejects_test_test_message(self):
        from lead_filter_common import check_trivial_content

        ok, reason = check_trivial_content("", "test test", self.rules)
        self.assertTrue(ok, reason)
        self.assertIn("test_message", reason)

    def test_rejects_hello_hi_message(self):
        from lead_filter_common import check_trivial_content

        ok, reason = check_trivial_content("John", "hello hi", self.rules)
        self.assertTrue(ok, reason)

    def test_accepts_real_inquiry(self):
        from lead_filter_common import check_trivial_content

        msg = "We need a quote for 4 office phone booths in Berlin"
        ok, reason = check_trivial_content("John", msg, self.rules)
        self.assertFalse(ok, reason)

    def test_accepts_quote_short_word(self):
        from lead_filter_common import check_trivial_content

        ok, reason = check_trivial_content("A", "quote", self.rules)
        self.assertFalse(ok, reason)


class TestSupplierOutreach(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_lead_rules()

    def test_rejects_vendor_pitch_003242(self):
        from lead_filter_common import check_supplier_outreach

        msg = (
            "We produce all types galvanized steel impaling clips, hooks & racks for acoustic panels: "
            "More than 18 years OEM experience. ISO, Rohs report, CE certified production. "
            "Low trial MOQ & samples available. If it is possible, Could you share me the email "
            "address of your purchasing department?"
        )
        ok, reason = check_supplier_outreach(msg, rules=self.rules)
        self.assertTrue(ok, reason)
        self.assertIn("supplier_outreach", reason)

    def test_accepts_real_buyer_inquiry(self):
        from lead_filter_common import check_supplier_outreach

        msg = "Hello, We are interested in renting phone booths for our office. Please send a quote."
        ok, reason = check_supplier_outreach(msg, rules=self.rules)
        self.assertFalse(ok, reason)


if __name__ == "__main__":
    unittest.main()
