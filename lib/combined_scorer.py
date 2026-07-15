#!/usr/bin/env python3
"""
combined_scorer.py — Clue Level × Company Grade → Follow-up Priority 合并评分

从 priority_matrix.json 读取矩阵配置，将询盘内容分级 (L1-L4) 和公司背调分级 (A-D)
合并为统一的跟进优先级 (P1-P4 / Pending)。
"""

import json
import logging
import re
from pathlib import Path
import requests as _requests

log = logging.getLogger("combined-scorer")

# 矩阵文件路径（本模块同级目录的 ../priority_matrix.json）
_MATRIX_PATH = Path(__file__).resolve().parent.parent / "priority_matrix.json"

# Company Research 文本中的等级提取正则
_GRADE_RE = re.compile(
    r"\bCustomer Grade:\s*(A|B|C|D)",
    re.IGNORECASE,
)

FEISHU_FIELD = "Follow-up Priority（跟进优先级）"
FEISHU_CLUE_LEVEL = "Clue level（线索等级）"
FEISHU_COMPANY_RESEARCH = "Company Research"

# 模块级缓存矩阵
_MATRIX_CACHE: dict = {}


def _flatten_rich_text(value) -> str:
    """飞书 rich text 字段 → 纯文本。支持 str / list[dict] / None"""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


def _load_matrix() -> dict:
    """加载 priority_matrix.json（带模块级缓存）"""
    global _MATRIX_CACHE
    if _MATRIX_CACHE:
        return _MATRIX_CACHE
    try:
        with open(_MATRIX_PATH, "r", encoding="utf-8") as f:
            _MATRIX_CACHE = json.load(f)
    except Exception as e:
        log.error("无法加载矩阵配置 %s: %s", _MATRIX_PATH, e)
    return _MATRIX_CACHE


def extract_company_grade(research_text: str) -> str:
    """从 Company Research 文本提取 Customer Grade。

    Returns:
        "A" / "B" / "C" / "D"，异常或无匹配返回 "NoResearch"
    """
    if not research_text:
        return "NoResearch"
    research_text = _flatten_rich_text(research_text)
    m = _GRADE_RE.search(research_text)
    if m:
        return m.group(1).upper()
    return "NoResearch"


def calc_priority(clue_level: str, company_grade: str) -> str:
    """根据矩阵查表返回 Follow-up Priority。

    Args:
        clue_level: "L1" / "L2" / "L3" / "L4"
        company_grade: "A" / "B" / "C" / "D" / "NoResearch"

    Returns:
        "P1" / "P2" / "P3" / "P4" / "Pending"
    """
    config = _load_matrix()
    if not config:
        log.warning("矩阵配置为空，返回 Pending")
        return "Pending"

    matrix = config.get("matrix", {})
    defaults = config.get("defaults", {})

    # 异常兜底
    if clue_level not in matrix:
        log.debug("无效 clue_level=%s，返回默认值", clue_level)
        return defaults.get("invalid_clue_level", "Pending")

    if company_grade == "NoResearch":
        return defaults.get("no_research", "Pending")

    row = matrix[clue_level]
    if company_grade not in row:
        log.debug("无效 company_grade=%s，返回默认值", company_grade)
        return defaults.get("invalid_company_grade", "Pending")

    return row[company_grade]


def recalculate_priority(token: str, app_token: str, table_id: str,
                         record_ids: list, requests_mod=None) -> list:
    """批量重算 Follow-up Priority 并写入飞书。

    Args:
        token: 飞书 tenant_token
        app_token: 飞书 Bitable app_token
        table_id: 飞书 Bitable table_id
        record_ids: 待重算的记录 ID 列表
        requests_mod: requests 模块（可注入，方便测试）

    Returns:
        变更日志列表: [{"record_id": ..., "old": ..., "new": ...}, ...]
    """
    import requests as _requests_injected
    if requests_mod is None:
        requests_mod = _requests_injected

    changelog = []

    for rid in record_ids:
        try:
            # 1. 读取当前记录的三个字段
            url = (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
                f"/tables/{table_id}/records/{rid}"
            )
            resp = requests_mod.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                log.warning("读取记录 %s 失败: %s", rid, data.get("msg"))
                continue

            fields = data.get("data", {}).get("record", {}).get("fields", {})
            clue_level_raw = fields.get(FEISHU_CLUE_LEVEL, "")
            research_text = fields.get(FEISHU_COMPANY_RESEARCH, "")
            old_priority = _flatten_rich_text(fields.get(FEISHU_FIELD, ""))

            # 2. 标准化输入
            clue_level = str(clue_level_raw).strip() if clue_level_raw else ""
            if clue_level not in ("L1", "L2", "L3", "L4"):
                clue_level = ""

            company_grade = extract_company_grade(str(research_text))

            # 3. 计算新优先级
            if not clue_level:
                new_priority = "Pending"
            else:
                new_priority = calc_priority(clue_level, company_grade)

            # 4. 与旧值比较，有变化才写入
            if new_priority == old_priority:
                continue

            # 5. 写入飞书
            update_url = (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
                f"/tables/{table_id}/records/{rid}"
            )
            resp2 = requests_mod.put(
                update_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"fields": {FEISHU_FIELD: new_priority}},
                timeout=15,
            )
            result = resp2.json()
            if result.get("code") == 0:
                entry = {"record_id": rid, "old": old_priority or "(空)", "new": new_priority}
                changelog.append(entry)
                log.info("Priority 更新: %s | %s → %s", rid, entry["old"], new_priority)
            else:
                log.warning("写入 %s 失败: %s", rid, result.get("msg"))

        except (_requests.RequestException, ValueError, KeyError) as e:
            log.error("处理记录 %s 异常: %s", rid, e)

    return changelog
