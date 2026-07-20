"""Trigger GitHub Actions follow-up jobs (workflow_dispatch / repository_dispatch)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_REPO = "pyyzheng/soundbox-lead-poller-public"
GITHUB_API = "https://api.github.com"
ASSIGNMENT_UNBLOCK_WORKFLOW = "assignment-unblock.yml"


def _auth_token(token: str | None = None) -> str | None:
    return token or os.environ.get("GH_DISPATCH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _repo_name(repo: str | None = None) -> str:
    return repo or os.environ.get("GITHUB_REPO") or DEFAULT_REPO


def _post_json(url: str, body: dict, token: str, label: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "soundbox-lead-poller",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = resp.getcode()
        if code in (204, 202):
            log.info("[dispatch] triggered %s", label)
            return {"status": "dispatched", "label": label}
        log.warning("[dispatch] unexpected status %s for %s", code, label)
        return {"status": "error", "code": code}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        log.error("[dispatch] %s failed: %s %s", label, exc.code, detail)
        return {"status": "error", "code": exc.code, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        log.error("[dispatch] %s error: %s", label, exc)
        return {"status": "error", "detail": str(exc)}


def trigger_workflow_dispatch(
    workflow_file: str,
    *,
    ref: str = "main",
    inputs: dict | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict:
    """Fire workflow_dispatch (works with GITHUB_TOKEN + actions:write in Actions)."""
    token = _auth_token(token)
    if not token:
        log.info("[dispatch] skip workflow %s: no token", workflow_file)
        return {"status": "skipped", "reason": "no token"}
    repo = _repo_name(repo)
    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    body: dict = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    return _post_json(url, body, token, f"workflow:{workflow_file}")


def trigger_repository_dispatch(
    event_type: str,
    *,
    record_id: str | None = None,
    payload: dict | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict:
    """Fire repository_dispatch (needs PAT with repo scope; GITHUB_TOKEN often 403)."""
    token = _auth_token(token)
    if not token:
        log.info("[dispatch] skip %s: no token", event_type)
        return {"status": "skipped", "reason": "no token"}

    repo = _repo_name(repo)
    body: dict = {"event_type": event_type, "client_payload": dict(payload or {})}
    if record_id:
        body["client_payload"]["record_id"] = record_id

    url = f"{GITHUB_API}/repos/{repo}/dispatches"
    return _post_json(url, body, token, f"repository_dispatch:{event_type}")


def trigger_assignment_unblock(record_id: str | None = None, **extra) -> dict:
    """Trigger assignment-unblock: prefer workflow_dispatch (Actions token), fallback repository_dispatch."""
    payload = dict(extra or {})
    if record_id:
        payload["record_id"] = record_id

    result = trigger_workflow_dispatch(
        ASSIGNMENT_UNBLOCK_WORKFLOW,
        inputs={"dry_run": "false"},
    )
    if result.get("status") == "dispatched":
        return result

    log.info("[dispatch] workflow_dispatch failed, trying repository_dispatch")
    return trigger_repository_dispatch("assignment-unblock", payload=payload or None)
