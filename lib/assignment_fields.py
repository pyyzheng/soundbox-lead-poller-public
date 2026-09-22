"""飞书线索分配相关字段名常量。"""

from __future__ import annotations

FIELD_ENTRY_TIME = "Entry Time（录入时间）"
FIELD_LEAD_ID = "Clue ID"
FIELD_ASSIGN_METHOD = "Allocation Method（分配方式）"
FIELD_CHANNELS = "Channels（渠道）"
FIELD_SUB_CHANNEL = "Channel segmentation (细分渠道)"
FIELD_COUNTRY = "Country（国家）"
FIELD_SUBOFFICE = "是否是子办国家"
FIELD_ROTATION = "是否满足渠道轮转"
FIELD_DUP_READY = "Dup Formula Ready（公式查重就绪）"
FIELD_STATUS = "Allocation Status（分配状态）"
FIELD_ASSIGNEE = "The final assigned salesperson（最终分配的业务员）"
FIELD_MANUAL_ASSIGNEE = "Manually reassigned sales representatives（人工改派的业务员）"
FIELD_SYSTEM = "Matched Sales Rep（系统匹配业务员）"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_QUEUE_ASSIGNEE = "渠道顺序队列匹配业务员"
FIELD_QUEUE_KEY = "队列Key"
FIELD_SUCCESS = "Allocation Status（是否成功分配）"
FIELD_AGENT_COUNTRY = "是否命中代理国家"
FIELD_AGENT_PRODUCT = "是否命中代理产品"
FIELD_AGENT_ASSIGNEE = "代理规则命中业务员"
FIELD_SUBOFFICE_OWNER = "子办规则命中负责人"
FIELD_ASSIGN_SOURCE = "Duplicate（重复）"
FIELD_PRODUCT_CAT = "Product Categories（产品大类）"
FIELD_PRODUCT_MODEL = "Product model（具体型号）"

# 飞书 Base 2026-07 字段双语化后，读取时兼容旧字段名。
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    FIELD_LEAD_ID: ("线索ID",),
    FIELD_ASSIGN_METHOD: ("分配方式",),
    FIELD_STATUS: ("分配状态",),
    FIELD_ASSIGNEE: ("最终分配的业务员",),
    FIELD_MANUAL_ASSIGNEE: ("人工改派的业务员",),
    FIELD_SYSTEM: ("系统匹配业务员",),
    FIELD_SUCCESS: ("是否成功分配",),
    FIELD_ASSIGN_SOURCE: ("分配来源",),
    FIELD_SUB_CHANNEL: ("细分渠道（Channel segmentation）", "细分渠道"),
}

# 队列表队列Key 前缀（渠道顺序队列表仍用短名，勿改）。
QUEUE_PREFIX_GOOGLE = "谷歌"
QUEUE_PREFIX_FACEBOOK = "Facebook"
QUEUE_PREFIX_LINKEDIN = "LinkedIn"
QUEUE_PREFIX_ALIBABA = "阿里国际站"
QUEUE_PREFIX_DOMESTIC = "国内渠道"
QUEUE_PREFIX_OUTBOUND = "Outbound渠道"

# 主表 Channels（渠道）写入展示名（English（中文））。
WRITE_CHANNEL_GOOGLE = "Google（谷歌）"
WRITE_CHANNEL_FACEBOOK = "Facebook（脸书）"
WRITE_CHANNEL_LINKEDIN = "LinkedIn（领英）"
WRITE_CHANNEL_ALIBABA = "Alibaba International（阿里国际站）"
WRITE_CHANNEL_DOMESTIC = "Domestic Channel（国内渠道）"
WRITE_CHANNEL_OUTBOUND = "Outbound Channel（出站渠道）"
WRITE_CHANNEL_UNRECOGNIZED = "Unrecognized（无法识别）"

