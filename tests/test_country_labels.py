"""Country bilingual label helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from country_labels import (  # noqa: E402
    normalize_country_short,
    to_write_country,
)


class TestCountryLabels(unittest.TestCase):
    def test_short_to_bilingual(self):
        self.assertEqual(to_write_country("美国"), "United States（美国）")
        self.assertEqual(to_write_country("印度"), "India（印度）")
        self.assertEqual(to_write_country("英国"), "United Kingdom（英国）")

    def test_bilingual_idempotent(self):
        self.assertEqual(
            to_write_country("United States（美国）"),
            "United States（美国）",
        )

    def test_normalize_short(self):
        self.assertEqual(normalize_country_short("United Kingdom（英国）"), "英国")
        self.assertEqual(normalize_country_short("美国"), "美国")
        self.assertEqual(normalize_country_short("USA"), "美国")

    def test_alias_indonesia(self):
        self.assertEqual(to_write_country("印尼"), "Indonesia（印度尼西亚）")


if __name__ == "__main__":
    unittest.main()
