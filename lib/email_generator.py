#!/usr/bin/env python3
"""
email_generator.py — 建联邮件生成模块（Master Prompt v1.3 代码化）

设计原则：
  - LLM 仅做邮件内容生成（Subject + Body + Signature + SendTimeSuggestion）
  - 模型选择（G-A/B/C/D）由代码规则确定，不依赖 LLM 判断
  - Prompt 精简：只传必要参数 + 模型规则片段，而非全量 Master Prompt

依赖：
  - lead_grader.py（分级结果）
  - slot_extractor.py（语义槽位）
"""

import os
import re
import json
import logging
import requests

log = logging.getLogger("email-generator")

ZHIPU_API_KEY  = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL    = os.environ.get("ZHIPU_EMAIL_MODEL", os.environ.get("ZHIPU_MODEL", "glm-4-flash"))
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

UNIFIED_SIGNATURE = """Best regards,
Frank Lin
Sales Engineer | Soundbox
NO.12, HUASHAN ROAD, SHILOU TOWN, PANYU DISTRICT, GUANGZHOU, CHINA
+86 13925496400
frank.lin@soundbox-sys.com
www.soundbox-sys.com"""

# Facebook 渠道专用签名（轮转分配给不同业务员，用统一团队签名不绑定具体人）
SOUNDBOX_TEAM_SIGNATURE = """Best regards,
Soundbox Team
Soundbox Acoustic
www.soundbox-sys.com"""

# 画册列表
BROCHURES = {
    "ART": "Soundbox-ART_POD Brochure.pdf",
    "VRT": "Soundbox-VRT_POD Brochure.pdf",
    "VR":  "VR Silence Booth Product Brochure.pdf",
    "SR":  "SR Silence Booth Product Brochure.pdf",
}

CALENDLY_LINK = "https://calendly.com/frank-lin-soundbox-sys/30min"

PRODUCT_PAGE = "https://www.soundbox-sys.com/products/list/vrt-series-office-pod"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. select_email_model — 邮件模型选择（纯代码规则）
# ═══════════════════════════════════════════════════════════════════════════════

def select_email_model(level: str, channel: str = "Google",
                       l2_tag: str = "N/A", email: str = "",
                       company: str = "") -> str:
    """根据 Level + 渠道选择邮件模型。

    Returns:
        "G-A" / "G-B" / "G-C" / "G-D" / "FB-A" / "FB-B1" / "FB-B2" / "FB-C" / "FB-D"
    """
    if channel == "Facebook":
        if level == "Level 1":
            return "FB-A"
        elif level == "Level 2":
            return "FB-B1" if l2_tag in ("L2-Strong",) else "FB-B2"
        elif level == "Level 3":
            return "FB-C"
        else:
            return "FB-D"
    else:  # Google (default)
        if level == "Level 1":
            return "G-A"
        elif level == "Level 2":
            return "G-B"
        elif level == "Level 3":
            return "G-C"
        else:
            return "G-D"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. get_model_rules — 获取当前模型对应的规则片段
# ═══════════════════════════════════════════════════════════════════════════════

