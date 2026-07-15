"""Integration-style simulation for assignment unblock eligibility."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from channel_queue_assign import (
    eligible_for_channel_queue,
    parse_channel_queue_map,
    parse_queue_pointers,
    pick_queue_assignee,
)


class TestAssignmentSimulation(unittest.TestCase):
  """模拟谷歌线索 003197 同类数据：公式就绪后应能兜底分到队列业务员。"""

  def test_google_lead_queue_pick_after_lookup_pointer(self):
      pointers = parse_queue_pointers(
          [
              {
                  "record_id": "recptr",
                  "fields": {
                      "队列Key": "谷歌|欧洲区队列",
                      "当前顺序号": 2,
                      "最大顺序号": {"type": 2, "value": [3]},
                  },
              }
          ]
      )
      queue_map = parse_channel_queue_map(
          [
              {"fields": {"队列Key": "谷歌|欧洲区队列", "顺位": 1, "业务员": "Sue"}},
              {"fields": {"队列Key": "谷歌|欧洲区队列", "顺位": 2, "业务员": "Kaka"}},
              {"fields": {"队列Key": "谷歌|欧洲区队列", "顺位": 3, "业务员": "Snow"}},
          ]
      )
      fields = {
          "分配方式": "自动",
          "Dup Formula Ready（公式查重就绪）": "是",
          "分配来源": "无重复",
          "是否是子办国家": "否",
          "是否命中代理国家": "否",
          "是否满足渠道轮转": "是",
          "是否成功分配": "否",
          "队列Key": "谷歌|欧洲区队列",
          "系统匹配业务员": "未命中规则",
          "渠道顺序队列匹配业务员": "",
      }
      self.assertTrue(eligible_for_channel_queue(fields))
      pick = pick_queue_assignee("谷歌|欧洲区队列", pointers, queue_map)
      self.assertIsNotNone(pick)
      assert pick is not None
      self.assertEqual(pick.assignee, "Kaka")
      self.assertEqual(pick.used_rank, 2)
      self.assertEqual(pick.next_rank, 3)

  def test_agent_product_pending_confirm_blocks_until_cleared(self):
      blocked = {
          "分配方式": "自动",
          "Dup Formula Ready（公式查重就绪）": "是",
          "分配来源": "无重复",
          "是否是子办国家": "否",
          "是否命中代理国家": "是",
          "是否命中代理产品": "待确认",
          "队列Key": "谷歌|中东/非洲区队列",
          "系统匹配业务员": "未命中规则",
          "渠道顺序队列匹配业务员": "",
      }
      self.assertFalse(eligible_for_channel_queue(blocked))
      cleared = {**blocked, "是否命中代理产品": "否", "是否满足渠道轮转": "是"}
      self.assertTrue(eligible_for_channel_queue(cleared))


if __name__ == "__main__":
    unittest.main()