# 读侧兼容：历史短名 / 英文 / 双语标签。
CHANNEL_GOOGLE_ALIASES = frozenset(
    {QUEUE_PREFIX_GOOGLE, "Google", "google", WRITE_CHANNEL_GOOGLE}
)
CHANNEL_FACEBOOK_ALIASES = frozenset(
    {QUEUE_PREFIX_FACEBOOK, "facebook", WRITE_CHANNEL_FACEBOOK, "脸书", "Facebook-Messenger"}
)
CHANNEL_LINKEDIN_ALIASES = frozenset(
    {QUEUE_PREFIX_LINKEDIN, "linkedin", "Linkedln", "领英", WRITE_CHANNEL_LINKEDIN}
)
CHANNEL_ALIBABA_ALIASES = frozenset(
    {QUEUE_PREFIX_ALIBABA, "Alibaba", WRITE_CHANNEL_ALIBABA}
)
CHANNEL_DOMESTIC_ALIASES = frozenset(
    {QUEUE_PREFIX_DOMESTIC, WRITE_CHANNEL_DOMESTIC}
)
CHANNEL_OUTBOUND_ALIASES = frozenset(
    {QUEUE_PREFIX_OUTBOUND, "Outbound", WRITE_CHANNEL_OUTBOUND, "出站渠道"}
)

INVALID_CHANNEL_VALUES = frozenset(
    {
        "",
        "无可用选项",
        "No options available",
        "无法识别",
        "Unrecognized",
        WRITE_CHANNEL_UNRECOGNIZED,
        "N/A",
    }
)
INVALID_SUB_CHANNEL_VALUES = INVALID_CHANNEL_VALUES

# 细分渠道：内部短名（lead-rules / 询盘标签）→ 主表双语写入名。
# Facebook messager 已删除，统一落到 Facebook（脸书）。
SUB_CHANNEL_SHORT_TO_WRITE: dict[str, str] = {
    "谷歌1": "Google 1（谷歌1）",
    "谷歌2": "Google 2（谷歌2）",
    "新官网": "New Website（新官网）",
    "总舱网": "Pod Site（总舱网）",
    "美国舱网": "US Pod Site（美国舱网）",
    "加拿大舱网": "Canada Pod Site（加拿大舱网）",
    "Shopping Mall（谷歌商城）": "Google Shopping Mall（谷歌商城）",
    "阿里1": "Alibaba 1（阿里1）",
    "阿里2": "Alibaba 2（阿里2）",
    "1688": "1688（1688）",
    "中文官网": "Chinese Website（中文官网）",
    "新媒体": "New Media（新媒体）",
    "国内电商": "Domestic E-commerce（国内电商）",
    "中国制造网": "Made-in-China（中国制造网）",
    "国内展会": "Domestic Exhibition（国内展会）",
    "小红书": "Xiaohongshu（小红书）",
    "百度": "Baidu（百度）",
    "电商": "E-commerce（电商）",
    "抖音旗舰店": "Douyin Flagship（抖音旗舰店）",
    "抖音品牌": "Douyin Brand（抖音品牌）",
    "Facebook": "Facebook（脸书）",
    "Facebook messager": "Facebook（脸书）",  # 已删选项；历史/推断兼容
    "Facebook-Messenger": "Facebook（脸书）",
    "Instagram": "Instagram（Instagram）",
    "Ins广告表单线索": "Instagram Lead Form（Ins广告表单线索）",
    "LinkedIn": "LinkedIn（领英）",
    "Linkedln": "LinkedIn（领英）",
    "领英": "LinkedIn（领英）",
    "linkedin": "LinkedIn（领英）",
    "REVOR": "REVOR（Outbound）",
    "Email": "Email（邮件）",
    "无法识别": WRITE_CHANNEL_UNRECOGNIZED,
}

# 双语写入名 → 内部短名（读侧归一；Facebook（脸书）归一为 Facebook）
WRITE_SUB_TO_SHORT: dict[str, str] = {
    v: k
    for k, v in SUB_CHANNEL_SHORT_TO_WRITE.items()
    if k not in {"Facebook messager", "Facebook-Messenger", "Linkedln", "领英", "linkedin"}
}
WRITE_SUB_TO_SHORT["Facebook（脸书）"] = "Facebook"
WRITE_SUB_TO_SHORT["LinkedIn（领英）"] = "LinkedIn"
WRITE_SUB_TO_SHORT[WRITE_CHANNEL_UNRECOGNIZED] = "无法识别"

