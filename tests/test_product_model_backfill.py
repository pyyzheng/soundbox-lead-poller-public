"""产品型号历史回填：只覆盖无法识别/空值。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from product_model_backfill import infer_product_updates, is_invalid_product_value  # noqa: E402
from tagline_fields import FIELD_PRODUCT_CAT, FIELD_PRODUCT_MODEL  # noqa: E402

RULES = json.loads((ROOT / "lead-rules.json").read_text(encoding="utf-8"))


class TestInferProductUpdates(unittest.TestCase):
    def test_skip_when_already_valid(self):
        updates = infer_product_updates(
            enquiry="need VRT pods",
            country="美国",
            channels="谷歌",
            sub_channel="谷歌2",
            current_category="Silence Booth 静音舱",
            current_model="VRT",
            rules=RULES,
        )
        self.assertEqual(updates, {})

    def test_facebook_latam_country_default(self):
        updates = infer_product_updates(
            enquiry="Name: A\nMessage: interested\n\n智利-Facebook-无法识别-无法识别",
            country="智利",
            channels="Facebook",
            sub_channel="Facebook",
            current_category="无法识别",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertEqual(updates[FIELD_PRODUCT_CAT], "Silence Booth 静音舱")
        self.assertEqual(updates[FIELD_PRODUCT_MODEL], "VRT")

    def test_facebook_capacity_upgrade(self):
        msg = (
            "is_this_booth_for_personal_use_or_for_resale?: resale; "
            "how_many_people_does_the_booth_need_to_fit?: 4_people"
        )
        updates = infer_product_updates(
            enquiry=msg,
            country="墨西哥",
            channels="Facebook",
            sub_channel="Facebook",
            current_category="Silence Booth 静音舱",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertNotIn(FIELD_PRODUCT_CAT, updates)  # 大类已有效，不改
        self.assertEqual(updates[FIELD_PRODUCT_MODEL], "VRT-L")

    def test_google2_size_l(self):
        msg = "4-person meeting pod (Size L). Please quote."
        updates = infer_product_updates(
            enquiry=msg,
            country="英国",
            channels="谷歌",
            sub_channel="谷歌2",
            current_category="Silence Booth 静音舱",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertEqual(updates[FIELD_PRODUCT_MODEL], "VRT-L")

    def test_bare_vrt_keyword(self):
        updates = infer_product_updates(
            enquiry="We need VRT pods for the office",
            country="德国",
            channels="谷歌",
            sub_channel="谷歌2",
            current_category="无法识别",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertEqual(updates[FIELD_PRODUCT_MODEL], "VRT")
        self.assertEqual(updates[FIELD_PRODUCT_CAT], "Silence Booth 静音舱")

    def test_homepod_not_forced_to_vrt(self):
        updates = infer_product_updates(
            enquiry="Hello! You have sound box that have mini kitchen and bathroom?Mini house",
            country="哥伦比亚",
            channels="阿里国际站",
            sub_channel="阿里1",
            current_category="Homepod 家居舱",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertEqual(updates, {})

    def test_acoustic_not_forced_without_series_kw(self):
        updates = infer_product_updates(
            enquiry="solutions for SUB DIPs for control Room acoustic treatment",
            country="约旦",
            channels="阿里国际站",
            sub_channel="阿里1",
            current_category="Acoustic products 声学产品",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertNotIn(FIELD_PRODUCT_MODEL, updates)

    def test_keyword_overrides_acoustic_block(self):
        updates = infer_product_updates(
            enquiry="Need VRT acoustic treatment quote 型号VRT",
            country="约旦",
            channels="阿里国际站",
            sub_channel="阿里1",
            current_category="Acoustic products 声学产品",
            current_model="无法识别",
            rules=RULES,
        )
        self.assertEqual(updates[FIELD_PRODUCT_MODEL], "VRT")


if __name__ == "__main__":
    unittest.main()
