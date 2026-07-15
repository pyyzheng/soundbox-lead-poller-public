"""
feishu_utils.py — 飞书 API 共享工具

跨 cloud-lead-poller / cloud-daily-report / cloud-check-unassigned 共用。
"""
import logging
import os
import sys
import time

import requests

log = logging.getLogger("feishu-utils")


def require_env(name: str, *aliases: str) -> str:
    """Return the first configured env var, or fail fast instead of falling back."""
    for key in (name, *aliases):
        value = os.environ.get(key)
        if value:
            return value
    names = ", ".join((name, *aliases))
    log.error("缺少必需环境变量：%s", names)
    sys.exit(1)


# 飞书 Base 配置必须由环境变量显式提供，避免静默回退到旧表。
FEISHU_APP_TOKEN = require_env("FEISHU_APP_TOKEN", "FEISHU_BITABLE_APP")
FEISHU_TABLE_ID = require_env("FEISHU_TABLE_ID", "FEISHU_BITABLE_TABLE")

FIELD_CONTENT = "Enquiry details（询盘内容）"
FIELD_DATE = "统计_每日"  # 公式字段，仅用于显示；不可用于 search API filter
FIELD_ENTRY_TIME = "Entry Time（录入时间）"  # DateTime 字段，用于日期过滤
FIELD_CLUE_LEVEL = "Clue level（线索等级）"
FIELD_EMAIL = "Email（客户邮箱）"


def get_feishu_token() -> str:
    timeout = float(os.environ.get("FEISHU_API_TIMEOUT", "15"))
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": os.environ["FEISHU_APP_ID"], "app_secret": os.environ["FEISHU_APP_SECRET"]},
        timeout=timeout,
    )
    data = resp.json()
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"飞书 token 获取失败: {data}")
    return token


def feishu_api(method: str, url: str, token: str, max_retries: int = 3,
               base_delay: float = 2.0, **kwargs) -> requests.Response:
    """带 429 重试的飞书 API 请求。429/5xx 指数退避，最多 max_retries 次。"""
    kwargs.setdefault("timeout", float(os.environ.get("FEISHU_API_TIMEOUT", "15")))
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"

    for attempt in range(max_retries + 1):
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code in (429,) or resp.status_code >= 500:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                log.warning(f"飞书 {resp.status_code}，{delay:.0f}s 后重试 ({attempt+1}/{max_retries})")
                time.sleep(delay)
                continue
        return resp

    return resp


def feishu_search_url() -> str:
    return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records/search"


def extract_text(field_val) -> str:
    if not field_val:
        return ""
    if isinstance(field_val, str):
        return field_val
    if isinstance(field_val, dict):
        val = field_val.get("value", "")
        if isinstance(val, dict):
            return str(val)
        return extract_text(val)
    if isinstance(field_val, list):
        return "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in field_val)
    return str(field_val)


def option_tokens(field_val) -> set[str]:
    """展开单选/公式布尔字段在 API 中可能出现的取值 token。"""
    tokens: set[str] = set()

    def _walk(val) -> None:
        if val in (None, ""):
            return
        if isinstance(val, bool):
            tokens.add("是" if val else "否")
            return
        if isinstance(val, str):
            text = val.strip()
            if text:
                tokens.add(text)
            return
        if isinstance(val, (int, float)):
            tokens.add(str(val))
            return
        if isinstance(val, list):
            for item in val:
                _walk(item)
            return
        if isinstance(val, dict):
            for key in ("id", "name", "text"):
                nested = val.get(key)
                if isinstance(nested, str) and nested.strip():
                    tokens.add(nested.strip())
            if "value" in val:
                _walk(val["value"])

    _walk(field_val)
    return tokens


def matches_option(field_val, tokens: frozenset[str] | set[str]) -> bool:
    return bool(option_tokens(field_val) & set(tokens))


def is_option_yes(field_val, yes_tokens: frozenset[str] | set[str]) -> bool:
    return matches_option(field_val, yes_tokens)


def is_option_no(field_val, no_tokens: frozenset[str] | set[str]) -> bool:
    return matches_option(field_val, no_tokens)


def send_alert_webhook(message: str):
    """飞书 webhook 告警，失败不影响主流程"""
    webhook_url = (os.environ.get("FEISHU_ALERT_WEBHOOK") or "").strip()
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={"msg_type": "text", "content": {"text": message}},
            timeout=10,
        )
    except Exception:
        pass


def alert_webhook_url() -> str:
    """告警/通知用 Webhook，仅来自环境变量（勿在代码中硬编码）。"""
    return (os.environ.get("FEISHU_ALERT_WEBHOOK") or "").strip()


def report_webhook_url() -> str:
    """日报/周报 Webhook；未配置 FEISHU_REPORT_WEBHOOK 时回退 FEISHU_ALERT_WEBHOOK。"""
    return (os.environ.get("FEISHU_REPORT_WEBHOOK") or os.environ.get("FEISHU_ALERT_WEBHOOK") or "").strip()