# 细分渠道（短名或双语）→ 主渠道写入名
_SUB_TO_MAIN_BY_SHORT: dict[str, str] = {
    "Facebook": WRITE_CHANNEL_FACEBOOK,
    "Instagram": WRITE_CHANNEL_FACEBOOK,
    "Facebook messager": WRITE_CHANNEL_FACEBOOK,
    "Facebook-Messenger": WRITE_CHANNEL_FACEBOOK,
    "Ins广告表单线索": WRITE_CHANNEL_FACEBOOK,
    "LinkedIn": WRITE_CHANNEL_LINKEDIN,
    "Linkedln": WRITE_CHANNEL_LINKEDIN,
    "领英": WRITE_CHANNEL_LINKEDIN,
    "linkedin": WRITE_CHANNEL_LINKEDIN,
    "谷歌1": WRITE_CHANNEL_GOOGLE,
    "谷歌2": WRITE_CHANNEL_GOOGLE,
    "新官网": WRITE_CHANNEL_GOOGLE,
    "总舱网": WRITE_CHANNEL_GOOGLE,
    "美国舱网": WRITE_CHANNEL_GOOGLE,
    "加拿大舱网": WRITE_CHANNEL_GOOGLE,
    "Shopping Mall（谷歌商城）": WRITE_CHANNEL_GOOGLE,
    "阿里1": WRITE_CHANNEL_ALIBABA,
    "阿里2": WRITE_CHANNEL_ALIBABA,
    "1688": WRITE_CHANNEL_DOMESTIC,
    "中文官网": WRITE_CHANNEL_DOMESTIC,
    "新媒体": WRITE_CHANNEL_DOMESTIC,
    "国内电商": WRITE_CHANNEL_DOMESTIC,
    "中国制造网": WRITE_CHANNEL_DOMESTIC,
    "国内展会": WRITE_CHANNEL_DOMESTIC,
    "小红书": WRITE_CHANNEL_DOMESTIC,
    "百度": WRITE_CHANNEL_DOMESTIC,
    "电商": WRITE_CHANNEL_DOMESTIC,
    "抖音旗舰店": WRITE_CHANNEL_DOMESTIC,
    "抖音品牌": WRITE_CHANNEL_DOMESTIC,
    "REVOR": WRITE_CHANNEL_OUTBOUND,
    "Email": WRITE_CHANNEL_OUTBOUND,
}

SUB_CHANNEL_TO_CHANNEL: dict[str, str] = dict(_SUB_TO_MAIN_BY_SHORT)
for _short, _write in SUB_CHANNEL_SHORT_TO_WRITE.items():
    _main = _SUB_TO_MAIN_BY_SHORT.get(_short)
    if _main:
        SUB_CHANNEL_TO_CHANNEL[_write] = _main
# 已删除的 Facebook messager 双语不单独建选项；写名与 Facebook 相同。
SUB_CHANNEL_TO_CHANNEL["Facebook（脸书）"] = WRITE_CHANNEL_FACEBOOK


# 任意 Channels 显示名 / 历史短名 → 队列表队列Key 前缀。
QUEUE_KEY_CHANNEL_ALIASES: dict[str, str] = {
    WRITE_CHANNEL_GOOGLE: QUEUE_PREFIX_GOOGLE,
    "Google": QUEUE_PREFIX_GOOGLE,
    "google": QUEUE_PREFIX_GOOGLE,
    QUEUE_PREFIX_GOOGLE: QUEUE_PREFIX_GOOGLE,
    WRITE_CHANNEL_FACEBOOK: QUEUE_PREFIX_FACEBOOK,
    "facebook": QUEUE_PREFIX_FACEBOOK,
    QUEUE_PREFIX_FACEBOOK: QUEUE_PREFIX_FACEBOOK,
    "脸书": QUEUE_PREFIX_FACEBOOK,
    "Facebook-Messenger": QUEUE_PREFIX_FACEBOOK,
    WRITE_CHANNEL_LINKEDIN: QUEUE_PREFIX_LINKEDIN,
    "linkedin": QUEUE_PREFIX_LINKEDIN,
    "Linkedln": QUEUE_PREFIX_LINKEDIN,
    "领英": QUEUE_PREFIX_LINKEDIN,
    QUEUE_PREFIX_LINKEDIN: QUEUE_PREFIX_LINKEDIN,
    WRITE_CHANNEL_ALIBABA: QUEUE_PREFIX_ALIBABA,
    "Alibaba": QUEUE_PREFIX_ALIBABA,
    QUEUE_PREFIX_ALIBABA: QUEUE_PREFIX_ALIBABA,
    WRITE_CHANNEL_DOMESTIC: QUEUE_PREFIX_DOMESTIC,
    QUEUE_PREFIX_DOMESTIC: QUEUE_PREFIX_DOMESTIC,
    WRITE_CHANNEL_OUTBOUND: QUEUE_PREFIX_OUTBOUND,
    "Outbound": QUEUE_PREFIX_OUTBOUND,
    "出站渠道": QUEUE_PREFIX_OUTBOUND,
    QUEUE_PREFIX_OUTBOUND: QUEUE_PREFIX_OUTBOUND,
}

