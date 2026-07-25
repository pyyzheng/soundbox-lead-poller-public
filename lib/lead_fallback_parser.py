#!/usr/bin/env python3
"""
lead-fallback-parser.py — 纯规则线索解析器（无 LLM 依赖）

当 lead-allocator 超时时，main agent 调用此脚本做规则解析。
输出格式与 lead-allocator 完全一致，可直接喂给 lead-finalize.js。

Usage:
  python3 lead-fallback-parser.py --body '<email body>' --from 'email@soundboxbooth.com'
  python3 lead-fallback-parser.py --file /tmp/email-body.txt --from 'email@soundboxbooth.com'
"""

import json
import re
import sys
import os
import urllib.request
import threading as _threading
from pathlib import Path

# Import shared filter functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lead_filter_common import (
    load_lead_rules as load_filter_rules,
    extract_email_address,
    check_skip_sender, check_skip_subject,
    check_spam, check_gibberish_message, check_promotional_content,
    check_supplier_outreach, check_trivial_content, check_short_message,
    body_has_message_field,
    check_irrelevant_business, check_inquiry_keywords
)

RULES_FILE = Path(__file__).parent.parent / "config" / "lead-rules.json"
IP_CACHE_FILE = Path(__file__).parent.parent / "memory" / "ip-country-cache.json"
_ip_cache_lock = _threading.Lock()

# IPv6 前缀缓存
IPV6_CACHE = {
    "2607:a400": "美国", "2602:ffe4": "新加坡", "2402:1980": "马来西亚",
    "2a02:8086": "爱尔兰", "2a02:2788": "比利时", "2401:4900": "印度", "2404:": "澳大利亚",
    "2400:": "亚太地区",
}

# 英文→中文国家名翻译（在线查询返回英文）
COUNTRY_EN_ZH = {
    "united states": "美国", "usa": "美国", "us": "美国", "canada": "加拿大",
    "germany": "德国", "france": "法国", "united kingdom": "英国", "uk": "英国",
    "italaly": "意大利", "italy": "意大利",  # italaly typo 兼容 LLM 输出 "spain": "西班牙", "netherlands": "荷兰",
    "belgium": "比利时", "switzerland": "瑞士", "austria": "奥地利",
    "sweden": "瑞典", "norway": "挪威", "denmark": "丹麦", "finland": "芬兰",
    "poland": "波兰", "czech republic": "捷克", "czechia": "捷克",
    "hungary": "匈牙利", "romania": "罗马尼亚", "greece": "希腊",
    "portugal": "葡萄牙", "ireland": "爱尔兰", "ukraine": "乌克兰",
    "russia": "俄罗斯", "turkey": "土耳其", "india": "印度",
    "singapore": "新加坡", "japan": "日本", "south korea": "韩国", "korea": "韩国",
    "china": "中国", "australia": "澳大利亚", "new zealand": "新西兰",
    "brazil": "巴西", "mexico": "墨西哥", "argentina": "阿根廷",
    "south africa": "南非", "uae": "阿联酋", "united arab emirates": "阿联酋", "saudi arabia": "沙特",
    "thailand": "泰国", "vietnam": "越南", "indonesia": "印尼", "malaysia": "马来西亚",
    "philippines": "菲律宾", "israel": "以色列", "egypt": "埃及",
    "colombia": "哥伦比亚", "chile": "智利", "peru": "秘鲁",
    "qatar": "卡塔尔",
    "latvia": "拉脱维亚", "lithuania": "立陶宛", "estonia": "爱沙尼亚",
    "slovakia": "斯洛伐克", "slovenia": "斯洛文尼亚", "croatia": "克罗地亚",
    "bulgaria": "保加利亚", "serbia": "塞尔维亚",
    "morocco": "摩洛哥", "nigeria": "尼日利亚", "kenya": "肯尼亚",
    "pakistan": "巴基斯坦", "bangladesh": "孟加拉", "taiwan": "台湾",
    "hong kong": "香港", "macau": "澳门", "macao": "澳门",
    "kazakhstan": "哈萨克斯坦", "uzbekistan": "乌兹别克斯坦",
    "georgia": "格鲁吉亚", "azerbaijan": "阿塞拜疆",
    "mongolia": "蒙古", "nepal": "尼泊尔", "sri lanka": "斯里兰卡",
    "cambodia": "柬埔寨", "myanmar": "缅甸", "laos": "老挝",
    "oman": "阿曼", "kuwait": "科威特", "bahrain": "巴林",
    "jordan": "约旦", "lebanon": "黎巴嫩", "iraq": "伊拉克",
    "iceland": "冰岛", "luxembourg": "卢森堡", "malta": "马耳他",
    "cyprus": "塞浦路斯", "moldova": "摩尔多瓦", "belarus": "白俄罗斯",
    "albania": "阿尔巴尼亚", "north macedonia": "北马其顿",
    "montenegro": "黑山", "bosnia and herzegovina": "波黑",
    "panama": "巴拿马", "costa rica": "哥斯达黎加",
    "dominican republic": "多米尼加", "ecuador": "厄瓜多尔",
    "uruguay": "乌拉圭", "venezuela": "委内瑞拉",
    "bolivia": "玻利维亚", "paraguay": "巴拉圭",
    "ghana": "加纳", "tanzania": "坦桑尼亚", "ethiopia": "埃塞俄比亚",
    "cameroon": "喀麦隆", "senegal": "塞内加尔",
}

