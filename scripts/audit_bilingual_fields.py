#!/usr/bin/env python3
"""审计代码与工作流 JSON 是否仍使用已废弃的主表字段名。

用法:
  python3 scripts/audit_bilingual_fields.py           # 静态扫描
  python3 scripts/audit_bilingual_fields.py --live  # 额外对照飞书线上字段表
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from assignment_fields import (  # noqa: E402
    FIELD_ASSIGNEE,
    FIELD_ASSIGN_METHOD,
    FIELD_ASSIGN_SOURCE,
    FIELD_LEAD_ID,
    FIELD_STATUS,
    FIELD_SUB_CHANNEL,
    FIELD_SUCCESS,
    FIELD_SYSTEM,
)

# 仅允许出现在 assignment_fields 别名定义与专门兼容测试中的旧名
DEPRECATED_FIELD_LITERALS: dict[str, str] = {
    "分配方式": FIELD_ASSIGN_METHOD,
    "是否成功分配": FIELD_SUCCESS,
    "分配状态": FIELD_STATUS,
    "最终分配的业务员": FIELD_ASSIGNEE,
    "系统匹配业务员": FIELD_SYSTEM,
    "线索ID": FIELD_LEAD_ID,
    "分配来源": FIELD_ASSIGN_SOURCE,
    "细分渠道（Channel segmentation）": FIELD_SUB_CHANNEL,
}

SCAN_GLOBS = (
    "cloud-*.py",
    "facebook-*.py",
    "lib/*.py",
    "scripts/*.py",
)

SKIP_FILES = {
    "lib/assignment_fields.py",
    "lib/workflow_bilingual.py",
    "scripts/audit_bilingual_fields.py",
    "scripts/build_dup_formulas.py",
    "scripts/align_rule_table_select_fields.py",
}

SKIP_TEST_PATH_PARTS = ("/tests/",)

API_MARKERS = ("field_name", "fields[", "fields.get(", "field_names", "FIELD_", '["fields"]')


def _should_scan(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel in SKIP_FILES:
        return False
    if any(part in rel for part in SKIP_TEST_PATH_PARTS):
        return False
    return True


def scan_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    issues: list[str] = []
    for old_name, new_name in DEPRECATED_FIELD_LITERALS.items():
        needle = f'"{old_name}"'
        if needle not in text:
            continue
        for line in text.splitlines():
            if needle not in line or line.strip().startswith("#"):
                continue
            if any(marker in line for marker in API_MARKERS):
                issues.append(f"{rel}: 仍使用旧字段名 {old_name!r} → 应改为 {new_name!r}")
                break
    return issues


def scan_workflows() -> list[str]:
    issues: list[str] = []
    for path in sorted((ROOT / "workflows").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        blob = json.dumps(data, ensure_ascii=False)
        for old_name, new_name in DEPRECATED_FIELD_LITERALS.items():
            if f'"field_name": "{old_name}"' in blob:
                issues.append(
                    f"{path.relative_to(ROOT)}: workflow 仍引用旧字段名 {old_name!r} → {new_name!r}"
                )
    return issues


def scan_live_fields() -> list[str]:
    from feishu_utils import feishu_api, get_feishu_token, require_env

    token = get_feishu_token()
    app_token = require_env("FEISHU_APP_TOKEN")
    table_id = require_env("FEISHU_TABLE_ID")
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=200"
    resp = feishu_api("GET", url, token=token)
    data = resp.json()
    if data.get("code") != 0:
        return [f"飞书字段表查询失败: {data}"]

    live_names = {f.get("field_name") for f in data.get("data", {}).get("items", [])}
    issues: list[str] = []
    for old_name, new_name in DEPRECATED_FIELD_LITERALS.items():
        if old_name in live_names:
            issues.append(f"飞书线上仍存在旧字段名 {old_name!r}（代码应使用 {new_name!r}）")
        elif new_name not in live_names:
            issues.append(f"飞书线上缺少预期字段 {new_name!r}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="对照飞书线上字段表")
    args = parser.parse_args()

    issues: list[str] = []
    for pattern in SCAN_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file() and _should_scan(path):
                issues.extend(scan_file(path))
    issues.extend(scan_workflows())
    if args.live:
        issues.extend(scan_live_fields())

    if issues:
        print("发现双语字段迁移问题:\n")
        for item in sorted(set(issues)):
            print(f"  - {item}")
        print(f"\n共 {len(set(issues))} 项")
        return 1

    print("audit ok: 未发现废弃主表字段名引用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
