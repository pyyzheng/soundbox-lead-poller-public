#!/usr/bin/env python3
"""Facebook Lead Ads 线索自动抓取管线

从 Meta Marketing API（Graph API）拉取 Lead Ads 线索，
解析、去重、分级后写入飞书多维表格。

用法:
  python3 facebook-lead-poller.py              # 正常运行
  python3 facebook-lead-poller.py --dry-run    # 试运行，不写入飞书
  python3 facebook-lead-poller.py --list-forms # 列出所有表单
"""

import json
import os
import re
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qsl
import re

# 匹配 Graph API 版本前缀 (如 /v20.0, /v25.0)
_API_VERSION_RE = re.compile(r"/v\d+\.\d+")

import requests

# ── 路径配置 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
LIB_DIR = SCRIPT_DIR / "lib"
CONFIG_PATH = SCRIPT_DIR / "facebook-config.json"
TASKS_PATH = SCRIPT_DIR / "facebook-tasks.json"
PENDING_PATH = SCRIPT_DIR / "facebook-pending.jsonl"

# 将 lib 目录加入 import 路径
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fb-poller")

# ── 常量 ─────────────────────────────────────────────────
GRAPH_API_BASE = "https://graph.facebook.com/v20.0"

def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    log.error("缺少必需环境变量：%s", name)
    sys.exit(1)


FEISHU_APP_TOKEN = require_env("FEISHU_APP_TOKEN")
FEISHU_TABLE_ID = require_env("FEISHU_TABLE_ID")
FIELD_ENQUIRY = "Enquiry details（询盘内容）"
FIELD_LEVEL = "Clue level（线索等级）"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_CHANNELS = "Channels（渠道）"
FIELD_AUTOREPLY_STATUS = "Auto-Reply Status"
FIELD_COUNTRY = "Country（国家）"
FIELD_SUB_CHANNEL = "Channel segmentation (细分渠道)"
FIELD_PRODUCT_CAT = "Product Categories（产品大类）"
FIELD_PRODUCT_MODEL = "Product model（具体型号）"
FIELD_CUSTOMER_NAME = "Customer Name（客户名称）"
FIELD_PHONE = "Phone（客户电话）"
FIELD_WECHAT = "Wechat（微信）"
FIELD_ALI_ID = "阿里ID"
FIELD_FB_LEADGEN = "Facebook Leadgen ID"

from assignment_fields import FIELD_ASSIGN_METHOD, WRITE_ASSIGN_AUTO  # noqa: E402
from tagline_fields import feishu_product_category  # noqa: E402
from feishu_writer import (  # noqa: E402
    check_feishu_fb_contact_duplicate,
    check_feishu_fb_leadgen_duplicate,
)

# ── 国家分类 ──────────────────────────────────────────────
SR_COUNTRIES = {"阿联酋", "香港", "印尼", "日本", "韩国", "马来西亚", "菲律宾", "卡塔尔", "沙特", "越南"}
VRT_COUNTRIES = {
    "澳大利亚", "新西兰", "德国", "法国", "意大利", "西班牙", "英国",
    "荷兰", "比利时", "瑞士", "奥地利", "瑞典", "挪威", "丹麦", "芬兰",
    "波兰", "捷克", "葡萄牙", "爱尔兰", "希腊",
    "美国", "加拿大",
}

# 电话区号 → 国家（中文），按长度降序排列确保最长匹配
PHONE_PREFIX_MAP = {
    # 3位区号
    "+852": "香港", "+853": "澳门", "+886": "台湾",
    # 2位区号
    "+1": "美国", "+44": "英国", "+33": "法国", "+49": "德国",
    "+81": "日本", "+82": "韩国", "+86": "中国", "+91": "印度",
    "+55": "巴西", "+61": "澳大利亚", "+62": "印尼", "+63": "菲律宾",
    "+65": "新加坡", "+66": "泰国", "+7": "俄罗斯",
    "+34": "西班牙", "+39": "意大利", "+31": "荷兰", "+46": "瑞典",
    "+47": "挪威", "+48": "波兰", "+351": "葡萄牙", "+30": "希腊",
    "+43": "奥地利", "+41": "瑞士", "+32": "比利时", "+45": "丹麦",
    "+358": "芬兰", "+353": "爱尔兰", "+420": "捷克", "+373": "摩尔多瓦",
    "+40": "罗马尼亚", "+36": "匈牙利", "+354": "冰岛",
    "+51": "秘鲁", "+56": "智利", "+381": "塞尔维亚", "+370": "立陶宛",
    # 非洲
    "+233": "加纳", "+234": "尼日利亚", "+20": "埃及", "+27": "南非",
    "+254": "肯尼亚",
    # 中东
    "+971": "阿联酋", "+974": "卡塔尔", "+966": "沙特", "+965": "科威特",
    "+968": "阿曼", "+973": "巴林",
    # 其他
    "+60": "马来西亚", "+84": "越南", "+64": "新西兰",
}