def translate_country(name):
    """翻译英文国家名为中文"""
    if not name:
        return ""
    key = name.strip().lower()
    # Exact match
    if key in COUNTRY_EN_ZH:
        return COUNTRY_EN_ZH[key]
    # Try first word (e.g. "United States" → "united states" already handled)
    first = key.split()[0]
    if first in COUNTRY_EN_ZH:
        return COUNTRY_EN_ZH[first]
    # Already Chinese? return as-is
    if any('\u4e00' <= ch <= '\u9fff' for ch in name):
        return name
    return name  # Unknown, return as-is

# 电话区号 → 国家
PHONE_PREFIXES = {
    "+1": "美国/加拿大", "+86": "中国", "+91": "印度", "+65": "新加坡",
    "+358": "芬兰", "+421": "斯洛伐克", "+971": "阿联酋", "+44": "英国",
    "+49": "德国", "+33": "法国", "+34": "西班牙", "+39": "意大利",
    "+31": "荷兰", "+46": "瑞典", "+47": "挪威", "+45": "丹麦",
    "+32": "比利时", "+43": "奥地利", "+41": "瑞士", "+48": "波兰",
    "+420": "捷克", "+36": "匈牙利", "+40": "罗马尼亚", "+30": "希腊",
    "+90": "土耳其", "+966": "沙特", "+968": "阿曼", "+974": "卡塔尔",
    "+20": "埃及", "+27": "南非", "+55": "巴西", "+52": "墨西哥",
    "+81": "日本", "+82": "韩国", "+84": "越南", "+62": "印尼",
    "+60": "马来西亚", "+63": "菲律宾", "+66": "泰国", "+856": "老挝",
    "+855": "柬埔寨", "+95": "缅甸", "+380": "乌克兰", "+375": "白俄罗斯",
    "+7": "俄罗斯/哈萨克", "+371": "拉脱维亚", "+370": "立陶宛", "+372": "爱沙尼亚",
    "+373": "摩尔多瓦", "+354": "冰岛",
}

# 地名 → 国家（城市名在前，国家名在后；城市名更精确，优先匹配）
# 城市名（优先匹配，比国家名更精确）
_CITY_NAMES = {
    "helsinki": "芬兰", "stockholm": "瑞典", "oslo": "挪威", "copenhagen": "丹麦",
    "london": "英国", "manchester": "英国", "berlin": "德国", "munich": "德国",
    "paris": "法国", "madrid": "西班牙", "rome": "意大利", "milan": "意大利",
    "amsterdam": "荷兰", "dubai": "阿联酋", "abu dhabi": "阿联酋",
    "singapore": "新加坡", "tokyo": "日本", "seoul": "韩国",
    "bangkok": "泰国", "mumbai": "印度", "delhi": "印度", "new york": "美国",
    "los angeles": "美国", "san francisco": "美国", "chicago": "美国",
    "sydney": "澳大利亚", "melbourne": "澳大利亚", "toronto": "加拿大",
    "vancouver": "加拿大", "johannesburg": "南非", "sao paulo": "巴西",
    "doha": "卡塔尔",
}
# 国家名（城市名未匹配时兜底）
_COUNTRY_NAMES = {k: v for k, v in COUNTRY_EN_ZH.items() if k not in (
    "us", "uk",  # 缩写太短易误匹配，排除
)}
PLACE_NAMES = {**_CITY_NAMES, **_COUNTRY_NAMES}


