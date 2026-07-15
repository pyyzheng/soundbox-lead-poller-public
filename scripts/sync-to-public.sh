#!/usr/bin/env bash
# 将当前工作区同步到公开仓库 soundbox-lead-poller-public（squash 单 commit，避免带入含密钥的旧 git 历史）
#
# 用法:
#   ./scripts/sync-to-public.sh              # 同步并推送（含未 commit 的工作区改动）
#   ./scripts/sync-to-public.sh --dry-run    # 仅预览将要提交的文件
#
# 前提:
#   git remote 已配置 public → github.com/pyyzheng/soundbox-lead-poller-public
#   Secrets 已用 ./scripts/setup-secrets.sh --github pyyzheng/soundbox-lead-poller-public 配置
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_REMOTE="${PUBLIC_REMOTE:-public}"
PUBLIC_REPO="${PUBLIC_REPO:-pyyzheng/soundbox-lead-poller-public}"
DRY_RUN=false
MSG="sync: update from private workspace $(date +%Y-%m-%d)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    -m|--message) MSG="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

cd "$ROOT"

if ! git remote get-url "$PUBLIC_REMOTE" >/dev/null 2>&1; then
  echo "添加 remote: $PUBLIC_REMOTE → https://github.com/$PUBLIC_REPO.git"
  git remote add "$PUBLIC_REMOTE" "https://github.com/$PUBLIC_REPO.git"
fi

# 禁止把密钥文件打进公开仓库
for forbidden in .secrets.local .env; do
  if [[ -f "$forbidden" ]] && git ls-files --error-unmatch "$forbidden" >/dev/null 2>&1; then
    echo "错误: $forbidden 已被 git 跟踪，请先移出索引" >&2
    exit 1
  fi
done

if git grep -l 'apps\.googleusercontent\.com' -- '*.md' '*.py' '*.json' 2>/dev/null | grep -v get-gmail-token.py | grep -q .; then
  echo "警告: 工作区可能仍含 Google OAuth client_id，公开推送前请检查:" >&2
  git grep -n 'apps\.googleusercontent\.com' -- '*.md' '*.py' '*.json' 2>/dev/null | grep -v get-gmail-token.py || true
  if [[ -t 0 ]]; then
    read -r -p "仍继续? [y/N] " ans
    [[ "${ans:-N}" =~ ^[Yy]$ ]] || exit 1
  else
    echo "非交互环境：检测到可能的 client_id，仍继续（请确认未含密钥）" >&2
  fi
fi

# 用临时目录做 orphan 推送，避免 stash + orphan 在本仓库产生 DU 冲突并弄脏工作区
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sync-public.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

rsync -a \
  --exclude '.git/' \
  --exclude '.secrets.local' \
  --exclude '.env' \
  --exclude '.wrangler/' \
  --exclude '.codegraph/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'facebook-tasks.json' \
  --exclude 'facebook-pending.jsonl' \
  "$ROOT/" "$TMP/"

cd "$TMP"
git init -b main >/dev/null
git add -A

if git ls-files | grep -E '(^\.secrets\.local$|^\.env$)'; then
  echo "错误: 暂存区含密钥文件，中止" >&2
  exit 1
fi

if $DRY_RUN; then
  echo "=== dry-run: 将提交以下文件 ==="
  git status --short | head -200
  echo "... (tmp commit not pushed)"
  exit 0
fi

git -c user.email="sync@local" -c user.name="sync" commit -m "$MSG" >/dev/null
git remote add public "$(cd "$ROOT" && git remote get-url "$PUBLIC_REMOTE")"
git push public HEAD:main --force
echo "✓ 已推送到 https://github.com/$PUBLIC_REPO"
echo "  验证: gh run list --repo $PUBLIC_REPO --limit 3"