# 英文国家名 → 中文
COUNTRY_EN_TO_CN = {
    "united states": "美国", "us": "美国", "usa": "美国",
    "canada": "加拿大", "ca": "加拿大",
    "united kingdom": "英国", "uk": "英国", "gb": "英国",
    "australia": "澳大利亚", "au": "澳大利亚",
    "new zealand": "新西兰", "nz": "新西兰",
    "germany": "德国", "de": "德国",
    "france": "法国", "fr": "法国",
    "italy": "意大利", "it": "意大利",
    "spain": "西班牙", "es": "西班牙",
    "netherlands": "荷兰", "nl": "荷兰",
    "belgium": "比利时", "be": "比利时",
    "switzerland": "瑞士", "ch": "瑞士",
    "austria": "奥地利", "at": "奥地利",
    "sweden": "瑞典", "se": "瑞典",
    "norway": "挪威", "no": "挪威",
    "denmark": "丹麦", "dk": "丹麦",
    "finland": "芬兰", "fi": "芬兰",
    "poland": "波兰", "pl": "波兰",
    "czech republic": "捷克", "czechia": "捷克", "cz": "捷克",
    "portugal": "葡萄牙", "pt": "葡萄牙",
    "ireland": "爱尔兰", "ie": "爱尔兰",
    "greece": "希腊", "gr": "希腊",
    "japan": "日本", "jp": "日本",
    "south korea": "韩国", "korea": "韩国", "kr": "韩国",
    "china": "中国", "cn": "中国",
    "india": "印度", "in": "印度",
    "indonesia": "印尼", "id": "印尼",
    "malaysia": "马来西亚", "my": "马来西亚",
    "philippines": "菲律宾", "ph": "菲律宾",
    "singapore": "新加坡", "sg": "新加坡",
    "thailand": "泰国", "th": "泰国",
    "vietnam": "越南", "vn": "越南",
    "hong kong": "香港", "hk": "香港",
    "taiwan": "台湾", "tw": "台湾",
    "united arab emirates": "阿联酋", "uae": "阿联酋", "ae": "阿联酋",
    "saudi arabia": "沙特", "sa": "沙特",
    "qatar": "卡塔尔", "qa": "卡塔尔",
    "brazil": "巴西", "br": "巴西",
    "mexico": "墨西哥", "mx": "墨西哥",
    "ghana": "加纳", "gh": "加纳",
    "nigeria": "尼日利亚", "ng": "尼日利亚",
    "egypt": "埃及", "eg": "埃及",
    "south africa": "南非", "za": "南非",
    "kenya": "肯尼亚", "ke": "肯尼亚",
    "russia": "俄罗斯", "ru": "俄罗斯",
}


# ── Meta API ─────────────────────────────────────────────

