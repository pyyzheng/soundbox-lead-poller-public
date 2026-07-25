"""产品型号识别：裸词系列名 + 容量/尺寸兜底 + Facebook 国家默认。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from facebook_product import (  # noqa: E402
    determine_product,
    looks_like_booth_form,
    refine_facebook_product,
)
from lead_fallback_parser import (  # noqa: E402
    extract_size_token,
    identify_product_model,
    sized_model_code,
)

RULES = json.loads((ROOT / "lead-rules.json").read_text(encoding="utf-8"))


class TestBareModelKeywords(unittest.TestCase):
    def test_vrt_bare_and_pods(self):
        self.assertEqual(identify_product_model("interested in VRT", RULES), "VRT")
        self.assertEqual(identify_product_model("We need VRT pods", RULES), "VRT")
        self.assertEqual(identify_product_model("VRT series phone booth", RULES), "VRT")

    def test_sr_vr_art_bare(self):
        self.assertEqual(identify_product_model("looking for SR booth", RULES), "SR")
        self.assertEqual(identify_product_model("VR pods for office", RULES), "VR")
        self.assertEqual(identify_product_model("ART series quote", RULES), "ART")

    def test_chinese_adjacent_and_cover_not_block(self):
        from lead_fallback_parser import extract_series_model_keyword

        self.assertEqual(extract_series_model_keyword("型号VRT请报价"), "VRT")
        self.assertEqual(identify_product_model("型号VRT请报价", RULES), "VRT")
        self.assertEqual(identify_product_model("cover the VR pod please", RULES), "VR")
        self.assertEqual(extract_series_model_keyword("需要SR-L静音舱"), "SR-L")


class TestCapacitySize(unittest.TestCase):
    def test_extract_size(self):
        self.assertEqual(extract_size_token("Model: L"), "L")
        self.assertEqual(extract_size_token("4-person meeting pod (Size L)"), "L")
        self.assertEqual(extract_size_token("how_many...: 4_people"), "L")
        self.assertEqual(extract_size_token("single-person phone booth"), "S")

    def test_sized_model_clamp_vrt(self):
        self.assertEqual(sized_model_code("VRT", "L"), "VRT-L")
        self.assertEqual(sized_model_code("VRT", "XXL"), "VRT-L")
        self.assertEqual(sized_model_code("SR", "XXL"), "SR-XXL")

    def test_google2_capacity_to_vrt(self):
        msg = (
            "quote for two SOUNDBOX soundproof booths. "
            "The specific models are: - 4-person meeting pod (Size L)."
        )
        self.assertEqual(
            identify_product_model(msg, RULES, sub_channel="谷歌2"),
            "VRT-L",
        )

    def test_google1_size_field_sr(self):
        self.assertEqual(
            identify_product_model("Model: M\nMessage: need booth", RULES, sub_channel="谷歌1"),
            "SR-M",
        )

    def test_facebook_4_people(self):
        msg = (
            "is_this_booth_for_personal_use_or_for_resale?: resale; "
            "how_many_people_does_the_booth_need_to_fit?: 4_people"
        )
        self.assertEqual(
            identify_product_model(msg, RULES, sub_channel="Facebook", default_series="VRT"),
            "VRT-L",
        )


class TestFacebookDefaults(unittest.TestCase):
    def test_latam_defaults_vrt(self):
        for country in ("墨西哥", "秘鲁", "智利", "哥伦比亚", "巴西", "阿根廷"):
            self.assertEqual(determine_product(country), ("静音舱", "VRT"), country)

    def test_sr_country_unchanged(self):
        self.assertEqual(determine_product("阿联酋"), ("静音舱", "SR"))

    def test_booth_form_unknown_country(self):
        fields = {
            "is_this_booth_for_personal_use_or_for_resale?": "personal_use",
            "how_many_people_does_the_booth_need_to_fit?": "2_people",
        }
        self.assertTrue(looks_like_booth_form(fields, ""))
        cat, model = refine_facebook_product("", fields, "", rules=RULES)
        self.assertEqual(cat, "静音舱")
        self.assertEqual(model, "VRT-M")  # 2_people → M

    def test_chile_booth_with_capacity(self):
        fields = {
            "how_many_people_does_the_booth_need_to_fit?": "4_people",
            "is_this_booth_for_personal_use_or_for_resale?": "resale",
        }
        cat, model = refine_facebook_product("智利", fields, "", rules=RULES)
        self.assertEqual(cat, "静音舱")
        self.assertEqual(model, "VRT-L")


if __name__ == "__main__":
    unittest.main()
