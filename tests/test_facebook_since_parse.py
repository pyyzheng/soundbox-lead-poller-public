"""facebook-lead-poller since ISO 解析与客户端时间过滤。"""

from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _load_poller():
    path = Path(__file__).resolve().parents[1] / "facebook-lead-poller.py"
    spec = importlib.util.spec_from_file_location("fb_poller_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    # Avoid require_env exit: inject before exec
    import os

    os.environ.setdefault("FEISHU_APP_TOKEN", "test_token")
    os.environ.setdefault("FEISHU_TABLE_ID", "test_table")
    spec.loader.exec_module(mod)
    return mod


class FacebookSinceParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_poller()

    def test_parse_plus0000(self):
        dt = self.mod.parse_iso_datetime("2026-07-13T00:00:00+0000")
        self.assertIsNotNone(dt)
        self.assertEqual(dt, datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc))

    def test_parse_plus00_00(self):
        dt = self.mod.parse_iso_datetime("2026-07-13T00:00:00+00:00")
        self.assertEqual(dt, datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc))

    def test_parse_meta_created_time(self):
        dt = self.mod.parse_iso_datetime("2026-07-14T01:06:08+0000")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.hour, 1)


if __name__ == "__main__":
    unittest.main()
