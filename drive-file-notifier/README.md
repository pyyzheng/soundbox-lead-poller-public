# 云盘文件变更 → 外部群 IM 附件通知

监听指定飞书云盘文件夹（新建/上传）和文件（内容更新），向目标群发送 **IM 附件**（并 `@Darren`）。

## 当前配置

| 项 | 值 |
|----|----|
| 文件夹 | `RsXZfdPMtlN6tjdCLI0c12munfc`（测试文件更新通知） |
| | `YYFCfwjMMley4md1SMgcPLvznEb`（C-产品画册&产品色卡） |
| | `BGGMfON8vl4uHVdac7Bcv8HDnvf`（M-Marketing 品牌营销资料） |
| | `DUdufPTFZlAKcwdHy6ychCCBnGc`（A-Case Gallery案例） |
| 文件 | `HubRbexqdo5VRqxh220c1TUpn0f`（公司简介及产品及解决方案.md） |
| 目标群 | `oc_f931f4c688a7da9c14e87e7a1e12e322` |
| 发送 | ≤30MB 直接发附件；更大先压缩（PDF/图片降质，其它 zip）；仍超限只发文字 |

## 推荐部署：GitHub Actions（关机也能跑）

GitHub **不能**常驻 WebSocket 长连接，因此用定时轮询（与仓库里询盘 Poller 同模式）：

- Workflow：`.github/workflows/drive-file-notifier.yml`
- 默认每 **5 分钟**跑一次 `npm run once`
- 状态文件缓存在 Actions Cache，避免重复通知

### 配置 Secrets

在仓库 **Settings → Secrets and variables → Actions** 添加：

| Secret | 说明 |
|--------|------|
| `FEISHU_APP_ID` | 应用 App ID |
| `FEISHU_APP_SECRET` | 应用 App Secret |
| `DRIVE_NOTIFIER_CHAT_ID` | 可选，覆盖 `config.json` 里的群 ID |

### 启用

1. 把含 workflow 的代码推到 GitHub（`main`）
2. 填好 Secrets
3. Actions 里打开 **Drive File Notifier**，可先点 **Run workflow** 手动跑一次（首次只建基线，不刷屏）
4. 之后改云盘文件，最多约 5 分钟群里会收到通知

> 本机 `npm start`（长连接+30 秒轮询）可继续用于调试；生产建议只用 GitHub Actions，避免双开重复发。

## 本机调试

```bash
cd drive-file-notifier
cp .env.example .env   # APP_ID / APP_SECRET
npm install
npm run once           # 单次轮询（适合对照 Actions）
npm start              # 长连接 + 本地轮询（需一直开着）
npm run send-once -- <file_token> file
```

## 前提

1. 机器人已进目标群，且已开外部群能力  
2. 监听的文件夹/文件已「添加文档应用」给本应用（可管理）  
3. 需要压缩大文件时，本机/CI 需有 `imagemagick`、`ghostscript`、`zip`
