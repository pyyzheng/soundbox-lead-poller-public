const APPROVE_EMOJI = new Set(['THUMBSUP', 'OK', 'YES', 'DONE', 'CHECK']);
const REJECT_EMOJI = new Set(['THUMBSDOWN', 'NO', 'CROSSMARK', 'WRONG']);
const TERMINAL_REVIEW = new Set(['sent', 'rejected']);
const REVIEW_KEEP_MS = 14 * 24 * 60 * 60 * 1000;

export function reviewersOf(config) {
  if (process.env.REVIEWER_OPEN_ID) {
    return [{ name: '', openId: process.env.REVIEWER_OPEN_ID }];
  }
  const people = [];
  const seen = new Set();
  const add = (person) => {
    const openId = String(person?.openId || '').trim();
    if (!openId || seen.has(openId)) return;
    seen.add(openId);
    people.push({ name: person?.name || '', openId });
  };
  for (const person of config?.review?.reviewers || []) add(person);
  add(config?.review?.reviewer);
  return people;
}

export function reviewerOpenId(config) {
  return reviewersOf(config)[0]?.openId || '';
}

export function isReviewer(config, openId) {
  if (!openId) return false;
  return reviewersOf(config).some((person) => person.openId === openId);
}

/** off: 直接发群；on: 先发给审核人；misconfigured: 开了审核但没有审核人。 */
export function reviewMode(config) {
  if (!config?.review?.enabled) return 'off';
  if (!reviewersOf(config).length) return 'misconfigured';
  return 'on';
}

export function parseCardAction(data) {
  const source = data?.event?.action ? data.event : (data || {});
  let value = source.action?.value || {};
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value);
    } catch {
      value = {};
    }
  }
  return {
    reviewId: String(value.reviewId || value.review_id || ''),
    decision: String(value.action || value.decision || ''),
    operatorId: source.operator?.open_id || data?.operator?.open_id || '',
    messageId: source.context?.open_message_id || data?.context?.open_message_id || '',
  };
}

export function decisionFromReactions(items, reviewerId) {
  let best = null;
  for (const item of items || []) {
    if (item?.operator?.operator_type === 'app') continue;
    const operatorId = item?.operator?.operator_id || '';
    if (reviewerId && operatorId && operatorId !== reviewerId) continue;
    const emoji = String(item?.reaction_type?.emoji_type || '').toUpperCase();
    let decision = '';
    if (APPROVE_EMOJI.has(emoji)) decision = 'approve';
    else if (REJECT_EMOJI.has(emoji)) decision = 'reject';
    if (!decision) continue;
    const at = Number(item.action_time) || 0;
    if (!best || at >= best.at) best = { decision, at };
  }
  return best?.decision || '';
}

export function pruneReviews(reviews, now = Date.now()) {
  const out = {};
  for (const [id, review] of Object.entries(reviews || {})) {
    if (!review) continue;
    if (TERMINAL_REVIEW.has(review.status)) {
      const at = Date.parse(review.updatedAt || review.createdAt || '');
      if (Number.isFinite(at) && now - at > REVIEW_KEEP_MS) continue;
    }
    out[id] = review;
  }
  return out;
}

