#!/usr/bin/env python3
"""Read-only health check for the two Feishu Quick Follow-up sync workflows.

Checks the *live* workflow HTTP request contract and the resulting Base data.
The workflow engine can mark an HTTP 400 run as successful, so status alone is
not evidence that a follow-up was stamped or linked.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).parent / "lib"))

LOG = logging.getLogger("quick-followup-health")
TZ_SH = timezone(timedelta(hours=8))
QUICK_FIELD = "Quick Follow-up / 快捷跟进"
CLUE_FIELD = "Clue ID"
FU_DETAILS_FIELD = "Follow-up Details"
FU_RELATED_FIELD = "Related Lead"
FU_DELETED_FIELD = "已删除"
DATE_LINE = re.compile(r"^(\d{4}/\d{2}/\d{2})\s*\|\s*(.*)$")
SYNC_URL = "https://webhook.soundboxbooth.com/sync-quick-followup"
WORKFLOWS = {
    "wkfIU1il4h5TFhF7": ("from_quick", "lead_record_id", QUICK_FIELD),
    "wkf1Sfkg8S0Y2tjO": ("from_record", "followup_record_id", None),
}
# Historical dated Quick lines have not all been audited. The 2026-11-17
# repair established this baseline; every subsequently stamped line is checked.
FOLLOWUP_CHECK_FROM = "2026/11/17"
DEFAULT_GRACE_MINUTES = 10
PAGE_SIZE = 500


class MonitorError(RuntimeError):
    """The monitor could not verify the current state."""


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(cell_text(part) for part in value)
    if isinstance(value, dict):
        for key in ("value", "text", "name"):
            if key in value:
                return cell_text(value[key])
        return ""
    return str(value)


def link_ids(value: Any) -> set[str]:
    """Handle both Base list/search link response shapes."""
    if not value:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(link_ids(v) for v in value)) if value else set()
    if isinstance(value, dict):
        ids = value.get("record_ids") or value.get("link_record_ids")
        if ids:
            return link_ids(ids)
        rid = value.get("id") or value.get("record_id")
        return {str(rid)} if rid else set()
    return set()


def quick_lines(value: Any) -> list[tuple[str | None, str]]:
    text = cell_text(value).replace("\r\n", "\n").strip()
    lines = []
    for raw in text.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        match = DATE_LINE.match(raw)
        if match:
            lines.append((match.group(1), match.group(2).strip()))
        else:
            lines.append((None, raw))
    return lines


def _workflow_body(parts: Any) -> dict | None:
    if not isinstance(parts, list):
        return None
    chunks = []
    ref_count = 0
    for part in parts:
        if not isinstance(part, dict):
            return None
        kind = part.get("value_type")
        if kind == "text":
            chunks.append(str(part.get("value", "")))
        elif kind == "ref" and part.get("value") == "$.trigger.recordId":
            ref_count += 1
            chunks.append("RECORD_ID_SENTINEL")
        else:
            return None
    if ref_count != 1:
        return None
    try:
        body = json.loads("".join(chunks))
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _header_value(headers: Any, wanted: str) -> str:
    for header in headers if isinstance(headers, list) else []:
        if str(header.get("key", "")).lower() != wanted.lower():
            continue
        parts = header.get("value") or []
        if not isinstance(parts, list):
            return ""
        return "".join(str(part.get("value", "")) for part in parts if part.get("value_type") == "text")
    return ""


def _literal_value(parts: Any) -> str:
    if not isinstance(parts, list) or any(p.get("value_type") != "text" for p in parts):
        return ""
    return "".join(str(p.get("value", "")) for p in parts)


def validate_workflow(document: dict, workflow_id: str) -> list[str]:
    """Return safe-to-log contract deviations; never expose Authorization data."""
    expected_direction, expected_id_key, watched_field = WORKFLOWS[workflow_id]
    workflow = document.get("data", document)
    if isinstance(workflow, dict) and "workflow" in workflow:
        workflow = workflow["workflow"]
    if not isinstance(workflow, dict):
        return ["workflow definition unavailable"]

    issues = []
    if workflow.get("status") != "enabled":
        issues.append("workflow is not enabled")
    steps = workflow.get("steps") or []
    if not isinstance(steps, list):
        return issues + ["workflow steps unavailable"]
    if any(not isinstance(step, dict) for step in steps):
        return issues + ["workflow steps malformed"]
    triggers = [s for s in steps if s.get("type") in {"SetRecordTrigger", "ChangeRecordTrigger"}]
    actions = [s for s in steps if s.get("type") == "HTTPClientAction"]
    if len(triggers) != 1 or len(actions) != 1:
        return issues + ["expected one record trigger and one HTTP action"]
    trigger, action = triggers[0], actions[0]
    if trigger.get("next") != action.get("id"):
        issues.append("record trigger is not connected to HTTP action")
    if expected_direction == "from_quick":
        if trigger.get("type") != "SetRecordTrigger":
            issues.append("Quick trigger type changed")
        if (trigger.get("data") or {}).get("table_name") != "线索总池 Case Database":
            issues.append("Quick trigger table changed")
        watch_info = (trigger.get("data") or {}).get("field_watch_info") or []
        if watched_field not in [f.get("field_name") for f in watch_info]:
            issues.append("Quick field is not watched")
    else:
        if (trigger.get("data") or {}).get("table_name") != "Follow-up Records":
            issues.append("Follow-up trigger table changed")

    data = action.get("data") or {}
    if str(data.get("method", "")).upper() != "POST":
        issues.append("HTTP method is not POST")
    if _literal_value(data.get("url")) != SYNC_URL:
        issues.append("sync endpoint changed")
    if data.get("body_type") != "raw":
        issues.append("HTTP body_type is not raw")
    if _header_value(data.get("headers"), "Content-Type").split(";")[0].strip().lower() != "application/json":
        issues.append("Content-Type is not application/json")
    if not _header_value(data.get("headers"), "Authorization").strip():
        issues.append("Authorization header is missing")
    body = _workflow_body(data.get("raw_body"))
    if not body or body.get("direction") != expected_direction or body.get(expected_id_key) != "RECORD_ID_SENTINEL":
        issues.append("JSON body direction or record ID reference changed")
    return issues


def fetch_live_workflow(token: str, workflow_id: str, app_token: str, app_id: str) -> dict:
    """Read the same Base v3 endpoint used by lark-cli workflow-get.

    The returned definition contains a live Authorization header. Never log
    response text or the parsed document; only emit fixed diagnostic strings.
    """
    from feishu_utils import feishu_api

    url = f"https://open.feishu.cn/open-apis/base/v3/bases/{app_token}/workflows/{workflow_id}"
    response = feishu_api("GET", url, token=token, headers={"X-App-Id": app_id})
    if response.status_code != 200:
        raise MonitorError(f"workflow-get HTTP {response.status_code} for {workflow_id}")
    try:
        document = response.json()
    except ValueError as exc:
        raise MonitorError(f"workflow-get returned invalid JSON for {workflow_id}") from exc
    if not isinstance(document, dict) or document.get("code") != 0:
        code = document.get("code") if isinstance(document, dict) else "malformed"
        raise MonitorError(f"workflow-get API error {code} for {workflow_id}")
    return document


def search_all_records(token: str, app_token: str, table_id: str, field_names: list[str]) -> list[dict]:
    from feishu_utils import feishu_api

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    records = []
    page_token = None
    used_tokens = set()
    for _ in range(100):
        body: dict[str, Any] = {
            "page_size": PAGE_SIZE,
            "automatic_fields": True,
            "field_names": field_names,
        }
        # Feishu's search API returns page_token in the response but reads the
        # next token from the URL query. Putting it in JSON repeats page one.
        params = {"page_token": page_token} if page_token else None
        response = feishu_api("POST", url, token=token, params=params, json=body)
        if response.status_code != 200:
            raise MonitorError(f"Base search HTTP {response.status_code} for {table_id}")
        try:
            result = response.json()
        except ValueError as exc:
            raise MonitorError(f"Base search returned invalid JSON for {table_id}") from exc
        if result.get("code") != 0:
            raise MonitorError(f"Base search API error {result.get('code')} for {table_id}")
        page = result.get("data") or {}
        batch = page.get("items") or []
        if not isinstance(batch, list):
            raise MonitorError(f"Base search returned malformed items for {table_id}")
        records.extend(batch)
        if not page.get("has_more"):
            return records
        page_token = page.get("page_token")
        if not page_token or page_token in used_tokens:
            raise MonitorError(f"Base search pagination stopped for {table_id}")
        used_tokens.add(page_token)
    raise MonitorError(f"Base search exceeded 100 pages for {table_id}")


def _modified_ms(record: dict) -> int:
    raw = record.get("last_modified_time")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise MonitorError("Base record missing last_modified_time") from exc
    if value <= 0:
        raise MonitorError("Base record has invalid last_modified_time")
    return value * 1000 if value < 10**12 else value


def inspect_records(
    leads: list[dict], followups: list[dict], *, now_ms: int,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    followup_check_from: str = FOLLOWUP_CHECK_FROM,
) -> tuple[list[dict], list[dict], int]:
    """Return undated lines, unlinked new lines, and grace-period row count."""
    cutoff_ms = now_ms - grace_minutes * 60_000
    by_lead: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for rec in followups:
        fields = rec.get("fields") or {}
        deleted = fields.get(FU_DELETED_FIELD)
        if deleted is True or deleted == "true" or deleted == 1:
            continue
        details = cell_text(fields.get(FU_DETAILS_FIELD)).strip()
        if not details:
            continue
        for lead_id in link_ids(fields.get(FU_RELATED_FIELD)):
            by_lead[lead_id][details] += 1

    undated: list[dict] = []
    unlinked: list[dict] = []
    pending = 0
    for rec in leads:
        fields = rec.get("fields") or {}
        lines = quick_lines(fields.get(QUICK_FIELD))
        if not lines:
            continue
        record_id = rec.get("record_id")
        if not record_id:
            raise MonitorError("Base record missing record_id")
        modified_ms = _modified_ms(rec)
        if modified_ms > cutoff_ms:
            pending += 1
            continue
        label = cell_text(fields.get(CLUE_FIELD)).strip() or record_id
        expected = collections.Counter()
        for date, details in lines:
            if not date:
                undated.append({"record_id": record_id, "label": label, "detail": details})
            elif date >= followup_check_from and details:
                expected[details] += 1
        actual = by_lead.get(record_id, collections.Counter())
        for detail, count in (expected - actual).items():
            unlinked.append({"record_id": record_id, "label": label, "detail": detail, "count": count})
    return undated, unlinked, pending


def send_alert(issues: list[str], undated: list[dict], unlinked: list[dict], *, dry_run: bool) -> None:
    if dry_run:
        LOG.info("Dry run: Feishu alert suppressed")
        return
    webhook = os.environ.get("FEISHU_ALERT_WEBHOOK", "").strip()
    if not webhook:
        LOG.error("FEISHU_ALERT_WEBHOOK not configured; GitHub job still fails")
        return
    now = datetime.now(TZ_SH).strftime("%Y-%m-%d %H:%M")
    lines = [f"**检查时间**：{now}", f"**缺日期**：{len(undated)} 行", f"**新跟进无关联记录**：{sum(i['count'] for i in unlinked)} 行"]
    for issue in issues[:6]:
        lines.append(f"- {issue}")
    for title, entries in (("缺日期样例", undated), ("缺关联记录样例", unlinked)):
        if entries:
            lines.append(f"**{title}**")
            for item in entries[:5]:
                lines.append(f"- {item['label']} (`{item['record_id']}`)")
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    if run_url:
        lines.append(f"**巡检日志**：{run_url}")
    lines.append("请检查飞书自动化 HTTP 请求体和同步 Worker 日志；巡检只读，未自动改写记录。")
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "【线索告警】快捷跟进同步异常"}},
            "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
        },
    }
    try:
        response = requests.post(webhook, json=payload, timeout=15)
        response.raise_for_status()
        if response.json().get("code") != 0:
            LOG.error("Feishu alert webhook returned a nonzero code")
    except (requests.RequestException, ValueError) as exc:
        LOG.error("Feishu alert delivery failed: %s", type(exc).__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    required = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN", "FEISHU_TABLE_ID", "FEISHU_FOLLOWUP_TABLE")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        issue = f"Missing configuration: {', '.join(missing)}"
        LOG.error("%s", issue)
        send_alert([issue], [], [], dry_run=dry_run)
        return 2
    app_token = os.environ["FEISHU_APP_TOKEN"]
    app_id = os.environ["FEISHU_APP_ID"]
    issues: list[str] = []
    undated: list[dict] = []
    unlinked: list[dict] = []
    pending = 0

    token = None
    try:
        from feishu_utils import get_feishu_token
        token = get_feishu_token()
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
        issues.append(f"Feishu authentication unavailable ({type(exc).__name__})")

    if token:
        for workflow_id in WORKFLOWS:
            try:
                definition = fetch_live_workflow(token, workflow_id, app_token, app_id)
                deviations = validate_workflow(definition, workflow_id)
                issues.extend(f"{workflow_id}: {item}" for item in deviations)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, MonitorError) else type(exc).__name__
                issues.append(f"{workflow_id}: live workflow check unavailable ({reason})")

    if token:
        try:
            leads = search_all_records(token, app_token, os.environ["FEISHU_TABLE_ID"], [CLUE_FIELD, QUICK_FIELD])
            if not leads:
                raise MonitorError("lead table returned no records")
            followups = search_all_records(
                token, app_token, os.environ["FEISHU_FOLLOWUP_TABLE"],
                [FU_RELATED_FIELD, FU_DETAILS_FIELD, FU_DELETED_FIELD],
            )
            if not followups:
                raise MonitorError("follow-up table returned no records")
            grace = int(os.environ.get("GRACE_MINUTES", str(DEFAULT_GRACE_MINUTES)))
            if grace < 0:
                raise MonitorError("GRACE_MINUTES must be nonnegative")
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            undated, unlinked, pending = inspect_records(leads, followups, now_ms=now_ms, grace_minutes=grace)
            LOG.info("Scanned leads=%d followups=%d grace_rows=%d undated=%d unlinked=%d",
                     len(leads), len(followups), pending, len(undated), sum(i["count"] for i in unlinked))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, MonitorError) else type(exc).__name__
            issues.append(f"Base data check unavailable ({reason})")

    if not issues and not undated and not unlinked:
        LOG.info("Quick Follow-up workflows and Base data healthy")
        return 0
    for issue in issues:
        LOG.error("CHECK FAILED: %s", issue)
    for item in undated[:10]:
        LOG.error("UNDATED %s %s", item["label"], item["record_id"])
    for item in unlinked[:10]:
        LOG.error("UNLINKED %s %s count=%s", item["label"], item["record_id"], item["count"])
    send_alert(issues, undated, unlinked, dry_run=dry_run)
    return 1


if __name__ == "__main__":
    sys.exit(main())
