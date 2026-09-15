#!/usr/bin/env python3
"""将规则子表的单选字段改为引用主表对应字段的动态选项（option id 对齐）。"""

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

from feishu_utils import FEISHU_APP_TOKEN, FEISHU_TABLE_ID, extract_text, feishu_api, get_feishu_token  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("align-select-field")

BASE_TOKEN = os.environ.get("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)

# 子表历史拼写与主表选项不一致时映射
NAME_ALIASES: dict[str, str] = {
    "Lindsey": "Linsley",
}


@dataclass(frozen=True)
class AlignSpec:
    table_id: str
    field_name: str
    old_field_id: str
    main_field_name: str
    main_field_id: str
    legacy_field_name: str
    workflow_id: str | None = None
    workflow_json_name: str | None = None


SPECS = (
    AlignSpec(
        table_id="tblk9x487yPMJGZr",
        field_name="业务员",
        old_field_id="fld9vps8a6",
        main_field_name="代理规则命中业务员",
        main_field_id="fld7jnKAvi",
        legacy_field_name="业务员_待删除",
        workflow_id="wkfKWPVBWT0NisJV",
        workflow_json_name="wkfKWPVBWT0NisJV-代理区域分配自动化.json",
    ),
    AlignSpec(
        table_id="tblYQpLxEBYjFN0T",
        field_name="负责人",
        old_field_id="fldxAsUa9t",
        main_field_name="子办规则命中负责人",
        main_field_id="fldBBzmesf",
        legacy_field_name="负责人_待删除",
        workflow_id="wkfaNTuMd6vAE5E0",
        workflow_json_name="wkfaNTuMd6vAE5E0-子办规则分配自动化.json",
    ),
)


def _lark(args: list[str]) -> dict:
    cmd = ["lark-cli", "base", *args, "--base-token", BASE_TOKEN, "--format", "json"]
    raw = subprocess.check_output(cmd, text=True, cwd=ROOT)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload


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
    log.info("[%s] 备份 %d 条", spec.table_id, len(backup))
    return backup


def _normalize_value(name: str) -> str:
    return NAME_ALIASES.get(name, name)


def _list_field_names(table_id: str, token: str) -> dict[str, str]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/fields"
    out: dict[str, str] = {}
    for item in feishu_api("GET", url, token=token).json()["data"]["items"]:
        out[item["field_name"]] = item["field_id"]
    return out


def _field_exists(table_id: str, field_name: str, token: str) -> str | None:
    return _list_field_names(table_id, token).get(field_name)


def _rename_legacy(spec: AlignSpec, token: str) -> None:
    if _field_exists(spec.table_id, spec.legacy_field_name, token):
        log.info("[%s] 跳过重命名（%s 已存在）", spec.table_id, spec.legacy_field_name)
        return
    if not _field_exists(spec.table_id, spec.field_name, token) and spec.old_field_id not in _list_field_names(spec.table_id, token).values():
        log.info("[%s] 跳过重命名（原字段已不存在）", spec.table_id)
        return
    field = _lark(["+field-get", "--table-id", spec.table_id, "--field-id", spec.old_field_id])["data"]["field"]
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
            spec.old_field_id,
            "--json",
            json.dumps(body, ensure_ascii=False),
            "--yes",
        ]
    )