def get_access_token() -> str:
    """从环境变量获取 Page Access Token"""
    token = os.environ.get("META_PAGE_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("META_PAGE_ACCESS_TOKEN 环境变量未设置")
    return token


def api_get(path: str, token: str, params: dict | None = None, retries: int = 3) -> dict:
    """调用 Graph API GET 请求，带重试"""
    url = f"{GRAPH_API_BASE}{path}"
    params = params or {}
    params["access_token"] = token

    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json()

            if "error" in data:
                err = data["error"]
                code = err.get("code", 0)

                # Token 过期
                if code == 190:
                    raise RuntimeError(f"Token 过期: {err.get('message', '')}")

                # 速率限制
                if code in (17, 80004) or resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    log.warning(f"速率限制，等待 {wait}s...")
                    time.sleep(wait)
                    continue

                # 其他错误
                raise RuntimeError(f"API 错误 [{code}]: {err.get('message', '')}")

            return data

        except requests.RequestException as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                log.warning(f"请求失败，{wait}s 后重试: {e}")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError("API 请求重试耗尽")


def list_lead_forms(
    token: str,
    page_id: str,
    min_created_time: str | None = None,
    min_updated_time: str | None = None,
) -> list[dict]:
    """列出 Page 下所有 Lead Forms，可选按 created_time / updated_time 过滤。

    优先用 updated_time：很多老表单仍在持续收线索，按创建日过滤会漏（如香日VRT舱-copy）。
    """
    all_forms = []
    path = f"/{page_id}/leadgen_forms"
    params = {
        "fields": "id,name,locale,status,created_time,updated_time",
        "limit": 100,
    }

    while True:
        data = api_get(path, token, params)
        forms = data.get("data", [])
        all_forms.extend(forms)

        paging = data.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break
        parsed = urlparse(next_url)
        path = _API_VERSION_RE.sub("", parsed.path, count=1)
        params = dict(parse_qsl(parsed.query))
        params.pop("access_token", None)

    log.info(f"API 返回 {len(all_forms)} 个表单")

    if min_updated_time:
        cutoff = parse_iso_datetime(min_updated_time)
        if cutoff is None:
            log.warning("无效的 min_updated_time=%r，跳过 updated_time 过滤", min_updated_time)
        else:
            filtered = []
            for f in all_forms:
                # updated_time 缺失时保留，避免误丢
                ref = parse_iso_datetime(f.get("updated_time") or "") or parse_iso_datetime(
                    f.get("created_time") or ""
                )
                if ref is None or ref >= cutoff:
                    filtered.append(f)
            log.info(
                "按 updated_time 过滤后保留 %d 个表单 (>= %s)",
                len(filtered),
                min_updated_time,
            )
            all_forms = filtered
    elif min_created_time:
        cutoff = parse_iso_datetime(min_created_time)
        if cutoff is None:
            log.warning("无效的 min_form_created_time=%r，跳过表单时间过滤", min_created_time)
        else:
            filtered = []
            for f in all_forms:
                ct = parse_iso_datetime(f.get("created_time", "") or "")
                if ct is None or ct >= cutoff:
                    filtered.append(f)
            log.info(f"过滤后保留 {len(filtered)} 个表单 (created_time >= {min_created_time})")
            all_forms = filtered

    for f in all_forms:
        log.info(f"  [{f['id']}] {f.get('name', '(无名)')} — {f.get('status', '?')}")
    return all_forms


def parse_iso_datetime(value: str) -> datetime | None:
    """Parse Meta/GH ISO times. Accepts +0000 and +00:00."""
    if not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    # 2026-07-13T00:00:00+0000 → +00:00
    s = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s)
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, OSError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_leads(token: str, form_id: str, since: str | None = None) -> list[dict]:
    """从指定表单拉取线索。

    Meta ``/leads?since=`` 不可靠，必须在客户端按 created_time 过滤。
    线索按时间倒序返回：遇到早于 since 的条目即可停止分页。
    """
    params = {
        "fields": "id,created_time,field_data,ad_id,form_id",
        "limit": 100,
    }
    since_dt: datetime | None = None
    if since:
        since_dt = parse_iso_datetime(since)
        if since_dt is None:
            # 绝不回退到全量历史（曾因此误导入大量旧线索）
            since_dt = datetime.now(timezone.utc) - timedelta(hours=24)
            log.warning(
                "无效的 since 格式 %r，安全回退到最近 24 小时: %s",
                since,
                since_dt.isoformat(),
            )
        # 仍传 since 给 Meta（若生效可减少流量），但不依赖它
        params["since"] = int(since_dt.timestamp())

    all_leads: list[dict] = []
    path = f"/{form_id}/leads"
    since_ts = params.get("since")
    stop_paging = False

    while True:
        data = api_get(path, token, params)
        leads = data.get("data", [])
        for lead in leads:
            ct = parse_iso_datetime(lead.get("created_time", "") or "")
            if since_dt and ct and ct < since_dt:
                stop_paging = True
                break
            all_leads.append(lead)

        if stop_paging:
            break

        paging = data.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break

        parsed = urlparse(next_url)
        path = _API_VERSION_RE.sub("", parsed.path, count=1)
        params = dict(parse_qsl(parsed.query))
        params.pop("access_token", None)
        if since_ts:
            params["since"] = since_ts

    return all_leads


# ── 数据解析 ─────────────────────────────────────────────

def parse_field_data(field_data: list[dict]) -> dict:
    """将 Meta API 的 field_data 数组转为 {字段名: 值} 字典"""
    result = {}
    for field in field_data:
        name = field.get("name", "")
        values = field.get("values", [])
        result[name] = values[0] if values else ""
    return result