MODEL_RULES = {
    "G-A": {
        "next_step": "quote",
        "max_questions": 2,
        "brochure_allowed": True,
        "calendly": "optional",
        "pricing_allowed": True,
        "question_priority": "If model clear → quantity + delivery city. Else → use case + quantity.",
    },
    "G-B": {
        "next_step": "quote",
        "max_questions": 2,
        "brochure_allowed": True,
        "calendly": "optional_if_corporate",
        "pricing_allowed": True,
        "question_priority": "If only 'quotation' no scenario → ask which model first. Ask quantity/delivery only if needed.",
    },
    "G-C": {
        "next_step": "exploration",
        "max_questions": 1,
        "brochure_allowed": True,
        "calendly": "no",
        "pricing_allowed": False,
        "question_priority": "Ask 1 easy question: model/size or use case.",
    },
    "G-D": {
        "next_step": "minimal",
        "max_questions": 1,
        "brochure_allowed": False,
        "calendly": "no",
        "pricing_allowed": False,
        "question_priority": "Exactly 1 short question. No links.",
    },
    "FB-A": {
        "next_step": "quote",
        "max_questions": 2,
        "brochure_allowed": True,
        "calendly": "optional",
        "pricing_allowed": True,
        "question_priority": "If models named → quantity + delivery city. If scenario but no model → which model + quantity.",
    },
    "FB-B1": {
        "next_step": "quote",
        "max_questions": 2,
        "brochure_allowed": True,
        "calendly": "optional",
        "pricing_allowed": True,
        "question_priority": "If only 1p/2p no model → ask which model first. If model clear → quantity + delivery city.",
    },
    "FB-B2": {
        "next_step": "exploration",
        "max_questions": 1,
        "brochure_allowed": False,
        "calendly": "no",
        "pricing_allowed": False,
        "question_priority": "Exactly 1 question: model/size OR use case.",
    },
    "FB-C": {
        "next_step": "exploration",
        "max_questions": 1,
        "brochure_allowed": True,
        "calendly": "no",
        "pricing_allowed": True,
        "question_priority": "1 easy question. Price-only no scenario → ask which model.",
    },
    "FB-D": {
        "next_step": "minimal",
        "max_questions": 1,
        "brochure_allowed": False,
        "calendly": "no",
        "pricing_allowed": False,
        "question_priority": "1 multiple-choice question. No links/brochure/pricing.",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. select_brochure — 画册选择
# ═══════════════════════════════════════════════════════════════════════════════

def select_brochure(message: str) -> str:
    """根据原文选择最匹配的画册。"""
    msg_lower = message.lower()
    if "art" in msg_lower:
        return BROCHURES["ART"]
    if "vrt" in msg_lower:
        return BROCHURES["VRT"]
    if any(w in msg_lower for w in ("movable", "portable", "relocate", "demountable")):
        return BROCHURES["VR"]
    if "sr" in msg_lower:
        return BROCHURES["SR"]
    # 默认 VRT
    return BROCHURES["VRT"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. build_email_prompt — 构建精简 Prompt
# ═══════════════════════════════════════════════════════════════════════════════

def build_email_prompt(raw_body: str, grading: dict, email_model: str,
                       country: str = "", company: str = "") -> str:
    """构建精简的邮件生成 Prompt（只传必要参数 + 模型规则片段）。"""

    slots = grading.get("slots", {})
    rules = MODEL_RULES.get(email_model, MODEL_RULES["G-C"])
    level = grading.get("level", "Level 3")
    intent = slots.get("intent_slot", "Unclear")
    product = slots.get("product_slot", "Unclear")
    scenario = slots.get("scenario_slot", "Unknown")
    timeline = slots.get("timeline_slot", "NotMentioned")
    qmax = slots.get("quantity_max", "N/A")
    capacity = slots.get("capacity_options", "N/A")
    identity = slots.get("identity_strength", "Unknown")
    lang = slots.get("language_hint", "English")
    price_only = grading.get("price_only_flag", "No")

    # 判断是否可以给价格（客户明确点名型号 + 报价请求）
    pricing_trigger = False
    if rules["pricing_allowed"] and intent == "Quotation":
        # 检查原文是否提到具体型号
        model_mentions = re.findall(
            r"\b(?:SR|VR|VRT|ART|SRP|EQ|DQ|AQ)[-\s]?(?:S|M|L|XL|SM|ML)?\b",
            raw_body, re.IGNORECASE
        )
        if model_mentions:
            pricing_trigger = True

    # 确定画册
    brochure_name = ""
    if rules["brochure_allowed"]:
        brochure_name = select_brochure(raw_body)

    # 确定是否有 Calendly
    has_calendly = False
    if rules["calendly"] == "optional":
        has_calendly = True
    elif rules["calendly"] == "optional_if_corporate":
        has_calendly = identity == "Company domain email" or (company and company not in ("N/A", "n/a", ""))

    # 构建结构化输入
    prompt = f"""You are Frank Lin, a senior B2B Sales Engineer at Soundbox. Generate a ready-to-send outreach email.

HARD RULES:
1) Output ONLY: Subject + Body + Signature + SendTimeSuggestion. No explanations.
2) Do NOT invent facts (company/project/qty/location/timeline/prices unless allowed).
3) Ask at most {rules['max_questions']} question(s). Each must be easy to answer in one line.
4) Keep paragraphs short (1-2 lines). No emojis.
5) Always use this exact signature:
{UNIFIED_SIGNATURE}

ROOM-STYLE BODY (mandatory):
A) First paragraph: "My name is Frank, and I'm your point person at Soundbox for this request."
B) One-sentence paraphrase of the customer's request using their words.
C) Next-step promise: {"Once I have the details below, I'll send a quotation with lead time and shipping options." if rules["next_step"] == "quote" else "Once I understand your use case, I'll recommend the best-fit model." if rules["next_step"] == "exploration" else ""}
D) Questions: {rules["question_priority"]}
E) First question must be the easiest to answer.

SUBJECT:
- Default: "Soundbox quote request"
- If Partnership: "Soundbox partnership inquiry"
- If info/brochure: "Soundbox product info request"
- If model/units mentioned: "Soundbox quote request — <Model/Units>"

ASSETS:
- Calendly: {CALENDLY_LINK if has_calendly else "NOT allowed"}
- Brochure: {brochure_name if brochure_name else "NOT allowed"}
- Brochure line (only if attaching): "I've attached our product brochure for a quick overview."
- Product page: {PRODUCT_PAGE} (use sparingly)

PRICING: {"Reference pricing allowed. Use FX: 1 USD = 6.86 CNY. Output max 3-6 price lines with 'Price reference (USD, unit):' header. Add '(Excluding shipping/taxes; final pricing depends on quantity, options, and delivery location.)'" if pricing_trigger else "NOT allowed. Do NOT list prices."}

SendTimeSuggestion format:
- Recipient country: {country or "Unknown"}
- Recommended local delivery time: Tue-Thu 10:00 (local) (fallback: 14:30 local)
- Recommended Beijing time to schedule: <HH:MM Beijing time>
- Note: <keep short>

--- LEAD INPUT ---
Level: {level}
Model: {email_model}
Intent: {intent}
Product: {product}
Scenario: {scenario}
Timeline: {timeline}
Quantity: {qmax}
Capacity: {capacity}
Identity: {identity}
Language: {lang}
PriceOnly: {price_only}
Country: {country}
Company: {company}

RAW_LEAD_TEXT:
{raw_body[:2000]}
--- END INPUT ---

Generate the email now:"""

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 5. generate_email — 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def generate_email(raw_body: str, grading: dict, channel: str = "Google",
                   country: str = "", company: str = "") -> dict | None:
    """生成建联邮件。

    Args:
        raw_body: 询盘原文
        grading: grade_lead() 的返回结果
        channel: 渠道 ("Google" / "Facebook")
        country: 客户国家
        company: 客户公司

    Returns:
        {"subject": "...", "body": "...", "signature": "...", "send_time_suggestion": "...",
         "email_model": "G-A", "full_email": "..."} 或 None
    """
    if not ZHIPU_API_KEY:
        log.error("ZHIPU_API_KEY 未设置")
        return None

    if not raw_body or not grading:
        return None

    level = grading.get("level", "Level 3")
    slots = grading.get("slots", {})
    email_addr = slots.get("extracted_email", "")

    # Step 1: 选择邮件模型
    email_model = select_email_model(
        level, channel,
        l2_tag=grading.get("l2_tag", "N/A"),
        email=email_addr,
        company=company,
    )
    log.info("邮件模型: %s (level=%s, channel=%s)", email_model, level, channel)

    # Step 2: 构建 Prompt
    prompt = build_email_prompt(raw_body, grading, email_model, country, company)

    # Step 3: 调用 LLM
    try:
        resp = requests.post(
            f"{ZHIPU_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {ZHIPU_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": ZHIPU_MODEL,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not content:
            log.warning("LLM 邮件生成返回空")
            return None

        # Step 4: 解析邮件结构
        result = _parse_email_output(content)
        result["email_model"] = email_model
        result["brochure"] = select_brochure(raw_body) if MODEL_RULES[email_model]["brochure_allowed"] else ""

        log.info("邮件生成完成: model=%s | subject=%s", email_model, result.get("subject", "")[:60])
        return result

    except requests.Timeout:
        log.warning("邮件生成超时（45s）")
        return None
    except Exception as e:
        log.error("邮件生成异常: %s", e)
        return None


def _parse_email_output(content: str) -> dict:
    """解析 LLM 输出的邮件结构。"""
    result = {
        "subject": "",
        "body": "",
        "signature": "",
        "send_time_suggestion": "",
        "full_email": content,
    }

    # 提取 Subject
    m = re.search(r"Subject:\s*\n?(.+?)(?=\n\nBody:|\nBody:|$)", content, re.DOTALL)
    if m:
        result["subject"] = m.group(1).strip()

    # 提取 Body
    m = re.search(r"Body:\s*\n?(.+?)(?=\nBest regards,|\nSendTimeSuggestion:|$)", content, re.DOTALL)
    if m:
        result["body"] = m.group(1).strip()

    # 提取 SendTimeSuggestion
    m = re.search(r"SendTimeSuggestion:\s*\n?(.+?)$", content, re.DOTALL)
    if m:
        result["send_time_suggestion"] = m.group(1).strip()

    # 如果解析失败，把整个内容作为 full_email
    if not result["subject"] and not result["body"]:
        result["full_email"] = content

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 独立测试入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python email_generator.py '<raw_lead_text>' [email]")
        print("       echo 'text' | python email_generator.py - [email]")
        sys.exit(1)

    email = sys.argv[2] if len(sys.argv) > 2 else ""
    text = sys.stdin.read() if sys.argv[1] == "-" else sys.argv[1]

    # 先分级
    sys.path.insert(0, ".")
    from lead_grader import grade_lead
    grading = grade_lead(text, email=email)
    print("--- Grading ---")
    print(json.dumps(grading, ensure_ascii=False, indent=2))

    # 再生成邮件
    print("\n--- Email ---")
    result = generate_email(text, grading, email=email)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Email generation failed")