# 队列表前缀 → 主表写入名（自愈写回 Channels 时用）。
QUEUE_PREFIX_TO_WRITE: dict[str, str] = {
    QUEUE_PREFIX_GOOGLE: WRITE_CHANNEL_GOOGLE,
    QUEUE_PREFIX_FACEBOOK: WRITE_CHANNEL_FACEBOOK,
    QUEUE_PREFIX_LINKEDIN: WRITE_CHANNEL_LINKEDIN,
    QUEUE_PREFIX_ALIBABA: WRITE_CHANNEL_ALIBABA,
    QUEUE_PREFIX_DOMESTIC: WRITE_CHANNEL_DOMESTIC,
    QUEUE_PREFIX_OUTBOUND: WRITE_CHANNEL_OUTBOUND,
}

# Channels 无效时，按区域后缀依次尝试这些队列表前缀（队列表里存在才命中）。
FALLBACK_QUEUE_CHANNELS: tuple[str, ...] = (
    QUEUE_PREFIX_GOOGLE,
    QUEUE_PREFIX_FACEBOOK,
    QUEUE_PREFIX_LINKEDIN,
    QUEUE_PREFIX_ALIBABA,
    QUEUE_PREFIX_DOMESTIC,
)

FIELD_ENQUIRY = "Enquiry details（询盘内容）"
FIELD_FB_LEADGEN = "Facebook Leadgen ID"
FIELD_GMAIL_MSG = "Gmail_Msg_ID"

ACOUSTIC_CATEGORY = "Acoustic products 声学产品"
ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")

QUEUE_POINTER_TABLE = "tblGWSsPla3eRfuY"
CHANNEL_QUEUE_TABLE = "tblav9GLrm8Vnf1j"
AGENT_RULE_TABLE = "tblk9x487yPMJGZr"
# 中东/亚洲/公区「按天最少优先」计数看板（只读角色：按天最少优先计数查看）
DAILY_LEAST_COUNT_TABLE = "tbl5SGCRhshZ8f4w"

# OpenAPI 中单选/公式字段可能返回中文标签、双语标签或 option id，需一并识别。
OPTION_YES = frozenset({"是", "Yes", "Yes（是）", "true", "True"})
OPTION_NO = frozenset({"否", "No", "No（否）", "false", "False"})

SUBOFFICE_COUNTRY_YES = OPTION_YES | frozenset({"opthA5jqMG"})
SUBOFFICE_COUNTRY_NO = OPTION_NO | frozenset({"opteBbb8vv"})
AGENT_COUNTRY_YES = OPTION_YES | frozenset({"optstg0Zdp"})
AGENT_COUNTRY_NO = OPTION_NO | frozenset({"opt6XJowhl"})

AGENT_PRODUCT_YES = OPTION_YES | frozenset({"optF9HsNyr"})
AGENT_PRODUCT_NO = OPTION_NO | frozenset({"optWdtyujk"})
AGENT_PRODUCT_PENDING = frozenset({"待确认", "optJ7X2CIx"})
SUCCESS_YES = OPTION_YES | frozenset({"optBhNG4cY"})
SUCCESS_NO = OPTION_NO | frozenset({"opta7i8dt6"})
ASSIGN_METHOD_AUTO = frozenset({"自动", "Automatic", "Automatic（自动）", "opt8r8I1Re"})
ASSIGN_METHOD_MANUAL = frozenset({"人工", "Artificial", "Artificial（人工）", "optBu1XeSg"})

# 写入飞书单选字段时使用的展示名（2026-07 双语字段）。
WRITE_ASSIGN_AUTO = "Automatic（自动）"
WRITE_ASSIGN_MANUAL = "Artificial（人工）"
WRITE_SUCCESS_YES = "Yes（是）"
WRITE_SUCCESS_NO = "No（否）"

FORMULA_YES = OPTION_YES
FORMULA_NO = OPTION_NO

