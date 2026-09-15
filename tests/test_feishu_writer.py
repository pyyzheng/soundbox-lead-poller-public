"""Tests for Gmail Feishu record creation defaults."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
os.environ.setdefault("FEISHU_APP_TOKEN", "test_app")
os.environ.setdefault("FEISHU_TABLE_ID", "tbltest")

from assignment_fields import FIELD_ASSIGN_METHOD, FIELD_SUCCESS, WRITE_ASSIGN_AUTO, WRITE_SUCCESS_NO  # noqa: E402
from feishu_writer import create_feishu_record  # noqa: E402


class TestCreateFeishuRecord(unittest.TestCase):
    @patch("feishu_writer.requests.post")
    def test_writes_assignment_prerequisites(self, mock_post: MagicMock):
        mock_post.return_value.json.return_value = {"code": 0, "data": {"record": {"record_id": "rec1"}}}
        mock_post.return_value.status_code = 200

        result = create_feishu_record("token", "hello enquiry")

        self.assertEqual(result.get("code"), 0)
        sent = mock_post.call_args.kwargs["json"]["fields"]
        self.assertEqual(sent[FIELD_ASSIGN_METHOD], WRITE_ASSIGN_AUTO)
        self.assertEqual(sent[FIELD_SUCCESS], WRITE_SUCCESS_NO)
        self.assertEqual(sent["Enquiry details（询盘内容）"], "hello enquiry")


if __name__ == "__main__":
    unittest.main()
