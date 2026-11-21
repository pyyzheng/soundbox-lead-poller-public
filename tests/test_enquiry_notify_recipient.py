"""询盘更新提醒：非业务员跟进人也应能解析到收件人。"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest

os.environ.setdefault("FEISHU_APP_TOKEN", "test-app")
os.environ.setdefault("FEISHU_TABLE_ID", "test-table")

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lead_notify import resolve_enquiry_open_id  # noqa: E402


def _load_patch():
    path = os.path.join(ROOT, "scripts", "patch_enquiry_update_notify_workflow.py")
    spec = importlib.util.spec_from_file_location("patch_enquiry_update_notify_workflow", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnquiryRecipient(unittest.TestCase):
    def test_non_salesperson_uses_follower_map(self):
        fields = {"Owner（最终负责人）": "Derek"}
        open_id = resolve_enquiry_open_id(fields, {}, {"Derek": "ou_derek"})
        self.assertEqual(open_id, "ou_derek")

    def test_follower_user_field_beats_sales_map(self):
        fields = {
            "Follower（实际跟进人账号）": [{"id": "ou_follower"}],
            "Owner（最终负责人）": "Sue",
        }
        open_id = resolve_enquiry_open_id(fields, {"Sue": "ou_sue"}, {})
        self.assertEqual(open_id, "ou_follower")

    def test_error_assignee_is_not_notified(self):
        fields = {"Owner（最终负责人）": "未命中规则"}
        self.assertEqual(resolve_enquiry_open_id(fields, {}, {"未命中规则": "ou_x"}), "")

    def test_workflow_sends_when_only_follower_matches(self):
        patch = _load_patch()
        live = {
            "title": "询盘更新通知业务员",
            "steps": [
                {
                    "id": "trigEvBreo",
                    "type": "SetRecordTrigger",
                    "next": "old",
                    "data": {"condition_list": []},
                },
                {
                    "id": "act36Vyyk",
                    "type": "LarkMessageAction",
                    "title": "发送飞书消息",
                    "data": {},
                },
            ],
        }
        body = patch.patch_workflow(live)
        steps = {step["id"]: step for step in body["steps"]}
        groups = steps["trigEvBreo"]["data"]["condition_list"]
        watched = {
            cond["field_name"]
            for group in groups
            for cond in group["conditions"]
            if cond["operator"] == "isNotEmpty"
        }
        self.assertIn("Follower（实际跟进人账号）", watched)
        self.assertEqual(steps["actEnquirySwitch"]["data"]["mode"], "exclusive")
        links = steps["actEnquirySwitch"]["children"]["links"]
        self.assertEqual(links[1]["to"], "actEnquiryMsgFollower")
        self.assertIn(
            "actEnquiryFollower",
            steps["actEnquiryMsgFollower"]["data"]["receiver"][0]["value"],
        )
        self.assertEqual(
            steps["actEnquiryMsgFollower"]["data"]["title"][0]["value"],
            "询盘内容更新提醒",
        )


if __name__ == "__main__":
    unittest.main()
