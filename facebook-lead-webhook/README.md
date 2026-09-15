# facebook-lead-webhook

Cloudflare **HTTP Worker**（不是定时任务）：`https://webhook.soundboxbooth.com`

| 路径 | 用途 |
|------|------|
| `GET/POST /webhook` | Facebook Lead Ads + Messenger（Meta 订阅回调） |
| `GET/POST /instagram` | Instagram Messaging |
| `GET /` | 健康检查 `{"status":"ok","service":"facebook-lead-webhook"}` |
| `GET /privacy` | 隐私政策页（App Review） |

**漏单补录**：Webhook 为主入口。GitHub Actions → **Facebook Lead Poller** 已恢复每 30 分钟定时（按 `Facebook Leadgen ID` / 邮箱电话去重，可安全重跑）；也可手动 `workflow_dispatch`。

## 部署

```bash
cd facebook-lead-webhook

# 方式一：API Token（推荐）
export CLOUDFLARE_API_TOKEN="..."
npx wrangler deploy

# 方式二：项目根目录脚本（读 .secrets.local 的 CLOUDFLARE_API_TOKEN）
../scripts/deploy-facebook-webhook.sh
```

部署后验证：

```bash
curl -s https://webhook.soundboxbooth.com/
# 应返回 {"status":"ok","service":"facebook-lead-webhook"}
```

## Secrets（Cloudflare Dashboard 或 `wrangler secret put`）

| Secret | 必需 |
|--------|------|
| `META_PAGE_ACCESS_TOKEN` | 是 |
| `META_APP_SECRET` | 是（Webhook 签名校验） |
| `META_WEBHOOK_VERIFY_TOKEN` | 是（GET 验证） |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 是 |
| `GITHUB_TOKEN` | 是（`repository_dispatch` → `pyyzheng/soundbox-lead-poller-public`） |
| `GLM_API_KEY` | 是（Messenger AI） |
| `ADMIN_TOKEN` | 是（/reprocess 等管理端点） |
| `IG_APP_SECRET` | Instagram 可选 |

变量 `GITHUB_REPO`（明文）应为 `pyyzheng/soundbox-lead-poller-public`（见 `wrangler.toml` `[vars]`）。

更新 `GITHUB_TOKEN` Secret（不重新 deploy 代码）：

```bash
# .secrets.local 填 GITHUB_TOKEN_CLOUDFLARE + CLOUDFLARE_API_TOKEN 后：
../scripts/configure-dispatch-tokens.sh --cloudflare
```

## Meta App 配置

- **Callback URL**：`https://webhook.soundboxbooth.com/webhook`
- **Verify Token**：与 `META_WEBHOOK_VERIFY_TOKEN` 一致
- **订阅字段**：`leadgen`、`messages`、`messaging_postbacks` 等

## 近期变更（2026-07-14）

- **漏单根因**：Webhook 停收后无兜底（Worker cron 与 Poller schedule 此前都关掉了）
- **修复**：恢复 Poller `*/30` 定时；`since` 支持 `+0000` 并强制客户端按 `created_time` 过滤（禁止无效 since 扫全量历史）
- **Webhook KV**：`processing` / 失败不再永久占坑；`/reprocess` 先清 KV 再重试
- **差集监控**：Actions `Facebook Gap Monitor`（每小时）对比 Meta↔飞书 leadgen_id，漏录则告警并自动触发 Poller

## 近期变更（2026-07-06）

- 移除 Worker `cron` 补录（根因：与 webhook 双写导致重复线索）
- 写入前按 `Facebook Leadgen ID` + 邮箱/电话飞书查重
- Webhook 与 Cron 共享 KV 去重标记（cron 已禁用，KV 逻辑保留在 processLead）

## 近期变更（2026-07-11）

- **IG/FB 渠道修复**：Instagram 会话不再误标为 Facebook-Messenger；飞书写入 `Channels=Instagram` / `细分渠道=Instagram`
- **发送失败不落盘**：Meta API 发送失败的 Bot 回复不再写入飞书 Enquiry；仅保留客户原文 + 已成功送达的 Bot 消息
- **Auto-Reply Error**：Bot 发送失败时写入飞书 `Auto-Reply Error` 字段，便于排查权限问题（如 `pages_messaging`）
