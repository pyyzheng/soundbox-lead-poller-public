#!/usr/bin/env python3
"""
CLI 封装：接收 JSON stdin，调 lead_filter_common 全量过滤，输出 JSON stdout。
供 lead-gate.js 调用。

用法: echo '{"name":"x","email":"x","message":"x"}' | python3 lead_filter_cli.py --rules /path/to/lead-rules.json
"""
import json, sys, os, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lead_filter_common import (
    check_spam, check_placeholder, check_promotional_content,
    check_gibberish_message, body_has_message_field,
    check_irrelevant_business, check_inquiry_keywords,
    check_supplier_outreach, check_trivial_content, check_short_message,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", required=True, help="lead-rules.json 绝对路径")
    args = parser.parse_args()

    with open(args.rules, "r", encoding="utf-8") as f:
        rules = json.load(f)

    data = json.load(sys.stdin)
    name = data.get("name", "")
    email = data.get("email", "")
    phone = data.get("phone", "")
    company = data.get("company", "")
    message = data.get("message", "")
    subject = data.get("subject", "")
    raw_body = data.get("raw_body", "")
    has_message_field = data.get("has_message_field")
    if has_message_field is None:
        has_message_field = body_has_message_field(raw_body) if raw_body else bool(message.strip())

    signals = []

    is_gibberish, reason = check_gibberish_message(
        message, rules, has_message_field=bool(has_message_field),
    )
    if is_gibberish:
        json.dump({"action": "reject", "score": 1, "signals": [reason]}, sys.stdout, ensure_ascii=False)
        return

    is_short, reason = check_short_message(
        message, rules, has_message_field=bool(has_message_field),
    )
    if is_short:
        json.dump({"action": "reject", "score": 1, "signals": [reason]}, sys.stdout, ensure_ascii=False)
        return

    is_supplier, reason = check_supplier_outreach(
        message, company, subject, raw_body, rules=rules,
    )
    if is_supplier:
        json.dump({"action": "reject", "score": 1, "signals": [reason]}, sys.stdout, ensure_ascii=False)
        return

    is_trivial, reason = check_trivial_content(name, message, rules)
    if is_trivial:
        json.dump({"action": "reject", "score": 1, "signals": [reason]}, sys.stdout, ensure_ascii=False)
        return

    is_spam, reason = check_spam(name, email, message, rules)
    if is_spam:
        signals.append(reason)

    is_ph, reason = check_placeholder(name, email, phone, company)
    if is_ph:
        signals.append(reason)

    is_sc, reason = check_promotional_content(name, subject, message, company, rules, raw_body)
    if is_sc:
        signals.append(reason)

    is_ib, reason = check_irrelevant_business(name, company, message, rules, raw_body)
    if is_ib:
        signals.append(reason)

    has_kw, reason = check_inquiry_keywords(name, message, company, rules)
    if not has_kw:
        signals.append(reason)

    score = len(signals)
    if score >= 2:
        action = "reject"
    else:
        action = "pass"

    json.dump({"action": action, "score": score, "signals": signals}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
