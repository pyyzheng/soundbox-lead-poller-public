"""垃圾漏网告警格式：带 outreach 原因和 skip_senders 域名提示。"""

from __future__ import annotations

_CONSUMER_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "qq.com", "163.com", "126.com",
    "foxmail.com", "proton.me", "protonmail.com",
}
HARD_OUTREACH_PREFIXES = (
    "advertising_outreach",
    "supplier_outreach",
    "seo_outreach",
)


def email_domain(email: str) -> str:
    addr = (email or "").strip().lower()
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1]


def skip_sender_hint(email: str) -> str:
    """非消费邮箱域名 → 可写入 skip_senders 的 pattern。"""
    domain = email_domain(email)
    if not domain or domain in _CONSUMER_EMAIL_DOMAINS:
        return ""
    return '{"pattern": "*@%s"}' % domain


def format_spam_leak_detail(item: dict) -> str:
    """健康检查/Issue 证据行：带 advertising_outreach 原因和 skip_senders 提示。"""
    signals = [str(s) for s in (item.get("signals") or []) if s]
    email = item.get("email") or ""
    domain = item.get("domain") or email_domain(email)
    outreach = next((s for s in signals if s.startswith(HARD_OUTREACH_PREFIXES)), "")
    parts = [
        f"record={item.get('record_id', '?')}",
        f"name={item.get('name', '')}",
        f"email={email}",
    ]
    if domain:
        parts.append(f"domain={domain}")
    if outreach:
        parts.append(f"reason={outreach}")
    elif signals:
        parts.append(f"signals={'+'.join(signals[:3])}")
    hint = item.get("skip_sender_hint") or skip_sender_hint(email)
    if hint:
        parts.append(f"skip_senders={hint}")
    return " | ".join(parts)


def leak_item(rec: dict, name: str, email: str, message: str, content: str, signals: list) -> dict:
    return {
        "record_id": rec.get("record_id", "?"),
        "name": (name or "")[:30],
        "email": email,
        "domain": email_domain(email),
        "message": (message or "")[:60],
        "signals": signals,
        "content_preview": (content or "")[:200],
        "skip_sender_hint": skip_sender_hint(email),
    }