def detect_form_type(fields: dict) -> str:
    """检测表单类型：A（无国家字段）或 B（有国家字段）"""
    # 检查是否有 country 字段
    for key in fields:
        if key.lower() in ("country", "country_code"):
            return "B"
    return "A"


def infer_country_from_phone(phone: str) -> str:
    """从电话区号推断国家"""
    if not phone:
        return ""

    # 标准化
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        if phone.startswith("00"):
            phone = "+" + phone[2:]
        else:
            phone = "+" + phone

    # 按长度降序匹配区号
    for prefix_len in (4, 3, 2):
        prefix = phone[:prefix_len]
        if prefix in PHONE_PREFIX_MAP:
            return PHONE_PREFIX_MAP[prefix]

    return ""


def normalize_country(raw: str) -> str:
    """英文国家名 → 中文"""
    if not raw:
        return ""
    return COUNTRY_EN_TO_CN.get(raw.strip().lower(), raw.strip())


def determine_product(country: str) -> tuple[str, str]:
    """根据国家确定产品大类和型号

    返回 (产品大类, 具体型号)，如 ("静音舱", "SR")
    """
    if not country:
        return ("无法识别", "无法识别")

    if country in SR_COUNTRIES:
        return ("静音舱", "SR")

    if country in VRT_COUNTRIES:
        return ("静音舱", "VRT")

    return ("无法识别", "无法识别")


def format_custom_answers(fields: dict, form_type: str) -> str:
    """将自定义问题答案格式化为 message 文本"""
    standard_fields = {"full_name", "email", "phone_number", "phone_number_verified",
                       "country", "country_code", "company_name", "work_email",
                       "business_email", "message", "message(project_type)"}
    parts = []
    for key, val in fields.items():
        if key.lower() not in standard_fields and val:
            parts.append(f"{key}: {val}")
    return "; ".join(parts) if parts else ""


def format_inquiry_details(parsed: dict) -> str:
    """格式化询盘内容（严格参照 lead-finalize.js 格式）

    格式:
    Name:{name}
    Email:{email}
    Company:{company}
    Telephone Number:{phone}
    Message:{message}

    {国家}-Facebook-{产品大类}-{具体型号}
    """
    name = parsed.get("full_name", "")
    email = parsed.get("email", "")
    company = parsed.get("company", "")
    phone = parsed.get("phone_number", "")
    message = parsed.get("message", "")

    # 尾行标签
    country = parsed.get("country", "")
    product_cat = parsed.get("product_category", "")
    product_model = parsed.get("product_model", "")
    sub_channel = "Facebook"

    tag_parts = [p for p in [country, sub_channel, product_cat, product_model] if p]
    tag_line = "-".join(tag_parts)

    # 组装（字段冒号后有空格，和 lead-finalize.js 一致）
    content = f"Name: {name}\nEmail: {email}\nCompany: {company}\nTelephone Number: {phone}\nMessage: {message}\n\n{tag_line}"
    return content


def process_lead(raw_lead: dict, config: dict) -> dict:
    """处理单条线索：解析 → 推断 → 格式化"""
    fields = parse_field_data(raw_lead.get("field_data", []))
    form_type = detect_form_type(fields)

    # 国家判断
    country_raw = ""
    for key in ("country", "country_code", "Country"):
        if key in fields:
            country_raw = fields[key]
            break

    if country_raw:
        country = normalize_country(country_raw)
    else:
        country = infer_country_from_phone(fields.get("phone_number", ""))

    # 产品路由
    product_cat, product_model = determine_product(country)

    # Message 处理
    message = ""
    for key in ("How can I help you?", "message", "Message", "message(project_type)"):
        if key in fields and fields[key]:
            message = fields[key]
            break

    if not message:
        message = format_custom_answers(fields, form_type)

    parsed = {
        "lead_id": raw_lead.get("id", ""),
        "created_time": raw_lead.get("created_time", ""),
        "form_id": raw_lead.get("form_id", ""),
        "ad_id": raw_lead.get("ad_id", ""),
        "form_type": form_type,
        "full_name": fields.get("full_name", ""),
        "email": fields.get("email", "") or fields.get("work_email", "") or fields.get("business_email", ""),
        "company": fields.get("company_name", "") or fields.get("company", ""),
        "phone_number": fields.get("phone_number", ""),
        "country": country,
        "product_category": product_cat,
        "product_model": product_model,
        "message": message,
        "raw_fields": fields,
    }

    parsed["inquiry_content"] = format_inquiry_details(parsed)
    return parsed


