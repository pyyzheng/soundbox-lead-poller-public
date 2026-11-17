"""Regression tests for the read-only Quick Follow-up health check."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("quick_followup_health", ROOT / "cloud-quick-followup-health.py")
assert SPEC and SPEC.loader
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


def _workflow(filename: str) -> dict:
    return {"ok": True, "data": json.loads((ROOT / "scripts" / filename).read_text(encoding="utf-8"))}


def _lead(rid: str, text: str, modified_ms: int) -> dict:
    return {
        "record_id": rid,
        "last_modified_time": str(modified_ms),
        "fields": {MONITOR.QUICK_FIELD: text, MONITOR.CLUE_FIELD: rid.upper()},
    }


def _fu(rid: str, lead: str, details: str, deleted=False) -> dict:
    return {
        "record_id": rid,
        "fields": {
            MONITOR.FU_RELATED_FIELD: [{"id": lead}],
            MONITOR.FU_DETAILS_FIELD: [{"text": details}],
            MONITOR.FU_DELETED_FIELD: deleted,
        },
    }


def test_current_local_workflow_contracts_are_valid():
    quick = _workflow("wf_sync_from_quick.json")
    record = _workflow("wf_sync_from_record.json")
    assert MONITOR.validate_workflow(quick, "wkfIU1il4h5TFhF7") == []
    assert MONITOR.validate_workflow(record, "wkf1Sfkg8S0Y2tjO") == []
    # The raw Base v3 endpoint uses code/data; lark-cli uses ok/data.
    assert MONITOR.validate_workflow({"code": 0, "data": quick["data"]}, "wkfIU1il4h5TFhF7") == []
    assert MONITOR.validate_workflow({"code": 0, "data": record["data"]}, "wkf1Sfkg8S0Y2tjO") == []


def test_failing_form_request_is_caught_even_when_workflow_enabled():
    workflow = _workflow("wf_sync_from_quick.json")
    action = workflow["data"]["steps"][1]
    action["data"]["body_type"] = "form-urlencoded"
    action["data"]["raw_body"] = []
    issues = MONITOR.validate_workflow(workflow, "wkfIU1il4h5TFhF7")
    assert "HTTP body_type is not raw" in issues
    assert "JSON body direction or record ID reference changed" in issues


def test_workflow_must_use_correct_direction_and_trigger_reference():
    workflow = _workflow("wf_sync_from_quick.json")
    action = workflow["data"]["steps"][1]
    action["data"]["raw_body"][0]["value"] = '{"direction":"from_record","lead_record_id":"'
    assert any("direction" in issue for issue in MONITOR.validate_workflow(workflow, "wkfIU1il4h5TFhF7"))
    action["data"]["raw_body"][0]["value"] = '{"direction":"from_quick","lead_record_id":"'
    action["data"]["raw_body"][1]["value"] = "$.other.recordId"
    assert any("reference" in issue for issue in MONITOR.validate_workflow(workflow, "wkfIU1il4h5TFhF7"))


def test_workflow_requires_enabled_trigger_connection_and_json_header():
    workflow = _workflow("wf_sync_from_quick.json")
    workflow["data"]["status"] = "disabled"
    workflow["data"]["steps"][0]["next"] = None
    headers = workflow["data"]["steps"][1]["data"]["headers"]
    headers[0]["value"][0]["value"] = "application/x-www-form-urlencoded"
    issues = MONITOR.validate_workflow(workflow, "wkfIU1il4h5TFhF7")
    assert "workflow is not enabled" in issues
    assert "record trigger is not connected to HTTP action" in issues
    assert "Content-Type is not application/json" in issues


def test_wrong_quick_trigger_table_is_caught():
    workflow = _workflow("wf_sync_from_quick.json")
    workflow["data"]["steps"][0]["data"]["table_name"] = "Different Table"
    assert "Quick trigger table changed" in MONITOR.validate_workflow(workflow, "wkfIU1il4h5TFhF7")


def test_contract_diagnostics_do_not_include_authorization_value():
    workflow = _workflow("wf_sync_from_quick.json")
    workflow["data"]["steps"][1]["data"]["headers"][1]["value"][0]["value"] = "Bearer SECRET_TEST_VALUE"
    workflow["data"]["steps"][1]["data"]["body_type"] = "form-urlencoded"
    issues = MONITOR.validate_workflow(workflow, "wkfIU1il4h5TFhF7")
    assert issues
    assert "SECRET_TEST_VALUE" not in repr(issues)


def test_missing_date_and_missing_link_are_reported_after_grace():
    now = 1_800_000_000_000
    old = now - 11 * 60_000
    leads = [
        _lead("recA", "Customer called\n2026/11/17 | sent quote", old),
        _lead("recB", "2026/11/17 | booked meeting", old),
    ]
    followups = [_fu("fuA", "recA", "sent quote")]
    undated, unlinked, pending = MONITOR.inspect_records(leads, followups, now_ms=now)
    assert [(x["record_id"], x["detail"]) for x in undated] == [("recA", "Customer called")]
    assert [(x["record_id"], x["detail"]) for x in unlinked] == [("recB", "booked meeting")]
    assert pending == 0


def test_newly_edited_row_waits_for_async_sync():
    now = 1_800_000_000_000
    lead = _lead("recA", "Customer called\n2026/11/17 | booked meeting", now - 2 * 60_000)
    assert MONITOR.inspect_records([lead], [], now_ms=now) == ([], [], 1)


def test_legacy_dated_lines_do_not_create_historic_false_positives():
    now = 1_800_000_000_000
    old = now - 11 * 60_000
    lead = _lead("recA", "2026/11/16 | old note\n2026/11/17 | new note", old)
    undated, unlinked, _ = MONITOR.inspect_records([lead], [], now_ms=now)
    assert undated == []
    assert [item["detail"] for item in unlinked] == ["new note"]


def test_duplicate_quick_lines_require_two_active_linked_records():
    now = 1_800_000_000_000
    lead = _lead("recA", "2026/11/17 | called\n2026/11/17 | called", now - 11 * 60_000)
    followups = [_fu("fuA", "recA", "called"), _fu("fuB", "recA", "called", deleted=True)]
    _, unlinked, _ = MONITOR.inspect_records([lead], followups, now_ms=now)
    assert len(unlinked) == 1
    assert unlinked[0]["count"] == 1


def test_missing_modified_timestamp_fails_closed():
    lead = _lead("recA", "undated", 1_800_000_000_000)
    lead.pop("last_modified_time")
    with pytest.raises(MONITOR.MonitorError, match="last_modified_time"):
        MONITOR.inspect_records([lead], [], now_ms=1_800_001_000_000)


def test_search_paginates_and_rejects_missing_next_token():
    replies = [
        {"code": 0, "data": {"items": [{"record_id": "recA"}], "has_more": True, "page_token": "next"}},
        {"code": 0, "data": {"items": [{"record_id": "recB"}], "has_more": False}},
    ]
    calls = []

    def fake_api(method, url, *, token, params, json):
        calls.append((params, json))
        result = replies.pop(0)
        return types.SimpleNamespace(status_code=200, json=lambda: result)

    with patch.dict(sys.modules, {"feishu_utils": types.SimpleNamespace(feishu_api=fake_api)}):
        rows = MONITOR.search_all_records("token", "base", "table", ["Quick"])
    assert [row["record_id"] for row in rows] == ["recA", "recB"]
    assert calls[0][1]["automatic_fields"] is True
    assert calls[1][0] == {"page_token": "next"}
    assert "page_token" not in calls[1][1]

    replies = [{"code": 0, "data": {"items": [], "has_more": True}}]
    with patch.dict(sys.modules, {"feishu_utils": types.SimpleNamespace(feishu_api=fake_api)}):
        with pytest.raises(MONITOR.MonitorError, match="pagination"):
            MONITOR.search_all_records("token", "base", "table", ["Quick"])


def test_live_workflow_read_uses_base_v3_with_app_id_header():
    calls = []
    document = {"code": 0, "data": _workflow("wf_sync_from_quick.json")["data"]}

    def fake_api(method, url, *, token, headers):
        calls.append((method, url, token, headers))
        return types.SimpleNamespace(status_code=200, json=lambda: document)

    with patch.dict(sys.modules, {"feishu_utils": types.SimpleNamespace(feishu_api=fake_api)}):
        result = MONITOR.fetch_live_workflow("token", "wkfIU1il4h5TFhF7", "base", "app_id")
    assert result is document
    assert calls == [(
        "GET", "https://open.feishu.cn/open-apis/base/v3/bases/base/workflows/wkfIU1il4h5TFhF7",
        "token", {"X-App-Id": "app_id"},
    )]


def test_missing_configuration_surfaces_alert_attempt():
    with patch.dict(MONITOR.os.environ, {}, clear=True):
        with patch.object(MONITOR, "send_alert") as alert:
            assert MONITOR.main() == 2
    alert.assert_called_once()


def test_alert_is_suppressed_in_dry_run():
    with patch.object(MONITOR.requests, "post") as post:
        MONITOR.send_alert(["broken"], [], [], dry_run=True)
    post.assert_not_called()


def test_alert_contains_ids_but_no_customer_note_text():
    response = types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"code": 0})
    with patch.dict(MONITOR.os.environ, {"FEISHU_ALERT_WEBHOOK": "https://example.test/hook"}):
        with patch.object(MONITOR.requests, "post", return_value=response) as post:
            MONITOR.send_alert([], [{"record_id": "recA", "label": "C123", "detail": "PRIVATE NOTE"}], [], dry_run=False)
    body = json.dumps(post.call_args.kwargs["json"], ensure_ascii=False)
    assert "recA" in body and "C123" in body
    assert "PRIVATE NOTE" not in body
