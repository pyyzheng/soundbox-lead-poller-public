"""
LLM 邮件解析 — 智谱 GLM 调用、输出标准化
"""

import os
import re
import json
import logging
import urllib.request

import requests

from lead_fallback_parser import strip_html, translate_country

log = logging.getLogger("lead-poller")

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

VALID_CATEGORIES = ["静音舱", "家居舱", "声学产品"]
VALID_MODELS = [
    "SR", "SR-S", "SR-M", "SR-L", "SR-XL", "SR-XXL", "SR-1", "SR-2",
    "VR", "VR-S", "VR-M", "VR-L", "VR-XL", "VR-XXL",
    "VRT", "VRT-S", "VRT-M", "VRT-L",
    "ART", "ART-S", "ART-M", "ART-L", "ART-XL", "ART-XXL",
    "EQ", "DQ", "AQ", "无法识别",
]

MODEL_ALIASES = {
    "model s": "SR", "model m": "SR", "model l": "SR", "model xl": "SR",
    "model-s": "SR", "model-m": "SR", "model-l": "SR", "model-xl": "SR",
}

LLM_SYSTEM_PROMPT = """你是线索解析助手。解析邮件表单内容，返回结构化 JSON。

## 规则
0. 邮件已通过基础过滤（系统通知、营销群发等已排除），你负责意图分类和字段提取
1. 只输出 JSON，不要任何其他文字
2. 从正文中提取：Name, Email, Company, Phone, Message
3. 过滤技术元数据（Date, Time, Source URL, Remote IP, User Agent, Powered by）
4. Message 禁止翻译；普通表单逐字复制原文；AI 聊天通知需概括客户需求（见下方专门规则）
5. 国家名统一输出中文（如 Germany → 德国, United Arab Emirates → 阿联酋, Cayman Islands → 开曼群岛）
6. 如果 Page URL 中包含具体产品页面路径（如 /soundbox-sr-s/），直接从 URL 确定型号，优先级高于正文匹配

## 意图分类（最重要，优先判断）

在提取字段之前，先判断这封邮件是否表达了对我方产品/服务的需求意图。

### inquiry（询盘）特征（满足任一即可）：
- 提及我方产品品类（booth, pod, soundbox, acoustic panel 等）
- 询问价格、交期、规格、MOQ、报价、运费
- 描述具体使用场景或项目需求（办公室装修、会议室隔音等）
- 请求产品目录、样本、资料（send me a catalog/brochure）
- 表达采购/代理/合作意向（interested in your products, want to distribute）
  注意："interested in your products" 后跟服务推销（如"we can help you market them"）≠ 采购意向，是 non_inquiry
- 来自网站产品页面的表单提交（有 Page URL 指向产品页）

### non_inquiry（非询盘）特征：
- 推销自己的服务（SEO、广告投放、网站开发、营销工具、品牌推广）
- 招商合作邀请（展会展位、广告位、赞助）
- 求职/投递简历
- 纯粹的社交寒暄，无任何产品相关内容
- 邮件核心内容是"我能为你做什么"而非"我想买你的产品"
- 测试提交：name 是 "test"/"testing"，或 message 是纯数字/占位符（如 "222"、"123"）
- Cold outreach 标准模板：先夸你（came across/found/discovered your company/website）→ 再推销自己的服务
- 主动邀约 meeting/call/demo，但没有描述任何具体产品需求
- 邮件署名含营销/设计/开发公司的 title（SEO Specialist, Digital Marketing Manager, Web Designer）
- 声称做过"类似行业客户"但不说具体要买什么（"worked with companies in your industry"）
- 提供 free audit/consultation/review/strategy session

### 判断基调：宽进严判
- 模糊采购意图算 inquiry（如"I'm interested in your products"，且后续有具体产品/需求描述）
- 纯寒暄 + 推销组合 = non_inquiry（"I came across your site" 后跟服务推销，不是买产品）
- 关键区分：客户描述"我要什么"= inquiry；对方描述"我能为你做什么"= non_inquiry

### 冷推销 vs 询盘 判定示例

non_inquiry 示例（必须拦截）：
- "I came across your website and noticed you could improve your search rankings. We've helped companies like yours boost their organic traffic by 200%..."
  → 原因：夸 + 推销 SEO 服务，无产品需求
- "Hi, I'm a web designer and I noticed your site could use a refresh. Would you like to schedule a quick call to see how we can help?"
  → 原因：推销设计服务 + 约会议
- "We've built an all-in-one sales platform that helps businesses find and close more leads. Can I show you how it works?"
  → 原因：推销 SaaS 工具

inquiry 示例（必须放行）：
- "I'm looking for a soundproof booth for our new office in Berlin. Can you send me a quote for the SR-M model?"
  → 明确产品需求
- "We are a hotel chain in Dubai interested in acoustic solutions for our conference rooms."
  → 真实使用场景
- "I found your products on Google. We're a distributor in Mexico looking to carry your pods."
  → 采购/代理意向
- 主题 "quiet pods enquiry" / "Loose Furniture request" / 正文以 "Dear Sir/Madam" 开头但询问产品价格目录
  → 主题或正文含 enquiry/request + 产品词，一律 inquiry（不是 SEO 推销）
- 主题仅 "Dear Sir/Madam"，正文描述需要 pod/booth/quote/catalog/MOQ
  → inquiry（B2B 商务邮件常见开头，看正文采购意图）

non_inquiry 与 inquiry 主题对比：
- "quiet pods enquiry" + 询问报价/目录 → inquiry
- "Message from SoundBox" + "We put your banner at the top of search results" → non_inquiry（SEO 服务推销）

## 渠道识别（只看发件人邮箱）
- email@soundboxbooth.com → 谷歌, 谷歌2
- inquiry@soundboxacoustic.com → 谷歌, 谷歌1
- 其他 → 谷歌, 谷歌2

## 产品识别
- pod/booth/soundbox/sam box/sound box → 静音舱
- home pod/homepod → 家居舱
- acoustic panel/soundproofing/acoustic foam → 声学产品
- 型号白名单（词边界匹配）：SR, SR-S/M/L/XL/XXL, VR, VR-S/M/L/XL/XXL, VRT, VRT-S/M/L, ART, ART-S/M/L/XL/XXL
- 其他 → 无法识别

### 静音舱 vs 声学产品 区分（重要）
我们是静音舱/隔音舱/隔音办公室制造商，产品是独立舱体，不是建材材料。
- **静音舱**（我们的核心产品）：包含 booth/pod/soundbox 关键词的都归静音舱
  - sound booth, phone booth, meeting booth, isolation booth, silence booth, office pod, meeting pod 等
  - 即使客户公司是听力中心/录音棚等声学相关行业，他们要的是"隔音舱"产品，不是声学材料
- **声学产品**（辅助建材）：仅限明确的建材/材料关键词
  - acoustic panel, acoustic foam, soundproofing material, acoustic slat wall, acoustic flooring 等
  - 关键区分：客户说的是"一个舱体"→ 静音舱；客户说的是"一块板/材料"→ 声学产品

## 国家识别（优先级）
1. 询盘内容中的项目/交付地（"ship to X", "deliver to X", "project in X", "for our X office"）→ 以目标国家为准
2. 签名档地址/公司地址（如 "Doha - Qatar"、"P.O. Box: xxx, Tokyo"）→ 优先于 Branches/分支机构
3. 电话区号（+974=卡塔尔, +1=美国/加拿大, +86=中国, +91=印度 等）
4. 其他地名（注意："Branches: UAE | India" 是分支机构，不是总部；总部地址在签名档开头）
5. Remote IP → 在线查询
6. 都没有 → 无法识别

## AI 聊天通知邮件处理
如果正文包含 "新官网询价通知" 或 "AI聊天系统" 或 "触发原因" + "会话信息" 关键词，说明这是 AI 聊天机器人捕获的询盘：
- email: 从正文中的 "Email:" 或 "检测到邮箱：" 提取客户真实邮箱（绝不用系统邮箱 service@soundbox-*.com）
- name: 从客户最后一条消息中的邮箱前缀推断（如 pyyzheng@qq.com → pyyzheng），或从对话中的称呼推断（如 AI 说 "Thanks, Zheng" → Zheng）
- message: 用 2-3 句话概括客户的核心需求（来自"核心对话"中的用户发言），不要复制完整对话记录。格式："[客户需求摘要]。对话轮次: N轮"
- is_website_form: true
- 过滤掉 AI 回复内容，只关注用户发言

## 非表单邮件（直接邮件）处理
如果邮件不是系统通知，也不是网站表单，而是真人直接邮件/商务合作/咨询：
- name: 从发件人姓名、邮件署名档、正文开头提取（如 "Best regards, Geeta" → Geeta）
- email: 使用发件人邮箱地址（已作为 "发件人" 字段传入）
- company: 从正文中的公司名、署名档推断
- phone: 从正文中提取电话号码
- message: 邮件正文主体内容（去掉签名档和重复的联系方式）
- is_website_form: false
- 仍然返回 status: "parsed"，不要 skip

## 输出格式（注意字段冒号后有一个空格）
```json
{
  "status": "parsed",
  "intent": "inquiry",
  "is_website_form": true,
  "is_duplicate": false,
  "name": "",
  "email": "",
  "company": "",
  "phone": "",
  "message": "",
  "country": "",
  "channel": "谷歌",
  "sub_channel": "谷歌2",
  "product_category": "",
  "product_model": "",
  "identifier": "[国家]-[细分渠道]-[产品大类]-[具体型号]",
  "inquiry_content": "Name: \\nEmail: \\nCompany: \\nTelephone Number: \\nMessage: \\n\\n[标识符]"
}
```
"""


