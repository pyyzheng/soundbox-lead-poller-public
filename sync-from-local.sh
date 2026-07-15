#!/bin/bash
# sync-from-local.sh — 从本地 workspace 同步共享模块到云端仓库
# 用法: ./sync-from-local.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_BASE="$HOME/.openclaw/workspace"

# 检查本地目录
if [ ! -d "$LOCAL_BASE" ]; then
    echo "错误: 找不到本地 workspace ($LOCAL_BASE)"
    exit 1
fi

# 同步共享模块
cp "$LOCAL_BASE/scripts/lead_filter_common.py" "$SCRIPT_DIR/lib/lead_filter_common.py"
# 修正规则文件路径：本地是 config/lead-rules.json，云端是 lead-rules.json
sed -i.bak 's|SCRIPT_DIR.parent / "config" / "lead-rules.json"|SCRIPT_DIR.parent / "lead-rules.json"|' "$SCRIPT_DIR/lib/lead_filter_common.py"
rm -f "$SCRIPT_DIR/lib/lead_filter_common.py.bak"
echo "✓ lib/lead_filter_common.py (已修正路径)"

# lead_fallback_parser.py 需要微调（去掉硬编码代理和文件缓存）
cp "$LOCAL_BASE/scripts/lead-fallback-parser.py" "$SCRIPT_DIR/lib/lead_fallback_parser.py"
# 自动应用云端适配补丁
sed -i.bak 's|or "http://127.0.0.1:7890"||' "$SCRIPT_DIR/lib/lead_fallback_parser.py"
sed -i.bak 's|SCRIPT_DIR.parent / "config" / "lead-rules.json"|SCRIPT_DIR.parent / "lead-rules.json"|' "$SCRIPT_DIR/lib/lead_fallback_parser.py"
rm -f "$SCRIPT_DIR/lib/lead_fallback_parser.py.bak"
echo "✓ lib/lead_fallback_parser.py (已去掉硬编码代理 + 修正路径)"

# 同步规则配置（完整版，包含 spam_content_patterns 等）
cp "$LOCAL_BASE/config/lead-rules.json" "$SCRIPT_DIR/lead-rules.json"
echo "✓ lead-rules.json"

echo ""
echo "同步完成。请用 git diff 检查变更后提交。"
