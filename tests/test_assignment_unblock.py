"""assignment-unblock 代理产品待确认处理。"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

spec = importlib.util.spec_from_file_location("unblock", ROOT / "cloud-assignment-unblock.py")
unblock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unblock)


class TestAgentProductClear(unittest.TestCase):
    def test_pending_confirm_needs_clear(self):
        fields = {
            "分配方式": "自动",
            "是否是子办国家": "否",
            "是否命中代理国家": "是",
            "是否命中代理产品": "待确认",
            "代理规则命中业务员": "",
            "Product Categories（产品大类）": "Silence Booth 静音舱",
            "Product model（具体型号）": "无法识别",
        }
        self.assertTrue(unblock._needs_agent_product_clear(fields))

    def test_pending_confirm_with_option_ids(self):
        fields = {
            "分配方式": ["opt8r8I1Re"],
            "是否是子办国家": ["opteBbb8vv"],
            "是否命中代理国家": ["optstg0Zdp"],
            "是否命中代理产品": ["optJ7X2CIx"],
            "代理规则命中业务员": "",
            "Product Categories（产品大类）": "Silence Booth 静音舱",
            "Product model（具体型号）": "无法识别",
        }
        self.assertTrue(unblock._needs_agent_product_clear(fields))

    def test_resolved_agent_product_skips(self):
        fields = {
            "分配方式": "自动",
            "是否是子办国家": "否",
            "是否命中代理国家": "是",
            "是否命中代理产品": "否",
            "代理规则命中业务员": "",
            "Product Categories（产品大类）": "Silence Booth 静音舱",
            "Product model（具体型号）": "无法识别",
        }
        self.assertFalse(unblock._needs_agent_product_clear(fields))


class TestPendingAlertWindow(unittest.TestCase):
    def test_collects_pending_alert_when_age_in_window(self):
        now_ms = 1_000_000
        fields = {
            "Entry Time（录入时间）": now_ms - 10 * 60 * 1000,
            "线索ID": "003999",
            "分配方式": "自动",
            "是否命中代理国家": "是",
            "是否命中代理产品": "待确认",
            "是否是子办国家": "否",
            "是否成功分配": "否",
            "队列Key": "谷歌|中东/非洲区队列",
            "分配状态": "⏳ 分配中/阻塞",
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
            "Entry Time（录入时间）": now_ms - 20 * 60 * 1000,
            "线索ID": "004000",
            "分配方式": "自动",
            "是否命中代理国家": "是",
            "是否命中代理产品": "待确认",
            "是否是子办国家": "否",
            "是否成功分配": "否",
        }
        alerts = unblock._collect_pending_agent_confirm_alerts(
            [{"record_id": "rec_test2", "fields": fields}],
            now_ms,
        )
        self.assertEqual(alerts, [])

    def test_skips_when_alert_marker_exists(self):
        now_ms = 1_000_000
        fields = {
            "Entry Time（录入时间）": now_ms - 10 * 60 * 1000,
            "线索ID": "004001",
            "分配方式": "自动",
            "是否命中代理国家": "是",
            "是否命中代理产品": "待确认",
            "是否是子办国家": "否",
            "是否成功分配": "否",
            "待确认超时告警时间": "2026-07-02 09:00:00 UTC",
        }
        alerts = unblock._collect_pending_agent_confirm_alerts(
            [{"record_id": "rec_test3", "fields": fields}],
            now_ms,
        )
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