def _create_dynamic(spec: AlignSpec, token: str) -> str:
    existing = _field_exists(spec.table_id, spec.field_name, token)
    if existing:
        log.info("[%s] 复用已有字段 %s id=%s", spec.table_id, spec.field_name, existing)
        return existing
    body = {
        "name": spec.field_name,
        "type": "select",
        "multiple": False,
        "dynamic_options_source": {
            "table_id": FEISHU_TABLE_ID,
            "field_id": spec.main_field_id,
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
    return resp["data"]["field"]["id"]


def _restore_records(token: str, spec: AlignSpec, backup: list[tuple[str, str]]) -> None:
    for i, (record_id, value) in enumerate(backup, 1):
        value = _normalize_value(value)
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{spec.table_id}/records/{record_id}"
        )
        resp = feishu_api("PUT", url, token=token, json={"fields": {spec.field_name: value}}, max_retries=3).json()
        if resp.get("code") != 0:
            raise RuntimeError(f"回填失败 {record_id}: {resp}")
        if i % 10 == 0:
            log.info("[%s] 回填 %d/%d", spec.table_id, i, len(backup))
        time.sleep(0.05)


def _delete_legacy(spec: AlignSpec, token: str) -> None:
    if not _field_exists(spec.table_id, spec.legacy_field_name, token):
        log.info("[%s] 无待删除字段 %s", spec.table_id, spec.legacy_field_name)
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


def _verify(token: str, spec: AlignSpec) -> int:
    def opts(table_id: str, field_name: str) -> dict[str, str]:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/fields"
        f = next(x for x in feishu_api("GET", url, token=token).json()["data"]["items"] if x["field_name"] == field_name)
        return {o["name"]: o["id"] for o in f.get("property", {}).get("options", [])}

    main = opts(FEISHU_TABLE_ID, spec.main_field_name)
    sub = opts(spec.table_id, spec.field_name)
    mismatch = [n for n in main if n in sub and main[n] != sub[n]]
    log.info("[%s] id 不一致 %d 项", spec.table_id, len(mismatch))
    return len(mismatch)


def _fix_suboffice_trigger(steps: list[dict]) -> None:
    """子办工作流触发器 isNot+双选项在 update API 校验不通过，改为 doesNotContainAny。"""
    for step in steps:
        if step.get("id") != "trigpJUkcCnQ":
            continue
        for group in step.get("data", {}).get("condition_list", []):
            for cond in group.get("conditions", []):
                if cond.get("field_name") == "分配来源" and cond.get("operator") == "isNot":
                    if len(cond.get("value", [])) > 1:
                        cond["operator"] = "doesNotContainAny"


def _patch_workflow_refs(spec: AlignSpec, new_field_id: str) -> None:
    if not spec.workflow_id:
        return
    live = _lark(["+workflow-get", "--workflow-id", spec.workflow_id])["data"]
    body = {"title": live["title"], "steps": live["steps"]}

    def _walk(node):
        if isinstance(node, dict):
            if node.get("value_type") == "ref" and isinstance(node.get("value"), str):
                if spec.old_field_id in node["value"]:
                    node["value"] = node["value"].replace(spec.old_field_id, new_field_id)
            for child in node.values():
                _walk(child)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(body)
    for step in body.get("steps", []):
        if step.get("type") != "FindRecordAction":
            continue
        names = step.get("data", {}).get("field_names")
        if names == [spec.old_field_id]:
            step["data"]["field_names"] = [spec.field_name]

    if spec.workflow_id == "wkfaNTuMd6vAE5E0":
        _fix_suboffice_trigger(body["steps"])

    out_path = ROOT / "workflows" / (spec.workflow_json_name or f"{spec.workflow_id}.json")
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            "lark-cli",
            "base",
            "+workflow-update",
            "--base-token",
            BASE_TOKEN,
            "--workflow-id",
            spec.workflow_id,
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
    log.info("[%s] 工作流 %s 已更新 field_id → %s", spec.table_id, spec.workflow_id, new_field_id)


def _backup_pending(token: str, spec: AlignSpec) -> list[tuple[str, str]]:
    """从待删除字段补回填（断点续跑）。"""
    if not _field_exists(spec.table_id, spec.legacy_field_name, token):
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
            json={"field_names": [spec.field_name, spec.legacy_field_name], "page_size": 500},
        ).json()["data"]
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    pending: list[tuple[str, str]] = []
    for item in items:
        fields = item.get("fields", {})
        if extract_text(fields.get(spec.field_name)):
            continue
        legacy = extract_text(fields.get(spec.legacy_field_name, ""))
        if legacy:
            pending.append((item["record_id"], legacy))
    return pending


def align_one(token: str, spec: AlignSpec) -> tuple[str, int]:
    log.info("=== 开始对齐 %s.%s ===", spec.table_id, spec.field_name)
    pending = _backup_pending(token, spec)
    backup = pending if pending else _backup_records(token, spec)
    _rename_legacy(spec, token)
    new_id = _create_dynamic(spec, token)
    if backup:
        _restore_records(token, spec, backup)
    _delete_legacy(spec, token)
    mismatch = _verify(token, spec)
    _patch_workflow_refs(spec, new_id)
    return new_id, mismatch


def main() -> int:
    if not BASE_TOKEN:
        log.error("缺少 FEISHU_APP_TOKEN")
        return 1
    token = get_feishu_token()
    failed = 0
    for spec in SPECS:
        new_id, mismatch = align_one(token, spec)
        print(f"{spec.table_id}.{spec.field_name} NEW_FIELD_ID={new_id} MISMATCH={mismatch}")
        if mismatch:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
