"""format_fix_strategy — 格式异常修复策略：直接修正飞书字段。

处理 format_anomaly 类型异常：
- 字段不一致：用标签行正确值覆盖飞书字段
- 关键字段为空：记录 record_id，由 needs_review 通知人工
"""
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from agents.types import Anomaly, FixResult

log = logging.getLogger("strategy:format-fix")

# 中文标识符 → 飞书字段名映射（对应 cloud-format-audit.py 行 139-146）
FIELD_MAP = {
    "国家": "Country（国家）",
    "渠道": "Channel segmentation (细分渠道)",
    "产品": "Product Categories（产品大类）",
    "型号": "Product model（具体型号）",
}


def _get_feishu_token() -> str:
    from feishu_utils import get_feishu_token
    return get_feishu_token()


def _fix_field_mismatch(evidence: dict, token: str) -> bool:
    """修复字段不一致：解析 detail，用标签行值更新飞书字段。"""
    record_id = evidence.get("id", "")
    if not record_id or record_id == "?":
        log.warning("[format-fix] 无 record_id，跳过字段不一致修复")
        return False

    detail = evidence.get("detail", "")
    if "标签=" not in detail or "字段=" not in detail:
        log.warning("[format-fix] 无法解析 detail: %s", detail)
        return False

    # detail 格式："国家: 标签=美国 vs 字段=UK; 渠道: 标签=Google Ads vs 字段=Organic"
    updates = {}
    for segment in detail.split("; "):
        if "标签=" not in segment or " vs " not in segment:
            continue
        field_cn = segment.split(":")[0].strip()
        label_val = segment.split("标签=")[1].split(" vs ")[0].strip()
        feishu_field = FIELD_MAP.get(field_cn)
        if feishu_field:
            updates[feishu_field] = label_val
        else:
            log.warning("[format-fix] 未知字段映射: %s", field_cn)

    if not updates:
        log.warning("[format-fix] 无可更新字段: %s", detail)
        return False

    # 调飞书 PUT API 更新
    from feishu_utils import feishu_api, FEISHU_APP_TOKEN, FEISHU_TABLE_ID
    url = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
           f"/tables/{FEISHU_TABLE_ID}/records/{record_id}")
    try:
        resp = feishu_api("PUT", url, token, json={"fields": updates})
        data = resp.json()
        if data.get("code") != 0:
            log.error("[format-fix] 飞书更新失败 %s: %s", record_id, data.get("msg", ""))
            return False
        log.info("[format-fix] 字段更新成功 %s: %s", record_id, list(updates.keys()))
        return True
    except Exception as e:
        log.error("[format-fix] 飞书 API 异常: %s", e)
        return False


def _fix_empty_field(evidence: dict, token: str) -> bool:
    """关键字段为空：记录诊断信息，由 needs_review 通知人工。"""
    record_id = evidence.get("id", "?")
    detail = evidence.get("detail", "")

    if "空字段:" not in detail:
        log.warning("[format-fix] 无法解析空字段 detail: %s", detail)
        return False

    empty_fields = [f.strip() for f in detail.split("空字段:")[1].split(",")]
    log.info("[format-fix] record=%s 关键字段为空: %s (需人工重新分级)", record_id, empty_fields)
    return False


async def format_fix_strategy(anomalies: list[Anomaly], context: dict) -> FixResult:
    """格式异常修复策略：分析 evidence 并修正飞书字段。"""
    log.info("[format-fix] 处理 %d 条格式异常", len(anomalies))

    try:
        token = _get_feishu_token()
    except Exception as e:
        log.error("[format-fix] 飞书 token 获取失败: %s", e)
        return FixResult(success=False, summary=f"Token 获取失败: {e}",
                         changed_files=[], confidence=0.0, needs_review=False)

    fixed = 0
    skipped = 0

    for anomaly in anomalies:
        check = anomaly.evidence.get("check", "")

        if check == "字段不一致":
            ok = _fix_field_mismatch(anomaly.evidence, token)
        elif check == "关键字段为空":
            ok = _fix_empty_field(anomaly.evidence, token)
        else:
            log.info("[format-fix] 未知检查类型: %s", check)
            ok = False

        if ok:
            fixed += 1
        else:
            skipped += 1

    summary = f"格式异常处理: {fixed} 条修复, {skipped} 条需人工"
    log.info("[format-fix] %s", summary)

    return FixResult(
        success=True,
        summary=summary,
        changed_files=[],
        confidence=0.8 if fixed > 0 else 0.5,
        needs_review=skipped > 0,
    )
