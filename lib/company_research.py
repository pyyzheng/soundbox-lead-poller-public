"""
company_research.py — B2B 客户背景调研核心模块

云端化版本，替代 Claude Code skill 的调研逻辑。
流程：查询未调研记录 → 过滤 → 网页抓取 → AI 分析 → 写入飞书
"""

import json
import logging
import os
import re
import socket
import sys
import datetime

import requests
from bs4 import BeautifulSoup

from lib.feishu_utils import (
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
    extract_text,
    feishu_api,
    get_feishu_token,
)
from lib.prompts.company_research_prompt import (
    PUBLIC_EMAIL_DOMAINS,
    SYSTEM_PROMPT,
    build_user_message,
    get_followup_priority,
)
from lib.zhipu_client import call_zhipu

log = logging.getLogger("company-research")

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4.5-air")

# 飞书字段 ID
FIELD_RESEARCH = "fldxVAIFBU"       # Company Research
FIELD_GRADE = "fldSppO2Mb"          # Customer Grade
FIELD_PRIORITY = "fldbYCL6TY"       # Follow-up Priority
FIELD_CLUE_LEVEL = "fldiAnOZD8"     # Clue level
FIELD_ENTRY_TIME = "Entry Time（录入时间）"
FIELD_EMAIL = "Email（客户邮箱）"
FIELD_NAME = "Customer Name（客户名称）"
FIELD_COUNTRY = "Country（国家）"
FIELD_ENQUIRY = "Enquiry details（询盘内容）"

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

COUNTRY_EN = {
    "美国": "USA", "英国": "UK", "德国": "Germany", "法国": "France",
    "澳大利亚": "Australia", "加拿大": "Canada", "日本": "Japan",
    "韩国": "South Korea", "印度": "India", "巴西": "Brazil",
    "墨西哥": "Mexico", "阿联酋": "UAE", "沙特": "Saudi Arabia",
    "新加坡": "Singapore", "马来西亚": "Malaysia", "泰国": "Thailand",
    "印度尼西亚": "Indonesia", "菲律宾": "Philippines", "越南": "Vietnam",
    "智利": "Chile", "阿根廷": "Argentina", "哥伦比亚": "Colombia",
    "秘鲁": "Peru", "西班牙": "Spain", "意大利": "Italy",
    "荷兰": "Netherlands", "比利时": "Belgium", "瑞典": "Sweden",
    "挪威": "Norway", "丹麦": "Denmark", "芬兰": "Finland",
    "波兰": "Poland", "捷克": "Czech Republic", "土耳其": "Turkey",
    "以色列": "Israel", "南非": "South Africa", "埃及": "Egypt",
    "新西兰": "New Zealand", "爱尔兰": "Ireland", "瑞士": "Switzerland",
    "奥地利": "Austria", "葡萄牙": "Portugal", "俄罗斯": "Russia",
    "乌克兰": "Ukraine", "匈牙利": "Hungary", "罗马尼亚": "Romania",
    "希腊": "Greece", "香港": "Hong Kong", "台湾": "Taiwan",
    "中国": "China",
}


def _get_proxies() -> dict | None:
    """从环境变量读取代理配置。
    GitHub Actions runner 在境外无需代理，保留入口以备后续需要。"""
    return None


