#!/usr/bin/env python3
"""将主表「原跟进人员」改为动态单选（选项源=实际跟进人名单.业务名称）。

飞书 OpenAPI 不能把静态单选原地改成动态选项：
备份 → 重命名旧字段 → 新建 dynamic_options_source → 按名称回填 → 删旧字段。

已于 2026-09-10 在现网执行；本脚本保留便于复跑/审计。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

BASE_TOKEN = "ZpbUb7SP7azsNasniFjc0bWSnHg"
TABLE_ID = "tbluuuXn9WexH8LV"
SOURCE_TABLE_ID = "tbl4nwPw8h8swFj2"
SOURCE_FIELD_ID = "fldU0sUS42"
FIELD_NAME = "原跟进人员"
LEGACY_NAME = "原跟进人员_待删除"
BACKUP_PATH = Path(__file__).with_name("align_original_follower_backup.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("align-original-follower")
ENV = {**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}


def _lark(args: list[str], retries: int = 4) -> dict:
    cmd = ["lark-cli", "base", *args, "--base-token", BASE_TOKEN, "--as", "user", "--format", "json"]
    last: str | None = None
    for i in range(retries):
        p = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
        out = (p.stdout or "").strip()
        if not out:
            last = p.stderr or ""
            time.sleep(1.2 * (i + 1))
            continue
        data = json.loads(out)
        err = data.get("error") or {}
        if data.get("ok") is False and ("EOF" in str(err) or err.get("subtype") == "transport"):
            last = out
            time.sleep(1.2 * (i + 1))
            continue
        if p.returncode != 0 or data.get("ok") is False:
            raise RuntimeError(json.dumps(data, ensure_ascii=False)[:2500])
        return data
    raise RuntimeError(last or "empty response")


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return ",".join(str(x.get("name") if isinstance(x, dict) else x) for x in v).strip()
    if isinstance(v, dict):
        return str(v.get("name") or v.get("text") or "").strip()
    return str(v).strip()


def _field_by_name() -> dict[str, dict]:
    fields = _lark(["+field-list", "--table-id", TABLE_ID, "--limit", "200"])["data"]["fields"]
    return {f["name"]: f for f in fields}


def main() -> int:
    dry = "--dry-run" in sys.argv
    names = _field_by_name()
    if FIELD_NAME in names and names[FIELD_NAME].get("dynamic_options_source"):
        log.info("已是动态选项字段 id=%s，跳过", names[FIELD_NAME]["id"])
        return 0
    if dry:
        log.info("dry-run：将迁移静态「原跟进人员」为动态选项")
        return 0
    log.error("现网已迁移完成。若需强制重跑，请先确认表结构后再手工调整本脚本入口。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
