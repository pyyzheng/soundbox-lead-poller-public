#!/usr/bin/env python3
"""cloud-research-audit.py — 公司调研质量抽检（独立运行入口）

核心逻辑在 lib/audit_core.py（fetch/抽样/格式检查/LLM 验证）。
本脚本只负责 CLI 入参 + 飞书告警 + GitHub Step Summary。

注：飞书告警已收敛进周报（cloud-weekly-report 调用 run_audit 作为区块），
本 workflow 已 disable，仅保留作手动 dry-run 调试入口。
"""
import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from feishu_utils import get_feishu_token, send_alert_webhook
from audit_core import run_audit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("research-audit")


def _write_summary(result, args):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## Research Quality Audit\n\n")
        f.write("| 指标 | 值 |\n|---|---|\n")
        f.write(f"| 抽查范围 | 最近 {args.days} 天 |\n")
        f.write(f"| 总池 | {result['population']} |\n")
        f.write(f"| 抽样数量 | {result['total']} |\n")
        f.write(f"| 异常记录 | {result['error_count']} |\n\n")

        if result["results"]:
            f.write("| Record | Grade | 格式 | LLM验证 | 问题 |\n|---|---|---|---|---|\n")
            for r in result["results"]:
                audit = r.get("audit", {})
                grade_match = audit.get("grade_match", "?")
                fmt = "OK" if not r["format_issues"] else "; ".join(r["format_issues"][:2])
                notes = audit.get("notes", "")[:60] if audit else "N/A"
                suggested = audit.get("suggested_grade", "")
                if suggested and suggested != r["original_grade"]:
                    notes = f"建议: {suggested}"
                f.write(f"| {r['record_id']} | {r['original_grade']} | {fmt} | {grade_match} | {notes} |\n")


def main():
    parser = argparse.ArgumentParser(description="Research Quality Audit")
    parser.add_argument("--days", type=int, default=int(os.environ.get("AUDIT_DAYS", "7")),
                        help="抽查最近 N 天的记录")
    parser.add_argument("--sample", type=int, default=int(os.environ.get("SAMPLE_SIZE", "10")),
                        help="抽样数量")
    parser.add_argument("--dry-run", action="store_true",
                        default=os.environ.get("DRY_RUN", "false") == "true")
    args = parser.parse_args()

    log.info("=== Research Quality Audit 启动 (days=%d, sample=%d, dry_run=%s) ===",
             args.days, args.sample, args.dry_run)

    token = get_feishu_token()
    result = run_audit(
        token, days=args.days, sample=args.sample,
        zhipu_key=os.environ.get("ZHIPU_API_KEY", ""),
        zhipu_model=os.environ.get("ZHIPU_MODEL", "glm-4.5-air"),
    )

    log.info("=== 抽检完成: 总池 %d 条, 抽样 %d 条, %d 条异常 ===",
             result["population"], result["total"], result["error_count"])

    if result["error_count"] > 0 and not args.dry_run:
        alert_lines = [f"[调研质量抽检] {result['error_count']}/{result['total']} 条异常"]
        for r in result["results"]:
            if r["format_issues"] or (r["audit"] and not r["audit"].get("grade_match", True)):
                alert_lines.append(
                    f"- {r['record_id']} ({r['original_grade']}): "
                    f"fmt={r['format_issues'] or 'OK'}, "
                    f"audit={r['audit'].get('notes', '')[:80]}"
                )
        send_alert_webhook("\n".join(alert_lines))

    _write_summary(result, args)


if __name__ == "__main__":
    main()
