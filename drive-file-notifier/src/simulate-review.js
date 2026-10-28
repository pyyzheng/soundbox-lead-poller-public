import * as lark from '@larksuiteoapi/node-sdk';
import { loadConfig } from './config.js';
import {
  createClient,
  listFolderChildren,
  patchInteractiveMessage,
  sendInteractiveMessage,
  sendTextMessage,
} from './feishu.js';
import {
  buildReviewCard,
  callbackResponse,
  decisionFromReactions,
  parseCardAction,
  reviewerOpenId,
} from './review-card.js';
import { hydrateReviewLinks } from './review.js';

const HOLD_MS = 20 * 60 * 1000;

const review = {
  id: `rv_sim_${Date.now().toString(36)}`,
  status: 'pending',
  messageId: '',
  error: '',
  tokens: [],
  spec: {
    kind: 'zip',
    tokens: [],
    summary: {
      eventLabel: '【测试】文件夹有新上传/新建',
      folderPath: '',
      fileNames: [],
      files: [],
      folder: null,
      links: [],
    },
    payload: {},
  },
};

let settled = false;
let finishing = null;

async function main() {
  const config = loadConfig();
  const openId = reviewerOpenId(config);
  if (!openId) throw new Error('没有审核人 open_id');
  const client = createClient(config.appId, config.appSecret);
  await fillRealFiles(client, config);
  await hydrateReviewLinks(client, review, config);
  const linkCount = (review.spec.summary.files || []).filter((file) => file.url).length;
  if (!linkCount) throw new Error('没有解析到可点击的云盘链接');
  const sent = await sendInteractiveMessage(
    client,
    openId,
    buildReviewCard(review, config),
    { receiveIdType: 'open_id' },
  );
  review.messageId = sent.message_id;
  console.log(`[sim] card sent message=${review.messageId} review=${review.id} reviewer=${config.review.reviewer.name || openId} links=${linkCount}`);
  for (const file of review.spec.summary.files || []) {
    console.log(`[sim] file ${file.name} ${file.url}`);
  }
  if (review.spec.summary.folder?.url) console.log(`[sim] folder ${review.spec.summary.folder.url}`);
  console.log('[sim] 请先点文件名或「查看」打开云盘。通过后群里只会出现一条测试说明，没有真实附件。');

  const eventDispatcher = new lark.EventDispatcher({}).register({
    'card.action.trigger': async (data) => {
      try {
        return await onCard(client, config, data);
      } catch (err) {
        console.error('[sim] card action failed', err);
        return callbackResponse({ toast: { type: 'error', content: '处理失败，请再点一次' } });
      }
    },
  });
  const wsClient = new lark.WSClient({
    appId: config.appId,
    appSecret: config.appSecret,
    loggerLevel: lark.LoggerLevel.info,
  });
  wsClient.start({ eventDispatcher }).catch((err) => {
    console.error('[sim] socket failed', err);
  });

  const deadline = Date.now() + HOLD_MS;
  while (!settled && Date.now() < deadline) {
    await sleep(15000);
    if (settled) break;
    const decision = await readReaction(client, review.messageId, openId);
    if (!decision) continue;
    console.log(`[sim] reaction ${decision}`);
    await finish(client, config, decision, 'reaction');
  }
  if (finishing) await finishing;
  if (!settled) console.log('[sim] timed out waiting for a decision');
  process.exit(settled ? 0 : 2);
}

