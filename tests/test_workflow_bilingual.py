"""workflow_bilingual 单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from assignment_fields import FIELD_ASSIGN_METHOD, FIELD_SUCCESS  # noqa: E402
from workflow_bilingual import migrate_workflow_document  # noqa: E402


class TestWorkflowBilingual(unittest.TestCase):
    def test_migrates_field_names_and_options(self):
        body = {
            "steps": [
                {
                    "data": {
                        "condition_list": [
                            {
                                "conditions": [
                                    {
                                        "field_name": "分配方式",
                                        "operator": "is",
                                        "value": [{"value": {"name": "自动"}, "value_type": "option"}],
                                    },
                                    {
                                        "field_name": "是否成功分配",
                                        "value": [{"value": {"name": "否"}, "value_type": "option"}],
                                    },
                                ]
                            }
                        ],
                        "field_values": [
                            {
                                "field_name": "是否成功分配",
                                "value": [{"value": {"name": "是"}, "value_type": "option"}],
                            }
                        ],
                    }
                }
            ]
        }
        migrate_workflow_document(body)
        conds = body["steps"][0]["data"]["condition_list"][0]["conditions"]
        self.assertEqual(conds[0]["field_name"], FIELD_ASSIGN_METHOD)
        self.assertEqual(conds[0]["value"][0]["value"]["name"], "Automatic（自动）")
        self.assertEqual(conds[1]["field_name"], FIELD_SUCCESS)
        self.assertEqual(conds[1]["value"][0]["value"]["name"], "No（否）")
        success = body["steps"][0]["data"]["field_values"][0]
        self.assertEqual(success["field_name"], FIELD_SUCCESS)
        self.assertEqual(success["value"][0]["value"]["name"], "Yes（是）")


if __name__ == "__main__":
    unittest.main()