# 自动回复相关字段
FIELD_AUTOREPLY_STATUS = "Auto-Reply Status"
FIELD_AUTOREPLY_SENT_AT = "Auto-Reply Sent At"
FIELD_AUTOREPLY_TEMPLATE = "Auto-Reply Template"
FIELD_AUTOREPLY_ERROR = "Auto-Reply Error"
FIELD_GMAIL_THREAD_ID = "Gmail_Thread_ID"
FIELD_GMAIL_MSG_ID = "Gmail_Msg_ID"


def update_feishu_autoreply(token: str, record_id: str,
                            status: str, sent_at: str = "",
                            template: str = "", error: str = "",
                            thread_id: str = "", msg_id: str = "") -> bool:
    """更新飞书记录的自动回复状态，可选写入 Gmail threadId/msgId。"""
    fields = {FIELD_AUTOREPLY_STATUS: status}
    if sent_at:
        fields[FIELD_AUTOREPLY_SENT_AT] = sent_at
    if template:
        fields[FIELD_AUTOREPLY_TEMPLATE] = template
    if error:
        fields[FIELD_AUTOREPLY_ERROR] = error
    if thread_id:
        fields[FIELD_GMAIL_THREAD_ID] = thread_id
    if msg_id:
        fields[FIELD_GMAIL_MSG_ID] = msg_id
    try:
        resp = feishu_api("PUT",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FEISHU_TABLE_ID}/records/{record_id}",
            token=token, json={"fields": fields})
        data = resp.json()
        return data.get("code") == 0
    except Exception as e:
        log.warning("飞书自动回复状态更新失败: %s", e)
        return False


def find_record_by_thread_id(token: str, thread_id: str) -> dict | None:
    """按 Gmail_Thread_ID 查找飞书记录，返回 {"record_id": ...} 或 None。"""
    if not thread_id:
        return None
    resp = feishu_api("POST", feishu_search_url(), token=token,
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": FIELD_GMAIL_THREAD_ID, "operator": "is", "value": [thread_id]},
                ],
            },
            "field_names": [FIELD_GMAIL_THREAD_ID],
            "page_size": 1,
        })
    data = resp.json()
    if data.get("code") != 0:
        log.warning("threadId 查询异常: %s", data)
        return None
    items = data.get("data", {}).get("items", [])
    if not items:
        return None
    return {"record_id": items[0].get("record_id")}


def fetch_records_since(token: str, cutoff_ms: int, channel: str = "", page_size: int = 100) -> list:
    """用 list API 获取 cutoff_ms 之后的记录，可选按渠道客户端过滤。

    list API 按创建时间倒序返回，客户端用 Entry Time 截断。
    替代 search API + 统计_每日 filter（公式字段 filter 不生效）。
    """
    import urllib.request
    import json as _json

    all_items = []
    page_token = ""
    base_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"

    while True:
        url = f"{base_url}?page_size={page_size}"
        if page_token:
            url += f"&page_token={page_token}"

        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())

        items = data.get("data", {}).get("items", [])
        has_more = data.get("data", {}).get("has_more", False)
        page_token = data.get("data", {}).get("page_token", "")

        for item in items:
            f = item.get("fields", {})
            entry = f.get(FIELD_ENTRY_TIME, 0)
            if isinstance(entry, list):
                entry = entry[0] if entry else 0

            # 超出时间范围，停止
            if entry < cutoff_ms:
                return all_items

            # 可选渠道过滤
            if channel:
                ch = extract_text(f.get("Channels（渠道）", ""))
                if ch != channel:
                    continue

            all_items.append(item)

        if not has_more or not items:
            break

    return all_items


def search_filter_logs(token: str, table_id: str, start_ms: int, end_ms: int,
                       date_field: str = "Date", page_size: int = 500) -> list:
    """按时间窗口 [start_ms, end_ms) 查询飞书表（search API）。

    飞书 DateTime 字段 isGreater 不支持毫秒时间戳，故用全量 desc 拉取
    + 客户端窗口过滤 + early termination（遇 <start_ms 停翻页）。
    供 cloud-daily-report / cloud-weekly-report 复用。
    """
    all_items = []
    page_token = None
    while True:
        body = {
            "page_size": page_size,
            "sort": [{"field_name": date_field, "desc": True}],
        }
        if page_token:
            body["page_token"] = page_token

        resp = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{table_id}/records/search",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            log.warning("过滤日志查询失败: %s", data)
            break

        hit_before_window = False
        for item in data.get("data", {}).get("items", []):
            date_val = item.get("fields", {}).get(date_field, 0)
            if not isinstance(date_val, (int, float)):
                continue
            if date_val < start_ms:
                hit_before_window = True
                continue
            if date_val < end_ms:
                all_items.append(item)

        if hit_before_window:
            break
        page_token = data.get("data", {}).get("page_token")
        if not data.get("data", {}).get("has_more") or not page_token:
            break

    return all_items
