#!/usr/bin/env bash
# 部署 facebook-lead-webhook 到 Cloudflare（HTTP Worker + 自定义域名）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBHOOK_DIR="$ROOT/facebook-lead-webhook"
SECRETS_FILE="${SECRETS_FILE:-$ROOT/.secrets.local}"

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]] && [[ -f "$SECRETS_FILE" ]]; then
  line=$(grep -E '^[[:space:]]*CLOUDFLARE_API_TOKEN=' "$SECRETS_FILE" | tail -1 || true)
  if [[ -n "$line" ]]; then
    CLOUDFLARE_API_TOKEN="${line#*=}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN#\'}"; CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN%\'}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN#\"}"; CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN%\"}"
    export CLOUDFLARE_API_TOKEN
  fi
fi

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "错误: 请设置 CLOUDFLARE_API_TOKEN 或写入 .secrets.local" >&2
  echo "  export CLOUDFLARE_API_TOKEN=..." >&2
  exit 1
fi

cd "$WEBHOOK_DIR"
echo "部署 facebook-lead-webhook → webhook.soundboxbooth.com ..."
npx wrangler deploy

echo ""
echo "验证:"
echo "  curl -s https://webhook.soundboxbooth.com/"
echo "  curl -s \"https://webhook.soundboxbooth.com/webhook?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test\""
