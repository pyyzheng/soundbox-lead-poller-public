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
    FIELD_SYSTEM: ("系统匹配业务员",),
    FIELD_SUCCESS: ("是否成功分配",),
    FIELD_ASSIGN_SOURCE: ("分配来源",),
    FIELD_SUB_CHANNEL: ("细分渠道（Channel segmentation）", "细分渠道"),
}

# 主渠道写入名必须与队列表队列Key前缀一致（队列表仍用「谷歌」）。
WRITE_CHANNEL_GOOGLE = "谷歌"
# 读侧兼容：Channels 选项曾被改成 Google，或写入失败落到无效选项。
CHANNEL_GOOGLE_ALIASES = frozenset({"谷歌", "Google", "google"})
INVALID_CHANNEL_VALUES = frozenset(
    {"", "无可用选项", "No options available", "无法识别", "N/A"}
)
INVALID_SUB_CHANNEL_VALUES = INVALID_CHANNEL_VALUES

# 细分渠道 → 主渠道（用于写回 / 自愈）
SUB_CHANNEL_TO_CHANNEL: dict[str, str] = {
    "Facebook": "Facebook",
    "Instagram": "Facebook",
    "Facebook messager": "Facebook",
    "Facebook-Messenger": "Facebook-Messenger",
    "谷歌1": WRITE_CHANNEL_GOOGLE,
    "谷歌2": WRITE_CHANNEL_GOOGLE,
    "新官网": WRITE_CHANNEL_GOOGLE,
    "总舱网": WRITE_CHANNEL_GOOGLE,
    "美国舱网": WRITE_CHANNEL_GOOGLE,
    "加拿大舱网": WRITE_CHANNEL_GOOGLE,
    "Shopping Mall（谷歌商城）": WRITE_CHANNEL_GOOGLE,
    "阿里1": "阿里国际站",
    "阿里2": "阿里国际站",
    "1688": "国内渠道",
    "中文官网": "国内渠道",
    "新媒体": "国内渠道",
    "国内电商": "国内渠道",
    "中国制造网": "国内渠道",
}

# 队列Key 前缀别名：Channels 显示名漂移时，查找队列表仍落到规范前缀。
QUEUE_KEY_CHANNEL_ALIASES: dict[str, str] = {
    "Google": WRITE_CHANNEL_GOOGLE,
    "google": WRITE_CHANNEL_GOOGLE,
}

# Channels=无法识别 时，按区域后缀依次尝试这些主渠道队列（队列表里存在才命中）。
FALLBACK_QUEUE_CHANNELS: tuple[str, ...] = (
    WRITE_CHANNEL_GOOGLE,
    "Facebook",
    "阿里国际站",
    "国内渠道",
    "Facebook-Messenger",
)

FIELD_ENQUIRY = "Enquiry details（询盘内容）"
FIELD_FB_LEADGEN = "Facebook Leadgen ID"
FIELD_GMAIL_MSG = "Gmail_Msg_ID"

ACOUSTIC_CATEGORY = "Acoustic products 声学产品"
ERROR_ASSIGNEES = ("未命中规则", "匹配错误请检查", "公式计算异常")

QUEUE_POINTER_TABLE = "tblGWSsPla3eRfuY"
CHANNEL_QUEUE_TABLE = "tblav9GLrm8Vnf1j"
AGENT_RULE_TABLE = "tblk9x487yPMJGZr"

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
    """把队列Key 渠道前缀归一到队列表使用的规范名（如 Google|… → 谷歌|…）。"""
    key = (queue_key or "").strip()
    if "|" not in key:
        return key
    channel, rest = key.split("|", 1)
    canonical = QUEUE_KEY_CHANNEL_ALIASES.get(channel, channel)
    if channel in CHANNEL_GOOGLE_ALIASES:
        canonical = WRITE_CHANNEL_GOOGLE
    return f"{canonical}|{rest}"


def resolve_channel_from_sub(sub_channel: str) -> str | None:
    """由细分渠道推导主渠道；无法识别时返回 None。"""
    sub = (sub_channel or "").strip()
    if not sub or sub in INVALID_CHANNEL_VALUES:
        return None
    return SUB_CHANNEL_TO_CHANNEL.get(sub)


def is_invalid_channel(channel: str) -> bool:
    return (channel or "").strip() in INVALID_CHANNEL_VALUES


def is_invalid_sub_channel(sub_channel: str) -> bool:
    return (sub_channel or "").strip() in INVALID_SUB_CHANNEL_VALUES


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
    if "select your country" in lower and "inquiry:" in lower:
        return "谷歌1"
    if "telephone number:" in lower and "message:" in lower:
        return "谷歌1"
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

    # 末行标签：国家-细分渠道-…（即使国家/型号无效也尝试取细分渠道）
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        parts = [p.strip() for p in lines[-1].replace("--", "-").split("-") if p.strip()]
        if len(parts) >= 2:
            sub = parts[1]
            if not is_invalid_sub_channel(sub) and sub in SUB_CHANNEL_TO_CHANNEL:
                return sub

    # 正文关键词（长词优先）
    for sub in sorted(SUB_CHANNEL_TO_CHANNEL.keys(), key=len, reverse=True):
        if sub in text:
            return sub
    return None


def infer_sub_channel_from_signals(
    *,
    enquiry: str = "",
    channels: str = "",
    gmail_msg_id: str = "",
    fb_leadgen: str = "",
) -> str | None:
    """综合询盘正文 + 主渠道 + 来源 ID 推断细分渠道。"""
    healed = infer_sub_channel_from_content(enquiry)
    if healed:
        return healed
    if (fb_leadgen or "").strip():
        return "Facebook"
    channel = (channels or "").strip()
    if channel in CHANNEL_GOOGLE_ALIASES and (gmail_msg_id or "").strip():
        # Gmail 表单询盘缺标签行时，国际站表单多为谷歌1
        return "谷歌1"
    if channel == "Facebook":
        return "Facebook"
    if channel == "阿里国际站":
        return "阿里1"
    if channel == "国内渠道":
        return "中文官网"
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
) -> str | None:
    """细分渠道无效时返回应写回值；无需修复则返回 None。"""
    if not is_invalid_sub_channel(sub_channel):
        return None
    return infer_sub_channel_from_signals(
        enquiry=enquiry,
        channels=channels,
        gmail_msg_id=gmail_msg_id,
        fb_leadgen=fb_leadgen,
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
        return "Facebook"
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
