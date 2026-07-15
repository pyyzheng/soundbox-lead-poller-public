#!/usr/bin/env bash
# 验证并部署 GitHub dispatch PAT（Apps Script + Cloudflare Worker）
#
# 在 .secrets.local 配置：
#   GITHUB_TOKEN_APPS_SCRIPT=...      # gmail-trigger → workflow_dispatch
#   GITHUB_TOKEN_CLOUDFLARE=...       # webhook Worker → repository_dispatch
#   CLOUDFLARE_API_TOKEN=...          # 可选，用于 wrangler secret put
#
# 用法：
#   ./scripts/configure-dispatch-tokens.sh --test-only
#   ./scripts/configure-dispatch-tokens.sh --cloudflare
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS_FILE="${SECRETS_FILE:-$ROOT/.secrets.local}"
PUBLIC_REPO="${PUBLIC_REPO:-pyyzheng/soundbox-lead-poller-public}"
WEBHOOK_DIR="$ROOT/facebook-lead-webhook"

die() { echo "错误: $*" >&2; exit 1; }

get_secret() {
  local key="$1"
  local line val
  line=$(grep -E "^[[:space:]]*${key}=" "$SECRETS_FILE" | tail -1 || true)
  [[ -n "$line" ]] || return 1
  val="${line#*=}"
  val="${val#\'}"; val="${val%\'}"
  val="${val#\"}"; val="${val%\"}"
  [[ -n "$val" ]] || return 1
  printf '%s' "$val"
}

test_apps_token() {
  local token="$1"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: token ${token}" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: gmail-trigger-test" \
    "https://api.github.com/repos/${PUBLIC_REPO}/actions/workflows/lead-poller.yml/dispatches" \
    -d '{"ref":"main"}')
  [[ "$code" == "204" ]]
}

test_cf_token() {
  local token="$1"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: token ${token}" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: cloudflare-worker-dispatch-test" \
    "https://api.github.com/repos/${PUBLIC_REPO}/dispatches" \
    -d '{"event_type":"company-research","client_payload":{"record_id":"configure-dispatch-tokens-test"}}')
  [[ "$code" == "204" ]]
}

cmd_test_only=false
cmd_cloudflare=false
for arg in "$@"; do
  case "$arg" in
    --test-only) cmd_test_only=true ;;
    --cloudflare) cmd_cloudflare=true ;;
    *) die "未知参数: $arg" ;;
  esac
done
$cmd_test_only || $cmd_cloudflare || cmd_test_only=true

[[ -f "$SECRETS_FILE" ]] || die "找不到 $SECRETS_FILE"

APPS_TOKEN=$(get_secret GITHUB_TOKEN_APPS_SCRIPT || true)
CF_TOKEN=$(get_secret GITHUB_TOKEN_CLOUDFLARE || true)

echo "目标仓库: $PUBLIC_REPO"
echo ""

if [[ -n "$APPS_TOKEN" ]]; then
  if test_apps_token "$APPS_TOKEN"; then
    echo "✓ GITHUB_TOKEN_APPS_SCRIPT → workflow_dispatch (HTTP 204)"
  else
    echo "✗ GITHUB_TOKEN_APPS_SCRIPT 测试失败（需 Actions: Read and write）" >&2
    exit 1
  fi
else
  echo "⊘ 未配置 GITHUB_TOKEN_APPS_SCRIPT（跳过 Apps Script 测试）"
fi

if [[ -n "$CF_TOKEN" ]]; then
  if test_cf_token "$CF_TOKEN"; then
    echo "✓ GITHUB_TOKEN_CLOUDFLARE → repository_dispatch (HTTP 204)"
  else
    echo "✗ GITHUB_TOKEN_CLOUDFLARE 测试失败（需 Actions + Contents: Read and write）" >&2
    exit 1
  fi
else
  echo "⊘ 未配置 GITHUB_TOKEN_CLOUDFLARE（跳过 Cloudflare 测试）"
fi

if $cmd_test_only && ! $cmd_cloudflare; then
  echo ""
  echo "Apps Script：请在 script.google.com → 项目设置 → 脚本属性"
  echo "  GITHUB_TOKEN = <GITHUB_TOKEN_APPS_SCRIPT 的值>"
  echo "  并确认 REPO = '${PUBLIC_REPO}'"
  exit 0
fi

if $cmd_cloudflare; then
  [[ -n "$CF_TOKEN" ]] || die "需要 GITHUB_TOKEN_CLOUDFLARE"
  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    CLOUDFLARE_API_TOKEN=$(get_secret CLOUDFLARE_API_TOKEN || true)
    export CLOUDFLARE_API_TOKEN
  fi
  [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]] || die "需要 CLOUDFLARE_API_TOKEN（.secrets.local 或环境变量）以更新 Worker Secret"
  echo ""
  echo "更新 Cloudflare Worker Secret: GITHUB_TOKEN ..."
  printf '%s' "$CF_TOKEN" | (cd "$WEBHOOK_DIR" && npx wrangler secret put GITHUB_TOKEN)
  echo "✓ Cloudflare GITHUB_TOKEN 已更新"
  echo "  确认 Dashboard 变量 GITHUB_REPO=${PUBLIC_REPO}"
fi
