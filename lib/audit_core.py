#!/usr/bin/env python3
"""audit_core.py — 调研质量抽检核心逻辑

供 cloud-weekly-report（周报区块）和 cloud-research-audit（独立抽检入口）复用。
从飞书线索表抽样已调研记录，做格式检查 + LLM 交叉验证。

合并自原 cloud-research-audit.py 的核心函数，抽离为可 import 模块
（原文件名含连字符无法被 import）。
"""
import datetime
import json
import random
import re

import requests

from feishu_utils import (
    FEISHU_APP_TOKEN, FEISHU_TABLE_ID, FIELD_ENTRY_TIME, extract_text,
)
from zhipu_client import call_zhipu

TZ_SH = datetime.timezone(datetime.timedelta(hours=8))

FIELD_EMAIL = "Email（客户邮箱）"
FIELD_NAME = "Customer Name（客户名称）"
FIELD_COUNTRY = "Country（国家）"

REQUIRED_FIELDS = [
    "Company Name", "Website", "Industry", "Company Size",
    "B2B Type", "B2B Relevance", "Customer Grade", "Core Business",
    "Research Summary", "Source", "Confidence",
]

VALID_GRADES = {"A-Key Account", "B-Standard", "C-Low Priority", "D-Manual Review"}

AUDIT_PROMPT = """You are a quality auditor for B2B customer research reports.

Given a research result, verify its internal consistency:
1. Is the Customer Grade justified by the evidence described in the report?
2. Are Company Size and B2B Type internally consistent with the description?
3. Are there logical contradictions (e.g. "Unknown" size but "500+ employees" mentioned in summary)?

Output JSON only:
{
  "grade_match": true/false,
  "suggested_grade": "A-Key Account" | "B-Standard" | "C-Low Priority" | "D-Manual Review",
  "size_accurate": true/false,
  "b2b_type_accurate": true/false,
  "issues": ["issue1", "issue2"],
  "notes": "brief explanation"
}"""


def fetch_researched_records(token, days=7):
    """拉取最近 N 天有 Company Research 值的记录。"""
    now = datetime.datetime.now(TZ_SH)
    cutoff = now - datetime.timedelta(days=days)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    all_items = []
    page_token = ""
    base_url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_TABLE_ID}/records"
    )

    while True:
        url = f"{base_url}?page_size=500"
        if page_token:
            url += f"&page_token={page_token}"

        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        has_more = data.get("data", {}).get("has_more", False)
        page_token = data.get("data", {}).get("page_token", "")

        for item in items:
            f = item.get("fields", {})
            entry = f.get(FIELD_ENTRY_TIME, 0)
            if isinstance(entry, list):
                entry = entry[0] if entry else 0
            if entry < cutoff_ms:
                has_more = False
                break

            cr = f.get("Company Research", "")
            if not cr or (isinstance(cr, list) and len(cr) == 0):
                continue

            all_items.append({
                "record_id": item["record_id"],
                "email": extract_text(f.get(FIELD_EMAIL, "")),
                "name": extract_text(f.get(FIELD_NAME, "")),
                "country": extract_text(f.get(FIELD_COUNTRY, "")),
                "research_text": extract_text(cr),
                "grade": extract_text(f.get("Customer Grade", "")),
            })

        if not has_more or not items:
            break

    return all_items


def stratified_sample(records, sample_size):
    """按 grade 分层抽样，A/B 多抽一些。"""
    by_grade = {}
    for r in records:
        g = r.get("grade", "Unknown")
        by_grade.setdefault(g, []).append(r)

    weights = {"A-Key Account": 3, "B-Standard": 3, "C-Low Priority": 2, "D-Manual Review": 2}
    total_weight = sum(weights.get(g, 1) for g in by_grade)

    sampled = []
    remaining = sample_size
    for g, recs in by_grade.items():
        if not recs or remaining <= 0:
            continue
        w = weights.get(g, 1)
        quota = max(1, round(sample_size * w / total_weight))
        quota = min(quota, len(recs), remaining)
        sampled.extend(random.sample(recs, quota))
        remaining -= quota

    if remaining > 0:
        sampled_ids = {r["record_id"] for r in sampled}
        pool = [r for r in records if r["record_id"] not in sampled_ids]
        if pool:
            extra = min(remaining, len(pool))
            sampled.extend(random.sample(pool, extra))

    return sampled


def check_format(research_text):
    """检查 research_text 格式是否合规。"""
    issues = []
    if not research_text:
        issues.append("research_text 为空")
        return issues

    fields = {}
    for m in re.finditer(r"\[([^\]]+)\]\s*(.+)", research_text):
        fields[m.group(1).strip()] = m.group(2).strip()

    for f in REQUIRED_FIELDS:
        if f not in fields:
            issues.append(f"缺少字段 [{f}]")

    grade = fields.get("Customer Grade", "")
    if grade and grade not in VALID_GRADES:
        issues.append(f"无效 grade: {grade}")

    summary = fields.get("Research Summary", "")
    if summary and "Grade:" not in summary:
        issues.append("Research Summary 缺少 'Grade:' 标记")

    if re.search(r"[一-鿿]", research_text):
        issues.append("包含中文字符")

    return issues


def audit_single(research_text, grade, zhipu_key, model="glm-4.5-air"):
    """用 LLM 交叉验证单条调研结果。无 key/失败返回降级结果。"""
    if not zhipu_key:
        return {"grade_match": None, "issues": ["ZHIPU_API_KEY 未设置"]}

    user_msg = (
        f"## Original Research (Grade: {grade})\n\n{research_text}\n\n"
        f"## Task\nVerify the above research. No new web search needed — "
        f"just assess internal consistency and whether the grade is justified by the stated evidence."
    )

    content, _stop = call_zhipu(AUDIT_PROMPT, user_msg, model=model, max_tokens=512)
    if not content:
        return None

    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return {"notes": content[:200]}
    return {"notes": content[:200]}


def run_audit(token, days=7, sample=10, zhipu_key="", zhipu_model="glm-4.5-air"):
    """跑完整抽检，返回 {population, total, error_count, results}。

    population = 总池；total = 抽样数；error_count = 异常数；results = 逐条详情。
    """
    records = fetch_researched_records(token, days)
    population = len(records)

    if not records:
        return {"population": 0, "total": 0, "error_count": 0, "results": []}

    sample_size = max(3, min(sample, len(records)))
    sampled = stratified_sample(records, sample_size)

    results = []
    error_count = 0
    for r in sampled:
        fmt_issues = check_format(r["research_text"])
        audit = audit_single(r["research_text"], r["grade"], zhipu_key, zhipu_model)
        has_error = (audit and not audit.get("grade_match", True)) or bool(fmt_issues)
        if has_error:
            error_count += 1
        results.append({
            "record_id": r["record_id"],
            "email": r["email"],
            "name": r["name"],
            "original_grade": r["grade"],
            "format_issues": fmt_issues,
            "audit": audit or {},
        })

    return {"population": population, "total": len(sampled), "error_count": error_count, "results": results}