export function buildReviewCard(review, config) {
  const status = review?.status || 'pending';
  const interactive = status === 'pending' || status === 'send_failed';
  const summary = review?.spec?.summary || {};
  const files = reviewFiles(summary);
  const shown = files.slice(0, 20);
  const lines = [`**${summary.eventLabel || '云盘有更新'}**`];
  if (summary.folder?.url) lines.push(`文件夹：${mdLink(summary.folder.name || summary.folderPath, summary.folder.url)}`);
  else if (summary.folderPath) lines.push(`文件夹：${summary.folderPath}`);
  if (shown.length) {
    lines.push(`文件（${files.length}）：`);
    for (const file of shown) lines.push(`- ${mdLink(file.name, file.url)}`);
    if (files.length > shown.length) lines.push(`- …另有 ${files.length - shown.length} 个`);
  }
  const mentionNames = (config?.mentions || []).map((m) => m?.name).filter(Boolean);
  lines.push('');
  const actor = review?.decidedByName ? `已由${review.decidedByName}` : '已';
  if (status === 'sent') lines.push(`${actor}通过，并已推送到通知群。`);
  else if (status === 'rejected') lines.push(`${actor}驳回，不会发送到通知群。`);
  else if (status === 'sending') lines.push('已通过，正在推送到通知群…');
  else {
    const who = mentionNames.length ? `，并 @${mentionNames.join('、')}` : '';
    const reviewerNames = reviewersOf(config).map((person) => person.name).filter(Boolean);
    if (reviewerNames.length > 1) {
      lines.push(`${reviewerNames.join('、')}都可审核：任一人通过即推送，任一人驳回则不发送。`);
    }
    lines.push('可先点文件名或下方按钮，打开云盘查看。');
    lines.push(`通过后才会推送到通知群${who}。不通过则不会发送。`);
    lines.push('若按钮没有反应，可在本消息点「赞」通过，或点「踩」驳回。');
  }
  if (status === 'send_failed' && review.error) {
    lines.push(`上次推送失败：${String(review.error).slice(0, 180)}`);
  }

  const card = {
    schema: '2.0',
    config: { update_multi: true },
    header: {
      title: { tag: 'plain_text', content: headerTitle(status) },
      template: headerTemplate(status),
    },
    body: {
      elements: [{ tag: 'markdown', content: lines.join('\n') }],
    },
  };
  for (const file of shown.filter((file) => file.url).slice(0, 8)) {
    card.body.elements.push(openUrlButton(`查看 ${shortLabel(file.name)}`, file.url));
  }
  if (summary.folder?.url) {
    card.body.elements.push(openUrlButton('打开所在文件夹', summary.folder.url));
  }
  if (interactive) card.body.elements.push(buttonRow(review.id));
  return card;
}

function reviewFiles(summary) {
  if (Array.isArray(summary.files) && summary.files.length) {
    return summary.files.filter((file) => file?.name || file?.url);
  }
  return (summary.fileNames || []).filter(Boolean).map((name) => ({ name, url: '' }));
}

function mdLink(label, url) {
  const text = String(label || '打开').replace(/[\[\]]/g, ' ').trim() || '打开';
  if (!url) return text;
  return `[${text}](${url})`;
}

function shortLabel(name) {
  const text = String(name || '文件').replace(/\s+/g, ' ').trim();
  return text.length > 18 ? `${text.slice(0, 17)}…` : text;
}

function openUrlButton(label, url) {
  return {
    tag: 'button',
    type: 'default',
    width: 'fill',
    text: { tag: 'plain_text', content: label },
    behaviors: [{ type: 'open_url', default_url: url }],
  };
}

function headerTitle(status) {
  if (status === 'sent') return '云盘更新已推送';
  if (status === 'rejected') return '云盘更新已驳回';
  if (status === 'sending') return '云盘更新推送中';
  if (status === 'send_failed') return '云盘更新推送失败';
  return '云盘更新待审核';
}

function headerTemplate(status) {
  if (status === 'sent') return 'green';
  if (status === 'rejected') return 'red';
  if (status === 'sending') return 'blue';
  if (status === 'send_failed') return 'grey';
  return 'orange';
}

function buttonRow(reviewId) {
  return {
    tag: 'column_set',
    flex_mode: 'stretch',
    horizontal_spacing: '8px',
    columns: [
      buttonColumn('通过并推送', 'primary', { action: 'approve', reviewId }),
      buttonColumn('不通过', 'danger', { action: 'reject', reviewId }),
    ],
  };
}

function buttonColumn(label, type, value) {
  return {
    tag: 'column',
    width: 'weighted',
    weight: 1,
    elements: [
      {
        tag: 'button',
        type,
        width: 'fill',
        text: { tag: 'plain_text', content: label },
        behaviors: [{ type: 'callback', value }],
      },
    ],
  };
}

export function callbackResponse({ toast, card } = {}) {
  const body = {};
  if (toast?.content) {
    body.toast = { type: toast.type || 'info', content: toast.content };
  }
  if (card) body.card = { type: 'raw', data: card };
  return body;
}
