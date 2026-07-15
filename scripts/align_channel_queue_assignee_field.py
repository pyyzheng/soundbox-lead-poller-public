#!/usr/bin/env python3
"""将渠道顺序队列表.业务员 改为引用主表「渠道顺序队列匹配业务员」的动态选项。

飞书 OpenAPI 无法修改已有单选 option id；动态选项源可使子表与主表共用同一套选项 id，
渠道轮转工作流跨表 ref 写入不再报类型不匹配。

步骤：备份 → 重命名旧字段 → 新建动态选项字段 → 回填 64 条记录 → 删除旧字段
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from assignment_fields import CHANNEL_QUEUE_TABLE, FIELD_QUEUE_ASSIGNEE  # noqa: E402
from feishu_utils import FEISHU_APP_TOKEN, FEISHU_TABLE_ID, extract_text, feishu_api, get_feishu_token  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("align-queue-assignee")

MAIN_TABLE = FEISHU_TABLE_ID
MAIN_FIELD_ID = "fld4Uk8KfA"
OLD_FIELD_ID = "fldNAEpBXi"
OLD_FIELD_NAME = "业务员"
LEGACY_FIELD_NAME = "业务员_待删除"
BASE_TOKEN = os.environ.get("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)


def _lark(args: list[str], *, dry_run: bool = False) -> dict:
    cmd = ["lark-cli", "base", *args, "--base-token", BASE_TOKEN, "--format", "json"]
    if dry_run:
        cmd.append("--dry-run")
    raw = subprocess.check_output(cmd, text=True, cwd=ROOT)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload


def _backup_records(token: str) -> list[tuple[str, str]]:
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{CHANNEL_QUEUE_TABLE}/records/search"
    )
    items: list[dict] = []
    page_token = ""
    while True:
        u = url + (f"?page_token={page_token}" if page_token else "")
        data = feishu_api(
            "POST",
            u,
            token=token,
            json={"field_names": [OLD_FIELD_NAME, "队列Key", "顺位"], "page_size": 500},
        ).json()["data"]
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    backup: list[tuple[str, str]] = []
    for item in items:
        name = extract_text(item.get("fields", {}).get(OLD_FIELD_NAME, ""))
        if name:
            backup.append((item["record_id"], name))
    log.info("已备份 %d 条含业务员记录", len(backup))
    return backup


def _rename_legacy_field() -> None:
    field = _lark(["+field-get", "--table-id", CHANNEL_QUEUE_TABLE, "--field-id", OLD_FIELD_ID])[
        "data"
    ]["field"]
    options = field.get("options") or []
    body = {
        "name": LEGACY_FIELD_NAME,
        "type": "select",
        "multiple": False,
        "options": options,
    }
    _lark(
        [
            "+field-update",
            "--table-id",
            CHANNEL_QUEUE_TABLE,
            "--field-id",
            OLD_FIELD_ID,
            "--json",
            json.dumps(body, ensure_ascii=False),
            "--yes",
        ]
    )
    log.info("已重命名旧字段 → %s", LEGACY_FIELD_NAME)


def _create_dynamic_field() -> str:
    body = {
        "name": OLD_FIELD_NAME,
        "type": "select",
        "multiple": False,
        "dynamic_options_source": {
            "table_id": MAIN_TABLE,
            "field_id": MAIN_FIELD_ID,
        },
    }
    resp = _lark(
        [
            "+field-create",
            "--table-id",
            CHANNEL_QUEUE_TABLE,
            "--json",
            json.dumps(body, ensure_ascii=False),
        ]
    )
    new_id = resp["data"]["field"]["id"]
    log.info("已创建动态选项字段 %s id=%s", OLD_FIELD_NAME, new_id)
    return new_id


def _restore_records(token: str, backup: list[tuple[str, str]]) -> None:
    for i, (record_id, assignee) in enumerate(backup, 1):
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{CHANNEL_QUEUE_TABLE}/records/{record_id}"
        )
        resp = feishu_api(
            "PUT",
            url,
            token=token,
            json={"fields": {OLD_FIELD_NAME: assignee}},
            max_retries=3,
        ).json()
        if resp.get("code") != 0:
            raise RuntimeError(f"回填失败 {record_id} {assignee}: {resp}")
        if i % 20 == 0:
            log.info("回填进度 %d/%d", i, len(backup))
        time.sleep(0.05)
    log.info("已回填 %d 条记录", len(backup))


def _delete_legacy_field() -> None:
    _lark(
        [
            "+field-delete",
            "--table-id",
            CHANNEL_QUEUE_TABLE,
            "--field-id",
            LEGACY_FIELD_NAME,
            "--yes",
        ]
    )
    log.info("已删除旧字段 %s", LEGACY_FIELD_NAME)


def _verify_option_ids(token: str) -> int:
    def opts(table_id: str, field_name: str) -> dict[str, str]:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/fields"
        items = feishu_api("GET", url, token=token).json()["data"]["items"]
        f = next(x for x in items if x["field_name"] == field_name)
        return {o["name"]: o["id"] for o in f.get("property", {}).get("options", [])}

    main = opts(MAIN_TABLE, FIELD_QUEUE_ASSIGNEE)
    sub = opts(CHANNEL_QUEUE_TABLE, OLD_FIELD_NAME)
    mismatch = [n for n in main if n in sub and main[n] != sub[n]]
    log.info("验证: 主表 %d 项, 子表 %d 项, id 不一致 %d 项", len(main), len(sub), len(mismatch))
    if mismatch:
        log.warning("仍不一致: %s", mismatch[:5])
    return len(mismatch)


def main() -> int:
    if not BASE_TOKEN:
        log.error("缺少 FEISHU_APP_TOKEN")
        return 1
    token = get_feishu_token()
    backup = _backup_records(token)
    _rename_legacy_field()
    new_field_id = _create_dynamic_field()
    _restore_records(token, backup)
    _delete_legacy_field()
    remaining = _verify_option_ids(token)
    print(f"NEW_FIELD_ID={new_field_id}")
    print(f"ID_MISMATCH_REMAINING={remaining}")
    return 0 if remaining == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