ASSIGN_SOURCE_ELIGIBLE = frozenset({"无重复", "查重不继承", "optGRVFdR1"})
ASSIGN_SOURCE_BLOCKED = frozenset(
    {
        "查重中",
        "查重冲突",
        "查重命中",
        "optLcU4ZPx",
        "optEV2hhbW",
    }
)

# 分配状态（公式字段 fldtmZOqB4）；GET 单条常只返回 option id。
ASSIGN_STATUS_ASSIGNED = frozenset({"✅ 已分配", "optpspV6LA"})
ASSIGN_STATUS_EXCEPTION = frozenset({"❌ 分配异常", "optqgb587m"})
ASSIGN_STATUS_BLOCKED = frozenset({"⏳ 分配中/阻塞", "optIZkcgkB"})
ASSIGN_STATUS_ASSIGNING = frozenset({"🔄 正在匹配规则", "正在分配"})
# 兜底脚本应捞起：真实异常 + 刚录入等待规则匹配 + 账号映射阻塞。
ASSIGN_STATUS_NEEDS_UNBLOCK = (
    ASSIGN_STATUS_EXCEPTION | ASSIGN_STATUS_ASSIGNING | ASSIGN_STATUS_BLOCKED
)
WRITE_STATUS_ASSIGNING = "🔄 正在匹配规则"
WRITE_STATUS_EXCEPTION = "❌ 分配异常"
WRITE_STATUS_BLOCKED = "⏳ 分配中/阻塞"

# 静态审计：禁止在 OpenAPI / 工作流写入中继续使用的旧主表字段名。
DEPRECATED_FIELD_LITERALS: frozenset[str] = frozenset(
    alias for aliases in _FIELD_ALIASES.values() for alias in aliases
)


def get_field(fields: dict, field_name: str, default=None):
    """按当前或历史字段名读取记录字段值。"""
    if field_name in fields:
        return fields[field_name]
    for alias in _FIELD_ALIASES.get(field_name, ()):
        if alias in fields:
            return fields[alias]
    return default


def normalize_queue_key(queue_key: str) -> str:
    """把队列Key 渠道前缀归一到队列表使用的规范名（如 Google（谷歌）|… → 谷歌|…）。"""
    key = (queue_key or "").strip()
    if "|" not in key:
        return key
    channel, rest = key.split("|", 1)
    canonical = channel_to_queue_prefix(channel) or channel
    return f"{canonical}|{rest}"


def channel_to_queue_prefix(channel: str) -> str | None:
    """Channels 显示名 / 历史短名 → 队列表队列Key 前缀。"""
    ch = (channel or "").strip()
    if not ch:
        return None
    if ch in QUEUE_KEY_CHANNEL_ALIASES:
        return QUEUE_KEY_CHANNEL_ALIASES[ch]
    # 大小写不敏感兜底（Facebook / facebook）
    lower_map = {k.lower(): v for k, v in QUEUE_KEY_CHANNEL_ALIASES.items()}
    return lower_map.get(ch.lower())


def to_write_channel(channel: str) -> str | None:
    """任意渠道别名 / 队列表前缀 → 主表双语写入名。"""
    prefix = channel_to_queue_prefix(channel)
    if not prefix:
        return None
    return QUEUE_PREFIX_TO_WRITE.get(prefix)


def normalize_sub_channel_short(sub_channel: str) -> str:
    """细分渠道双语/历史别名 → 内部短名（询盘标签 / lead-rules）。"""
    sub = (sub_channel or "").strip()
    if not sub:
        return ""
    if sub in WRITE_SUB_TO_SHORT:
        return WRITE_SUB_TO_SHORT[sub]
    if sub in SUB_CHANNEL_SHORT_TO_WRITE:
        # 已是短名，或短名别名（如 Linkedln）
        if sub in {"Linkedln", "领英", "linkedin"}:
            return "LinkedIn"
        if sub in {"Facebook messager", "Facebook-Messenger"}:
            return "Facebook"
        return sub
    return sub


def to_write_sub_channel(sub_channel: str) -> str:
    """任意细分渠道别名 → 主表双语写入名；无法映射时原样返回。"""
    sub = (sub_channel or "").strip()
    if not sub:
        return ""
    if sub in SUB_CHANNEL_SHORT_TO_WRITE.values():
        return sub
    short = normalize_sub_channel_short(sub)
    return SUB_CHANNEL_SHORT_TO_WRITE.get(short) or SUB_CHANNEL_SHORT_TO_WRITE.get(sub) or sub