# ── 飞书 API ─────────────────────────────────────────────

def get_feishu_token() -> str:
    """获取飞书 tenant_access_token"""
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": os.environ["FEISHU_APP_ID"],
            "app_secret": os.environ["FEISHU_APP_SECRET"],
        },
        timeout=15,
    )
    data = resp.json()
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"飞书 token 获取失败: {data}")
    return token


def build_feishu_write_fields(parsed: dict, clue_level: str = "") -> dict:
    """Write structured fields directly; do not rely on Feishu AI shortcuts."""
    write_fields = {
        FIELD_ENQUIRY: parsed["inquiry_content"],
        FIELD_CHANNELS: "Facebook",
        FIELD_ASSIGN_METHOD: WRITE_ASSIGN_AUTO,
        FIELD_SUB_CHANNEL: "Facebook",
    }
    if clue_level:
        write_fields[FIELD_LEVEL] = clue_level
    if parsed.get("country"):
        write_fields[FIELD_COUNTRY] = parsed["country"]
    if parsed.get("product_category"):
        write_fields[FIELD_PRODUCT_CAT] = feishu_product_category(parsed["product_category"])
    if parsed.get("product_model"):
        write_fields[FIELD_PRODUCT_MODEL] = parsed["product_model"]
    if parsed.get("full_name"):
        write_fields[FIELD_CUSTOMER_NAME] = parsed["full_name"]
    if parsed.get("phone_number"):
        write_fields[FIELD_PHONE] = parsed["phone_number"]
    if parsed.get("email"):
        write_fields[FIELD_EMAIL] = parsed["email"]
        write_fields[FIELD_AUTOREPLY_STATUS] = "Pending"
    if parsed.get("lead_id"):
        write_fields[FIELD_FB_LEADGEN] = parsed["lead_id"]
    write_fields.setdefault(FIELD_ALI_ID, "N/A")
    return write_fields


