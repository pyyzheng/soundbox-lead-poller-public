"""Unit tests for Facebook Meta↔Feishu gap helpers."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_monitor():
    os.environ.setdefault("FEISHU_APP_TOKEN", "tok")
    os.environ.setdefault("FEISHU_TABLE_ID", "tbl")
    path = Path(__file__).resolve().parents[1] / "cloud-facebook-gap-monitor.py"
    spec = importlib.util.spec_from_file_location("fb_gap_monitor_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Avoid importing feishu_writer.require_env side effects beyond setdefault above
    with patch.dict(os.environ, {"FEISHU_APP_TOKEN": "tok", "FEISHU_TABLE_ID": "tbl"}):
        spec.loader.exec_module(mod)
    return mod


class FacebookGapMonitorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_monitor()

    def test_parse_field_data(self):
        fields = self.mod.parse_field_data(
            [
                {"name": "full_name", "values": ["Ada"]},
                {"name": "email", "values": ["a@b.com"]},
            ]
        )
        self.assertEqual(fields["full_name"], "Ada")
        self.assertEqual(fields["email"], "a@b.com")

    def test_find_gaps_filters_existing(self):
        meta = [
            {"leadgen_id": "111", "name": "A"},
            {"leadgen_id": "222", "name": "B"},
            {"leadgen_id": "333", "name": "C"},
        ]

        def fake_dup(_token, lid):
            return {"record_id": "rec"} if lid == "222" else None

        with patch.object(self.mod, "check_feishu_fb_leadgen_duplicate", side_effect=fake_dup):
            gaps = self.mod.find_gaps("token", meta)
        self.assertEqual([g["leadgen_id"] for g in gaps], ["111", "333"])


if __name__ == "__main__":
    unittest.main()