def _domain_resolves(domain: str) -> bool:
    """检测域名是否能 DNS 解析（区分"域名不存在"与"站点宕机"）。

    域名无 DNS 记录 → 联系邮箱大概率无效，应提示销售改用电话/社交媒体。
    """
    clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
    try:
        socket.getaddrinfo(clean, 443, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except socket.gaierror:
        return False


def _domain_to_company_name(domain: str) -> str:
    """从域名提取可能的公司名（conexionesltda.cl → Conexionesltda）"""
    name = domain.split(".")[0]
    return name.replace("-", " ").replace("_", " ").title()


# ── 查询未调研记录 ──────────────────────────────────────────────────────────

def query_unresearched_records(token: str, date_range: str = "today") -> list[dict]:
    """查询飞书中未调研的记录，移植自 company-research-query.sh"""
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    if date_range == "today":
        start, end = today, today + datetime.timedelta(days=1)
    elif date_range == "yesterday":
        start, end = yesterday, today
    else:
        # 尝试解析日期或日期范围
        parts = date_range.strip().split()
        try:
            start = datetime.date.fromisoformat(parts[0])
            end = (datetime.date.fromisoformat(parts[1]) + datetime.timedelta(days=1)
                   if len(parts) > 1 else start + datetime.timedelta(days=1))
        except (ValueError, IndexError):
            log.warning(f"无法解析日期范围: {date_range}，使用今天")
            start, end = today, today + datetime.timedelta(days=1)

    start_ts = int(datetime.datetime(start.year, start.month, start.day).timestamp() * 1000)
    end_ts = int(datetime.datetime(end.year, end.month, end.day).timestamp() * 1000)

    api = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
           f"/tables/{FEISHU_TABLE_ID}/records")

    all_pending = []
    page_token = ""
    seen = 0

    while True:
        url = f"{api}?page_size=100"
        if page_token:
            url += f"&page_token={page_token}"

        resp = feishu_api("GET", url, token, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("data", {}).get("items", [])
        has_more = data.get("data", {}).get("has_more", False)
        page_token = data.get("data", {}).get("page_token", "")

        for item in items:
            f = item["fields"]
            entry = f.get(FIELD_ENTRY_TIME, 0)
            if isinstance(entry, list):
                entry = entry[0] if entry else 0

            if entry < start_ts:
                has_more = False
                break

            if entry >= end_ts:
                seen += 1
                continue

            # 已有调研结果则跳过
            cr = f.get("Company Research", "")
            if cr and not (isinstance(cr, list) and len(cr) == 0):
                seen += 1
                continue

            dt = datetime.datetime.fromtimestamp(entry / 1000).strftime("%Y-%m-%d %H:%M")
            all_pending.append({
                "record_id": item["record_id"],
                "date": dt,
                "email": extract_text(f.get(FIELD_EMAIL, "")),
                "name": extract_text(f.get(FIELD_NAME, "")),
                "country": f.get(FIELD_COUNTRY, ""),
                "enquiry": extract_text(f.get(FIELD_ENQUIRY, ""))[:500],
                "clue_level": f.get(FIELD_CLUE_LEVEL, ""),
            })
            seen += 1

        if not has_more:
            break

    log.info(f"查询范围 {start}~{end}，扫描 {seen} 条，待调研 {len(all_pending)} 条")
    return all_pending


def get_single_record(token: str, record_id: str) -> dict | None:
    """获取单条飞书记录"""
    api = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
           f"/tables/{FEISHU_TABLE_ID}/records/{record_id}")
    try:
        resp = feishu_api("GET", api, token, timeout=15)
        resp.raise_for_status()
        item = resp.json().get("data", {}).get("record")
        if not item:
            return None
        f = item["fields"]
        return {
            "record_id": record_id,
            "email": extract_text(f.get(FIELD_EMAIL, "")),
            "name": extract_text(f.get(FIELD_NAME, "")),
            "country": f.get(FIELD_COUNTRY, ""),
            "enquiry": extract_text(f.get(FIELD_ENQUIRY, ""))[:500],
            "clue_level": f.get(FIELD_CLUE_LEVEL, ""),
        }
    except Exception as e:
        log.error(f"获取记录 {record_id} 失败: {e}")
        return None


# ── 判断是否需要调研 ──────────────────────────────────────────────────────────

