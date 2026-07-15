/**
 * Gmail → GitHub Actions 触发器（DRY-RUN 预过滤版）
 *
 * 每 1 分钟由 Apps Script 时间驱动器调用。
 * 用 Gmail History API 增量检测新邮件，有新邮件时触发 GitHub Actions workflow_dispatch。
 *
 * 🧪 当前为 DRY-RUN 模式：预过滤规则会运行并记录日志，但不改变触发行为
 *    （所有目标邮件仍触发 lead-poller）。跑 3-7 天确认 0 漏检后，按文件尾说明启用。
 *
 * 配置步骤：
 *   1. 打开 script.google.com，创建新项目，粘贴此文件
 *   2. 启用 Advanced Gmail Service：资源 → 高级服务 → Gmail API → 启用
 *   3. 文件 → 项目属性 → 脚本属性，添加：
 *      GITHUB_TOKEN = <GITHUB_TOKEN_APPS_SCRIPT，见 .secrets.local.example>
 *      （Fine-grained PAT：仅 soundbox-lead-poller-public，Actions Read and write）
 *   4. 添加 OAuth scope：https://www.googleapis.com/auth/gmail.readonly
 *   5. 设置时间触发器：每分钟运行 checkNewEmails
 *   6. 首次运行 authorizeOAuth() 授权，然后手动运行 initHistoryId() 初始化
 *
 * 注意：TARGET_EMAILS 需与 lead-rules.json channels 保持同步
 *       SKIP_* 预过滤规则与 lead-rules.json 的 skip_* 同步（更新规则时两边一起改）
 */

const REPO = 'pyyzheng/soundbox-lead-poller-public';
const WORKFLOW = 'lead-poller.yml';
const PROPS = PropertiesService.getScriptProperties();

const TARGET_EMAILS = [
  'soundboxbooth@gmail.com',
  'email@soundboxbooth.com',
  'inquiry@soundboxacoustic.com',
  'service@soundbox-sys.com',
  'service@soundbox-pod.com',
].map(e => e.toLowerCase());

// History API 404 后 historyId 失效，最多保留 30 天
const COOLDOWN_MS = 120000;

// === 预过滤规则（与 lead-rules.json 的 skip_* 同步，更新时两边改）===
// subject 含这些子串（小写匹配）→ 拦截
const SKIP_SUBJECT = [
  'unsubscribe', 'out of office', 'auto-reply', 'automatic reply', 'vacation',
  'delivery status notification', 'mail delivery failed', 'returned mail', 'undelivered',
  'email change request', 'password reset', 'new user registration', 'your username', 'your password'
];
// 发件人域名后缀命中 → 拦截（电商/社交/营销SaaS/设计/平台通知 + skip_senders 的 @yuguo/@creatify）
const SKIP_SENDER_DOMAINS = [
  'aliexpress.com', 'alibaba.com', 'amazon', 'amazon.com', 'ebay.com', 'faire.com', 'shopify.com', 'etsy.com', 'wish.com', 'temu.com', 'shein.com',
  'instagram.com', 'tiktok.com', 'facebook.com', 'linkedin.com', 'twitter.com', 'youtube.com', 'pinterest.com', 'reddit.com', 'snapchat.com', 'threads.net',
  'semrush.com', 'ahrefs.com', 'moz.com', 'hubspot.com', 'mailchimp.com', 'sendgrid.com', 'beehiiv.com', 'constantcontact.com', 'elementor.com', 'wordpress.com', 'wix.com', 'squarespace.com', 'mailgun.com', 'postmarkapp.com', 'brevo.com', 'mailjet.com', 'activecampaign.com', 'convertkit.com', 'klaviyo.com',
  'dribbble.com', 'behance.net', 'figma.com', 'canva.com', 'upwork.com', 'fiverr.com', 'freelancer.com', 'toptal.com',
  'google.com', 'calendly.com', 'zoom.us', 'siteground.com', 'namesilo.com', 'godaddy.com', 'namecheap.com', 'cloudflare.com', 'wordfence.com', 'letsencrypt.org', 'docker.com', 'github.com', 'gitlab.com', 'atlassian.net', 'notion.so', 'slack.com', 'orangeconnex.com',
  'yuguo.com', 'creatify.ai'
];
// 精确发件人 → 拦截
const SKIP_SENDERS = [
  'hazelc0212@163.com', 'wordpress@soundboxbooth.com', 'soundboxbooth@gmail.com', 'system@soundbox.com', 'pyyzheng@qq.com'
];


function authorizeOAuth() {
  Gmail.Users.Messages.list('me', { maxResults: 1 });
  Logger.log('OAuth 授权完成');
}


function initHistoryId() {
  const profile = Gmail.Users.getProfile('me');
  PROPS.setProperty('lastHistoryId', profile.historyId);
  Logger.log(`historyId 已初始化: ${profile.historyId}`);
}


function checkNewEmails() {
  const lastHistoryId = PROPS.getProperty('lastHistoryId');
  if (!lastHistoryId) {
    Logger.log('lastHistoryId 未初始化，请先运行 initHistoryId()');
    return;
  }

  const lastTrigger = PROPS.getProperty('lastTriggerTime');
  if (lastTrigger && Date.now() - parseInt(lastTrigger) < COOLDOWN_MS) {
    Logger.log('冷却中，跳过');
    return;
  }

  let hasNew = false;
  let newHistoryId = lastHistoryId;

  try {
    const history = Gmail.Users.History.list('me', {
      startHistoryId: lastHistoryId,
      historyTypes: ['messageAdded'],
    });

    if (history.history && history.history.length > 0) {
      for (const record of history.history) {
        if (record.messagesAdded) {
          for (const msg of record.messagesAdded) {
            if (isTargetMessage(msg.message)) {
              hasNew = true;
              break;
            }
          }
        }
        if (hasNew) break;
      }
      newHistoryId = history.history[history.history.length - 1].id;
    }

  } catch (e) {
    Logger.log(`History API 错误: ${e.message}`);
    if (e.message && e.message.includes('404')) {
      initHistoryId();
      return;
    }
    return;
  }

  PROPS.setProperty('lastHistoryId', newHistoryId);

  if (hasNew) {
    Logger.log('检测到新目标邮件，触发 GitHub Actions');
    triggerWorkflow();
  } else {
    Logger.log('无新目标邮件');
  }
}


