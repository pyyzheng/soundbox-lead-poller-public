"""子办负责人回填条件判断。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

spec = importlib.util.spec_from_file_location(
    "suboffice_fix",
    ROOT / "cloud-suboffice-assignee-fix.py",
)
suboffice_fix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(suboffice_fix)

from assignment_fields import (  # noqa: E402
    FIELD_ASSIGN_METHOD,
    FIELD_COUNTRY,
    FIELD_SUBOFFICE,
    FIELD_SUBOFFICE_OWNER,
)

RULES = {"新西兰": "Feoney", "美国": "Rita_USA"}


def _fields(**overrides):
    base = {
        FIELD_ASSIGN_METHOD: "自动",
        FIELD_SUBOFFICE: "是",
        FIELD_COUNTRY: "新西兰",
        FIELD_SUBOFFICE_OWNER: "",
    }
    base.update(overrides)
    return base


class TestNeedsSubofficeBackfill(unittest.TestCase):
    def test_backfills_missing_owner(self):
        self.assertEqual(
            suboffice_fix.needs_suboffice_backfill(_fields(), RULES),
            "Feoney",
        )

    def test_backfills_when_suboffice_is_option_id(self):
        self.assertEqual(
            suboffice_fix.needs_suboffice_backfill(
                _fields(**{FIELD_SUBOFFICE: ["opthA5jqMG"], FIELD_COUNTRY: "加拿大"}),
                {"加拿大": "Rita_USA"},
            ),
            "Rita_USA",
        )

    def test_skips_when_owner_correct(self):
        self.assertIsNone(
            suboffice_fix.needs_suboffice_backfill(
                _fields(**{FIELD_SUBOFFICE_OWNER: "Feoney"}),
                RULES,
            )
        )

    def test_skips_non_suboffice_country(self):
        self.assertIsNone(
            suboffice_fix.needs_suboffice_backfill(
                _fields(**{FIELD_SUBOFFICE: "否"}),
                RULES,
            )
        )

    def test_skips_manual_assignment(self):
        self.assertIsNone(
            suboffice_fix.needs_suboffice_backfill(
                _fields(**{FIELD_ASSIGN_METHOD: "人工"}),
                RULES,
            )
        )

    def test_backfills_bilingual_assign_method(self):
        fields = {
            "Allocation Method（分配方式）": "Automatic（自动）",
            FIELD_SUBOFFICE: "是",
            FIELD_COUNTRY: "美国",
            FIELD_SUBOFFICE_OWNER: "",
        }
        self.assertEqual(
            suboffice_fix.needs_suboffice_backfill(fields, {"美国": "Rita_USA"}),
            "Rita_USA",
        )

    def test_backfills_with_legacy_field_names(self):
        fields = {
            "分配方式": "自动",
            FIELD_SUBOFFICE: "是",
            FIELD_COUNTRY: "美国",
            FIELD_SUBOFFICE_OWNER: "",
        }
        self.assertEqual(
            suboffice_fix.needs_suboffice_backfill(fields, {"美国": "Rita_USA"}),
            "Rita_USA",
        )

    def test_skips_unknown_country(self):
        self.assertIsNone(
            suboffice_fix.needs_suboffice_backfill(
                _fields(**{FIELD_COUNTRY: "法国"}),
                RULES,
            )
        )


if __name__ == "__main__":
    unittest.main()