def should_research(record: dict) -> tuple[bool, str]:
    """判断是否需要调研，返回 (need_research, company_identity)

    company_identity 可能是域名或公司名。
    """
    email = record.get("email", "").strip()
    name = record.get("name", "").strip()
    country = record.get("country", "").strip()
    enquiry = record.get("enquiry", "").strip()

    # 提取邮箱域名
    domain = ""
    if "@" in email:
        domain = email.split("@")[-1].lower().strip()

    # 公共邮箱 + 无公司名 → C 级，不需要调研
    if domain in PUBLIC_EMAIL_DOMAINS:
        if _extract_company_from_enquiry(enquiry):
            return True, _extract_company_from_enquiry(enquiry)
        return False, ""

    # 企业邮箱 → 用域名调研
    if domain and domain not in PUBLIC_EMAIL_DOMAINS:
        return True, domain

    # 有公司名（从 enquiry 提取）
    company = _extract_company_from_enquiry(enquiry)
    if company:
        return True, company

    return False, ""


# 不是公司名的字段（电话/邮箱等）
_NON_COMPANY_PREFIXES = re.compile(
    r"^(?:telephone|phone|fax|mobile|tel|email|address|city|country|state|zip|postal)",
    re.IGNORECASE,
)


def _extract_company_from_enquiry(enquiry: str) -> str:
    """从询盘内容提取公司名称"""
    if not enquiry:
        return ""
    # 匹配 "company: xxx" / "company:xxx" 等模式
    m = re.search(r"company\s*[:：]\s*(.+?)(?:\n|$)", enquiry, re.IGNORECASE)
    if m:
        val = m.group(1).strip()[:100]
        # 过滤明显不是公司名的值（如 "Telephone Number:+573174396274"）
        if _NON_COMPANY_PREFIXES.match(val):
            log.debug(f"忽略非公司名字段: {val[:50]}")
        else:
            return val
    # 匹配签名块中的公司后缀
    m = re.search(r"([A-Z][A-Za-z0-9 &'-]+(?:Inc|Ltd|GmbH|LLC|Co\.|Corp|S\.A\.|B\.V\.|AG|Pty|PLC))",
                  enquiry)
    if m:
        return m.group(1).strip()[:100]
    return ""


# ── 网页抓取 ──────────────────────────────────────────────────────────────────

def fetch_webpage(url: str) -> str | None:
    """抓取网页，提取文本内容"""
    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15,
                            allow_redirects=True, proxies=_get_proxies())
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct and "application/xhtml" not in ct:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        # 移除 script/style
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # 清理多余空行
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines)[:6000]
    except requests.RequestException as e:
        log.warning(f"网页抓取失败 {url}: {e}")
        return None


def _fetch_site_pages(base_url: str) -> str:
    """抓取官网多个页面（首页 + /about + /products）"""
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    base = base_url.rstrip("/")

    pages = [
        ("homepage", base),
        ("about", f"{base}/about"),
        ("products", f"{base}/products"),
    ]

    contents = []
    for label, url in pages:
        text = fetch_webpage(url)
        if text:
            contents.append(f"== {label} ({url}) ==\n{text}")

    return "\n\n".join(contents)


# ── 搜索 ──────────────────────────────────────────────────────────────────────

def _country_en(country: str) -> str:
    """中文国家名转英文（搜素 query 用）"""
    if not country:
        return ""
    en = COUNTRY_EN.get(country.strip(), "")
    return en


