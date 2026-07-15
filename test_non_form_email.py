#!/usr/bin/env python3
"""
模拟非表单邮件处理测试 — Geeta/Idealab 印度合作邮件 + 名片附件

用法: ZHIPU_API_KEY=xxx python test_non_form_email.py
GitHub Actions: 自动从 secrets 读取
"""

import json
import os
import re
import sys

import requests

sys.path.insert(0, "lib")
from lead_filter_common import load_lead_rules, extract_email_address, check_spam, check_promotional_content, check_irrelevant_business, check_inquiry_keywords, check_marketing_footer, check_placeholder, check_skip_sender, check_skip_subject
from lead_fallback_parser import strip_html, extract_fields, extract_remote_ip, translate_country, identify_country, identify_product_category, identify_product_model, resolve_channel

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# ═══════════════════════════════════════════════════════════════
# 模拟数据
# ═══════════════════════════════════════════════════════════════

MOCK_MSG_ID = "19df908300a125c2"
MOCK_FROM = "Geeta Chaturvedi <geeta@idealab.company>"
MOCK_SUBJECT = "Partnership Opportunity - Soundproof Booths for Indian Market"
MOCK_BODY = """Dear Team,

I came across your soundproof booth products and I am very interested in exploring a partnership opportunity for the Indian market.

We are ANJ Group, a leading distribution company based in Mumbai, India with extensive networks in the commercial real estate and office solutions sector. We believe there is significant demand for your soundproof pods and meeting booths in India.

We would like to discuss becoming your exclusive distributor for the Indian market. Could you please share your product catalog, pricing, and any existing distribution agreements for India?

We are particularly interested in your SR and VR series models.

Please find my business card attached for your reference.

Best regards,
Geeta Chaturvedi
Director - Business Development
ANJ Group
geeta@idealab.company
+91 98765 43210
Mumbai, India"""

MOCK_ATTACHMENTS = [{"filename": "Geeta_Business_Card.vcf", "mime_type": "text/vcard", "size": 2048}]


# ═══════════════════════════════════════════════════════════════
# 从 cloud-lead-poller.py 提取的 LLM prompt（内联，避免 import 副作用）
# ═══════════════════════════════════════════════════════════════

def _load_llm_prompt():
    """从 cloud-lead-poller.py 源码中提取 LLM_SYSTEM_PROMPT"""
    with open("cloud-lead-poller.py", "r") as f:
        source = f.read()
    start = source.index('LLM_SYSTEM_PROMPT = """') + len('LLM_SYSTEM_PROMPT = """')
    end = source.index('"""', start)
    return source[start:end]


def call_llm_parse(from_addr, body, system_prompt):
    """调用智谱 GLM 解析邮件"""
    if not ZHIPU_API_KEY:
        return None, ""
    ip = extract_remote_ip(body)
    ip_info = ""
    ip_country_zh = ""
    if ip:
        try:
            with __import__("urllib.request").request.urlopen(
                f"http://ip-api.com/json/{ip}?fields=country,status", timeout=5
            ) as resp:
                data = json.loads(resp.read())
            if data.get("status") == "success" and data.get("country"):
                ip_info = f"\nRemote IP: {ip} (归属: {data['country']})"
                ip_country_zh = translate_country(data["country"])
        except Exception:
            pass

    user_msg = f"发件人: {from_addr}\n\n邮件正文:\n{body}{ip_info}"
    resp = requests.post(
        f"{ZHIPU_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": ZHIPU_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        },
        timeout=30,
    )
    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    text = m.group(1).strip() if m else content.strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return json.loads(text), ip_country_zh


def _build_tag_line(country, sub_channel, product_category, product_model):
    return "-".join(s or "无法识别" for s in [country, sub_channel, product_category, product_model])


def _format_inquiry_content(name, email, company, phone, message, tag_line):
    return (
        f"Name: {name}\nEmail: {email}\nCompany: {company}\n"
        f"Telephone Number: {phone}\nMessage: {message}\n\n{tag_line}"
    )