def load_rules():
    if RULES_FILE.exists():
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def strip_html(text: str) -> str:
    """去除 HTML 标签和实体"""
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'</(?:p|div|span|strong|em|b|i|ul|ol|li)[^>]*>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    for entity, char in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'), ('&nbsp;', ' '), ('&quot;', '"')]:
        text = text.replace(entity, char)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


_INVALID_VALUES = {"/", "-", "N/A", "n/a", "null", "none"}


def _valid_match(m):
    """提取正则匹配值，有效返回字符串，否则返回 None"""
    val = m.group(1).strip()
    return val if val and val not in _INVALID_VALUES else None


def extract_field(body, field_names):
    """Extract field value — same-line "Name: Alice" and next-line "Name:\\nAlice" """
    for name in field_names:
        m = re.search(rf'(?<!\w){re.escape(name)}[ \t]*[:：][ \t]*([^\n]+)', body, re.IGNORECASE)
        val = _valid_match(m) if m else None
        if val:
            return val
        m = re.search(rf'(?<!\w){re.escape(name)}[ \t]*[:：][ \t]*\n([^\n]+)', body, re.IGNORECASE)
        val = _valid_match(m) if m else None
        if val:
            return val
    return ""


def extract_fields(body: str) -> dict:
    """提取所有表单字段（支持英文和中文表单）"""
    clean_body = strip_html(body)
    has_message_field = body_has_message_field(clean_body)
    # 英文表单字段
    name = extract_field(clean_body, ["Name"])
    email = extract_field(clean_body, ["Email", "E-mail"])
    company = extract_field(clean_body, ["Company"])
    phone = extract_field(clean_body, ["Phone", "Telephone", "Telephone Number"])
    # Phone 字段邮箱误填校验：用户在 Phone 框填邮箱时清空，避免污染数据
    if phone and "@" in phone:
        phone = ""
    # Message/Inquiry 在下方多行匹配块统一处理
    message = ""
    country = extract_field(clean_body, ["Country", "Select your country *"])
    # 中文表单字段（舱网系列：姓名/邮箱/电话/公司/留言内容/国家）
    if not name:
        name = extract_field(clean_body, ["姓名"])
    if not email:
        email = extract_field(clean_body, ["邮箱"])
    if not company:
        company = extract_field(clean_body, ["公司"])
    if not phone:
        phone = extract_field(clean_body, ["电话"])
    if not message:
        message = extract_field(clean_body, ["留言内容", "留言"])
    if not country:
        country = extract_field(clean_body, ["国家"])

    # Message/Inquiry 优先多行匹配（这些字段通常包含多行内容）
    # extract_field 只取下一行，会导致多行 inquiry 被截断为第一行
    m = re.search(
        r'(?:Message|Inquiry|留言内容|留言)\s*[:：]\s*(.+?)(?=\n\s*(?:[A-Z][^:\n]{2,30}\*?\s*[:：]|Date|Time|Page URL|Remote IP|User Agent|Powered by|是否|页面信息|---|$))',
        clean_body, re.IGNORECASE | re.DOTALL
    )
    if m:
        message = m.group(1).strip()

    # 多行未匹配 → 回退到单行 extract_field
    if not message:
        message = extract_field(clean_body, ["Message", "Inquiry"])

    # Fallback：Message 仍为空时，去掉表单字段行，用剩余正文作为 message
    if not message:
        cleaned = re.sub(
            r'^(?:Name|E-?mail|Company|Telephone\s*Number|Phone|Message|Inquiry|Inquiry\s*No|Date|Time|'
            r'Page URL|Page Type|Page Title|Product Name|Remote IP|IP Address|User Agent|Powered by|Country|Select your country|Device Type|Submitted At'
            r'|姓名|邮箱|电话|公司|留言内容|留言|国家|咨询单号|是否需要回调|页面类型|页面标题|页面完整URL|来源页面URL'
            r'|客户IP地址|浏览器类型|设备类型|处理状态|优先级|提交时间'
            r')\s*[:：][^\n]*\n?',
            '', clean_body, flags=re.MULTILINE | re.IGNORECASE
        )
        cleaned = re.sub(r'^-{3,}\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = strip_html(cleaned).strip()
        if cleaned and len(cleaned) > 10:
            message = cleaned

    return {
        "name": name,
        "email": email,
        "company": company,
        "phone": phone,
        "message": strip_html(message),
        "country": country,
        "has_message_field": has_message_field,
    }


def extract_remote_ip(body: str) -> str:
    m = re.search(r'(?:Remote IP|IP Address)\s*[:：]\s*(\S+)', body, re.IGNORECASE)
    return m.group(1).strip() if m else ""


# 交付地/项目地提取模式（只捕获国家名长度的词，介词/连词处截断）
_STOP_WORDS = r'for|and|or|with|the|a|an|in|at|of|by|is|are|was|were|be|been|being'
_CAP_PLACE = rf'([A-Z][a-z]+(?:(?:\s+(?!{_STOP_WORDS})\s*)[A-Z]?[a-z]+){{0,2}})'
_DELIVERY_PATTERNS = [
    re.compile(rf'(?:ship|shipp?ing|deliver|send|export|supply).{{0,20}}\bto\b\s+{_CAP_PLACE}', re.IGNORECASE),
    re.compile(rf'(?:project|site|client).{{0,15}}\b(?:in|at)\b\s+{_CAP_PLACE}', re.IGNORECASE),
    re.compile(rf'\bfor\s+(?:our\s+)?(?:new\s+)?(?:office|project|site|building)\s+(?:in|at)\b\s+{_CAP_PLACE}', re.IGNORECASE),
]

# 预计算排序后的地名（城市名优先）和电话区号（避免每次调用 sorted）
_SORTED_CITY_NAMES = sorted(_CITY_NAMES.items(), key=lambda x: -len(x[0]))
_SORTED_COUNTRY_NAMES = sorted(_COUNTRY_NAMES.items(), key=lambda x: -len(x[0]))
_SORTED_PLACE_NAMES = sorted(PLACE_NAMES.items(), key=lambda x: -len(x[0]))
_SORTED_PHONE_PREFIXES = sorted(PHONE_PREFIXES.items(), key=lambda x: -len(x[0]))


def identify_country(ip: str, phone: str, message: str, country_field: str = "") -> str:
    """国家识别：表单字段 → 交付地/项目地 → 地名 → IP → 电话区号"""
    # 0. 表单 Country/国家 字段（最准确）
    if country_field:
        translated = translate_country(country_field)
        if translated:
            return translated

    # 0.5 交付地/项目地（"ship to India", "project in Dubai" 等）
    for pattern in _DELIVERY_PATTERNS:
        m = pattern.search(message)
        if m:
            place = m.group(1).strip()
            words = place.split()
            for i in range(len(words), 0, -1):
                candidate = " ".join(words[:i]).lower()
                if candidate in PLACE_NAMES:
                    return PLACE_NAMES[candidate]

    # 1. 询盘内容中的地名（城市名优先，国家名兜底）
    text = f"{message} {phone}".lower()
    for place, country in _SORTED_CITY_NAMES:
        if re.search(rf'\b{re.escape(place)}\b', text, re.IGNORECASE):
            return country
    for place, country in _SORTED_COUNTRY_NAMES:
        if re.search(rf'\b{re.escape(place)}\b', text, re.IGNORECASE):
            return country

    # 2. Remote IP
    if ip:
        country_en = lookup_ip(ip)
        if country_en:
            return translate_country(country_en)

    # 3. 电话区号
    if phone:
        clean_phone = phone.replace(" ", "").replace("-", "")
        for prefix, country in _SORTED_PHONE_PREFIXES:
            if clean_phone.startswith(prefix):
                return country

    return ""


def _load_ip_cache():
    """Load persistent IP cache from file"""
    if IP_CACHE_FILE.exists():
        try:
            with open(IP_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def _save_ip_cache(cache):
    """Save persistent IP cache to file"""
    IP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(IP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except:
        pass

def _cache_ip_prefix(ip, country):
    """Cache first 2 segments of IPv4, first 3 segments of IPv6"""
    if not country:
        return
    if ":" in ip:
        # IPv6: cache first 3 groups (e.g. 2a02:2788:11e4)
        parts = ip.split(":")
        prefix = ":".join(parts[:3]) if len(parts) >= 3 else ip
    else:
        # IPv4: cache first 2 octets (e.g. 142.93)
        parts = ip.split(".")
        prefix = ".".join(parts[:2]) if len(parts) >= 2 else ip
    _ip_cache_lock.acquire()
    try:
        file_cache = _load_ip_cache()
        file_cache[prefix] = country
        _save_ip_cache(file_cache)
    finally:
        _ip_cache_lock.release()

def _lookup_all_caches(ip):
    """Check inline cache + file cache"""
    ip_lower = ip.lower()
    # Inline cache
    for prefix, country in IPV6_CACHE.items():
        if ip_lower.startswith(prefix.lower()):
            return country
    # File cache
    file_cache = _load_ip_cache()
    for prefix, country in file_cache.items():
        if ip_lower.startswith(prefix.lower()):
            return country
    return ""

def lookup_ip(ip: str) -> str:
    """查询 IP 归属（inline cache → file cache → online query → auto-cache）"""
    country = _lookup_all_caches(ip)
    if country:
        return country

    # Online query
    if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip) or ':' in ip:
        country = _lookup_ip_online(ip)
        if country:
            country = translate_country(country)
            _cache_ip_prefix(ip, country)
        return country

    return ""


def _lookup_ip_online(ip):
    """Online IP lookup via ip-api.com (with proxy fallback)"""
    import os as _os
    url = f"http://ip-api.com/json/{ip}?fields=country,status"
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw/1.0"})
    # Try with system proxy if set
    proxy = _os.environ.get("https_proxy") or _os.environ.get("HTTPS_PROXY") or _os.environ.get("http_proxy") or _os.environ.get("HTTP_PROXY") 
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
            opener = urllib.request.build_opener(handler)
            with opener.open(req, timeout=5) as resp:
                data = json.loads(resp.read())
        else:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        if data.get("status") == "success" and data.get("country"):
            return data["country"]
    except Exception:
        pass
    return ""


def identify_product_category(message: str, rules: dict) -> str:
    """识别产品大类"""
    cats = rules.get("product_categories", {})
    text = message.lower()

    for cat_name, keywords in cats.items():
        for kw in keywords:
            if re.search(rf'\b{re.escape(kw)}\b', text, re.IGNORECASE):
                return cat_name

    return ""


# 容量人数 → 尺寸档；与 url_model_map / 官网规格大致对齐
_PERSON_TO_SIZE = {
    1: "S",
    2: "M",
    3: "M",
    4: "L",
    5: "L",
    6: "XL",
    7: "XL",
    8: "XXL",
}
_WORD_PERSON_TO_N = {
    "single": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "eight": 8,
}
# 各系列可用尺寸后缀（超出则钳制到最大档）
_SERIES_MAX_SIZE = {
    "SR": "XXL",
    "VR": "XXL",
    "VRT": "L",
    "ART": "XXL",
}
_SIZE_RANK = {"S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5}

# ASCII 边界：中文旁的「型号VRT」「VRT请报价」也能命中；VRT 必须在 VR 前
_SERIES_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(VRT|ART|SR|VR)(?:-(S|M|L|XL|XXL))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def sized_model_code(series: str, size: str) -> str:
    """系列 + 尺寸 → 型号码；VRT 最大到 L。"""
    series = (series or "").upper()
    size = (size or "").upper()
    if size == "XS":
        size = "S"
    if series not in _SERIES_MAX_SIZE or size not in _SIZE_RANK:
        return series or ""
    max_size = _SERIES_MAX_SIZE[series]
    if _SIZE_RANK[size] > _SIZE_RANK[max_size]:
        size = max_size
    return f"{series}-{size}"


def extract_series_model_keyword(message: str) -> str:
    """从正文提取系列/型号码：VRT、SR、VR、ART 及 VRT-L 等。

    使用 ASCII 边界，避免 Unicode \\b 在中文旁失效。
    只要出现这些关键词即可识别，不依赖前后空格。
    """
    if not message:
        return ""
    sized = re.finditer(
        r"(?<![A-Za-z0-9])(VRT|ART|SR|VR)-(S|M|L|XL|XXL)(?![A-Za-z0-9])",
        message,
        re.IGNORECASE,
    )
    for m in sized:
        return sized_model_code(m.group(1).upper(), m.group(2).upper())

    m = _SERIES_TOKEN_RE.search(message)
    if not m:
        return ""
    series = m.group(1).upper()
    size = (m.group(2) or "").upper()
    if size:
        return sized_model_code(series, size)
    return series


def _is_excluded_hit(text: str, exclude_words: list[str], matched_kw: str) -> bool:
    """排除词用 ASCII 词边界；裸词系列（SR/VR/VRT/ART）命中时不否决。"""
    kw = (matched_kw or "").strip()
    if re.match(r"^(VRT|ART|SR|VR)(-(S|M|L|XL|XXL))?$", kw, re.I):
        return False
    if not exclude_words:
        return False
    lower = text.lower()
    for ex in exclude_words:
        ex = (ex or "").strip().lower()
        if not ex:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(ex)}(?![A-Za-z0-9])", lower):
            return True
    return False


def default_series_for_sub_channel(sub_channel: str) -> str:
    """按细分渠道给出默认系列（容量兜底用）。"""
    if sub_channel in {"谷歌1", "总舱网"}:
        return "SR"
    if sub_channel in {"谷歌2", "Facebook", "新官网"}:
        return "VRT"
    return ""


def extract_size_token(message: str) -> str:
    """从正文提取尺寸档：S/M/L/XL/XXL；找不到返回空串。"""
    if not message:
        return ""
    text = message

    # Model:/Size: 表单字段（含换行）
    m = re.search(
        r"(?:Model|Size)\s*[:：]\s*\n?\s*(XS|S|M|L|XL|XXL)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        val = m.group(1).upper()
        return "S" if val == "XS" else val

    # (Size L) / size L / size: L
    m = re.search(
        r"(?:\(|\b)size\s*[:：]?\s*(XS|S|M|L|XL|XXL)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        val = m.group(1).upper()
        return "S" if val == "XS" else val

    # Facebook / 多语言：4_people、4 people、1_persona
    m = re.search(
        r"\b(\d+)\s*_?(?:people|persons|person|personas|persona|pax)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        n = int(m.group(1))
        if n >= 8:
            return "XXL"
        return _PERSON_TO_SIZE.get(n, "L" if n >= 4 else "")

    # single-person / four-person
    m = re.search(
        r"\b(single|one|two|three|four|five|six|eight)[- ](?:person|people|persons)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        n = _WORD_PERSON_TO_N.get(m.group(1).lower())
        if n:
            return _PERSON_TO_SIZE.get(n, "")

    return ""


def identify_product_model(
    message: str,
    rules: dict,
    sub_channel: str = "",
    default_series: str = "",
) -> str:
    """白名单机制识别具体型号；正文出现 VRT/SR/VR/ART 即可命中。"""
    text = (message or "") + " "

    # 1) 优先：ASCII 边界裸词（含中文旁、带尺寸）
    direct = extract_series_model_keyword(text)
    if direct:
        size = extract_size_token(text)
        series = direct.split("-", 1)[0]
        if size and "-" not in direct and series in _SERIES_MAX_SIZE:
            return sized_model_code(series, size)
        return direct

    models = rules.get("product_models", {})
    sized_kw = re.compile(r"^(SR|VR|VRT|ART)-(S|M|L|XL|XXL)$", re.I)

    matched_series = ""
    for _model_name, model_info in models.items():
        code = model_info.get("code", _model_name)
        exclude_words = [w.lower() for w in model_info.get("exclude", [])]
        keywords = sorted(model_info.get("keywords", []), key=len, reverse=True)
        for kw in keywords:
            # 长关键词仍用 \\b；裸词已由 extract_series_model_keyword 覆盖
            if len(kw) <= 4 and kw.upper() in _SERIES_MAX_SIZE:
                continue
            if not re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE):
                continue
            if _is_excluded_hit(text, exclude_words, kw):
                continue
            kw_upper = kw.upper()
            if sized_kw.match(kw_upper):
                return kw_upper
            matched_series = code
            break
        if matched_series:
            break

    size = extract_size_token(text)
    series = (
        matched_series
        or (default_series or "").upper()
        or default_series_for_sub_channel(sub_channel)
    )

    if size and series in _SERIES_MAX_SIZE:
        return sized_model_code(series, size)

    if matched_series:
        return matched_series

    if sub_channel == "谷歌1" and size:
        return sized_model_code("SR", size)

    return "无法识别"


def resolve_channel(from_addr: str, rules: dict, subject: str = "",
                    to_addr: str = "") -> tuple[str, str]:
    """识别渠道。先按 from 匹配（表单邮件 from=渠道邮箱），from 不匹配时按 to 匹配
    （客户直接发到渠道邮箱的邮件，如腾讯转发后 from=客户邮箱、to=渠道邮箱）。
    支持 sub_channel_map 从 subject 匹配子渠道。"""
    channels = rules.get("channels", {})

    def _emails(addr: str) -> list:
        if not addr:
            return []
        angles = re.findall(r'<([^>]+)>', addr)
        if angles:
            return [a.lower().strip() for a in angles]
        return [p.strip().lower() for p in addr.split(",") if "@" in p]

    def _match_one(email: str):
        for sender, ch in channels.items():
            if sender.startswith("_") or sender == "_default":
                continue
            if email == sender.lower():
                channel = ch.get("channel", "谷歌")
                if "sub_channel_map" in ch and subject:
                    for keyword, sub_ch in ch["sub_channel_map"].items():
                        if keyword in subject:
                            return channel, sub_ch
                    return channel, ch.get("default_sub_channel", "谷歌2")
                return channel, ch.get("sub_channel", "谷歌2")
        return None

    for addr in (from_addr, to_addr):
        for e in _emails(addr):
            hit = _match_one(e)
            if hit:
                return hit
    default = channels.get("_default", {})
    return default.get("channel", "谷歌"), default.get("sub_channel", "谷歌2")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=str, help="邮件正文")
    parser.add_argument("--file", type=str, help="邮件正文文件路径")
    parser.add_argument("--from", dest="from_addr", required=True, help="发件人地址")
    args = parser.parse_args()

    body = ""
    if args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    elif args.body:
        body = args.body
    else:
        print(json.dumps({"status": "error", "reason": "no input body"}))
        sys.exit(1)

    # ── Filtering chain (same as gmail-webhook-router.py) ──
    filter_rules = load_filter_rules()
    from_addr = extract_email_address(args.from_addr or "")
    subject = getattr(args, 'subject', '') or ""

    # 1. Skip sender
    skip, skip_reason = check_skip_sender(from_addr, filter_rules)
    if skip:
        print(json.dumps({"status": "skipped", "reason": skip_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    # 2. Skip subject
    skip_sub, skip_sub_reason = check_skip_subject(subject, filter_rules)
    if skip_sub:
        print(json.dumps({"status": "skipped", "reason": skip_sub_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    fields_pre = extract_fields(body)
    gibberish, gibberish_reason = check_gibberish_message(
        fields_pre.get("message", ""),
        filter_rules,
        has_message_field=fields_pre.get("has_message_field", False),
    )
    if gibberish:
        print(json.dumps({"status": "skipped", "reason": gibberish_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    short_msg, short_reason = check_short_message(
        fields_pre.get("message", ""),
        filter_rules,
        has_message_field=fields_pre.get("has_message_field", False),
    )
    if short_msg:
        print(json.dumps({"status": "skipped", "reason": short_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    supplier, supplier_reason = check_supplier_outreach(
        fields_pre.get("message", ""),
        fields_pre.get("company", ""),
        subject,
        body,
        rules=filter_rules,
    )
    if supplier:
        print(json.dumps({"status": "skipped", "reason": supplier_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    trivial, trivial_reason = check_trivial_content(
        fields_pre.get("name", ""), fields_pre.get("message", ""), rules,
    )
    if trivial:
        print(json.dumps({"status": "skipped", "reason": trivial_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    # 3. Bot signals — extract fields early for filtering
    is_spam, spam_reason = check_spam(
        fields_pre.get("name", ""), fields_pre.get("email", "") or from_addr,
        fields_pre.get("message", ""), filter_rules
    )
    if is_spam:
        print(json.dumps({"status": "skipped", "reason": spam_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    # 4. Semantic spam
    content_spam, content_reason = check_promotional_content(
        fields_pre.get("name", ""), subject,
        fields_pre.get("message", ""), fields_pre.get("company", ""), filter_rules
    )
    if content_spam:
        print(json.dumps({"status": "skipped", "reason": content_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    # 5. Irrelevant business
    irr, irr_reason = check_irrelevant_business(
        fields_pre.get("name", ""), fields_pre.get("company", ""),
        fields_pre.get("message", ""), filter_rules
    )
    if irr:
        print(json.dumps({"status": "skipped", "reason": irr_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    # 6. Inquiry keywords
    has_kw, kw_reason = check_inquiry_keywords(
        fields_pre.get("name", ""), fields_pre.get("message", ""),
        fields_pre.get("company", ""), filter_rules
    )
    if not has_kw:
        print(json.dumps({"status": "skipped", "reason": kw_reason, "_parser": "fallback-rule"}, ensure_ascii=False))
        sys.exit(0)

    # ── End filtering, proceed to normal parsing ──

    rules = load_rules()
    fields = extract_fields(body)
    clean_body = strip_html(body)
    ip = extract_remote_ip(clean_body)
    country = identify_country(ip, fields["phone"], fields["message"])
    channel, sub_channel = resolve_channel(args.from_addr, rules)
    # 提取 Page URL 用于产品型号识别
    page_url = ""
    m = re.search(r'Page URL\s*[:：]\s*(\S+)', clean_body, re.IGNORECASE)
    if m:
        page_url = m.group(1).strip()

    # 用 message + page_url 一起匹配产品类别和型号
    match_text = fields["message"]
    if page_url:
        match_text += " " + page_url

    product_category = identify_product_category(match_text, rules)
    # 谷歌2是舱网，无匹配产品关键词时默认静音舱；谷歌1是总官网，不默认
    if not product_category and sub_channel == "谷歌2":
        product_category = "静音舱"
    product_model = identify_product_model(match_text, rules, sub_channel)

    identifier = "-".join(filter(None, [country, sub_channel, product_category, product_model]))

    inquiry_content = (
        f"Name: {fields['name']}\n"
        f"Email: {fields['email']}\n"
        f"Company: {fields['company']}\n"
        f"Telephone Number: {fields['phone']}\n"
        f"Message: {fields['message']}\n"
        f"\n{identifier}"
    )

    result = {
        "status": "parsed",
        "is_website_form": True,
        "is_duplicate": False,
        "name": fields["name"],
        "email": fields["email"],
        "company": fields["company"],
        "phone": fields["phone"],
        "message": fields["message"],
        "country": country,
        "channel": channel,
        "sub_channel": sub_channel,
        "product_category": product_category,
        "product_model": product_model,
        "identifier": identifier,
        "inquiry_content": inquiry_content,
        "raw_body": body,
        "remote_ip": ip,
        "_parser": "fallback-rule",
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