def _ddgs_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo 搜索，支持代理"""
    try:
        from duckduckgo_search import DDGS
        proxies = _get_proxies()
        proxy = (proxies or {}).get("https", "") or ""
        with DDGS(proxy=proxy if proxy else None) as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except ImportError:
        log.warning("duckduckgo-search 未安装，跳过搜索")
        return []
    except Exception as e:
        log.warning(f"DuckDuckGo 搜索失败: {e}")
        return []


def search_company_info(company_name: str, country: str = "") -> str:
    """用 DuckDuckGo 搜索公司信息，多轮 query"""
    country_en = _country_en(country)

    queries = [f'"{company_name}" company profile']
    if country_en:
        queries.append(f'"{company_name}" {country_en}')

    for q in queries:
        log.info(f"搜索 query: {q}")
        results = _ddgs_search(q, max_results=5)
        if results:
            snippets = []
            for r in results:
                title = r.get("title", "")
                body = r.get("body", "")
                href = r.get("href", "")
                if title or body:
                    snippets.append(f"{title}\n{body}\nURL: {href}")
            if snippets:
                return "\n\n".join(snippets)[:4000]

    return ""


def search_company_website(company_name: str, country: str = "") -> str | None:
    """搜索公司官网 URL"""
    country_en = _country_en(country)
    query = f'"{company_name}" official website'
    if country_en:
        query += f" {country_en}"

    results = _ddgs_search(query, max_results=3)
    for r in results:
        href = r.get("href", "")
        if href and href.startswith("http"):
            return href
    return None


# ── 调研主流程 ────────────────────────────────────────────────────────────────

def research_company(identity: str, country: str = "", enquiry: str = "") -> dict:
    """调研主流程：域名直达 → 官网深挖 → 补充验证

    返回 {"web_content": str, "source": str}
    """
    # 判断 identity 是域名还是公司名
    is_domain = "." in identity and " " not in identity and not identity.startswith("http")

    if is_domain:
        # 3A: 域名直达（裸域失败则试 www 变体，救"裸域无 A 记录、www 有"的情况）
        candidates = [identity]
        if not identity.startswith("www."):
            candidates.append("www." + identity)
        for dom in candidates:
            log.info(f"域名直达: {dom}")
            web_content = _fetch_site_pages(dom)
            if web_content:
                return {"web_content": web_content, "source": "Website"}

        # 抓取失败：域名根本无 DNS 记录 → 标记 dns_fail（邮箱大概率无效）
        if not _domain_resolves(identity):
            log.warning(f"域名无法解析（无 DNS 记录）: {identity}")
            return {"web_content": "", "source": "Search Only", "dns_fail": True}

        # 域名能解析但站点抓不到 → 降级搜索
        log.info(f"域名直达失败，降级搜索: {identity}")
        company_hint = _domain_to_company_name(identity)
        search_results = search_company_info(company_hint, country)
        if search_results:
            return {"web_content": search_results, "source": "Search Only"}

        return {"web_content": "", "source": "Search Only"}

    # 公司名 → 先搜官网
    log.info(f"搜索公司官网: {identity}")
    website_url = search_company_website(identity, country)
    if website_url:
        web_content = _fetch_site_pages(website_url)
        if web_content:
            search_snippet = search_company_info(identity, country)
            if search_snippet:
                web_content += f"\n\n== Supplementary search ==\n{search_snippet}"
                return {"web_content": web_content, "source": "Search+Website"}
            return {"web_content": web_content, "source": "Website"}

    # 搜不到官网，纯搜索
    search_results = search_company_info(identity, country)
    if search_results:
        return {"web_content": search_results, "source": "Search Only"}

    return {"web_content": "", "source": "Search Only"}


# ── AI 分析分级 ───────────────────────────────────────────────────────────────

def analyze_company(web_content: str, company_name: str = "",
                    domain: str = "", country: str = "",
                    enquiry: str = "") -> dict | None:
    """调用智谱 GLM-4 分析公司信息，返回解析后的结构化结果"""
    if not ZHIPU_API_KEY:
        log.error("ZHIPU_API_KEY 未设置")
        return None

    user_msg = build_user_message(web_content, company_name, domain, country, enquiry)

    content, stop_reason = call_zhipu(
        SYSTEM_PROMPT, user_msg, model=ZHIPU_MODEL, max_tokens=2048
    )

    if not content and stop_reason == "max_tokens":
        log.warning("LLM token 耗尽 (stop_reason=max_tokens)，max_tokens 不够")
        return None

    if not content:
        log.warning("LLM 返回空, stop_reason=%s", stop_reason)
        return None

    log.info("LLM 输出 %d 字符, stop_reason=%s", len(content), stop_reason)
    return _parse_analysis(content)


def _parse_analysis(raw: str) -> dict | None:
    """解析 LLM 输出的 11 字段结构化文本"""
    # 提取 [Field] value 格式
    fields = {}
    for m in re.finditer(r"\[([^\]]+)\]\s*(.+)", raw):
        key = m.group(1).strip()
        val = m.group(2).strip()
        fields[key] = val

    if not fields:
        log.warning(f"无法解析 LLM 输出: {raw[:200]}")
        return None

    grade = fields.get("Customer Grade", "")
    if grade not in ("A-Key Account", "B-Standard", "C-Low Priority", "D-Manual Review"):
        # 尝试模糊匹配
        for valid in ("A-Key Account", "B-Standard", "C-Low Priority", "D-Manual Review"):
            if grade and valid.lower().startswith(grade.lower()[:3]):
                grade = valid
                break

    return {
        "research_text": raw.strip(),
        "grade": grade or "D-Manual Review",
        "fields": fields,
    }


# ── 写入飞书 ──────────────────────────────────────────────────────────────────

def write_research_result(token: str, record_id: str, analysis: dict,
                          clue_level: str = "", dry_run: bool = False) -> bool:
    """写入调研结果到飞书（Company Research + Customer Grade + Follow-up Priority）"""
    grade = analysis.get("grade", "D-Manual Review")
    research_text = analysis.get("research_text", "")
    priority = get_followup_priority(clue_level, grade)

    fields = {
        "Company Research": research_text,
        "Customer Grade": grade,
        "Follow-up Priority（跟进优先级）": priority,
    }

    if dry_run:
        log.info(f"[DRY-RUN] 记录 {record_id}: grade={grade}, priority={priority}")
        log.info(f"[DRY-RUN] research:\n{research_text[:200]}...")
        return True

    api = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
           f"/tables/{FEISHU_TABLE_ID}/records/{record_id}")

    try:
        resp = feishu_api("PUT", api, token, json={"fields": fields}, timeout=15)
        resp.raise_for_status()
        resp_json = resp.json()
        if resp_json.get("code") != 0:
            log.error(f"写入飞书失败 {record_id}: code={resp_json.get('code')} msg={resp_json.get('msg')}")
            return False
        log.info(f"写入成功: {record_id} grade={grade} priority={priority}")
        return True
    except Exception as e:
        log.error(f"写入飞书失败 {record_id}: {e}")
        return False


def write_c_grade(token: str, record_id: str, dry_run: bool = False) -> bool:
    """公共邮箱无公司信息 → 写入 C 级"""
    research_text = (
        "[Company Name] Unknown\n"
        "[Website] Unknown\n"
        "[Industry] Unknown\n"
        "[Company Size] Unknown\n"
        "[B2B Type] Unknown\n"
        "[B2B Relevance] Low\n"
        "[Customer Grade] C-Low Priority\n"
        "[Core Business] Unknown\n"
        "[Research Summary] Public email used, unable to identify company background. Grade: C-Low Priority\n"
        "[Source] Enquiry Content\n"
        "[Confidence] Low (Insufficient)"
    )
    return write_research_result(token, record_id,
                                 {"research_text": research_text, "grade": "C-Low Priority"},
                                 dry_run=dry_run)


# ── 去重 ──────────────────────────────────────────────────────────────────────

def deduplicate_by_email(records: list[dict]) -> list[tuple[dict, list[str]]]:
    """按邮箱域名去重，返回 [(representative_record, [record_ids])]"""
    groups = {}
    for r in records:
        email = r.get("email", "").strip()
        domain = email.split("@")[-1].lower() if "@" in email else email
        key = domain if domain not in PUBLIC_EMAIL_DOMAINS else f"_public_{r['record_id']}"
        if key not in groups:
            groups[key] = (r, [r["record_id"]])
        else:
            groups[key][1].append(r["record_id"])
    return list(groups.values())