def resolve_channel_from_sub(sub_channel: str) -> str | None:
    """由细分渠道推导主渠道；无法识别时返回 None。"""
    sub = (sub_channel or "").strip()
    if not sub or sub in INVALID_CHANNEL_VALUES:
        return None
    if sub in SUB_CHANNEL_TO_CHANNEL:
        return SUB_CHANNEL_TO_CHANNEL[sub]
    short = normalize_sub_channel_short(sub)
    return SUB_CHANNEL_TO_CHANNEL.get(short)


def is_invalid_channel(channel: str) -> bool:
    return (channel or "").strip() in INVALID_CHANNEL_VALUES


def is_invalid_sub_channel(sub_channel: str) -> bool:
    return (sub_channel or "").strip() in INVALID_SUB_CHANNEL_VALUES


def infer_sub_channel_from_email(
    from_addr: str = "",
    subject: str = "",
    *,
    rules: dict | None = None,
) -> str | None:
    """按 Gmail 发件人/主题推断细分渠道（与 lead-rules resolve_channel 一致）。"""
    if not (from_addr or "").strip() and not (subject or "").strip():
        return None
    if rules is None:
        import json
        from pathlib import Path

        rules_path = Path(__file__).resolve().parent.parent / "lead-rules.json"
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    from lead_fallback_parser import resolve_channel

    _, sub_channel = resolve_channel(from_addr, rules, subject)
    return sub_channel or None


def infer_sub_channel_from_content(content: str) -> str | None:
    """从询盘正文推断细分渠道（无标签行或标签无效时用）。"""
    text = (content or "").strip()
    if not text:
        return None

    lower = text.lower()

    # 表单/通知特征（长特征优先）
    if "新官网询价通知" in text or "soundbox-sys.com" in lower:
        return "新官网"
    if "message from soundbox" in lower or "inquiry@soundboxacoustic.com" in lower:
        return "谷歌1"
    if "new booking entry" in lower:
        return "谷歌2"
    if "soundboxacoustic.com" in lower:
        return "谷歌1"
    if "soundboxbooth.com" in lower or "email@soundboxbooth.com" in lower:
        return "谷歌2"
    if "加拿大舱网" in text:
        return "加拿大舱网"
    if "美国舱网" in text:
        return "美国舱网"
    if "总舱网" in text or "soundbox-pod.com" in lower:
        return "总舱网"
    if (
        "linkedin.com" in lower
        or "inmail" in lower
        or "linkedin lead" in lower
        or "领英询盘" in text
        or "领英表单" in text
        or "领英主页" in text
    ):
        return "LinkedIn"

    # 末行标签：国家-细分渠道-…（即使国家/型号无效也尝试取细分渠道）
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        parts = [p.strip() for p in lines[-1].replace("--", "-").split("-") if p.strip()]
        if len(parts) >= 2:
            sub = parts[1]
            if not is_invalid_sub_channel(sub) and sub in SUB_CHANNEL_TO_CHANNEL:
                return sub

    # 正文关键词（长词优先）。LinkedIn/领英只走上面的平台来源特征，避免官网表单随口提到被改渠道。
    # Email 是表单字段名，禁止当细分渠道关键词。
    skip_generic = {
        "LinkedIn",
        "Linkedln",
        "领英",
        "linkedin",
        "Email",
        "Email（邮件）",
        "LinkedIn（领英）",
    }
    for sub in sorted(SUB_CHANNEL_TO_CHANNEL.keys(), key=len, reverse=True):
        if sub in skip_generic:
            continue
        if sub in text:
            return sub
    return None


def infer_sub_channel_from_signals(
    *,
    enquiry: str = "",
    channels: str = "",
    gmail_msg_id: str = "",
    fb_leadgen: str = "",
    email_from: str = "",
    email_subject: str = "",
    rules: dict | None = None,
) -> str | None:
    """综合邮件元数据、询盘正文、主渠道 + 来源 ID 推断细分渠道。"""
    healed = infer_sub_channel_from_email(email_from, email_subject, rules=rules)
    if healed:
        return healed
    healed = infer_sub_channel_from_content(enquiry)
    if healed:
        return healed
    if (fb_leadgen or "").strip():
        return "Facebook"
    channel = (channels or "").strip()
    if channel in CHANNEL_GOOGLE_ALIASES and (gmail_msg_id or "").strip():
        # 与 lead-rules _default 一致：无法从正文/邮件判定时默认谷歌2
        return "谷歌2"
    if channel in CHANNEL_FACEBOOK_ALIASES:
        return "Facebook"
    if channel in CHANNEL_LINKEDIN_ALIASES:
        return "LinkedIn"
    if channel in CHANNEL_ALIBABA_ALIASES:
        return "阿里1"
    if channel in CHANNEL_DOMESTIC_ALIASES:
        return "中文官网"
    if channel in CHANNEL_OUTBOUND_ALIASES:
        return "REVOR"
    if channel and channel in SUB_CHANNEL_TO_CHANNEL:
        return channel
    return None


