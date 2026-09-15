#!/usr/bin/env python3
"""将子表业务员单选字段的 option id 对齐到主表（若 OpenAPI 允许）。

根因：渠道顺序队列表.业务员 与 主表.渠道顺序队列匹配业务员 选项名相同但 id 不同，
工作流 actnfeoNaFo 用 ref 跨表写入时会报「字段类型不匹配」。

注意：飞书 OpenAPI 更新已有单选选项 id 会返回 SingleSelectFieldPropertyError，
本脚本在 API 拒绝时会记录警告并跳过；实际修复见 patch_channel_rotation_workflow.py
（移除跨表 ref 写入，由 assignment-unblock 按名称写入）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from assignment_fields import AGENT_RULE_TABLE, CHANNEL_QUEUE_TABLE  # noqa: E402
from feishu_utils import FEISHU_APP_TOKEN, FEISHU_TABLE_ID, feishu_api, get_feishu_token  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync-assignee-options")

SUBOFFICE_RULE_TABLE = os.environ.get("FEISHU_SUBOFFICE_RULE_TABLE", "tblYQpLxEBYjFN0T")

# (table_id, field_name, canonical_main_field_name)
SYNC_TARGETS: tuple[tuple[str, str, str], ...] = (
    (CHANNEL_QUEUE_TABLE, "业务员", "渠道顺序队列匹配业务员"),
    (AGENT_RULE_TABLE, "业务员", "代理规则命中业务员"),
    (SUBOFFICE_RULE_TABLE, "负责人", "子办规则命中负责人"),
)


def _list_fields(token: str, table_id: str) -> dict[str, dict]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/fields"
    items = feishu_api("GET", url, token=token, max_retries=3).json().get("data", {}).get("items", [])
    return {f["field_name"]: f for f in items}


def _merge_options(canonical: list[dict], target: list[dict]) -> tuple[list[dict], int]:
    """按主表 option id 重建目标字段 options；保留目标表多出的选项。"""
    canon_by_name = {o["name"]: o for o in canonical}
    target_by_name = {o["name"]: o for o in target}
    merged: list[dict] = []
    realigned = 0

    for name, opt in canon_by_name.items():
        merged.append(
            {
                "id": opt["id"],
                "name": name,
                "color": opt.get("color", target_by_name.get(name, {}).get("color", 0)),
            }
        )
        if target_by_name.get(name, {}).get("id") != opt["id"]:
            realigned += 1

    for name, opt in target_by_name.items():
        if name not in canon_by_name:
            merged.append({"id": opt["id"], "name": name, "color": opt.get("color", 0)})

    return merged, realigned


def sync_field(token: str, table_id: str, field_name: str, canonical_options: list[dict], dry_run: bool) -> int:
    fields = _list_fields(token, table_id)
    field = fields.get(field_name)
    if not field:
        log.warning("跳过：表 %s 无字段 %s", table_id, field_name)
        return 0

    target_options = field.get("property", {}).get("options", [])
    merged, realigned = _merge_options(canonical_options, target_options)
    if realigned == 0:
        log.info("已对齐 %s.%s（无需更新）", table_id, field_name)
        return 0

    log.info("将更新 %s.%s：%d 个选项 id 与主表对齐", table_id, field_name, realigned)
    if dry_run:
        return realigned

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{table_id}/fields/{field['field_id']}"
    )
    body = {
        "field_name": field_name,
        "type": field["type"],
        "property": {"options": merged},
    }
    resp = feishu_api("PUT", url, token=token, json=body, max_retries=3).json()
    if resp.get("code") != 0:
        if resp.get("code") == 1254082:
            log.warning(
                "OpenAPI 不允许修改 %s.%s 的 option id（SingleSelectFieldPropertyError），已跳过",
                table_id,
                field_name,
            )
            return 0
        raise RuntimeError(f"更新字段失败 {table_id}.{field_name}: {resp}")
    return realigned


def run(dry_run: bool) -> int:
    token = get_feishu_token()
    main_fields = _list_fields(token, FEISHU_TABLE_ID)
    total = 0

    for table_id, field_name, canonical_name in SYNC_TARGETS:
        canonical = main_fields.get(canonical_name, {}).get("property", {}).get("options", [])
        if not canonical:
            log.warning("主表缺少字段 %s，跳过 %s", canonical_name, table_id)
            continue
        total += sync_field(token, table_id, field_name, canonical, dry_run)

    log.info("完成 realigned=%s dry_run=%s", total, dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="对齐业务员单选字段 option id 到主表")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入飞书")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
