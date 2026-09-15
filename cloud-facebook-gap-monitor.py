#!/usr/bin/env python3
"""Meta Lead Ads ↔ 飞书差集监控

对比近 N 小时 Meta leadgen_id 与飞书「Facebook Leadgen ID」：
  - 差集 > 0 → 发飞书告警，并自动 workflow_dispatch 触发 Facebook Lead Poller 补录

用法:
  CHECK_HOURS=6 python3 cloud-facebook-gap-monitor.py
  DRY_RUN=true CHECK_HOURS=6 python3 cloud-facebook-gap-monitor.py
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent.resolve()
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from feishu_utils import alert_webhook_url  # noqa: E402
from feishu_writer import (  # noqa: E402
    check_feishu_fb_leadgen_duplicate,
    get_feishu_token,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fb-gap-monitor")

TZ_SH = timezone(timedelta(hours=8))
CONFIG_PATH = SCRIPT_DIR / "facebook-config.json"
DEFAULT_HOURS = 6
DEFAULT_REPO = "pyyzheng/soundbox-lead-poller-public"
WORKFLOW_FILE = "facebook-lead-poller.yml"


def _load_poller():
    """复用 facebook-lead-poller 的 Meta 拉取与 since 解析逻辑。"""
    os.environ.setdefault("FEISHU_APP_TOKEN", os.environ.get("FEISHU_APP_TOKEN", "missing"))
    os.environ.setdefault("FEISHU_TABLE_ID", os.environ.get("FEISHU_TABLE_ID", "missing"))
    path = SCRIPT_DIR / "facebook-lead-poller.py"
    spec = importlib.util.spec_from_file_location("fb_poller_for_gap", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少配置: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def parse_field_data(field_data: list) -> dict:
    out = {}
    for field in field_data or []:
        name = field.get("name", "")
        values = field.get("values") or []
        out[name] = values[0] if values else ""
    return out


def collect_meta_leads(poller, config: dict, since_dt: datetime) -> list[dict]:
    """拉取 since 之后的 Meta 线索（全 ACTIVE 表单，客户端时间过滤）。"""
    token = poller.get_access_token()
    page_id = (config.get("meta") or {}).get("page_id", "")
    if not page_id:
        raise RuntimeError("facebook-config.json 缺少 meta.page_id")

    forms_cfg = config.get("forms") or []
    if forms_cfg:
        forms = [
            {"id": f["form_id"], "name": f.get("form_name", f["form_id"]), "status": "ACTIVE"}
            for f in forms_cfg
            if f.get("form_id")
        ]
    else:
        # 勿用 min_form_created_time：老表单（香日/欧洲）仍在收线索
        lookback_days = int(config.get("min_form_updated_days", 120))
        min_updated = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        forms = poller.list_lead_forms(
            token,
            page_id,
            min_updated_time=min_updated,
        )

    since_iso = since_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    leads: list[dict] = []
    seen: set[str] = set()

    for form in forms:
        if form.get("status") and form["status"] != "ACTIVE":
            continue
        form_id = form.get("id") or form.get("form_id")
        form_name = form.get("name") or form.get("form_name") or form_id
        raw = poller.fetch_leads(token, form_id, since=since_iso)
        log.info("表单 %s: 拉取 %d 条 (since=%s)", form_name, len(raw), since_iso)
        for item in raw:
            lid = str(item.get("id") or "").strip()
            if not lid or lid in seen:
                continue
            seen.add(lid)
            fields = parse_field_data(item.get("field_data") or [])
            leads.append(
                {
                    "leadgen_id": lid,
                    "created_time": item.get("created_time") or "",
                    "form_id": form_id,
                    "form_name": form_name,
                    "name": fields.get("full_name") or fields.get("name") or "",
                    "email": fields.get("email") or "",
                }
            )
        time.sleep(0.15)

    return leads


def find_gaps(feishu_token: str, meta_leads: list[dict]) -> list[dict]:
    gaps = []
    for lead in meta_leads:
        existing = check_feishu_fb_leadgen_duplicate(feishu_token, lead["leadgen_id"])
        if existing:
            continue
        gaps.append(lead)
    return gaps


def send_gap_alert(gaps: list[dict], hours: int, poller_triggered: bool, run_url: str = "") -> bool:
    webhook = alert_webhook_url()
    if not webhook:
        log.warning("FEISHU_ALERT_WEBHOOK 未配置，跳过告警")
        return False

    now_str = datetime.now(TZ_SH).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"**检查时间**：{now_str}",
        f"**窗口**：近 {hours} 小时",
        f"**漏录数**：{len(gaps)}",
        f"**自动补录**：{'已触发 Facebook Lead Poller' if poller_triggered else '未触发'}",
    ]
    if run_url:
        lines.append(f"**Actions**：{run_url}")
    lines.append("")
    lines.append("**缺失线索（最多 10 条）**")
    for g in gaps[:10]:
        lines.append(
            f"- {g.get('name') or '(无姓名)'} | {g.get('email') or '-'} | "
            f"`{g['leadgen_id']}` | {g.get('form_name')}"
        )
    if len(gaps) > 10:
        lines.append(f"- …另有 {len(gaps) - 10} 条")

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "【线索告警】Facebook Lead Ads 漏录入飞书",
                }
            },
            "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
        },
    }
    try:
        resp = requests.post(webhook, json=card, timeout=15)
        ok = resp.json().get("code") == 0
        if not ok:
            log.warning("告警返回异常: %s", resp.text[:200])
        return ok
    except Exception as e:
        log.warning("告警发送失败: %s", e)
        return False


def trigger_facebook_poller(
    gh_token: str,
    repo: str,
    since_iso: str,
    dry_run: bool = False,
) -> str:
    """触发 public 仓 Facebook Lead Poller；返回可能的 run 列表 URL。"""
    if not gh_token:
        raise RuntimeError("缺少 GITHUB_TOKEN / GHA_PAT，无法触发 Poller")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = {
        "ref": "main",
        "inputs": {
            "dry_run": "true" if dry_run else "false",
            "since": since_iso,
        },
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code not in (204, 200):
        raise RuntimeError(f"触发 Poller 失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return f"https://github.com/{repo}/actions/workflows/{WORKFLOW_FILE}"


def main() -> int:
    hours = int(os.environ.get("CHECK_HOURS", str(DEFAULT_HOURS)))
    dry_run = os.environ.get("DRY_RUN", "false").lower() in {"1", "true", "yes"}
    auto_trigger = os.environ.get("AUTO_TRIGGER_POLLER", "true").lower() in {"1", "true", "yes"}
    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO)
    gh_token = (
        os.environ.get("GHA_PAT")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()

    since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    log.info("开始差集检查: hours=%d since=%s dry_run=%s", hours, since_iso, dry_run)

    poller = _load_poller()
    config = load_config()
    meta_leads = collect_meta_leads(poller, config, since_dt)
    log.info("Meta 近 %d 小时线索: %d", hours, len(meta_leads))

    if not meta_leads:
        log.info("无 Meta 线索，退出")
        return 0

    feishu_token = get_feishu_token()
    gaps = find_gaps(feishu_token, meta_leads)
    log.info("飞书缺失: %d / Meta %d", len(gaps), len(meta_leads))

    if not gaps:
        log.info("无漏录")
        return 0

    for g in gaps:
        log.warning(
            "MISS %s %s %s form=%s",
            g.get("created_time"),
            g.get("name"),
            g["leadgen_id"],
            g.get("form_name"),
        )

    poller_triggered = False
    run_url = ""
    if dry_run:
        log.info("[DRY-RUN] 跳过告警与 Poller 触发")
    else:
        if auto_trigger:
            try:
                run_url = trigger_facebook_poller(gh_token, repo, since_iso, dry_run=False)
                poller_triggered = True
                log.info("已触发 Facebook Lead Poller since=%s → %s", since_iso, run_url)
            except Exception as e:
                log.error("触发 Poller 失败: %s", e)
        send_gap_alert(gaps, hours, poller_triggered, run_url=run_url)

    # 非零退出便于 Actions 标红（有漏录）
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