/**
 * 判断邮件是否来自目标渠道 + 预过滤
 *
 * History API 默认只返回 message id/threadId，不含 payload/headers。
 * 需要额外调用 get() 获取 metadata 才能读 Delivered-To/From/Subject 头。
 *
 * 🧪 DRY-RUN：预过滤命中时记日志，但 return true 保持触发（不改行为）
 *    验证日志无询盘被误拦后，把末尾 return true 改成 return !filteredBy 即启用
 */
function isTargetMessage(message) {
  if (!message || !message.id) return false;

  try {
    const full = Gmail.Users.Messages.get('me', message.id, {
      format: 'metadata',
      metadataHeaders: ['Delivered-To', 'From', 'Subject'],
    });
    if (!full.payload || !full.payload.headers) return false;
    const headers = {};
    (full.payload.headers || []).forEach(h => headers[h.name.toLowerCase()] = (h.value || ''));

    // 1. 必须发到目标邮箱（原逻辑）
    const deliveredTo = (headers['delivered-to'] || '').toLowerCase();
    if (!deliveredTo || !TARGET_EMAILS.some(t => deliveredTo.includes(t))) return false;

    // 2. 解析发件人 email / domain
    const fromRaw = headers['from'] || '';
    const senderEmail = ((fromRaw.match(/<([^>]+)>/) || [, fromRaw])[1] || fromRaw).trim().toLowerCase();
    const localPart = senderEmail.split('@')[0] || '';
    const domain = senderEmail.split('@')[1] || '';
    const subject = (headers['subject'] || '').toLowerCase();

    // 3. 预过滤（反向：命中"明确非询盘"类别 → 拦截）
    let filteredBy = null;
    if (domain && SKIP_SENDER_DOMAINS.some(d => domain === d || domain.endsWith('.' + d))) filteredBy = '域名黑名单';
    else if (SKIP_SENDERS.some(s => senderEmail === s)) filteredBy = '发件人黑名单';
    else if (localPart.startsWith('test')) filteredBy = 'test前缀';
    else if (subject && SKIP_SUBJECT.some(p => subject.includes(p))) filteredBy = 'subject黑名单';

    if (filteredBy) {
      // 🧪 DRY-RUN：记录会被拦截的邮件，但仍触发（不改行为）
      Logger.log(`[DRY-RUN 拦截] ${filteredBy} | from=${senderEmail} | subject="${subject.slice(0, 80)}"`);
    }

    return true;  // 🧪 DRY-RUN：保持原触发行为。启用时改为 → return !filteredBy;
  } catch (e) {
    // 404 = 已删除/垃圾邮件，正常跳过；其他错误记录日志
    if (e.message && e.message.includes('404')) return false;
    Logger.log(`isTargetMessage 错误 (msg ${message.id}): ${e.message}`);
    return false;
  }
}


function triggerWorkflow() {
  const token = PROPS.getProperty('GITHUB_TOKEN');
  if (!token) {
    Logger.log('GITHUB_TOKEN 未配置');
    return;
  }

  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const options = {
    method: 'post',
    headers: {
      'Authorization': `token ${token}`,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'gmail-trigger-apps-script',
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  };

  try {
    const resp = UrlFetchApp.fetch(url, options);
    if (resp.getResponseCode() === 204) {
      Logger.log('workflow_dispatch 触发成功');
      PROPS.setProperty('lastTriggerTime', String(Date.now()));
    } else {
      Logger.log(`触发失败: ${resp.getResponseCode()} ${resp.getContentText()}`);
    }
  } catch (e) {
    Logger.log(`触发异常: ${e.message}`);
  }
}


/**
 * ═══════════════════════════════════════════════════════════════════
 * 启用预过滤（dry-run 验证通过后操作）：
 *   把 isTargetMessage() 末尾的 `return true;` 改成 `return !filteredBy;`
 *   即：预过滤命中（filteredBy != null）→ 不触发；通过 → 触发
 *
 * 验证方法（dry-run 跑 3-7 天）：
 *   1. Apps Script「执行记录」页面，逐条看 [DRY-RUN 拦截] 日志
 *   2. 审查被拦截的 from / subject：
 *      ✅ 全是 notification / marketing / bounce / test / 系统邮件 → 安全启用
 *      ❌ 出现任何询盘（如 "Re: pod inquiry" / "quote" / 客户企业邮箱）→
 *         移除对应规则（域名/subject）后重跑 dry-run
 *   3. 估算省额度：拦截条数 / 总触发数 = 启用后预估省的触发占比
 *      （lead-poller 当前 ~1747min/月，按该比例折算省多少分钟）
 *
 * 注意：预过滤只做减法，永远不漏询盘——被排除的全是明确非询盘类别，
 *       真实询盘必通过；万一漏网，lead-poller 的 132 关键词 + require_inquiry_keyword 兜底
 * ═══════════════════════════════════════════════════════════════════
 */
