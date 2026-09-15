#!/usr/bin/env python3
"""测试 Phase 4: 客户回复追踪 — threadId 检测 + 写回逻辑"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# 确保可导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from feishu_utils import find_record_by_thread_id, update_feishu_autoreply


class TestFindRecordByThreadId(unittest.TestCase):
    """find_record_by_thread_id: 按 Gmail_Thread_ID 查找飞书记录"""

    @patch("feishu_utils.feishu_api")
    def test_found(self, mock_api):
        """匹配到已有记录时返回 record_id"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"items": [{"record_id": "rec123"}], "has_more": False},
        }
        mock_api.return_value = mock_resp

        result = find_record_by_thread_id("fake_token", "thread_abc")
        self.assertEqual(result, {"record_id": "rec123"})
        mock_api.assert_called_once()

    @patch("feishu_utils.feishu_api")
    def test_not_found(self, mock_api):
        """查不到时返回 None"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"items": [], "has_more": False},
        }
        mock_api.return_value = mock_resp

        result = find_record_by_thread_id("fake_token", "thread_nonexist")
        self.assertIsNone(result)

    def test_empty_thread_id(self):
        """空 threadId 直接返回 None，不发 API 请求"""
        result = find_record_by_thread_id("fake_token", "")
        self.assertIsNone(result)

    @patch("feishu_utils.feishu_api")
    def test_api_error(self, mock_api):
        """API 返回错误时返回 None（不抛异常）"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": -1, "msg": "internal error"}
        mock_api.return_value = mock_resp

        result = find_record_by_thread_id("fake_token", "thread_err")
        self.assertIsNone(result)


class TestUpdateFeishuAutoreply(unittest.TestCase):
    """update_feishu_autoreply: 更新自动回复状态 + threadId"""

    @patch("feishu_utils.feishu_api")
    def test_write_thread_id(self, mock_api):
        """写入 threadId 时字段包含 Gmail_Thread_ID"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_api.return_value = mock_resp

        ok = update_feishu_autoreply(
            "fake_token", "rec123", "Sent",
            sent_at="2026-05-14T12:00:00Z", thread_id="thread_abc",
        )
        self.assertTrue(ok)

        # 检查 PUT 请求中 fields 包含 Gmail_Thread_ID
        call_kwargs = mock_api.call_args
        fields = call_kwargs.kwargs.get("json", {}).get("fields", {})
        self.assertEqual(fields.get("Gmail_Thread_ID"), "thread_abc")
        self.assertEqual(fields.get("Auto-Reply Status"), "Sent")

    @patch("feishu_utils.feishu_api")
    def test_no_thread_id(self, mock_api):
        """不传 threadId 时不包含该字段"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_api.return_value = mock_resp

        ok = update_feishu_autoreply("fake_token", "rec123", "Customer-Replied")
        self.assertTrue(ok)

        fields = mock_api.call_args.kwargs.get("json", {}).get("fields", {})
        self.assertNotIn("Gmail_Thread_ID", fields)


class TestWorkerThreadIdWriteback(unittest.TestCase):
    """Worker process_record 的 threadId 写回逻辑（直接测试 feishu_utils 函数）"""

    @patch("feishu_utils.feishu_api")
    def test_dry_run_thread_id(self, mock_api):
        """dry-run 模式应写入原始邮件的 threadId"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_api.return_value = mock_resp

        # 模拟 Worker dry-run 场景：传入 orig_thread_id
        ok = update_feishu_autoreply(
            "fake_token", "rec1", "Dry-Run",
            thread_id="orig_thread_123",
        )
        self.assertTrue(ok)
        fields = mock_api.call_args.kwargs.get("json", {}).get("fields", {})
        self.assertEqual(fields["Gmail_Thread_ID"], "orig_thread_123")

    @patch("feishu_utils.feishu_api")
    def test_sent_thread_id(self, mock_api):
        """实际发送后应写入 sent_thread_id"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_api.return_value = mock_resp

        ok = update_feishu_autoreply(
            "fake_token", "rec1", "Sent",
            sent_at="2026-05-14T12:00:00Z",
            thread_id="sent_thread_456",
        )
        self.assertTrue(ok)
        fields = mock_api.call_args.kwargs.get("json", {}).get("fields", {})
        self.assertEqual(fields["Gmail_Thread_ID"], "sent_thread_456")


if __name__ == "__main__":
    unittest.main()
