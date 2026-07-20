#!/usr/bin/env python3
"""将规则子表「国家」改为引用主表 Country（国家）的动态选项。

根因：子办/代理规则表的国家单选与主表 Country 选项名相同但 option id 不同，
工作流 FindRecord 用主表 Country ref 过滤时会报「字段类型不匹配」。

飞书 OpenAPI 不能直接改已有选项 id；正确做法与业务员字段对齐相同：
备份 → 重命名旧字段 → 新建 dynamic_options_source → 按名称回填 → 删旧字段。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from assignment_fields import AGENT_RULE_TABLE, FIELD_COUNTRY  # noqa: E402
from feishu_utils import FEISHU_APP_TOKEN, FEISHU_TABLE_ID, extract_text, feishu_api, get_feishu_token  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("align-country")

BASE_TOKEN = os.environ.get("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
SUBOFFICE_RULE_TABLE = os.environ.get("FEISHU_SUBOFFICE_RULE_TABLE", "tblYQpLxEBYjFN0T")
MAIN_COUNTRY_FIELD_ID = "fldAEhwYJU"


@dataclass(frozen=True)
class AlignSpec:
    table_id: str
    table_label: str
    field_name: str
    old_field_id: str
    legacy_field_name: str


SPECS = (
    AlignSpec(
        table_id=SUBOFFICE_RULE_TABLE,
        table_label="子办分配规则表",
        field_name="国家",
        old_field_id="fldprNaDcy",
        legacy_field_name="国家_待删除",
    ),
    AlignSpec(
        table_id=AGENT_RULE_TABLE,
        table_label="代理优先规则表",
        field_name="国家",
        old_field_id="fldqTJjAD7",
        legacy_field_name="国家_待删除",
    ),
)

# 对齐后必须同步工作流里对旧 field id 的引用（FindRecord filter 等）
WORKFLOW_FIELD_FIXES: tuple[tuple[str, str, str], ...] = (
    # (workflow_id, local json name, stale_field_id)
    ("wkfKWPVBWT0NisJV", "wkfKWPVBWT0NisJV-代理区域分配自动化.json", "fldqTJjAD7"),
    ("wkfaNTuMd6vAE5E0", "wkfaNTuMd6vAE5E0-子办规则分配自动化.json", "fldprNaDcy"),
)


def _lark(args: list[str]) -> dict:
    cmd = ["lark-cli", "base", *args, "--base-token", BASE_TOKEN, "--format", "json", "--as", "user"]
    raw = subprocess.check_output(cmd, text=True, cwd=ROOT)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload


def _list_fields(token: str, table_id: str) -> dict[str, dict]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/fields"
    return {
        item["field_name"]: item
        for item in feishu_api("GET", url, token=token).json()["data"]["items"]
    }


def _backup_records(token: str, spec: AlignSpec) -> list[tuple[str, str]]:
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{spec.table_id}/records/search"
    )
    items: list[dict] = []
    page_token = ""
    while True:
        u = url + (f"?page_token={page_token}" if page_token else "")
        data = feishu_api(
            "POST",
            u,
            token=token,
            json={"field_names": [spec.field_name], "page_size": 500},
        ).json()["data"]
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    backup = [
        (item["record_id"], extract_text(item.get("fields", {}).get(spec.field_name, "")))
        for item in items
        if extract_text(item.get("fields", {}).get(spec.field_name, ""))
    ]
    log.info("[%s] 备份 %d 条国家值", spec.table_label, len(backup))
    return backup


def _backup_pending(token: str, spec: AlignSpec) -> list[tuple[str, str]]:
    fields = _list_fields(token, spec.table_id)
    if spec.legacy_field_name not in fields:
        return []
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{spec.table_id}/records/search"
    )
    items: list[dict] = []
    page_token = ""
    while True:
        u = url + (f"?page_token={page_token}" if page_token else "")
        data = feishu_api(
            "POST",
            u,
            token=token,
            json={
                "field_names": [spec.field_name, spec.legacy_field_name],
                "page_size": 500,
            },
        ).json()["data"]
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    pending: list[tuple[str, str]] = []
    for item in items:
        row = item.get("fields", {})
        if extract_text(row.get(spec.field_name)):
            continue
        legacy = extract_text(row.get(spec.legacy_field_name, ""))
        if legacy:
            pending.append((item["record_id"], legacy))
    return pending


def _rename_legacy(spec: AlignSpec, token: str) -> None:
    fields = _list_fields(token, spec.table_id)
    if spec.legacy_field_name in fields:
        log.info("[%s] 跳过重命名（%s 已存在）", spec.table_label, spec.legacy_field_name)
        return
    if spec.field_name not in fields and spec.old_field_id not in {
        f["field_id"] for f in fields.values()
    }:
        log.info("[%s] 跳过重命名（原字段已不存在）", spec.table_label)
        return
    field_id = fields.get(spec.field_name, {}).get("field_id", spec.old_field_id)
    field = _lark(["+field-get", "--table-id", spec.table_id, "--field-id", field_id])["data"]["field"]
    body = {
        "name": spec.legacy_field_name,
        "type": "select",
        "multiple": False,
        "options": field.get("options") or [],
    }
    _lark(
        [
            "+field-update",
            "--table-id",
            spec.table_id,
            "--field-id",
            field_id,
            "--json",
            json.dumps(body, ensure_ascii=False),
            "--yes",
        ]
    )
    log.info("[%s] 已重命名 %s → %s", spec.table_label, spec.field_name, spec.legacy_field_name)


def _create_dynamic(spec: AlignSpec, token: str) -> str:
    fields = _list_fields(token, spec.table_id)
    if spec.field_name in fields:
        existing = fields[spec.field_name]["field_id"]
        log.info("[%s] 复用已有字段 %s id=%s", spec.table_label, spec.field_name, existing)
        return existing
    body = {
        "name": spec.field_name,
        "type": "select",
        "multiple": False,
        "dynamic_options_source": {
            "table_id": FEISHU_TABLE_ID,
            "field_id": MAIN_COUNTRY_FIELD_ID,
        },
    }
    resp = _lark(
        [
            "+field-create",
            "--table-id",
            spec.table_id,
            "--json",
            json.dumps(body, ensure_ascii=False),
        ]
    )
    new_id = resp["data"]["field"]["id"]
    log.info("[%s] 已创建动态选项字段 %s id=%s", spec.table_label, spec.field_name, new_id)
    return new_id


def _restore_records(token: str, spec: AlignSpec, backup: list[tuple[str, str]]) -> None:
    for i, (record_id, value) in enumerate(backup, 1):
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{spec.table_id}/records/{record_id}"
        )
        resp = feishu_api(
            "PUT",
            url,
            token=token,
            json={"fields": {spec.field_name: value}},
            max_retries=3,
        ).json()
        if resp.get("code") != 0:
            raise RuntimeError(f"回填失败 {spec.table_label} {record_id}={value}: {resp}")
        if i % 10 == 0 or i == len(backup):
            log.info("[%s] 回填 %d/%d", spec.table_label, i, len(backup))
        time.sleep(0.05)


def _delete_legacy(spec: AlignSpec, token: str) -> None:
    fields = _list_fields(token, spec.table_id)
    if spec.legacy_field_name not in fields:
        log.info("[%s] 无待删除字段 %s", spec.table_label, spec.legacy_field_name)
        return
    _lark(
        [
            "+field-delete",
            "--table-id",
            spec.table_id,
            "--field-id",
            spec.legacy_field_name,
            "--yes",
        ]
    )
    log.info("[%s] 已删除 %s", spec.table_label, spec.legacy_field_name)


def _verify(token: str, spec: AlignSpec) -> int:
    def opts(table_id: str, field_name: str) -> dict[str, str]:
        fields = _list_fields(token, table_id)
        f = fields[field_name]
        return {o["name"]: o["id"] for o in (f.get("property") or {}).get("options") or []}

    main = opts(FEISHU_TABLE_ID, FIELD_COUNTRY)
    sub = opts(spec.table_id, spec.field_name)
    shared = set(main) & set(sub)
    mismatch = sorted(n for n in shared if main[n] != sub[n])
    log.info(
        "[%s] 与主表共享 %d 项，id 不一致 %d 项（子表选项总数 %d）",
        spec.table_label,
        len(shared),
        len(mismatch),
        len(sub),
    )
    if mismatch[:5]:
        log.warning("[%s] 不一致样例: %s", spec.table_label, mismatch[:5])
    return len(mismatch)


def align_one(token: str, spec: AlignSpec) -> tuple[str, int]:
    log.info("=== 开始对齐 %s.%s → 主表 %s ===", spec.table_label, spec.field_name, FIELD_COUNTRY)
    pending = _backup_pending(token, spec)
    backup = pending if pending else _backup_records(token, spec)
    _rename_legacy(spec, token)
    new_id = _create_dynamic(spec, token)
    if backup:
        _restore_records(token, spec, backup)
    _delete_legacy(spec, token)
    mismatch = _verify(token, spec)
    return new_id, mismatch


def _patch_workflows_after_align() -> None:
    """把工作流里对旧「国家」field id 的引用改成字段名「国家」。"""
    for workflow_id, json_name, stale_id in WORKFLOW_FIELD_FIXES:
        live = _lark(["+workflow-get", "--workflow-id", workflow_id])["data"]
        body = {"title": live["title"], "steps": live["steps"]}
        raw_before = json.dumps(body, ensure_ascii=False)
        if stale_id not in raw_before:
            log.info("[%s] 工作流无旧字段引用 %s", workflow_id, stale_id)
            continue

        def _walk(node):
            if isinstance(node, dict):
                if node.get("field_name") == stale_id:
                    node["field_name"] = "国家"
                val = node.get("value")
                if node.get("value_type") == "ref" and isinstance(val, str) and stale_id in val:
                    node["value"] = val.replace(stale_id, "国家")
                for child in node.values():
                    _walk(child)
            elif isinstance(node, list):
                for child in node:
                    _walk(child)

        _walk(body)
        out_path = ROOT / "workflows" / json_name
        out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                "lark-cli",
                "base",
                "+workflow-update",
                "--base-token",
                BASE_TOKEN,
                "--workflow-id",
                workflow_id,
                "--json",
                f"@{out_path.relative_to(ROOT)}",
                "--as",
                "user",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stdout or result.stderr)
        log.info("[%s] 已替换工作流旧字段 %s → 国家", workflow_id, stale_id)


def main() -> int:
    if not BASE_TOKEN:
        log.error("缺少 FEISHU_APP_TOKEN")
        return 1
    token = get_feishu_token()
    failed = 0
    for spec in SPECS:
        new_id, mismatch = align_one(token, spec)
        print(f"{spec.table_label}.{spec.field_name} NEW_FIELD_ID={new_id} MISMATCH={mismatch}")
        if mismatch:
            failed += 1
    if failed:
        return 1
    _patch_workflows_after_align()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
