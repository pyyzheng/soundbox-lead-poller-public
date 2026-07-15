#!/usr/bin/env bash
# 加载 .env 并在本地运行云端脚本（默认 DRY_RUN=true，不写飞书、不打标签）
#
# 用法：
#   ./scripts/run-local.sh                          # 默认跑 cloud-lead-poller.py
#   ./scripts/run-local.sh cloud-health-check       # 跑其他脚本
#   DRY_RUN=false ./scripts/run-local.sh cloud-lead-poller  # 真实写入（慎用）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

die() { echo "错误: $*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || die "找不到 $ENV_FILE\n请先运行: ./scripts/setup-secrets.sh --local-env"

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

export DRY_RUN="${DRY_RUN:-true}"

SCRIPT="${1:-cloud-lead-poller}"
shift || true

case "$SCRIPT" in
  cloud-lead-poller)        PY="cloud-lead-poller.py" ;;
  cloud-health-check)       PY="cloud-health-check.py" ;;
  cloud-daily-report)       PY="cloud-daily-report.py" ;;
  cloud-check-unassigned)   PY="cloud-check-unassigned.py" ;;
  cloud-auto-reply-worker)  PY="cloud-auto-reply-worker.py" ;;
  cloud-reply-forwarder)    PY="cloud-reply-forwarder.py" ;;
  cloud-company-research)   PY="cloud-company-research.py" ;;
  *)                        PY="${SCRIPT}.py" ;;
esac

[[ -f "$ROOT/$PY" ]] || die "脚本不存在: $ROOT/$PY"

cd "$ROOT"
echo "▶ 运行: $PY"
echo "▶ DRY_RUN=$DRY_RUN"
echo "▶ Python: $(python3 --version 2>/dev/null || echo '未安装')"
echo "---"

python3 "$PY" "$@"
