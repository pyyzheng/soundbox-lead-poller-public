# SoundBox 线索分配系统

## 项目上下文
@PROJECT_CONTEXT.md
@QA-RULES.md

## 文件结构
cloud-lead-poller.py      # 主管线：Gmail → 6层过滤 → LLM解析 → 飞书写入
cloud-health-check.py     # 自动健康检查（每6h）：垃圾漏网 + Actions状态 + OAuth
cloud-daily-report.py     # 每日线索报告（飞书卡片）
cloud-check-unassigned.py # 未分配线索检查（每4h）
get-gmail-token.py        # 一次性获取 Gmail refresh token
lib/
  lead_filter_common.py   # 6层过滤链（check_spam/placeholder/spam_content/...）
  lead_grader.py          # 线索分级（A/B/C/D）
  lead_fallback_parser.py # LLM 解析失败时的正则兜底
  slot_extractor.py       # 产品型号/规格提取
  feishu_utils.py         # 飞书 token + 搜索 + 字段映射
lead-rules.json           # 唯一规则配置源（关键词、阈值、黑名单）
.github/workflows/
  lead-poller.yml         # 每30分钟线索轮询
  health-check.yml        # 每6小时健康检查
  daily-report.yml        # 每日报告（北京时间9:15）
  unassigned-check.yml    # 每4小时未分配检查

## 开发命令
- `DRY_RUN=true python cloud-lead-poller.py` — 测试模式（不写飞书、不打标签）
- `python cloud-lead-poller.py` — 正式运行（环境变量驱动）
- `DRY_RUN=true python cloud-health-check.py` — 健康检查 dry-run（不发告警）
- `python cloud-health-check.py` — 手动触发健康检查
- `python cloud-daily-report.py` — 每日线索报告
- `python cloud-check-unassigned.py` — 未分配线索检查

## 所需环境变量
GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_APP_TOKEN, FEISHU_TABLE_ID
ZHIPU_API_KEY, GITHUB_TOKEN (健康检查用)

## Git 协作
- main 分支直推，不用 PR（单人项目）
- commit 格式：`type: 简述`（feat/fix/docs/refactor/chore）
- 涉及钱/客户数据的修改必须先 dry-run 验证
- 修改过滤规则后必须跑 QA-RULES.md 回归用例

## 代码约定
- 云端脚本（cloud-*.py）不走代理，GitHub Actions 直连
- 用 requests 不用 urllib
- 新增文件建议不超过 500 行；超过时确保函数职责单一
- cloud-lead-poller.py 作为主管道入口例外，新功能优先放 lib/
- API 凭据走环境变量，不硬编码