# 智谱端点列表（failover）：anthropic 端点快(~2s)，openai 端点作备用
_ZHIPU_ENDPOINTS = [
    {"style": "anthropic", "url": "https://open.bigmodel.cn/api/anthropic/v1/messages",
     "model": os.environ.get("ZHIPU_MODEL_FAST", "glm-4.5-flash"), "timeout": 30},
    {"style": "openai", "url": f"{ZHIPU_BASE_URL}/chat/completions",
     "model": ZHIPU_MODEL, "timeout": 60},
]


def _call_zhipu_endpoint(ep: dict, system: str, user: str) -> str:
    """调用单个智谱端点，返回 content 文本。失败抛异常。"""
    if ep["style"] == "anthropic":
        resp = requests.post(
            ep["url"],
            headers={"x-api-key": ZHIPU_API_KEY, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json={"model": ep["model"], "system": system,
                  "messages": [{"role": "user", "content": user}],
                  "max_tokens": 1024, "temperature": 0.1},
            timeout=ep["timeout"],
        )
        data = resp.json()
        if resp.status_code != 200 or not data.get("content"):
            raise RuntimeError(f"anthropic {resp.status_code}: {str(data)[:200]}")
        return data["content"][0].get("text", "")
    resp = requests.post(
        ep["url"],
        headers={"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"},
        json={"model": ep["model"],
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "max_tokens": 1024, "temperature": 0.1},
        timeout=ep["timeout"],
    )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_llm_parse(from_addr: str, body: str) -> tuple[dict | None, str]:
    """调用智谱 GLM 解析邮件，返回 (JSON, ip_country_zh)。ip_country_zh 为 IP 归属的中文国名"""
    from lead_fallback_parser import extract_remote_ip

    if not ZHIPU_API_KEY:
        return None, ""
    try:
        ip = extract_remote_ip(body)
        ip_info = ""
        ip_country_zh = ""
        if ip:
            country_en = _lookup_ip_direct(ip)
            if country_en:
                ip_info = f"\nRemote IP: {ip} (归属: {country_en})"
                ip_country_zh = translate_country(country_en)

        user_msg = f"发件人: {from_addr}\n\n邮件正文:\n{body}{ip_info}"

        # 双端点 failover：anthropic(快) → openai(备用)，每端点重试 2 次
        content = None
        last_err = None
        for ep in _ZHIPU_ENDPOINTS:
            for attempt in range(2):
                try:
                    content = _call_zhipu_endpoint(ep, LLM_SYSTEM_PROMPT, user_msg)
                    break
                except Exception as e:
                    last_err = e
                    if attempt < 1:
                        import time
                        time.sleep(2)
            if content:
                log.info("LLM 解析成功: %s/%s", ep["style"], ep["model"])
                break
        if not content:
            raise last_err

        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        text = m.group(1).strip() if m else content.strip()
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        return json.loads(text), ip_country_zh
    except Exception as e:
        log.warning("LLM 解析失败，将回退到规则引擎: %s", e)
        return None, ""


def _lookup_ip_direct(ip: str) -> str:
    """云端直接查询 IP（无代理）"""
    url = f"http://ip-api.com/json/{ip}?fields=country,status"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "success" and data.get("country"):
            return data["country"]
    except Exception:
        pass
    return ""


def normalize_llm_output(parsed: dict, rules: dict, sub_channel: str = "",
                         build_tag_line=None, format_inquiry_content=None) -> dict:
    """标准化 LLM 输出。

    build_tag_line / format_inquiry_content 由调用方传入（避免循环依赖）。
    """
    product_name_map = rules.get("product_name_map", {})
    model_name_map = rules.get("model_name_map", {})
    acoustic_subtype_map = rules.get("acoustic_subtype_map", {})

    raw_product_category = (parsed.get("product_category") or "").strip()
    raw_product_model = (parsed.get("product_model") or "").strip()
    message = parsed.get("message", "")
    company = parsed.get("company", "")

    # ── Smart category/model split ──
    model_patterns = [
        re.compile(r"^model\s", re.I),
        re.compile(r"\bSR\b", re.I), re.compile(r"\bVR\b", re.I),
        re.compile(r"\bVRT\b", re.I), re.compile(r"\bART\b", re.I),
        re.compile(r"\bSR-"), re.compile(r"\bVR-"),
        re.compile(r"\b(S|M|L|XL)\b"),
    ]
    if raw_product_category and raw_product_category not in VALID_CATEGORIES:
        mapped = product_name_map.get(raw_product_category.lower())
        if not mapped and any(p.search(raw_product_category) for p in model_patterns):
            if not raw_product_model:
                raw_product_model = raw_product_category
            raw_product_category = ""

    # ── normalizeProductCategory ──
    product_category = raw_product_category
    if product_category:
        mapped = product_name_map.get(product_category.lower())
        if mapped:
            product_category = mapped

    if product_category not in VALID_CATEGORIES:
        full_text = f"{message} {company}".lower()
        for key, std_name in product_name_map.items():
            if key.lower() in full_text and std_name in VALID_CATEGORIES:
                product_category = std_name
                break

    # ── 后验证：LLM 误判声学产品时，用规则引擎关键词 override ──
    if product_category == "声学产品":
        override_cats = rules.get("product_categories", {})
        cabin_keywords = override_cats.get("静音舱", [])
        msg_lower = message.lower()
        for kw in cabin_keywords:
            if re.search(rf'\b{re.escape(kw)}\b', msg_lower, re.IGNORECASE):
                log.info("LLM 声学产品 override → 静音舱（原文含关键词: %s）", kw)
                product_category = "静音舱"
                break

    # ── normalizeProductModel ──
    product_model = raw_product_model

    if product_category == "声学产品":
        if not product_model:
            raw_lower = raw_product_category.lower()
            if raw_lower in acoustic_subtype_map:
                product_model = acoustic_subtype_map[raw_lower]
        else:
            mapped = acoustic_subtype_map.get(product_model.lower())
            if mapped:
                product_model = mapped
            elif product_model not in ("无法识别",):
                product_model = ""
    else:
        if product_model and product_model not in VALID_MODELS:
            model_parts = re.split(r"[,/]+|\band\b", product_model)
            alias_matched = False
            for part in model_parts:
                part = part.strip().lower()
                if part in MODEL_ALIASES:
                    product_model = MODEL_ALIASES[part]
                    alias_matched = True
                    break

            if not alias_matched:
                full_text = f"{raw_product_category} {raw_product_model} {message}".lower()
                for code, info in rules.get("product_models", {}).items():
                    excludes = [w.lower() for w in info.get("exclude", [])]
                    for kw in info.get("keywords", []):
                        if kw.lower() in full_text:
                            if not any(ex in full_text for ex in excludes):
                                product_model = code
                                break
                    if product_model in VALID_MODELS:
                        break

        if product_model and product_model not in VALID_MODELS:
            mapped = model_name_map.get(product_model.lower())
            if mapped:
                product_model = mapped
            else:
                product_model = ""
        if product_model and product_model not in VALID_MODELS:
            product_model = ""

    # ── Infer category from model ──
    if not product_category and product_model and product_model in VALID_MODELS:
        product_category = "静音舱"

    # ── 表单渠道兜底：无产品类别时默认静音舱 ──
    # 这些渠道本身就是 SoundBox 表单入口，缺少产品词时仍应入库给销售判断。
    if product_category in ("", "无法识别") and sub_channel in (
        "谷歌1", "谷歌2", "新官网", "总舱网", "美国舱网", "加拿大舱网",
    ):
        product_category = "静音舱"

    # ── Country ──
    country = parsed.get("country", "")
    if country:
        country = translate_country(country)

    # ── Rebuild inquiry_content ──
    if not sub_channel:
        sub_channel = parsed.get("sub_channel", "谷歌2")

    tag_line = ""
    if build_tag_line:
        tag_line = build_tag_line(country, sub_channel, product_category, product_model)

    clean_msg = strip_html(message)

    inquiry_content = ""
    if format_inquiry_content:
        inquiry_content = format_inquiry_content(
            parsed.get("name", ""), parsed.get("email", ""), company,
            parsed.get("phone", ""), clean_msg, tag_line,
        )

    return {
        "product_category": product_category,
        "product_model": product_model,
        "country": country,
        "sub_channel": sub_channel,
        "tag_line": tag_line,
        "inquiry_content": inquiry_content,
    }
