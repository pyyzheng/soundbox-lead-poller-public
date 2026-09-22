"""Product model bilingual label helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from product_model_labels import (  # noqa: E402
    normalize_product_model_short,
    to_write_product_model,
)


class TestProductModelLabels(unittest.TestCase):
    def test_short_to_bilingual(self):
        self.assertEqual(to_write_product_model("尖顶"), "Pointed top（尖顶）")
        self.assertEqual(to_write_product_model("吸音板"), "Acoustic panel（吸音板）")
        self.assertEqual(to_write_product_model("无法识别"), "Unrecognized（无法识别）")

    def test_code_unchanged(self):
        self.assertEqual(to_write_product_model("VRT"), "VRT")
        self.assertEqual(to_write_product_model("SR-M"), "SR-M")

    def test_bilingual_idempotent(self):
        self.assertEqual(
            to_write_product_model("Pointed top（尖顶）"),
            "Pointed top（尖顶）",
        )

    def test_normalize_short(self):
        self.assertEqual(normalize_product_model_short("Pointed top（尖顶）"), "尖顶")
        self.assertEqual(normalize_product_model_short("Full range（全系列）"), "全系列")
        self.assertEqual(normalize_product_model_short("VRT-L"), "VRT-L")


if __name__ == "__main__":
    unittest.main()