def main():
    rules = load_lead_rules()
    from_email = extract_email_address(MOCK_FROM)

    print("=" * 60)
    print("模拟非表单邮件处理 — Geeta/Idealab 印度合作 + 名片附件")
    print("=" * 60)
    print(f"  MSG ID:    {MOCK_MSG_ID}")
    print(f"  From:      {MOCK_FROM}")
    print(f"  Email:     {from_email}")
    print(f"  Subject:   {MOCK_SUBJECT}")
    print(f"  Body:      {len(MOCK_BODY)} chars")
    print(f"  附件:      {[a['filename'] for a in MOCK_ATTACHMENTS]}")
    print()

    # ── 1. extract_fields ──
    fields_pre = extract_fields(MOCK_BODY)
    print("── 1. extract_fields (规则引擎预提取) ──")
    for k, v in fields_pre.items():
        val = v[:80] if isinstance(v, str) and len(v) > 80 else v
        print(f"  {k}: {repr(val)}")
    print()

    # ── 2. 过滤链 ──
    print("── 2. 过滤链 ──")
    gate_action, gate_signals = run_filter_chain(
        from_email, MOCK_SUBJECT,
        fields_pre.get("name", ""), fields_pre.get("email", ""),
        fields_pre.get("message", ""), fields_pre.get("phone", ""),
        fields_pre.get("company", ""),
        MOCK_BODY, rules,
    )
    print(f"  action: {gate_action}, signals: {gate_signals}")
    if gate_action == "reject":
        print("\n⛔ 被过滤拦截")
        return
    print()

    # ── 3. LLM 解析 ──
    print("── 3. LLM 解析 ──")
    system_prompt = _load_llm_prompt()
    llm_result, ip_country_zh = call_llm_parse(MOCK_FROM, MOCK_BODY, system_prompt)

    if not llm_result:
        print("  ⚠️ LLM 解析失败")
    else:
        print(f"  status:          {llm_result.get('status')}")
        print(f"  is_website_form: {llm_result.get('is_website_form')}")
        print(f"  name:            {llm_result.get('name')}")
        print(f"  email:           {llm_result.get('email')}")
        print(f"  company:         {llm_result.get('company')}")
        print(f"  phone:           {llm_result.get('phone')}")
        print(f"  country:         {llm_result.get('country')}")
        print(f"  product_category:{llm_result.get('product_category')}")
        print(f"  product_model:   {llm_result.get('product_model')}")
        print(f"  channel:         {llm_result.get('channel')}")
        print(f"  sub_channel:     {llm_result.get('sub_channel')}")
        print(f"  message:         {repr((llm_result.get('message') or '')[:120])}")
    print()

    if not llm_result or llm_result.get("status") != "parsed":
        print("── 规则引擎兜底 ──")
        fields = extract_fields(MOCK_BODY)
        country = identify_country("", fields["phone"], fields["message"], "")
        channel, sub_channel = resolve_channel(MOCK_FROM, rules, MOCK_SUBJECT)
        product_category = identify_product_category(fields["message"], rules)
        product_model = identify_product_model(fields["message"], rules)
        tag_line = _build_tag_line(country, sub_channel, product_category, product_model)
        email = fields["email"] or from_email
        inquiry_content = _format_inquiry_content(
            fields["name"], email, fields["company"],
            fields["phone"], fields["message"], tag_line,
        )
        print(f"  tag_line: {tag_line}")
        print(f"  email: {email}")
    else:
        _, llm_sub_channel = resolve_channel(MOCK_FROM, rules, MOCK_SUBJECT)
        # 内联简化版 normalize（只看关键输出）
        tag_line = _build_tag_line(
            llm_result.get("country", ""), llm_sub_channel,
            llm_result.get("product_category", ""), llm_result.get("product_model", ""),
        )
        email = llm_result.get("email") or from_email
        inquiry_content = _format_inquiry_content(
            llm_result.get("name", ""), email, llm_result.get("company", ""),
            llm_result.get("phone", ""), strip_html(llm_result.get("message", "")),
            tag_line,
        )

    print()
    print("── 5. 最终 inquiry_content ──")
    print(inquiry_content)
    print()
    print("── 6. 附件处理（模拟） ──")
    print(f"  附件列表: {[a['filename'] for a in MOCK_ATTACHMENTS]}")
    print(f"  → process_attachments() 会:")
    print(f"     1. download_gmail_attachment(service, msg_id, attachment_id)")
    print(f"     2. upload_to_feishu(token, file_data, filename, mime_type)")
    print(f"     3. 返回 [{{'file_token': 'xxx'}}]")
    print(f"     4. 写入 create_feishu_record(attachment_tokens=[...])")
    print(f"     5. 飞书字段: Enquiry attachments（询盘附件）")
    print()
    print("=" * 60)
    print("模拟完成")


# 过滤链内联（避免 import cloud-lead-poller 的副作用）
def run_filter_chain(from_addr, subject, name, email, message, phone, company, raw_body, rules):
    signals = []
    ok, reason = check_skip_sender(from_addr, rules)
    if ok: signals.append(reason)
    ok, reason = check_skip_subject(subject, rules)
    if ok: signals.append(reason)
    ok, reason = check_spam(name, email, message, rules)
    if ok: signals.append(reason)
    ok, reason = check_placeholder(name, email, phone, company)
    if ok: signals.append(reason)
    ok, reason = check_promotional_content(name, subject, message, company, rules, raw_body)
    if ok: signals.append(reason)
    ok, reason = check_irrelevant_business(name, company, message, rules, raw_body)
    if ok: signals.append(reason)
    ok, reason = check_inquiry_keywords(name, message, company, rules)
    if not ok: signals.append(reason)
    ok, reason = check_marketing_footer(raw_body)
    if ok: signals.append(reason)

    action = "reject" if len(signals) >= 2 else "pass"
    return action, signals


if __name__ == "__main__":
    main()
