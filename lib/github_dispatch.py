"""Trigger GitHub repository_dispatch for near-realtime follow-up jobs."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_REPO = "pyyzheng/soundbox-lead-poller-public"
GITHUB_API = "https://api.github.com"


def trigger_repository_dispatch(
    event_type: str,
    *,
    record_id: str | None = None,
    payload: dict | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict:
    """Fire repository_dispatch. Returns status dict; never raises."""
    token = token or os.environ.get("GH_DISPATCH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        log.info("[dispatch] skip %s: no GITHUB_TOKEN/GH_DISPATCH_TOKEN", event_type)
        return {"status": "skipped", "reason": "no token"}

    repo = repo or os.environ.get("GITHUB_REPO") or DEFAULT_REPO
    body: dict = {"event_type": event_type, "client_payload": dict(payload or {})}
    if record_id:
        body["client_payload"]["record_id"] = record_id

    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}/dispatches",
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
            log.info("[dispatch] triggered %s repo=%s record=%s", event_type, repo, record_id or "-")
            return {"status": "dispatched", "event_type": event_type}
        log.warning("[dispatch] unexpected status %s for %s", code, event_type)
        return {"status": "error", "code": code}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        log.error("[dispatch] %s failed: %s %s", event_type, exc.code, detail)
        return {"status": "error", "code": exc.code, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        log.error("[dispatch] %s error: %s", event_type, exc)
        return {"status": "error", "detail": str(exc)}


def trigger_assignment_unblock(record_id: str | None = None, **extra) -> dict:
    return trigger_repository_dispatch(
        "assignment-unblock",
        record_id=record_id,
        payload=extra or None,
    )
