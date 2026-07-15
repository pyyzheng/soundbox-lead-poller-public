#!/usr/bin/env python3
"""
cloud-company-research.py — B2B 客户背景调研云端处理器

在 soundbox-lead-poller 仓库中运行，通过 GitHub Actions 定时触发。
查询飞书中未调研的询盘记录，自动进行公司调研和客户分级。

环境变量：
  FEISHU_APP_ID        — 飞书应用 ID（已在仓库 secrets 中配置）
  FEISHU_APP_SECRET    — 飞书应用密钥
  ZHIPU_API_KEY        — 智谱 GLM API Key
  FEISHU_ALERT_WEBHOOK — 飞书告警 webhook
  ZHIPU_MODEL          — 智谱模型（默认 glm-4）
  DATE_RANGE           — 日期范围 today/yesterday/YYYY-MM-DD（默认 today）
  RECORD_ID            — 单条记录 ID（可选，指定后只处理该记录）
  DRY_RUN              — true 时只分析不写入飞书
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from feishu_utils import get_feishu_token, send_alert_webhook
from company_research import (
    analyze_company,
    deduplicate_by_email,
    get_single_record,
    query_unresearched_records,
    research_company,
    should_research,
    write_c_grade,
    write_research_result,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("company-research")


def process_single(token: str, record: dict, dry_run: bool) -> dict:
    """处理单条记录，返回结果状态"""
    rid = record["record_id"]
    email = record.get("email", "")
    clue_level = record.get("clue_level", "")

    # 判断是否需要调研
    need_research, identity = should_research(record)
    if not need_research:
        # 公共邮箱无公司信息 → C 级
        if write_c_grade(token, rid, dry_run=dry_run):
            return {"id": rid, "status": "c_grade", "email": email}
        return {"id": rid, "status": "error", "email": email, "error": "write C grade failed"}

    # 调研
    log.info(f"开始调研: {identity} (email={email})")
    research_result = research_company(identity, record.get("country", ""), record.get("enquiry", ""))

    # AI 分析
    web_content = research_result.get("web_content", "")
    dns_fail = research_result.get("dns_fail", False)
    if not web_content:
        log.warning(f"无调研内容: {identity}" + ("（域名无法解析，邮箱可能无效）" if dns_fail else ""))
        # 域名无 DNS 记录 → 明确提示销售改用电话/社交媒体；否则泛指信息不足
        if dns_fail:
            summary = "Domain does not resolve (no DNS record) — the contact email is likely undeliverable. Verify the buyer via phone or social media before follow-up. Grade: D"
        else:
            summary = "No information found from web or search. Grade: D-Manual Review"
        analysis = {
            "research_text": (
                "[Company Name] Unknown\n[Website] Unknown\n[Industry] Unknown\n"
                "[Company Size] Unknown\n[B2B Type] Unknown\n[B2B Relevance] Unknown\n"
                "[Customer Grade] D-Manual Review\n[Core Business] Unknown\n"
                f"[Research Summary] {summary}\n"
                "[Source] Search Only\n[Confidence] Low (Insufficient)"
            ),
            "grade": "D-Manual Review",
        }
    else:
        # 提取域名或公司名
        domain = identity if "." in identity and " " not in identity else ""
        company_name = identity if not domain else ""
        analysis = analyze_company(web_content, company_name, domain,
                                   record.get("country", ""), record.get("enquiry", ""))

    if not analysis:
        return {"id": rid, "status": "error", "email": email, "error": "LLM analysis failed"}

    # 写入飞书
    if write_research_result(token, rid, analysis, clue_level, dry_run=dry_run):
        grade = analysis.get("grade", "?")
        return {"id": rid, "status": "ok", "email": email, "grade": grade,
                "identity": identity, "analysis": analysis}
    return {"id": rid, "status": "error", "email": email, "error": "write failed"}


def main():
    parser = argparse.ArgumentParser(description="Company Research Cloud Worker")
    parser.add_argument("--date", default=os.environ.get("DATE_RANGE", "today"),
                        help="Date range: today/yesterday/YYYY-MM-DD")
    parser.add_argument("--record-id", default=os.environ.get("RECORD_ID", ""),
                        help="Single record ID to process")
    parser.add_argument("--dry-run", action="store_true",
                        default=os.environ.get("DRY_RUN", "false") == "true")
    args = parser.parse_args()

    log.info("=== Company Research 启动 (dry_run=%s) ===", args.dry_run)

    # 飞书 token
    try:
        token = get_feishu_token()
    except Exception as e:
        err_msg = f"[Company Research 告警] 飞书认证失败: {e}"
        log.error(err_msg)
        send_alert_webhook(err_msg)
        sys.exit(1)

    # 获取待处理记录
    if args.record_id:
        record = get_single_record(token, args.record_id)
        if not record:
            log.error(f"记录不存在: {args.record_id}")
            sys.exit(1)
        records = [record]
    else:
        records = query_unresearched_records(token, args.date)

    if not records:
        log.info("无待调研记录")
        _write_summary([], 0, 0, 0, 0, args.dry_run)
        return

    log.info(f"待处理记录: {len(records)} 条")

    # 去重
    groups = deduplicate_by_email(records)
    log.info(f"去重后: {len(groups)} 个独立调研任务")

    # 逐条处理
    results = []
    for i, (rep_record, record_ids) in enumerate(groups):
        try:
            result = process_single(token, rep_record, args.dry_run)
            # 同一公司的结果写入所有匹配记录
            if result["status"] in ("ok", "c_grade") and len(record_ids) > 1:
                for extra_rid in record_ids[1:]:
                    if result["status"] == "c_grade":
                        write_c_grade(token, extra_rid, dry_run=args.dry_run)
                    else:
                        write_research_result(token, extra_rid, result["analysis"],
                                             rep_record.get("clue_level", ""),
                                             dry_run=args.dry_run)
            results.append(result)
        except Exception as e:
            log.error(f"处理异常 {rep_record['record_id']}: {e}", exc_info=True)
            results.append({"id": rep_record["record_id"], "status": "exception",
                           "email": rep_record.get("email", ""), "error": str(e)})

        # 处理间节流，避免触发飞书 429
        if i < len(groups) - 1:
            time.sleep(1)

    # 汇总
    ok = sum(1 for r in results if r["status"] == "ok")
    c_grade = sum(1 for r in results if r["status"] == "c_grade")
    errors = sum(1 for r in results if r["status"] in ("error", "exception"))

    # 分级分布
    grade_dist = {}
    for r in results:
        if r.get("grade"):
            grade_dist[r["grade"]] = grade_dist.get(r["grade"], 0) + 1

    log.info(f"=== 完成: 调研={ok} C级={c_grade} 错误={errors} ===")
    log.info(f"分级分布: {grade_dist}")
    _write_summary(results, ok, c_grade, errors, len(records), args.dry_run)

    if errors > 0:
        log.warning(f"有 {errors} 条处理失败")
        sys.exit(1)


def _write_summary(results, ok, c_grade, errors, total, dry_run):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        mode = " (DRY-RUN)" if dry_run else ""
        f.write(f"## Company Research Summary{mode}\n\n")
        f.write(f"| 指标 | 数量 |\n|---|---|\n")
        f.write(f"| 扫描记录 | {total} |\n")
        f.write(f"| 调研完成 | {ok} |\n")
        f.write(f"| C级（个人买家） | {c_grade} |\n")
        f.write(f"| 错误 | {errors} |\n\n")
        if results:
            f.write("### 处理明细\n\n| Record ID | 状态 | Grade | Email |\n|---|---|---|---|\n")
            for r in results:
                f.write(f"| {r['id']} | {r['status']} | {r.get('grade', '-')} | {r.get('email', '')[:40]} |\n")


if __name__ == "__main__":
    main()
