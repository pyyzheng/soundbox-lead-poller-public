# GitHub Actions 云端部署指南
## soundbox-lead-poller

本目录是一个完整的 GitHub Actions 项目，部署后可在云端**每 30 分钟**自动轮询 Gmail，
将询盘写入飞书多维表格。Mac 休眠或关机均不影响运行。

---

## 第一步：获取 Gmail refresh_token（只需做一次）

1. 打开 [Google Cloud Console](https://console.cloud.google.com/) → 你的项目 → **API 和服务** → **凭据**
2. 找到现有的 OAuth 客户端 ID，或新建一个（类型选「桌面应用」）
3. 将 **Client Secret** 填入 `get-gmail-token.py` 脚本中的 `CLIENT_SECRET` 字段
4. 在终端运行（需在本地 Mac 上运行，不是云端）：

```bash
cd ~/path/to/soundbox-lead-poller
pip install google-auth-oauthlib
python3 get-gmail-token.py
```

5. 浏览器会自动弹出 Google 授权页面，用 **soundboxbooth@gmail.com** 登录并点击「允许」
6. 终端会输出 `GMAIL_REFRESH_TOKEN`，复制备用

> ⚠️ 运行完后建议执行 `clear` 清除终端记录，不要将 refresh_token 提交到 Git

---

## 第二步：创建 GitHub 私有仓库

```bash
# 安装 GitHub CLI（如未安装）
brew install gh

# 登录
gh auth login

# 在本目录初始化并推送
cd ~/path/to/soundbox-lead-poller
git init
git add .
git commit -m "init: soundbox lead poller cloud deployment"
gh repo create soundbox-lead-poller --private --source=. --push
```

---

## 第三步：配置 GitHub Secrets

进入仓库页面 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

依次添加以下 Secrets：

| Secret 名称           | 值                                                                                    |
|-----------------------|---------------------------------------------------------------------------------------|
| `GMAIL_CLIENT_ID`     | 从 Google Cloud Console OAuth 客户端获取的 client_id（勿提交到仓库） |
| `GMAIL_CLIENT_SECRET` | 从 Google Cloud Console 获取的 client_secret                                          |
| `GMAIL_REFRESH_TOKEN` | 第一步中获取的 refresh_token                                                           |
| `FEISHU_APP_ID`       | 飞书应用 App ID                                                                        |
| `FEISHU_APP_SECRET`   | 飞书应用密钥（从飞书开放平台获取）                                                     |
| `FEISHU_APP_TOKEN`    | 飞书 Base app_token，必须显式配置                                                       |
| `FEISHU_TABLE_ID`     | 线索总池 table_id，必须显式配置                                                         |
| `FEISHU_FILTER_LOG_TABLE_ID` | 过滤日志 table_id，日报/周报需要显式配置                                         |
| `FEISHU_FOLLOWUP_TABLE` | Follow-up Records table_id，回复/首联相关流程需要显式配置                            |
| `FEISHU_SALES_NOTIFY_TABLE` | 业务通知名单 table_id，客户回复转发流程需要显式配置                               |

---

## 第四步：验证部署

1. 进入仓库 → **Actions** 标签页
2. 点击左侧「Gmail Lead Poller」→ 点击右上角「Run workflow」手动触发一次
3. 查看运行日志，确认输出类似：

```
2026-04-06T10:00:01Z [INFO] === Lead Poller 启动 ===
2026-04-06T10:00:02Z [INFO] Gmail API 认证中...
2026-04-06T10:00:03Z [INFO] 找到 0 封未处理邮件（或 N 封）
2026-04-06T10:00:03Z [INFO] === 本次运行完成: 成功=0 跳过=0 错误=0 ===
```

4. 确认无报错后，GitHub Actions 会自动按 5 分钟 cron 持续运行

---

## 工作原理

```
GitHub Actions (每5分钟)
    │
    ▼
cloud-lead-poller.py
    │
    ├── Gmail API (refresh_token 直连，无需代理)
    │   └── 搜索: from:(email@soundboxbooth.com OR inquiry@soundboxacoustic.com)
    │          且无 processed-by-openclaw 标签
    │
    ├── 邮件解析 (规则引擎, lead-rules.json)
    │   ├── 提取 Name / Email / Company / Phone / Message
    │   ├── 识别国家（IP → 电话区号 → 地名）
    │   ├── 识别渠道（谷歌1 / 谷歌2）
    │   └── 识别产品（静音舱 / 型号）
    │
    ├── 飞书去重（查 Email（客户邮箱）字段）
    │
    ├── 飞书写入（Enquiry details 字段）
    │   格式: Name:xxx / Email:xxx / ... / 国家-渠道-产品-型号
    │
    └── Gmail 打标签（processed-by-openclaw）
        └── 下次搜索自动跳过已处理邮件
```

---

## 常见问题

**Q：GitHub Actions 免费额度够用吗？**
A：每月 2000 分钟。当前设定每 30 分钟运行一次，每次约 20-30 秒（按 1 分钟计），
   每天 48 次 × 1 分钟 ≈ 48 分钟，每月约 1440 分钟，免费额度完全够用。

**Q：如何修改轮询频率？**
A：编辑 `.github/workflows/lead-poller.yml` 中的 cron 表达式，推送后自动生效。
   - `*/30 * * * *` — 每 30 分钟（当前设定，免费额度充裕）
   - `*/10 * * * *` — 每 10 分钟（免费额度也够用）
   - `*/5 * * * *`  — 每 5 分钟（需 GitHub Pro）

**Q：如何更新规则（产品型号、渠道等）？**
A：直接编辑 `lead-rules.json`，`git commit && git push`，下次运行自动生效。

**Q：如何查看历史运行记录？**
A：GitHub 仓库 → Actions 标签页 → 点击任意一次运行查看日志

**Q：飞书写入失败了怎么办？**
A：Gmail 标签不会被打上，下次运行会自动重试该邮件，确保不会漏单。

**Q：如何临时暂停？**
A：GitHub 仓库 → Actions → Gmail Lead Poller → 右上角「Disable workflow」

---

## 文件结构

```
soundbox-lead-poller/
├── .github/
│   └── workflows/
│       └── lead-poller.yml      # GitHub Actions 调度配置
├── cloud-lead-poller.py         # 主轮询脚本（替代本地 lead-finalize.js）
├── lead-rules.json              # 规则配置（渠道/产品/跳过名单）
├── requirements.txt             # Python 依赖
├── get-gmail-token.py           # 一次性：获取 Gmail refresh_token（本地运行）
└── DEPLOY-GUIDE.md              # 本文档
```
