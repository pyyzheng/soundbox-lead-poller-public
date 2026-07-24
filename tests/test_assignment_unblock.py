"""assignment-unblock 代理产品待确认处理。"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from assignment_fields import (  # noqa: E402
    FIELD_AGENT_ASSIGNEE,
    FIELD_AGENT_COUNTRY,
    FIELD_AGENT_PRODUCT,
    FIELD_ASSIGN_METHOD,
    FIELD_ENTRY_TIME,
    FIELD_LEAD_ID,
    FIELD_PRODUCT_CAT,
    FIELD_PRODUCT_MODEL,
    FIELD_QUEUE_KEY,
    FIELD_STATUS,
    FIELD_SUBOFFICE,
    FIELD_SUCCESS,
    WRITE_ASSIGN_AUTO,
)

spec = importlib.util.spec_from_file_location("unblock", ROOT / "cloud-assignment-unblock.py")
unblock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unblock)


class TestAgentProductClear(unittest.TestCase):
    def test_pending_confirm_needs_clear(self):
        fields = {
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_SUBOFFICE: "否",
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "待确认",
            FIELD_AGENT_ASSIGNEE: "",
            FIELD_PRODUCT_CAT: "Silence Booth 静音舱",
            FIELD_PRODUCT_MODEL: "无法识别",
        }
        self.assertTrue(unblock._needs_agent_product_clear(fields))

    def test_pending_confirm_with_option_ids(self):
        fields = {
            FIELD_ASSIGN_METHOD: ["opt8r8I1Re"],
            FIELD_SUBOFFICE: ["opteBbb8vv"],
            FIELD_AGENT_COUNTRY: ["optstg0Zdp"],
            FIELD_AGENT_PRODUCT: ["optJ7X2CIx"],
            FIELD_AGENT_ASSIGNEE: "",
            FIELD_PRODUCT_CAT: "Silence Booth 静音舱",
            FIELD_PRODUCT_MODEL: "无法识别",
        }
        self.assertTrue(unblock._needs_agent_product_clear(fields))

    def test_resolved_agent_product_skips(self):
        fields = {
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_SUBOFFICE: "否",
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "否",
            FIELD_AGENT_ASSIGNEE: "",
            FIELD_PRODUCT_CAT: "Silence Booth 静音舱",
            FIELD_PRODUCT_MODEL: "无法识别",
        }
        self.assertFalse(unblock._needs_agent_product_clear(fields))

    def test_agent_product_yes_without_assignee_needs_heal(self):
        """工作流只写了命中=是、未写业务员时，仍应回填。"""
        fields = {
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_SUBOFFICE: "否",
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "是",
            FIELD_AGENT_ASSIGNEE: "",
            FIELD_PRODUCT_CAT: "Silence Booth 静音舱",
            FIELD_PRODUCT_MODEL: "无法识别",
        }
        self.assertTrue(unblock._needs_agent_product_clear(fields))

    def test_agent_product_yes_with_assignee_skips(self):
        fields = {
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_SUBOFFICE: "否",
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "是",
            FIELD_AGENT_ASSIGNEE: "Cathy",
            FIELD_PRODUCT_CAT: "Silence Booth 静音舱",
            FIELD_PRODUCT_MODEL: "无法识别",
        }
        self.assertFalse(unblock._needs_agent_product_clear(fields))


class TestPendingAlertWindow(unittest.TestCase):
    def test_collects_pending_alert_when_age_in_window(self):
        now_ms = 1_000_000
        fields = {
            FIELD_ENTRY_TIME: now_ms - 10 * 60 * 1000,
            FIELD_LEAD_ID: "003999",
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "待确认",
            FIELD_SUBOFFICE: "否",
            FIELD_SUCCESS: "No（否）",
            FIELD_QUEUE_KEY: "谷歌|中东/非洲区队列",
            FIELD_STATUS: "⏳ 分配中/阻塞",
        }
        alerts = unblock._collect_pending_agent_confirm_alerts(
            [{"record_id": "rec_test", "fields": fields}],
            now_ms,
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], "rec_test")
        self.assertIn("003999", alerts[0][1])

    def test_skips_pending_alert_outside_window(self):
        now_ms = 1_000_000
        fields = {
            FIELD_ENTRY_TIME: now_ms - 20 * 60 * 1000,
            FIELD_LEAD_ID: "004000",
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "待确认",
            FIELD_SUBOFFICE: "否",
            FIELD_SUCCESS: "No（否）",
        }
        alerts = unblock._collect_pending_agent_confirm_alerts(
            [{"record_id": "rec_test2", "fields": fields}],
            now_ms,
        )
        self.assertEqual(alerts, [])

    def test_skips_when_alert_marker_exists(self):
        now_ms = 1_000_000
        fields = {
            FIELD_ENTRY_TIME: now_ms - 10 * 60 * 1000,
            FIELD_LEAD_ID: "004001",
            FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
            FIELD_AGENT_COUNTRY: "是",
            FIELD_AGENT_PRODUCT: "待确认",
            FIELD_SUBOFFICE: "否",
            FIELD_SUCCESS: "No（否）",
            "待确认超时告警时间": "2026-07-02 09:00:00 UTC",
        }
        alerts = unblock._collect_pending_agent_confirm_alerts(
            [{"record_id": "rec_test3", "fields": fields}],
            now_ms,
        )
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
