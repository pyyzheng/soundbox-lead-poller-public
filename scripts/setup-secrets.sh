#!/usr/bin/env bash
# 从 .secrets.local 生成本地 .env 或同步到 GitHub Actions Secrets
#
# 用法：
#   ./scripts/setup-secrets.sh --local-env          # 生成/更新 .env（本地调试）
#   ./scripts/setup-secrets.sh --github             # 上传到当前 git remote 对应仓库
#   ./scripts/setup-secrets.sh --github owner/repo  # 上传到指定仓库
#   ./scripts/setup-secrets.sh --list-github        # 列出已配置的 GitHub Secrets 名称
set -euo pipefail

# 若 shell 设置了 GH_CONFIG_DIR 指向空目录，会找不到 gh 登录态；统一用默认配置路径
unset GH_CONFIG_DIR

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS_FILE="${SECRETS_FILE:-$ROOT/.secrets.local}"
ENV_FILE="$ROOT/.env"

# GitHub Secrets 名称列表（与 .github/workflows/*.yml 一致）
GITHUB_SECRET_KEYS=(
  FEISHU_APP_ID
  FEISHU_APP_SECRET
  FEISHU_APP_TOKEN
  FEISHU_TABLE_ID
  FEISHU_ALERT_WEBHOOK
  FEISHU_REPORT_WEBHOOK
  GMAIL_CLIENT_ID
  GMAIL_CLIENT_SECRET
  GMAIL_REFRESH_TOKEN
  ZHIPU_API_KEY
  GLM_API_KEY
  GHA_PAT
  META_PAGE_ACCESS_TOKEN
  AUTO_REPLY_WORKER_ENABLED
  AUTO_REPLY_WORKER_DRY_RUN
  AUTO_REPLY_ALLOWED_SALESPERSONS
  AUTO_REPLY_DRY_RUN
  AUTO_REPLY_SAMPLE_RATE
  SALES_EMAIL_MAP
  CRONJOB_API_KEY
  CRONJOB_JOB_ID
  CLOUDFLARE_API_TOKEN
  FEISHU_FILTER_LOG_TABLE_ID
  FEISHU_FOLLOWUP_TABLE
  FEISHU_SALES_NOTIFY_TABLE
)

die() { echo "错误: $*" >&2; exit 1; }

load_secrets() {
  [[ -f "$SECRETS_FILE" ]] || die "找不到 $SECRETS_FILE\n请先: cp .secrets.local.example .secrets.local 并填入真实值"
}

# 解析 KEY=VALUE（支持引号、忽略注释和空行）
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

resolve_repo() {
  if [[ -n "${1:-}" ]]; then
    echo "$1"
    return
  fi
  local url
  url=$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)
  [[ -n "$url" ]] || die "未配置 git remote。请先 git remote add origin git@github.com:<你>/<仓库>.git"
  if [[ "$url" =~ github\.com[:/]([^/]+)/([^/.]+) ]]; then
    echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  else
    die "无法从 remote 解析仓库: $url"
  fi
}

cmd_local_env() {
  load_secrets
  echo "# 由 scripts/setup-secrets.sh 自动生成，请勿提交" > "$ENV_FILE"
  echo "# 生成时间: $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$ENV_FILE"
  local key val count=0
  for key in "${GITHUB_SECRET_KEYS[@]}" SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS FEISHU_FILTER_LOG_TABLE_ID; do
    if val=$(get_secret "$key" 2>/dev/null); then
      echo "${key}=${val}" >> "$ENV_FILE"
      count=$((count + 1))
    fi
  done
  # GLM_API_KEY 未单独配置时，复用 ZHIPU_API_KEY
  if ! grep -q '^GLM_API_KEY=' "$ENV_FILE" 2>/dev/null; then
    if val=$(get_secret ZHIPU_API_KEY 2>/dev/null); then
      echo "GLM_API_KEY=${val}" >> "$ENV_FILE"
      count=$((count + 1))
    fi
  fi
  chmod 600 "$ENV_FILE"
  echo "已生成 ${ENV_FILE} (${count} vars)"
  echo "本地运行: ./scripts/run-local.sh cloud-lead-poller"
}

cmd_github() {
  load_secrets
  command -v gh >/dev/null 2>&1 || die "未安装 GitHub CLI。运行: brew install gh && gh auth login"
  gh auth status >/dev/null 2>&1 || die "未登录 GitHub。运行: gh auth login"
  local repo count=0 skipped=0
  repo=$(resolve_repo "${1:-}")
  echo "目标仓库: $repo"
  for key in "${GITHUB_SECRET_KEYS[@]}"; do
    local val
    if ! val=$(get_secret "$key" 2>/dev/null); then
      skipped=$((skipped + 1))
      continue
    fi
    echo "  设置 secret: $key"
    printf '%s' "$val" | gh secret set "$key" --repo "$repo"
    count=$((count + 1))
  done
  # 兼容旧命名：FEISHU_BITABLE_APP → FEISHU_APP_TOKEN
  if ! get_secret FEISHU_APP_TOKEN >/dev/null 2>&1; then
    if val=$(get_secret FEISHU_BITABLE_APP 2>/dev/null); then
      echo "  设置 secret: FEISHU_APP_TOKEN (来自 FEISHU_BITABLE_APP)"
      printf '%s' "$val" | gh secret set FEISHU_APP_TOKEN --repo "$repo"
      count=$((count + 1))
    fi
  fi
  if ! get_secret GLM_API_KEY >/dev/null 2>&1; then
    if val=$(get_secret ZHIPU_API_KEY 2>/dev/null); then
      echo "  设置 secret: GLM_API_KEY (复用 ZHIPU_API_KEY)"
      printf '%s' "$val" | gh secret set GLM_API_KEY --repo "$repo"
      count=$((count + 1))
    fi
  fi
  echo "完成：写入 ${count} 个 secret，跳过 ${skipped} 个空项"
  echo "验证: gh secret list --repo $repo"
}

cmd_list_github() {
  command -v gh >/dev/null 2>&1 || die "未安装 GitHub CLI"
  gh auth status >/dev/null 2>&1 || die "未登录 GitHub。运行: gh auth login"
  local repo
  repo=$(resolve_repo "${1:-}")
  echo "仓库 $repo 已配置的 Secrets:"
  gh secret list --repo "$repo"
}

usage() {
  cat <<'EOF'
用法:
  ./scripts/setup-secrets.sh --local-env [--secrets-file PATH]
  ./scripts/setup-secrets.sh --github [owner/repo]
  ./scripts/setup-secrets.sh --list-github [owner/repo]

环境变量:
  SECRETS_FILE  默认: <项目根>/.secrets.local
EOF
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    --local-env)   cmd_local_env ;;
    --github)      cmd_github "${1:-}" ;;
    --list-github) cmd_list_github "${1:-}" ;;
    -h|--help|"")  usage ;;
    *) die "未知参数: $cmd" ;;
  esac
}

main "$@"