def feishu_create_record(token: str, fields: dict) -> dict:
    """在飞书多维表格创建记录"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"fields": fields},
        timeout=30,
    )
    return resp.json()


# ── 去重 ─────────────────────────────────────────────────

def load_tasks() -> dict:
    """加载已处理任务记录"""
    if TASKS_PATH.exists():
        with open(TASKS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_leads": {}, "last_pull_time": None}


def save_tasks(tasks: dict):
    """保存任务记录"""
    with open(TASKS_PATH, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def is_processed(lead_id: str, tasks: dict) -> bool:
    """检查线索是否已处理"""
    return lead_id in tasks.get("processed_leads", {})


def mark_processed(lead_id: str, record_id: str, tasks: dict):
    """标记线索已处理"""
    tasks.setdefault("processed_leads", {})[lead_id] = {
        "record_id": record_id,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


def save_pending(lead: dict, error: str):
    """保存失败线索到 pending 文件"""
    with open(PENDING_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "lead_id": lead.get("lead_id", ""),
            "error": error,
            "raw_data": lead,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False) + "\n")


# ── 线索分级 ─────────────────────────────────────────────

def grade_lead_content(inquiry_content: str, email: str = "") -> str:
    """调用 lead_grader.py 进行线索分级"""
    try:
        from lead_grader import grade_lead
        result = grade_lead(inquiry_content, email)
        return result.get("level", "")
    except Exception as e:
        log.warning(f"线索分级失败: {e}")
        return ""


# ── 主流程 ───────────────────────────────────────────────

def load_config() -> dict:
    """加载配置"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def run(dry_run: bool = False, since_override: str | None = None):
    """主流程：拉取 → 解析 → 去重 → 分级 → 写入"""
    config = load_config()
    token = get_access_token()
    tasks = load_tasks()

    feishu_token = None
    if not dry_run:
        feishu_token = get_feishu_token()

    meta_cfg = config.get("meta", {})
    page_id = meta_cfg.get("page_id", "")
    forms = config.get("forms", [])

    if not forms:
        # 自动发现：按 updated_time（默认 120 天）而非 created_time，避免漏掉仍在出单的老表单
        lookback_days = int(config.get("min_form_updated_days", 120))
        min_updated = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        all_forms = list_lead_forms(token, page_id, min_updated_time=min_updated)
        if not all_forms:
            log.info("没有找到任何表单")
            return
        forms = [{"form_id": f["id"], "form_name": f.get("name", ""), "form_type": "A"} for f in all_forms]

    total_new = 0
    total_skipped = 0
    total_written = 0
    total_failed = 0

    since = since_override or tasks.get("last_pull_time")

    # 安全兜底：如果 cache 丢失（since 为 None），只拉最近 1 小时
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        log.warning(f"last_pull_time 为空，安全回退到最近 1 小时: {since}")

    for form_cfg in forms:
        form_id = form_cfg["form_id"]
        form_name = form_cfg.get("form_name", form_id)
        log.info(f"拉取表单: {form_name} ({form_id})")

        leads = fetch_leads(token, form_id, since)
        log.info(f"  获取 {len(leads)} 条线索")

        for raw_lead in leads:
            lead_id = raw_lead.get("id", "")

            # 去重：本地 cache
            if is_processed(lead_id, tasks):
                total_skipped += 1
                continue

            total_new += 1

            try:
                parsed = process_lead(raw_lead, config)

                if not dry_run and feishu_token:
                    existing = check_feishu_fb_leadgen_duplicate(feishu_token, lead_id)
                    if not existing:
                        existing = check_feishu_fb_contact_duplicate(
                            feishu_token,
                            parsed.get("email", ""),
                            parsed.get("phone_number", ""),
                        )
                    if existing:
                        total_skipped += 1
                        total_new -= 1
                        mark_processed(lead_id, existing.get("record_id", ""), tasks)
                        log.info(
                            "飞书已存在，跳过: %s → %s",
                            lead_id,
                            existing.get("record_id", ""),
                        )
                        continue

                # 分级
                clue_level = grade_lead_content(
                    parsed["inquiry_content"],
                    parsed.get("email", ""),
                )

                write_fields = build_feishu_write_fields(parsed, clue_level=clue_level)

                if dry_run:
                    log.info(f"[DRY-RUN] 线索 {lead_id}:")
                    log.info(f"  国家={parsed['country']} 产品={parsed['product_category']}-{parsed['product_model']} 等级={clue_level}")
                    log.info(f"  询盘内容:\n{parsed['inquiry_content'][:200]}...")
                    mark_processed(lead_id, "dry-run", tasks)
                else:
                    # 写入飞书
                    result = feishu_create_record(feishu_token, write_fields)
                    record_data = result.get("data", {}).get("record", {})
                    record_id = record_data.get("record_id", "")

                    if record_id:
                        mark_processed(lead_id, record_id, tasks)
                        total_written += 1
                        log.info(f"写入成功: {lead_id} → {record_id} ({parsed['country']}-{parsed['product_category']}-{parsed['product_model']})")
                    else:
                        total_failed += 1
                        err_msg = result.get("msg", "未知错误")
                        log.error(f"写入失败: {lead_id} — {err_msg}")
                        save_pending(parsed, err_msg)

            except Exception as e:
                total_failed += 1
                log.error(f"处理线索 {lead_id} 失败: {e}")
                save_pending(raw_lead, str(e))

    # 更新最后拉取时间
    tasks["last_pull_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    save_tasks(tasks)

    log.info(f"完成: 新增={total_new} 跳过={total_skipped} 写入={total_written} 失败={total_failed}")

    if total_written > 0 and not dry_run:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
            from github_dispatch import trigger_assignment_unblock

            trigger_assignment_unblock(
                source="facebook-lead-poller",
                created_count=total_written,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("触发 assignment-unblock 失败: %s", exc)


def cmd_list_forms(config: dict):
    """列出所有 Lead Forms"""
    token = get_access_token()
    page_id = config.get("meta", {}).get("page_id", "")
    if not page_id:
        log.error("配置中缺少 page_id")
        return
    list_lead_forms(token, page_id)


def main():
    parser = argparse.ArgumentParser(description="Facebook Lead Ads 线索管线")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不写入飞书")
    parser.add_argument("--list-forms", action="store_true", help="列出所有表单")
    parser.add_argument("--since", type=str, help="只拉取此时间之后的线索 (ISO 8601, 如 2026-05-22T01:00:00+0800)")
    args = parser.parse_args()

    if args.list_forms:
        config = load_config()
        cmd_list_forms(config)
        return

    run(dry_run=args.dry_run, since_override=args.since)


if __name__ == "__main__":
    main()