async function onCard(client, config, data) {
  const parsed = parseCardAction(data);
  console.log(`[sim] click decision=${parsed.decision || '-'} operator=${parsed.operatorId || '-'} review=${parsed.reviewId || '-'}`);
  if (parsed.reviewId && parsed.reviewId !== review.id) {
    return callbackResponse({ toast: { type: 'info', content: '这不是当前这次测试' } });
  }
  const reviewer = reviewerOpenId(config);
  if (!parsed.operatorId || parsed.operatorId !== reviewer) {
    return callbackResponse({ toast: { type: 'error', content: '只有审核人可以操作' } });
  }
  if (settled) {
    return callbackResponse({
      toast: { type: 'info', content: '已经处理过了' },
      card: buildReviewCard(review, config),
    });
  }
  if (parsed.decision !== 'approve' && parsed.decision !== 'reject') {
    return callbackResponse({ toast: { type: 'error', content: '无法识别的操作' } });
  }
  const toast = parsed.decision === 'approve'
    ? { type: 'info', content: '已通过，正在发测试说明到群' }
    : { type: 'info', content: '已驳回，不会发到群' };
  review.status = parsed.decision === 'approve' ? 'sending' : 'rejected';
  finishing = finish(client, config, parsed.decision, 'button').catch((err) => {
    console.error('[sim] finish failed', err);
  });
  return callbackResponse({ toast, card: buildReviewCard(review, config) });
}

async function finish(client, config, decision, source) {
  if (settled && review.status !== 'sending') return;
  if (decision === 'reject') {
    settled = true;
    review.status = 'rejected';
    await patchInteractiveMessage(client, review.messageId, buildReviewCard(review, config));
    console.log(`[sim] rejected via ${source}; group was not notified`);
    return;
  }
  if (decision !== 'approve') return;
  settled = true;
  review.status = 'sending';
  try {
    const names = (review.spec.summary.files || []).map((file) => file.name).join('、') || '（无）';
    const text = [
      '【云盘更新】这是审核流程测试，请忽略。',
      `文件夹：${review.spec.summary.folderPath || '测试文件更新通知'}`,
      `文件：${names}`,
      '说明：模拟审核，没有发送真实附件，也不会 @人。',
    ].join('\n');
    const posted = await sendTextMessage(client, config.chatId, text);
    review.status = 'sent';
    await patchInteractiveMessage(client, review.messageId, buildReviewCard(review, config));
    console.log(`[sim] approved via ${source}; test line posted message=${posted.message_id}`);
  } catch (err) {
    review.status = 'send_failed';
    review.error = err.message || String(err);
    await patchInteractiveMessage(client, review.messageId, buildReviewCard(review, config)).catch(() => {});
    console.error(`[sim] approved via ${source} but group send failed: ${review.error}`);
  }
}

async function fillRealFiles(client, config) {
  const folders = config.watchFolders || [];
  const preferred = folders.find((folder) => /测试/.test(folder.name || '')) || folders[0];
  if (!preferred?.token) throw new Error('config 里没有可用来取样的云盘文件夹');
  const children = await listFolderChildren(client, preferred.token);
  const files = children.filter((item) => item.type !== 'folder' && item.type !== 'shortcut').slice(0, 2);
  if (!files.length) throw new Error(`文件夹 ${preferred.name} 里没有可打开的文件`);
  review.tokens = files.map((file) => file.token);
  review.spec.tokens = review.tokens;
  review.spec.summary.folderPath = preferred.name;
  review.spec.summary.fileNames = files.map((file) => file.name);
  review.spec.summary.files = files.map((file) => ({
    name: file.name,
    token: file.token,
    type: file.type || 'file',
    url: file.url || '',
  }));
  review.spec.summary.folder = { name: preferred.name, token: preferred.token, url: '' };
  review.spec.payload = {
    folderToken: preferred.token,
    folderName: preferred.name,
    files: review.spec.summary.files,
  };
}

async function readReaction(client, messageId, reviewerId) {
  try {
    const res = await client.im.v1.messageReaction.list({
      path: { message_id: messageId },
      params: { page_size: 20, user_id_type: 'open_id' },
    });
    if (res?.code && res.code !== 0) return '';
    return decisionFromReactions(res?.data?.items || res?.items || [], reviewerId);
  } catch (err) {
    console.warn(`[sim] reaction poll skipped: ${err.message}`);
    return '';
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((err) => {
  console.error('[sim] fatal', err);
  process.exit(1);
});
