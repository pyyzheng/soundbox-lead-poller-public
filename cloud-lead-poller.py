#!/usr/bin/env python3
"""
cloud-lead-poller.py — Gmail 询盘云端轮询处理器
适用于 GitHub Actions / 任何 Linux 云端环境

每次运行流程：
  1. Gmail API 认证（使用 refresh_token，无需本地 Keychain / 代理）
  2. 搜索未处理邮件（无 processed-by-openclaw 标签 + 来自目标发件人）
  3. 6 层过滤链（skip_sender → skip_subject → spam → semantic_spam → irrelevant → keyword）
  4. 解析邮件内容（LLM 优先，规则引擎兜底）
  5. LLM 输出标准化校验（product_name_map / model_name_map / acoustic_subtype_map）
  6. 飞书去重（按 Email 字段查重）
  7. 写入飞书多维表格
  8. Gmail 邮件打标签（processed-by-openclaw）

核心过滤/解析模块复用自本地版 lead_filter_common.py 和 lead_fallback_parser.py。
"""

import os
import sys
import json
import re
import random
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests

# ── 导入共享模块 ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lead_filter_common import (
    load_lead_rules,
    extract_email_address,
    check_skip_sender, check_skip_subject, check_skip_sender_categories,
    check_spam,
    check_gibberish_message,
    check_gibberish_inquiry,
    check_short_inquiry,
    check_promotional_content,
    check_irrelevant_business, check_inquiry_keywords, should_force_inquiry_intent,
    check_marketing_header,
    check_platform_marketplace_notification,
    check_marketing_footer,
    check_placeholder,
    check_trivial_content,
    check_form_spam_submission,
    check_supplier_outreach,
    check_system_notification,
)
from lead_fallback_parser import (
    strip_html, extract_fields, extract_remote_ip,
    translate_country, identify_country,
    identify_product_category, identify_product_model,
    resolve_channel,
)
from lead_grader import grade_lead, format_grading_section
from email_template import generate_template_email
from email_sender import send_reply_email, compose_email_reply
from feishu_utils import find_record_by_thread_id, feishu_api, extract_text, require_env

