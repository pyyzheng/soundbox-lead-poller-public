"""Product category bilingual label helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from product_category_labels import (  # noqa: E402
    normalize_product_category_short,
    to_write_product_category,
)


class TestProductCategoryLabels(unittest.TestCase):
    def test_short_to_bilingual(self):
        self.assertEqual(to_write_product_category("静音舱"), "Silence Booth 静音舱")
        self.assertEqual(to_write_product_category("无法识别"), "Unrecognized（无法识别）")

    def test_idempotent(self):
        self.assertEqual(
            to_write_product_category("Acoustic products 声学产品"),
            "Acoustic products 声学产品",
        )

    def test_normalize(self):
        self.assertEqual(
            normalize_product_category_short("Silence Booth 静音舱"),
            "静音舱",
        )


if __name__ == "__main__":
    unittest.main()
