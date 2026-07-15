"""Duplicate 公式字段工作流条件改写。"""

from __future__ import annotations

import unittest

from workflow_bilingual import (
    fix_duplicate_formula_in_workflow,
    rewrite_duplicate_option_filters,
)


class DuplicateFormulaFilterTest(unittest.TestCase):
    def test_rewrites_does_not_contain_any_options(self):
        conds = [
            {
                "field_name": "Duplicate（重复）",
                "operator": "doesNotContainAny",
                "value": [
                    {"value": {"id": "opt1", "name": "查重命中"}, "value_type": "option"},
                    {"value": {"id": "opt2", "name": "查重冲突"}, "value_type": "option"},
                ],
            },
            {"field_name": "队列Key", "operator": "isNotEmpty", "value": []},
        ]
        out = rewrite_duplicate_option_filters(conds)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["operator"], "isNot")
        self.assertEqual(out[0]["value"][0]["value_type"], "option")
        self.assertEqual(out[0]["value"][0]["value"]["name"], "查重命中")
        self.assertNotIn("id", out[0]["value"][0]["value"])
        self.assertEqual(out[1]["value"][0]["value"]["name"], "查重冲突")
        self.assertEqual(out[2]["field_name"], "队列Key")

    def test_fix_workflow_trigger(self):
        body = {
            "title": "t",
            "steps": [
                {
                    "type": "SetRecordTrigger",
                    "data": {
                        "condition_list": [
                            {
                                "conjunction": "and",
                                "conditions": [
                                    {
                                        "field_name": "分配来源",
                                        "operator": "doesNotContainAny",
                                        "value": [
                                            {
                                                "value": {"name": "查重命中"},
                                                "value_type": "option",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                }
            ],
        }
        fixed = fix_duplicate_formula_in_workflow(body)
        conds = fixed["steps"][0]["data"]["condition_list"][0]["conditions"]
        self.assertEqual(conds[0]["field_name"], "Duplicate（重复）")
        self.assertEqual(conds[0]["operator"], "isNot")
        self.assertEqual(conds[0]["value"][0]["value_type"], "option")
        self.assertEqual(conds[0]["value"][0]["value"]["name"], "查重命中")


if __name__ == "__main__":
    unittest.main()