from gmail_client import (
    get_gmail_service, get_or_create_label, search_unprocessed_emails,
    get_message_detail, apply_label, extract_email_body,
    get_header, get_reply_to, process_attachments,
    GMAIL_LABEL, MAX_EMAIL_AGE_DAYS,
)
from url_model_map import identify_model_from_url, extract_page_url, extract_url_keyword
from feishu_writer import (
    get_feishu_token, check_feishu_duplicate, check_feishu_email_duplicate,
    merge_feishu_record,
    create_feishu_record, update_feishu_autoreply,
    FEISHU_APP_TOKEN, FEISHU_TABLE_ID, FEISHU_FIELD_NAME,
)
from assignment_fields import FIELD_CHANNELS, FIELD_SUB_CHANNEL, SUB_CHANNEL_TO_CHANNEL  # noqa: E402
from tagline_fields import feishu_product_category  # noqa: E402
from llm_parser import (
    call_llm_parse, normalize_llm_output,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("lead-poller")

# ── 固定配置 ──────────────────────────────────────────────────────────────────
RULES_FILE = Path(__file__).parent / "lead-rules.json"
AUTO_REPLY_ENABLED = os.environ.get("AUTO_REPLY_ENABLED", "false") == "true"
AUTO_REPLY_DRY_RUN = os.environ.get("AUTO_REPLY_DRY_RUN", "true") == "true"
AUTO_REPLY_SAMPLE_RATE = float(os.environ.get("AUTO_REPLY_SAMPLE_RATE", "1.0"))
NO_TEMPLATE_ALERT_THRESHOLD = 3

# 自动回复状态常量
AR_SKIPPED = "Skipped"
AR_SAMPLED_OUT = "Sampled-Out"
AR_NO_TEMPLATE = "No-Template"
AR_ERROR = "Error"
AR_SENT = "Sent"
AR_DRY_RUN = "Dry-Run"


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def _structured_write_fields(
    *,
    sub_channel: str,
    country: str = "",
    product_category: str = "",
    product_model: str = "",
) -> dict[str, str]:
    """Gmail 写入飞书时同步结构化渠道/产品字段，避免只靠询盘尾行标签。"""
    fields: dict[str, str] = {}
    sub = (sub_channel or "").strip()
    if sub and sub != "无法识别":
        fields[FIELD_SUB_CHANNEL] = sub
        fields[FIELD_CHANNELS] = SUB_CHANNEL_TO_CHANNEL.get(sub, sub)
    if country and country not in {"Unknown", "无法识别"}:
        fields["Country（国家）"] = country
    if product_category and product_category not in {"", "无法识别"}:
        fields["Product Categories（产品大类）"] = feishu_product_category(product_category)
    if product_model and product_model not in {"", "无法识别"}:
        fields["Product model（具体型号）"] = product_model
    return fields


def _build_tag_line(country: str, sub_channel: str, product_category: str, product_model: str) -> str:
    return "-".join(s or "无法识别" for s in [country, sub_channel, product_category, product_model])


def _format_inquiry_content(name: str, email: str, company: str, phone: str,
                            message: str, tag_line: str) -> str:
    return (
        f"Name: {name}\nEmail: {email}\nCompany: {company}\n"
        f"Telephone Number: {phone}\nMessage: {message}\n\n{tag_line}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6 层过滤链
# ═══════════════════════════════════════════════════════════════════════════════

def run_filter_chain(from_addr: str, subject: str, name: str, email: str,
                     message: str, phone: str, company: str, body: str, rules: dict,
                     has_unsubscribe_header: bool = False,
                     has_message_field: bool = False) -> tuple[str, list[str]]:
    """运行完整 6 层过滤链，返回 (action, signals)

    action: "pass" | "reject"
      - pass: 0-1 个信号，正常通过（grader 评估线索质量）
      - reject: ≥2 个信号，跳过并打标签
    """
    # Layer 1 & 2 是邮件级硬拦截，直接 reject
    skip, reason = check_skip_sender(from_addr, rules)
    if skip:
        return "reject", [reason]

    skip, reason = check_skip_sender_categories(from_addr, rules)
    if skip:
        return "reject", [reason]

    skip, reason = check_skip_subject(subject, rules)
    if skip:
        return "reject", [reason]

    # 硬拦截：系统自动通知邮件（发件人 noreply 或正文含"系统自动"+"请勿回复"）
    sys_notif, reason = check_system_notification(from_addr, body)
    if sys_notif:
        return "reject", [reason]

    plat_case, reason = check_platform_marketplace_notification(from_addr, subject, body)
    if plat_case:
        return "reject", [reason]

    # 硬拦截：List-Unsubscribe header = 确定性营销邮件
    mkt_header, reason = check_marketing_header(has_unsubscribe_header)
    if mkt_header:
        return "reject", [reason]

    gibberish, reason = check_gibberish_inquiry(
        message, rules, body=body, fields_pre={"has_message_field": has_message_field},
    )
    if gibberish:
        return "reject", [reason]

    short_msg, reason = check_short_inquiry(
        message, rules, body=body, fields_pre={"has_message_field": has_message_field},
    )
    if short_msg:
        return "reject", [reason]

    supplier, reason = check_supplier_outreach(
        message, company, subject, body, rules=rules,
    )
    if supplier:
        return "reject", [reason]

    trivial, reason = check_trivial_content(name, message, rules)
    if trivial:
        return "reject", [reason]

    form_spam, reason = check_form_spam_submission(
        name, message, phone, company, rules,
        has_message_field=has_message_field,
    )
    if form_spam:
        return "reject", [reason]

    # T2: 内容级信号累加（≥2 → reject）
    signals = []

    spam, reason = check_spam(name, email or from_addr, message, rules)
    if spam:
        signals.append(reason)

    placeholder, reason = check_placeholder(name, email or from_addr, phone, company)
    if placeholder:
        signals.append(reason)

    promo, reason = check_promotional_content(name, subject, message, company, rules, raw_body=body)
    if promo:
        signals.append(reason)

    irr, reason = check_irrelevant_business(name, company, message, rules, raw_body=body)
    if irr:
        signals.append(reason)

    has_kw, reason = check_inquiry_keywords(name, message, company, rules, subject=subject)
    if not has_kw:
        signals.append(reason)

    marketing, reason = check_marketing_footer(body)
    if marketing:
        signals.append(reason)

    score = len(signals)
    if score >= 2:
        return "reject", signals
    return "pass", signals


# ═══════════════════════════════════════════════════════════════════════════════
# 主处理流程
# ═══════════════════════════════════════════════════════════════════════════════

def _skip_and_label(service, msg_id: str, label_id: str, status: str, reason: str, **extra) -> dict:
    apply_label(service, msg_id, label_id)
    return {"id": msg_id, "status": status, "reason": reason, **extra}


def is_ooo_reply(body: str, msg_data: dict) -> bool:
    """识别 OOO/自动回复（不当客户回复，避免 OOO 触发 Customer-Replied/分配）。

    两类信号：
    1. RFC 3834 Auto-Submitted 头（值 != "no" 即自动）
    2. 正文关键词（out of office / short leave / limited access / will respond upon return 等）
    """
    auto_submitted = (get_header(msg_data, "Auto-Submitted") or "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return True
    text = (body or "").lower()
    if not text:
        return False
    ooo_patterns = [
        r"out of (the )?office", r"currently on (a )?short leave", r"on (annual|maternity|sick) leave",
        r"limited access to my email", r"will respond.*(upon|after) (my )?return",
        r"auto[- ]reply", r"automatic(ally)? reply", r"automated response",
        r"do not reply to this (automated|auto)", r"this is an automated message",
    ]
    return any(re.search(p, text) for p in ooo_patterns)


def is_bounce_reply(body: str, msg_data: dict) -> bool:
    """识别退信/投递失败通知（不当客户回复）。

    信号：from mailer-daemon/postmaster，或 subject 含 Delivery Status Notification /
    Undelivered / Mail Delivery Failed / Failure Notice / Returned mail。
    """
    from_addr = (get_header(msg_data, "From") or "").lower()
    if "mailer-daemon" in from_addr or "postmaster" in from_addr:
        return True
    subject = (get_header(msg_data, "Subject") or "").lower()
    bounce_patterns = [
        r"delivery status notification", r"undeliverable", r"undelivered",
        r"mail delivery failed", r"delivery failure", r"failure notice",
        r"returned mail", r"delivery has failed",
    ]
    return any(re.search(p, subject) for p in bounce_patterns)


# ── Follow-up Records 表（Facebook 线索客户回复内容同步）─────────────────────
FOLLOWUP_TABLE_ID = require_env("FEISHU_FOLLOWUP_TABLE")
FIELD_RELATED_LEAD = "Related Lead"
FIELD_FOLLOWUP_DETAILS = "Follow-up Details"
FIELD_FOLLOWUP_TIME = "Follow-up Time"
FIELD_CONTACT_RESULT = "Contact Result"
FIELD_CONTACT_METHOD = "Contact Method"
FIELD_CHANNELS = "Channels（渠道）"


def _parse_email_date_ms(date_str: str):
    """RFC2822 Date header → 毫秒时间戳（飞书 datetime 字段）。"""
    if not date_str:
        return None
    try:
        return int(parsedate_to_datetime(date_str).timestamp() * 1000)
    except Exception:
        return None


def _get_record_fields(token: str, record_id: str):
    """读取主表记录的字段值（用于渠道判断）。"""
    resp = feishu_api("GET",
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records/{record_id}",
        token=token)
    data = resp.json()
    if data.get("code") != 0:
        log.warning("读取主记录字段失败: %s", data)
        return None
    return data.get("data", {}).get("record", {}).get("fields", {})


def _followup_exists(token: str, lead_record_id: str, contact_result: str) -> bool:
    """Follow-up 表是否已有该 lead + Contact Result 记录（去重，尽力而为）。"""
    try:
        resp = feishu_api("POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FOLLOWUP_TABLE_ID}/records/search",
            token=token, json={
                "filter": {"conjunction": "and", "conditions": [
                    {"field_name": FIELD_RELATED_LEAD, "operator": "is", "value": [lead_record_id]},
                    {"field_name": FIELD_CONTACT_RESULT, "operator": "is", "value": [contact_result]},
                ]},
                "page_size": 1,
            })
        data = resp.json()
        return (data.get("data", {}).get("total", 0) > 0)
    except Exception as e:
        log.warning("Follow-up 去重查询异常: %s", e)
        return False


def sync_reply_followup(token: str, lead_record_id: str,
                        reply_body: str, msg_data: dict) -> None:
    """客户回复 → 在 Follow-up Records 表创建跟进记录（同步回复内容）。

    所有渠道生效（Facebook/Google 等）。失败不影响主链路（已 try 包裹）。
    """
    try:
        # 去重：同 lead + Customer Replied 已存在则跳过（防重复处理）
        if _followup_exists(token, lead_record_id, "Customer Replied 客户已回复"):
            log.info("Follow-up 已存在，跳过: lead=%s", lead_record_id)
            return

        followup_fields = {
            FIELD_RELATED_LEAD: [{"id": lead_record_id, "type": "text"}],
            FIELD_FOLLOWUP_DETAILS: (reply_body or "").strip()[:8000] or "(空正文)",
            FIELD_CONTACT_RESULT: "Customer Replied 客户已回复",
            FIELD_CONTACT_METHOD: "Email",
        }
        ts_ms = _parse_email_date_ms(get_header(msg_data, "Date"))
        if ts_ms:
            followup_fields[FIELD_FOLLOWUP_TIME] = ts_ms

        resp = feishu_api("POST",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
            f"/tables/{FOLLOWUP_TABLE_ID}/records",
            token=token, json={"fields": followup_fields})
        data = resp.json()
        if data.get("code") == 0:
            fup_id = data.get("data", {}).get("record", {}).get("record_id", "")
            log.info("Facebook 回复已同步 Follow-up: lead=%s | followup=%s",
                     lead_record_id, fup_id)
        else:
            log.warning("Follow-up 创建失败: %s", data)
    except Exception as e:
        log.warning("Facebook 回复同步 Follow-up 异常: %s", e)


def process_email(service, msg_data: dict, label_id: str, feishu_token: str, rules: dict,
                  _batch_dedup: set | None = None) -> dict:
    """处理单封邮件：过滤 → 解析 → 标准化 → 去重 → 写入飞书"""
    msg_id = msg_data["id"]

    from_addr = get_header(msg_data, "From")
    to_addr = get_header(msg_data, "To")
    subject = get_header(msg_data, "Subject")
    body = extract_email_body(msg_data)

    log.info("处理邮件: id=%s | from=%s | subject=%s", msg_id, from_addr, subject[:60])

    # 提取纯邮箱地址（用于过滤）
    from_email = extract_email_address(from_addr)

    # 公司自有邮箱（用于黑名单 + 自回复检测 + 邮箱关联排除）
    _own_emails = [k for k in rules.get("channels", {}).keys() if not k.startswith("_")]
    _own_emails.append("soundboxbooth@gmail.com")

    # ── 0.4 OOO/退信过滤：不当客户回复（避免触发 Customer-Replied/分配）──
    if is_ooo_reply(body, msg_data):
        log.info("OOO 自动回复，跳过: msg=%s | from=%s", msg_id, from_email)
        return _skip_and_label(service, msg_id, label_id, "skipped", "ooo_auto_reply")
    if is_bounce_reply(body, msg_data):
        log.info("退信/投递失败通知，跳过: msg=%s | from=%s", msg_id, from_email)
        return _skip_and_label(service, msg_id, label_id, "skipped", "bounce_notification")

    # ── 0.5 客户回复检测：threadId 命中 → 轻量更新 + 跳过完整处理 ──
    thread_id = msg_data.get("threadId", "")
    if thread_id:
        reply_record = find_record_by_thread_id(feishu_token, thread_id)
        if reply_record:
            lead_rid = reply_record["record_id"]
            update_feishu_autoreply(feishu_token, lead_rid, "Customer-Replied", msg_id=msg_id)
            # Facebook 线索：同步客户回复内容到 Follow-up Records 表
            sync_reply_followup(feishu_token, lead_rid, body, msg_data)
            log.info("客户回复检测命中: thread=%s | record=%s | msg=%s",
                     thread_id, lead_rid, msg_id)
            apply_label(service, msg_id, label_id)
            return {"id": msg_id, "status": "reply_tracked",
                    "record_id": lead_rid}

    # ── 0.6 邮箱关联兜底：threadId miss 时，按发件人邮箱匹配已发首联的 lead ──
    # 场景：首联是手动发的（threadId 未写回飞书），客户回复时 threadId 无法命中。
    # 启发式：已发首联（Auto-Reply Status=Sent）的 lead 收到同邮箱来信 → 识别为客户回复。
    if from_email and from_email not in _own_emails:
        email_lead = check_feishu_email_duplicate(
            feishu_token, from_email,
            hours=336,  # 14 天，覆盖回复延迟
            extra_fields=["Auto-Reply Status"],
        )
        if email_lead:
            ar_status = extract_text(
                email_lead.get("fields", {}).get("Auto-Reply Status", ""))
            if ar_status == "Sent":
                lead_rid = email_lead.get("record_id", "")
                update_feishu_autoreply(feishu_token, lead_rid, "Customer-Replied", msg_id=msg_id)
                sync_reply_followup(feishu_token, lead_rid, body, msg_data)
                log.info("邮箱关联兜底命中: email=%s | record=%s | msg=%s",
                         from_email, lead_rid, msg_id)
                apply_label(service, msg_id, label_id)
                return {"id": msg_id, "status": "reply_tracked", "record_id": lead_rid}

    # ── 1. 预过滤：先提取字段用于过滤链 ──
    fields_pre = extract_fields(body)

    # 客户邮箱黑名单检测（表单提取的 email，非邮件发件人）
    cust_email = (fields_pre.get("email", "") or "").strip().lower()
    for entry in rules.get("skip_senders", []):
        if isinstance(entry, dict):
            pattern = entry.get("pattern", "")
            if pattern:
                regex = pattern.replace(".", r"\.").replace("*", ".*")
                if re.match(f"^{regex}$", cust_email, re.IGNORECASE):
                    log.info("客户邮箱黑名单: %s matched %s", cust_email, pattern)
                    apply_label(service, msg_id, label_id)
                    return {"id": msg_id, "status": "skipped", "reason": f"skip_customer_email(pattern:{pattern})"}

    # 自回复检测（规则引擎阶段）：客户邮箱是公司自有地址 → 跳过
    if cust_email in _own_emails:
        log.info("自回复拦截: 客户邮箱=公司邮箱 %s", cust_email)
        apply_label(service, msg_id, label_id)
        return {"id": msg_id, "status": "skipped", "reason": "self_reply(own_email)"}

    unsub_header = get_header(msg_data, "List-Unsubscribe")
    has_unsub_header = bool(unsub_header and unsub_header.strip())

    gate_action, gate_signals = run_filter_chain(
        from_email, subject,
        fields_pre.get("name", ""), fields_pre.get("email", ""),
        fields_pre.get("message", ""), fields_pre.get("phone", ""),
        fields_pre.get("company", ""),
        body, rules,
        has_unsubscribe_header=has_unsub_header,
        has_message_field=fields_pre.get("has_message_field", False),
    )
    if gate_action == "reject":
        log.info("过滤拦截(reject): %s", " + ".join(gate_signals))
        apply_label(service, msg_id, label_id)
        return {"id": msg_id, "status": "skipped", "reason": f"gate_reject: {'+'.join(gate_signals)}"}
    elif gate_signals:
        log.info("过滤通过(1信号放行): %s", " + ".join(gate_signals))

    # ── 2. LLM 解析 + 标准化 ──
    inquiry_content = ""
    email = fields_pre.get("email", "")

    llm_result, ip_country_zh = call_llm_parse(from_addr, body)

    if llm_result and llm_result.get("status") == "parsed":
        # ── L3: intent gate ──
        if llm_result.get("intent") == "non_inquiry":
            llm_message = strip_html(llm_result.get("message", ""))
            if should_force_inquiry_intent(
                subject, llm_message,
                llm_result.get("name", ""), llm_result.get("company", ""),
                rules=rules, body=body,
            ) and not check_platform_marketplace_notification(from_email, subject, body)[0]:
                log.info(
                    "L3 non_inquiry 覆写为 inquiry: subject=%s | email=%s",
                    subject[:60], (llm_result.get("email") or from_email)[:40],
                )
                llm_result["intent"] = "inquiry"
            else:
                log.info("L3 intent 拦截(non_inquiry): name=%s, email=%s, msg=%s",
                         llm_result.get("name", "")[:20],
                         llm_result.get("email", "")[:30],
                         llm_message[:50])
                return _skip_and_label(service, msg_id, label_id, "skipped", "L3_non_inquiry")

        gibberish, gib_reason = check_gibberish_inquiry(
            strip_html(llm_result.get("message", "")),
            rules,
            body=body,
            fields_pre=fields_pre,
        )
        if gibberish:
            log.info("post-LLM 乱码拦截: %s", gib_reason)
            return _skip_and_label(service, msg_id, label_id, "skipped", gib_reason)

        form_spam, form_reason = check_form_spam_submission(
            llm_result.get("name", ""),
            strip_html(llm_result.get("message", "")),
            llm_result.get("phone", ""),
            llm_result.get("company", ""),
            rules,
            has_message_field=fields_pre.get("has_message_field", True),
        )
        if form_spam:
            log.info("post-LLM 表单垃圾拦截: %s", form_reason)
            return _skip_and_label(service, msg_id, label_id, "skipped", form_reason)

        short_msg, short_reason = check_short_inquiry(
            strip_html(llm_result.get("message", "")),
            rules,
            body=body,
            fields_pre=fields_pre,
        )
        if short_msg:
            log.info("post-LLM 过短留言拦截: %s", short_reason)
            return _skip_and_label(service, msg_id, label_id, "skipped", short_reason)

        supplier, sup_reason = check_supplier_outreach(
            strip_html(llm_result.get("message", "")),
            llm_result.get("company", ""),
            subject,
            body,
            rules=rules,
        )
        if supplier:
            log.info("post-LLM 供应商推销拦截: %s", sup_reason)
            return _skip_and_label(service, msg_id, label_id, "skipped", sup_reason)

        # LLM 解析成功 → 标准化输出
        _, llm_sub_channel = resolve_channel(from_addr, rules, subject, to_addr)
        normalized = normalize_llm_output(
            llm_result, rules, sub_channel=llm_sub_channel,
            build_tag_line=_build_tag_line,
            format_inquiry_content=_format_inquiry_content,
        )

        # IP 交叉验证：港澳台以 IP 为准（LLM 可能输出"中国"）
        if ip_country_zh and ip_country_zh in ("香港", "澳门", "台湾"):
            if normalized["country"] != ip_country_zh:
                log.info("IP 覆盖 LLM 国家: LLM=%s → IP=%s", normalized["country"], ip_country_zh)
                normalized["country"] = ip_country_zh
                tag_line = _build_tag_line(ip_country_zh, normalized["sub_channel"], normalized["product_category"], normalized["product_model"])
                parsed_msg = strip_html(llm_result.get("message", ""))
                normalized["inquiry_content"] = _format_inquiry_content(
                    llm_result.get("name", ""), llm_result.get("email", ""),
                    llm_result.get("company", ""), llm_result.get("phone", ""),
                    parsed_msg, tag_line,
                )
                normalized["tag_line"] = tag_line
        inquiry_content = normalized["inquiry_content"]
        email = llm_result.get("email") or email

        # ── post-LLM 自回复检测：LLM 提取的客户邮箱是公司自有地址 ──
        # 规则引擎可能无法解析聊天机器人通知格式，但 LLM 能提取到
        if email and email.strip().lower() in _own_emails:
            log.info("post-LLM 自回复拦截: email=%s", email)
            return _skip_and_label(service, msg_id, label_id, "skipped", "self_reply(own_email)")

        # ── post-LLM 门控：LLM 无法识别产品类别 → 硬拒绝 ──
        # LLM 也无法判断产品类别，说明邮件内容与产品无关（招商/赞助/服务推销等）
        # 不再依赖关键词判断：公司名(soundbox/acoustic)和通用词(partner)会穿透关键词检查
        if normalized.get("product_category") in ("无法识别", ""):
            log.info("post-LLM 硬拒绝: 产品类别无法识别 | from=%s", from_email)
            return _skip_and_label(service, msg_id, label_id, "skipped",
                                   "product_unrecognized")

        # 兜底：非表单邮件 LLM 可能未提取 email，用发件人地址填充
        # 排除公司自有邮箱：AI 通知发件人是系统邮箱，客户没邮箱时不应 fallback 成系统邮箱
        if not email and from_email and from_email not in _own_emails:
            inquiry_content = _format_inquiry_content(
                llm_result.get("name", ""), from_email, llm_result.get("company", ""),
                llm_result.get("phone", ""), strip_html(llm_result.get("message", "")),
                normalized["tag_line"],
            )
            email = from_email

        log.info("LLM 解析+标准化: name=%s | country=%s | tag=%s",
                 llm_result.get("name"), normalized["country"], normalized["tag_line"])

        # inquiry_content 不能为空
        if not inquiry_content or not any([
            llm_result.get("name"), llm_result.get("email"),
            llm_result.get("company"), llm_result.get("phone"), llm_result.get("message"),
        ]):
            log.info("LLM 返回内容为空，回退到规则引擎")
            llm_result = None

    elif llm_result and llm_result.get("status") == "skipped":
        log.warning("LLM 判定跳过（理论上不应触发，T1 应已拦截）: %s", llm_result.get("reason"))
        apply_label(service, msg_id, label_id)
        return {"id": msg_id, "status": "skipped", "reason": llm_result.get("reason")}

    if not llm_result or llm_result.get("status") != "parsed":
        # ── 3. 规则引擎兜底 ──
        log.info("回退到规则引擎")
        fields = extract_fields(body)
        ip = extract_remote_ip(body)
        country = identify_country(ip, fields["phone"], fields["message"], fields.get("country", ""))
        channel, sub_channel = resolve_channel(from_addr, rules, subject, to_addr)
        product_category = identify_product_category(fields["message"], rules)
        # 规则引擎兜底时：无产品类别 → 检查是否有关键词
        # 无关键词 + 无产品类别 = 内容与业务无关 → 硬拒绝
        if not product_category:
            has_kw, _ = check_inquiry_keywords(
                fields["name"], fields["message"], fields["company"], rules, subject=subject)
            if not has_kw:
                log.info("规则引擎硬拒绝: 无产品类别 + 无关键词 | from=%s", from_email)
                return _skip_and_label(service, msg_id, label_id, "skipped",
                                       "rule_engine_no_keyword_no_product")
            # 谷歌2是舱网，有关键词但无匹配产品时默认静音舱
            if sub_channel in ("谷歌1", "谷歌2"):
                product_category = "静音舱"
        product_model = identify_product_model(fields["message"], rules, sub_channel)
        # URL 型号优先级更高
        page_url = extract_page_url(body)
        url_model = identify_model_from_url(page_url)
        if url_model:
            product_model = url_model
            log.info("从 Page URL 提取型号: %s → %s", page_url, url_model)

        tag_line = _build_tag_line(country, sub_channel, product_category, product_model)
        # from_email 是系统邮箱时（AI 通知客户没邮箱），不 fallback，email 留空
        email = fields["email"] or (from_email if from_email not in _own_emails else "")
        gibberish, gib_reason = check_gibberish_inquiry(
            fields["message"], rules, body=body, fields_pre=fields,
        )
        if gibberish:
            log.info("规则引擎乱码拦截: %s", gib_reason)
            return _skip_and_label(service, msg_id, label_id, "skipped", gib_reason)
        form_spam, form_reason = check_form_spam_submission(
            fields["name"], fields["message"], fields["phone"], fields["company"],
            rules, has_message_field=fields.get("has_message_field", True),
        )
        if form_spam:
            log.info("规则引擎表单垃圾拦截: %s", form_reason)
            return _skip_and_label(service, msg_id, label_id, "skipped", form_reason)
        inquiry_content = _format_inquiry_content(
            fields["name"], email, fields["company"],
            fields["phone"], fields["message"], tag_line,
        )

    # ── 4. 线索分级 ──
    # 统一提取国家/公司/产品/姓名（供自动回复使用）
    if llm_result and llm_result.get("status") == "parsed":
        customer_country = normalized.get("country", "")
        customer_company = llm_result.get("company", "")
        customer_product = normalized.get("product_category", "")
        customer_name = llm_result.get("name", "")
        customer_channel = normalized.get("sub_channel", "Google")
    else:
        customer_country = country
        customer_company = fields.get("company", "")
        customer_product = product_category
        customer_name = fields.get("name", "")
        customer_channel = sub_channel if isinstance(sub_channel, str) else "Google"
    clue_level = ""
    grading_text = ""
    grading_result = None
    try:
        grading_result = grade_lead(body, email=email)
        if grading_result:
            level_raw = grading_result.get("level", "")
            # "Level 1" → "L1"
            clue_level = level_raw.replace("Level ", "L") if level_raw else ""
            grading_text = format_grading_section(grading_result, body)
            log.info("线索分级: level=%s | tag=%s", clue_level, grading_result.get("l2_tag", "N/A"))
    except Exception as e:
        log.warning("线索分级异常（不影响写入）: %s", e)

    # ── 4.5 L4 跳过写入 ──
    if clue_level == "L4":
        log.info("L4 跳过写入: %s", grading_result.get("l2_tag", "N/A") if grading_result else "N/A")
        return _skip_and_label(service, msg_id, label_id, "skipped", "L4_discard")

    # ── 4.8 内存级去重（防同批次竞态） ──
    dedup_key = msg_id
    if _batch_dedup is not None and dedup_key in _batch_dedup:
        log.info("批次内去重命中（msg_id=%s），跳过写入", msg_id)
        return _skip_and_label(service, msg_id, label_id, "duplicate", "batch dedup hit", email=email)

    # ── 5. 飞书去重（Gmail_Msg_ID 精确匹配） ──
    if check_feishu_duplicate(feishu_token, msg_id):
        log.info("飞书去重命中（msg_id=%s），跳过写入", msg_id)
        return _skip_and_label(service, msg_id, label_id, "duplicate", "feishu dedup hit", email=email)

    # ── 5.1 飞书邮箱去重（12h 内同邮箱 → 合并追加，避免重复分配） ──
    dedup_email = email.strip().lower() if email else ""
    if dedup_email:
        existing = check_feishu_email_duplicate(feishu_token, dedup_email)
        if existing:
            append_body = inquiry_content.rsplit("\n\n", 1)[0] if "\n\n" in inquiry_content else inquiry_content
            merge_msg = ""
            for line in append_body.splitlines():
                if line.strip().lower().startswith("message:"):
                    merge_msg = line.split(":", 1)[1].strip()
                    break
            gibberish, gib_reason = check_gibberish_inquiry(
                merge_msg or append_body, rules, body=body, fields_pre=fields_pre,
            )
            if gibberish:
                log.info("合并前乱码拦截: %s", gib_reason)
                return _skip_and_label(service, msg_id, label_id, "skipped", gib_reason)

            existing_record_id = existing.get("record_id", "")
            existing_fields = existing.get("fields", {})
            old_content = extract_text(existing_fields.get(FEISHU_FIELD_NAME, ""))
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 沿用第一条分类：第二条只追加正文(去掉末尾 tag_line)，表单国家用注释保留供人工判断
            append_body = inquiry_content.rsplit("\n\n", 1)[0] if "\n\n" in inquiry_content else inquiry_content
            country_note = f" [表单国家: {customer_country}]" if customer_country else ""
            merged_content = f"{old_content}\n\n--- 追加询盘 [{now_str}]{country_note} ---\n{append_body}"
            merge_patch = _structured_write_fields(
                sub_channel=customer_channel if isinstance(customer_channel, str) else str(customer_channel or ""),
                country=customer_country,
                product_category=customer_product,
                product_model=(normalized.get("product_model", "") if llm_result and llm_result.get("status") == "parsed"
                               else (product_model if not llm_result or llm_result.get("status") != "parsed" else "")),
            )
            log.info("邮箱合并: email=%s | record=%s", dedup_email, existing_record_id)
            merge_result = merge_feishu_record(
                feishu_token, existing_record_id,
                merged_content=merged_content,
                new_msg_id=msg_id,
                extra_fields=merge_patch or None,
            )
            if merge_result.get("code") == 0:
                log.info("合并写入成功: record_id=%s", existing_record_id)
                apply_label(service, msg_id, label_id)
                return {"id": msg_id, "status": "merged", "email": email,
                        "feishu_record_id": existing_record_id,
                        "autoreply": "Skipped(Merged)"}
            else:
                log.error("合并写入失败: %s，回退跳过", merge_result)
                return _skip_and_label(service, msg_id, label_id, "duplicate",
                                       "email_dedup_merge_failed", email=email)

    # ── 5.5 附件处理（非阻塞） ──
    attachment_tokens = []
    try:
        attachment_tokens = process_attachments(service, msg_data, msg_id, feishu_token, FEISHU_APP_TOKEN)
    except Exception as e:
        log.warning("附件处理失败（不影响主流程）: %s", e)

    # ── 6. 写入飞书 ──
    url_keyword = extract_url_keyword(body)
    structured = _structured_write_fields(
        sub_channel=customer_channel if isinstance(customer_channel, str) else str(customer_channel or ""),
        country=customer_country,
        product_category=customer_product,
        product_model=(normalized.get("product_model", "") if llm_result and llm_result.get("status") == "parsed"
                       else (product_model if not llm_result or llm_result.get("status") != "parsed" else "")),
    )
    create_result = create_feishu_record(
        feishu_token, inquiry_content, clue_level=clue_level,
        grading_text=grading_text, attachment_tokens=attachment_tokens,
        gmail_msg_id=msg_id, keyword=url_keyword,
        channels=structured.get(FIELD_CHANNELS, ""),
        sub_channel=structured.get(FIELD_SUB_CHANNEL, ""),
        country=structured.get("Country（国家）", ""),
        product_category=structured.get("Product Categories（产品大类）", ""),
        product_model=structured.get("Product model（具体型号）", ""),
    )
    if create_result.get("code") == 0:
        record_id = create_result.get("data", {}).get("record", {}).get("record_id")
        log.info("飞书写入成功: record_id=%s", record_id)
        apply_label(service, msg_id, label_id)
        if _batch_dedup is not None and dedup_key:
            _batch_dedup.add(dedup_key)
    else:
        log.error("飞书写入失败: %s", create_result)
        return {"id": msg_id, "status": "feishu_error", "error": create_result.get("msg", "unknown")}

    # ── 6.5 自动回复邮件（L1-L3, 非阻塞） ──
    autoreply_status = AR_SKIPPED
    autoreply_template = ""
    autoreply_error = ""
    if (AUTO_REPLY_ENABLED and clue_level in ("L1", "L2", "L3") and record_id
            and email and email.strip().lower() not in _own_emails):
        # 灰度采样
        if AUTO_REPLY_SAMPLE_RATE < 1.0 and random.random() > AUTO_REPLY_SAMPLE_RATE:
            autoreply_status = AR_SAMPLED_OUT
            log.info("灰度采样跳过: rate=%.2f", AUTO_REPLY_SAMPLE_RATE)
        else:
            try:
                email_gen_result = generate_template_email(
                    product_category=customer_product,
                    grading=grading_result or {},
                    customer_name=customer_name,
                    channel=customer_channel,
                    country=customer_country,
                    message=body,
                )
                if email_gen_result:
                    autoreply_template = email_gen_result.get("email_model", "?")
                    reply_subject, reply_html, reply_plain = compose_email_reply(
                        email_gen_result, msg_data,
                    )
                    send_result = send_reply_email(
                        gmail_service=service,
                        original_msg_data=msg_data,
                        reply_subject=reply_subject,
                        reply_body_html=reply_html,
                        reply_body_plain=reply_plain,
                        dry_run=AUTO_REPLY_DRY_RUN,
                        attachments=email_gen_result.get("attachments"),
                        to_email_override=email,
                    )
                    autoreply_status = send_result.get("status", "Error").replace("_", "-").title()
                    autoreply_error = send_result.get("error", "") or ""
                    log.info("自动回复: status=%s | model=%s | To=%s",
                             autoreply_status, autoreply_template,
                             get_reply_to(msg_data))
                    # Dry-run 模式把邮件内容写入飞书供业务 review
                    if AUTO_REPLY_DRY_RUN and autoreply_status == AR_DRY_RUN:
                        preview = f"[Subject] {reply_subject}\n\n{email_gen_result.get('body', '')[:500]}"
                        autoreply_template = f"[PREVIEW] {preview}"
                    # Token 失效告警
                    if "401" in autoreply_error or "invalid_grant" in autoreply_error:
                        send_alert_webhook("⚠️ Gmail token 失效，自动回复已停止。请重新运行 get-gmail-token.py 并更新 GitHub Secret。")
                else:
                    autoreply_status = AR_NO_TEMPLATE
                    log.info("无匹配模板，跳过自动回复: product=%s", customer_product)
            except Exception as e:
                autoreply_status = AR_ERROR
                autoreply_error = str(e)[:200]
                log.error("自动回复异常（不影响管线）: %s", e)

        # 更新飞书记录（Sampled-Out 不更新，保持 Pending）
        if autoreply_status != AR_SAMPLED_OUT:
            try:
                sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if autoreply_status == AR_SENT else ""
                update_feishu_autoreply(feishu_token, record_id, autoreply_status, sent_at,
                                        template=autoreply_template, error=autoreply_error)
            except Exception as e:
                log.warning("飞书自动回复状态更新失败（不影响管线）: %s", e)
    return {
        "id": msg_id, "status": "ok",
        "email": email,
        "feishu_record_id": create_result.get("data", {}).get("record", {}).get("record_id"),
        "autoreply": autoreply_status,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def send_alert_webhook(message: str):
    """认证失败等严重错误时发飞书告警"""
    webhook_url = os.environ.get("FEISHU_ALERT_WEBHOOK", "")
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={"msg_type": "text", "content": {"text": message}},
            timeout=10,
        )
    except Exception:
        pass  # 告警失败不影响主流程


def main():
    log.info("=== Lead Poller 启动 (UTC %s) ===", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    # 加载规则
    if not RULES_FILE.exists():
        log.error("找不到规则文件: %s", RULES_FILE)
        sys.exit(1)
    with open(RULES_FILE, encoding="utf-8") as f:
        rules = json.load(f)
    log.info("规则文件加载成功（版本 %s）", rules.get("version", "?"))

    # Gmail 认证（含失败告警）
    log.info("Gmail API 认证中...")
    try:
        service = get_gmail_service()
    except Exception as e:
        err_msg = f"[Lead Poller 告警] Gmail 认证失败: {e}"
        log.error(err_msg)
        send_alert_webhook(err_msg)
        sys.exit(1)

    # 确保标签存在
    label_id = get_or_create_label(service, GMAIL_LABEL)
    log.info("Gmail 标签 ID: %s", label_id)

    # 飞书 token
    feishu_token = get_feishu_token()
    log.info("飞书 token 获取成功")

    # 搜索未处理邮件
    messages = search_unprocessed_emails(service, rules)
    if not messages:
        log.info("无待处理邮件，本次运行结束")
        _write_summary([], 0, 0, 0)
        return

    # 逐封处理
    results = []
    batch_dedup: set[str] = set()
    for msg_ref in messages:
        try:
            msg_data = get_message_detail(service, msg_ref["id"])
            result = process_email(service, msg_data, label_id, feishu_token, rules,
                                   _batch_dedup=batch_dedup)
            # 附加 from/subject 用于过滤日志
            result["_from"] = get_header(msg_data, "From")
            result["_subject"] = get_header(msg_data, "Subject")
            results.append(result)
        except Exception as e:
            log.error("处理邮件 %s 时发生异常: %s", msg_ref["id"], e, exc_info=True)
            results.append({"id": msg_ref["id"], "status": "exception", "error": str(e)})

    # 写入过滤日志表
    _write_filter_logs(feishu_token, results, messages, service)

    # 汇总
    ok       = sum(1 for r in results if r["status"] == "ok")
    skipped  = sum(1 for r in results if r["status"] in ("skipped", "duplicate"))
    merged   = sum(1 for r in results if r["status"] == "merged")
    errors   = sum(1 for r in results if r["status"] in ("feishu_error", "exception"))
    replies  = sum(1 for r in results if r["status"] == "reply_tracked")
    log.info("=== 本次运行完成: 成功=%d 跳过=%d 合并=%d 错误=%d 回复=%d ===", ok, skipped, merged, errors, replies)

    # 输出 GitHub Actions 状态：是否有客户回复待转发
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"reply_count={replies}\n")

    # No-Template 连续告警（只统计成功处理的记录，跳过 skipped/duplicate/exception）
    if AUTO_REPLY_ENABLED and ok > 0:
        no_tpl_count = 0
        for r in results:
            if r["status"] != "ok":
                continue
            if r.get("autoreply") == AR_NO_TEMPLATE:
                no_tpl_count += 1
            elif r.get("autoreply") in (AR_SENT, AR_DRY_RUN):
                no_tpl_count = 0
        if no_tpl_count >= NO_TEMPLATE_ALERT_THRESHOLD:
            send_alert_webhook(
                f"⚠️ 连续 {no_tpl_count} 条线索无匹配模板，请检查模板覆盖率。"
                f"产品品类可能需要新增模板。"
            )

    _write_summary(results, ok, skipped, errors)

    if ok > 0 or merged > 0:
        try:
            from github_dispatch import trigger_assignment_unblock

            created_ids = [
                r.get("feishu_record_id")
                for r in results
                if r.get("status") in ("ok", "merged") and r.get("feishu_record_id")
            ]
            # 一次 dispatch 即可；unblock 会扫近期异常/待分配
            trigger_assignment_unblock(
                record_id=created_ids[0] if created_ids else None,
                source="gmail-lead-poller",
                created_count=len(created_ids),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("触发 assignment-unblock 失败: %s", exc)

    if errors > 0:
        log.warning("有 %d 封邮件处理失败，将在下次运行时重试（Gmail 标签未打）", errors)
        sys.exit(1)


FILTER_LOG_TABLE = os.environ.get("FEISHU_FILTER_LOG_TABLE_ID", "")

CHANNEL_KEYWORDS = {
    "谷歌1": "inquiry@soundboxacoustic.com",
    "谷歌2": "email@soundboxbooth.com",
    "新官网": "新官网",
    "总舱网": "总舱网",
    "美国舱网": "美国舱网",
    "加拿大舱网": "加拿大舱网",
}


def _classify_filter_channel(from_addr: str, subject: str) -> str:
    addr = re.search(r'<([^>]+)>', from_addr or "")
    addr = addr.group(1).lower().strip() if addr else (from_addr or "").lower().strip()
    if addr == "inquiry@soundboxacoustic.com":
        return "谷歌1"
    if addr == "email@soundboxbooth.com":
        return "谷歌2"
    for kw in ("加拿大舱网", "美国舱网", "总舱网", "新官网"):
        if kw in (subject or ""):
            return kw
    if addr in ("service@soundbox-sys.com", "service@soundbox-pod.com"):
        return "总舱网"
    return "谷歌2"


def _write_filter_logs(token: str, results: list, messages: list, service):
    """将本轮处理的每封邮件写入过滤日志表。"""
    if not FILTER_LOG_TABLE:
        return
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for r in results:
        status = r.get("status", "")
        if status == "reply_tracked":
            continue
        msg_id = r.get("id", "")
        reason = r.get("reason", "")
        email_addr = r.get("email", "")
        from_addr = r.get("_from", "")
        subject = r.get("_subject", "")

        if status == "ok":
            action = "pass"
        elif status == "duplicate":
            action = "duplicate"
        elif status == "skipped":
            action = "reject"
        elif status in ("feishu_error", "exception"):
            action = "error"
        else:
            action = status

        channel = _classify_filter_channel(from_addr, subject)
        snippet = reason or r.get("error", "") or email_addr

        fields = {
            "Date": now_ms,
            "Channel": channel,
            "From": from_addr or email_addr or msg_id,
            "Subject": (subject or "")[:100],
            "Action": action,
            "Reason": reason,
            "Message Snippet": snippet[:200],
        }
        try:
            feishu_api("POST",
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
                f"/tables/{FILTER_LOG_TABLE}/records",
                token=token, json={"fields": fields})
        except Exception as e:
            log.warning("过滤日志写入失败（不影响主管线）: %s", e)


def _write_summary(results: list, ok: int, skipped: int, errors: int):
    """写入 $GITHUB_STEP_SUMMARY，方便并行测试阶段对比"""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(f"## Lead Poller Summary\n\n")
        f.write(f"| 指标 | 数量 |\n|---|---|\n")
        f.write(f"| 成功写入 | {ok} |\n")
        f.write(f"| 跳过/去重 | {skipped} |\n")
        f.write(f"| 错误 | {errors} |\n\n")
        if results:
            f.write("### 处理明细\n\n| Message ID | 状态 | 说明 |\n|---|---|---|\n")
            for r in results:
                detail = r.get("email") or r.get("reason") or r.get("error", "")
                f.write(f"| {r['id']} | {r['status']} | {detail[:60]} |\n")


if __name__ == "__main__":
    main()