def heal_invalid_sub_channel(
    sub_channel: str,
    *,
    enquiry: str = "",
    channels: str = "",
    gmail_msg_id: str = "",
    fb_leadgen: str = "",
    email_from: str = "",
    email_subject: str = "",
    rules: dict | None = None,
) -> str | None:
    """细分渠道无效，或与主渠道 LinkedIn 冲突时返回应写回值。"""
    channel = (channels or "").strip()
    mapped = resolve_channel_from_sub(sub_channel)
    if channel in CHANNEL_LINKEDIN_ALIASES and mapped != WRITE_CHANNEL_LINKEDIN:
        return "LinkedIn"
    if not is_invalid_sub_channel(sub_channel):
        return None
    return infer_sub_channel_from_signals(
        enquiry=enquiry,
        channels=channels,
        gmail_msg_id=gmail_msg_id,
        fb_leadgen=fb_leadgen,
        email_from=email_from,
        email_subject=email_subject,
        rules=rules,
    )


def infer_channel_from_content(content: str) -> str | None:
    """从询盘正文/尾行推断主渠道（用于 Channels 无效时自愈）。"""
    text = (content or "").strip()
    if not text:
        return None

    # 优先解析末行标签：国家-细分渠道-产品-型号
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        tag = lines[-1]
        parts = [p.strip() for p in tag.replace("--", "-").split("-") if p.strip()]
        if len(parts) >= 2:
            healed = resolve_channel_from_sub(parts[1])
            if healed:
                return healed
        # 阿里尾注：阿里1 菲律宾
        for line in reversed(lines[-3:]):
            for prefix in ("阿里1", "阿里2", "1688", "中文官网"):
                if line.startswith(prefix):
                    healed = resolve_channel_from_sub(prefix)
                    if healed:
                        return healed

    # 正文关键词（长词优先，避免误伤）
    for sub in sorted(SUB_CHANNEL_TO_CHANNEL.keys(), key=len, reverse=True):
        if sub in text:
            return SUB_CHANNEL_TO_CHANNEL[sub]
    return None


def infer_channel_from_source_ids(*, fb_leadgen: str = "", gmail_msg_id: str = "") -> str | None:
    """按来源 ID 推断主渠道。"""
    if (fb_leadgen or "").strip():
        return WRITE_CHANNEL_FACEBOOK
    if (gmail_msg_id or "").strip():
        return WRITE_CHANNEL_GOOGLE
    return None


def heal_invalid_channel(
    channel: str,
    *,
    sub_channel: str = "",
    enquiry: str = "",
    fb_leadgen: str = "",
    gmail_msg_id: str = "",
) -> str | None:
    """Channels 无效时返回应写回的主渠道；无需修复则返回 None。"""
    if not is_invalid_channel(channel):
        return None
    return (
        resolve_channel_from_sub(sub_channel)
        or infer_channel_from_content(enquiry)
        or infer_channel_from_source_ids(fb_leadgen=fb_leadgen, gmail_msg_id=gmail_msg_id)
    )


def expand_queue_key_candidates(queue_key: str) -> list[str]:
    """生成队列Key 候选：原值、归一化、以及无效前缀时的区域兜底渠道。"""
    key = (queue_key or "").strip()
    out: list[str] = []
    for cand in (key, normalize_queue_key(key)):
        if cand and cand not in out:
            out.append(cand)
    if "|" not in key:
        return out
    channel, region = key.split("|", 1)
    region = region.strip()
    if not region or not is_invalid_channel(channel):
        return out
    for fb_channel in FALLBACK_QUEUE_CHANNELS:
        cand = f"{fb_channel}|{region}"
        if cand not in out:
            out.append(cand)
    return out
