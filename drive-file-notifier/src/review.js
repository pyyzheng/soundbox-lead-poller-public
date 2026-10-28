import * as lark from '@larksuiteoapi/node-sdk';
import {
  getFileMetas,
  patchInteractiveMessage,
  resolveDriveFileUrl,
  sendInteractiveMessage,
} from './feishu.js';
import { notifyFileAttachment, notifyFilesAsZip } from './notify.js';
import {
  buildReviewCard,
  callbackResponse,
  decisionFromReactions,
  parseCardAction,
  isReviewer,
  pruneReviews,
  reviewMode,
  reviewerOpenId,
  reviewersOf,
} from './review-card.js';
import { mergeRemoteReviewsInto, saveRemoteReviews } from './remote-notified.js';

const publishing = new Set();
let reactionPollBroken = false;
const STALE_SEND_MS = 2 * 60 * 1000;
const CARD_RETRY_MS = 10 * 60 * 1000;

export { reviewMode, reviewerOpenId, reviewersOf };

export async function dispatchNotice(client, config, spec) {
  if (spec?.kind === 'zip') return notifyFilesAsZip(client, config, spec.payload);
  return notifyFileAttachment(client, config, spec.payload);
}

export async function notifyOrReview(runtime, spec) {
  if (reviewMode(runtime.config) === 'off') return dispatchNotice(runtime.client, runtime.config, spec);
  return submitReview(runtime, spec);
}

export async function submitReview(runtime, spec, deps = {}) {
  if (spec == null && runtime?.spec) {
    spec = runtime.spec;
    if (runtime.deps) deps = runtime.deps;
  }
  if (!runtime.state.reviews) runtime.state.reviews = {};
  const existing = findOpenReview(runtime.state, spec.tokens);
  if (existing?.messageId && existing.status === 'pending') return existing;

  const review = existing || createReview(spec);
  runtime.state.reviews[review.id] = review;
  runtime.persist?.();
  await hydrateReviewLinks(runtime.client, review, runtime.config);
  await deliverCard(runtime, review, deps);
  return review;
}

export async function resumeReviews(runtime, deps = {}) {
  if (reviewMode(runtime.config) === 'off') return;
  const reviews = Object.values(runtime.state?.reviews || {});
  for (const review of reviews) {
    try {
      const notes = messageNotes(review);
      if (review.status === 'card_failed' || (review.status === 'pending' && !notes.length)) {
        if (review.status === 'card_failed' && reviewAge(review) < CARD_RETRY_MS) continue;
        if (reviewersOf(runtime.config).length) await deliverCard(runtime, review, deps);
        continue;
      }
      if (review.status === 'sending' && !publishing.has(review.id) && !review.groupSent) {
        const age = Date.now() - Date.parse(review.updatedAt || '');
        if (Number.isFinite(age) && age > STALE_SEND_MS) await publishReview(runtime, review.id, deps);
        continue;
      }
      if (review.status !== 'pending') continue;
      let result = null;
      for (const note of messageNotes(review)) {
        const decision = await readReactionDecision(runtime, note.messageId, note.openId);
        if (!decision) continue;
        console.log(`[review] reaction ${decision} on ${review.id} by ${note.name || note.openId || '-'}`);
        result = await decideReview(runtime, {
          reviewId: review.id,
          decision,
          operatorId: note.openId || reviewerOpenId(runtime.config),
        }, deps);
        break;
      }
      if (!result) continue;
      if (result.schedulePublish) await publishReview(runtime, review.id, deps);
    } catch (err) {
      console.warn(`[review] resume ${review.id} failed: ${err.message}`);
    }
  }
  pruneStoredReviews(runtime);
}

export async function decideReview(runtime, parsed, deps = {}) {
  if (!isReviewer(runtime.config, parsed.operatorId)) {
    return { toast: { type: 'error', content: '只有审核人可以操作' } };
  }
  const review = await loadReview(runtime, parsed.reviewId);
  if (!review) return { toast: { type: 'error', content: '找不到这条审核，可能已过期' } };

  if (review.status === 'sent') {
    return { toast: { type: 'info', content: '已经推送到群' }, card: buildReviewCard(review, runtime.config) };
  }
  if (review.status === 'rejected') {
    return { toast: { type: 'info', content: '已经驳回' }, card: buildReviewCard(review, runtime.config) };
  }
  if (review.status === 'sending' || publishing.has(review.id)) {
    return { toast: { type: 'info', content: '正在推送到群，请稍候' }, card: buildReviewCard(review, runtime.config) };
  }

  if (parsed.decision === 'reject') {
    review.status = 'rejected';
    review.error = '';
    review.decidedByName = reviewerName(runtime.config, parsed.operatorId);
    touch(review);
    await persistReview(runtime);
    await patchReviewCards(runtime.client, review, runtime.config, deps.patchCard || patchReviewCard);
    console.log(`[review] rejected ${review.id} by ${review.decidedByName || parsed.operatorId}`);
    return {
      toast: { type: 'info', content: '已驳回，不会发到群' },
      card: buildReviewCard(review, runtime.config),
    };
  }

  if (parsed.decision === 'approve') {
    review.status = 'sending';
    review.error = '';
    review.decidedByName = reviewerName(runtime.config, parsed.operatorId);
    touch(review);
    await persistReview(runtime);
    return {
      schedulePublish: true,
      toast: { type: 'info', content: '已通过，正在推送到群' },
      card: buildReviewCard(review, runtime.config),
    };
  }

  return { toast: { type: 'error', content: '无法识别的操作' } };
}

export async function publishReview(runtime, reviewId, deps = {}) {
  const review = runtime.state?.reviews?.[reviewId];
  if (!review || review.status === 'sent' || review.status === 'rejected' || review.groupSent) return;
  if (publishing.has(reviewId)) return;
  publishing.add(reviewId);
  const patch = deps.patchCard || patchReviewCard;
  const dispatch = deps.dispatchNotice || dispatchNotice;
  try {
    review.status = 'sending';
    touch(review);
    await persistReview(runtime);
    await patchReviewCards(runtime.client, review, runtime.config, patch);
    await dispatch(runtime.client, runtime.config, review.spec);
    review.groupSent = true;
    review.status = 'sent';
    review.error = '';
    touch(review);
    await persistReview(runtime);
    await patchReviewCards(runtime.client, review, runtime.config, patch);
    console.log(`[review] published ${reviewId} to chat`);
  } catch (err) {
    if (review.groupSent) {
      review.status = 'sent';
      review.error = '';
      touch(review);
      await persistReview(runtime);
      console.warn(`[review] ${reviewId} already posted; status kept as sent after ${err.message}`);
      return;
    }
    review.status = 'send_failed';
    review.error = err.message || String(err);
    touch(review);
    await persistReview(runtime);
    await patchReviewCards(runtime.client, review, runtime.config, patch);
    console.error(`[review] publish ${reviewId} failed: ${review.error}`);
  } finally {
    publishing.delete(reviewId);
  }
}

export function handleCardAction(runtime, data) {
  return onCardAction(runtime, data).catch((err) => {
    console.error('[review] card action failed', err);
    return callbackResponse({ toast: { type: 'error', content: '处理失败，请再点一次' } });
  });
}

export function startReviewSocket(runtime) {
  const { appId, appSecret } = runtime.config || {};
  if (!appId || !appSecret) {
    console.warn('[review] missing app credentials, approval buttons will not connect');
    return null;
  }
  const eventDispatcher = new lark.EventDispatcher({}).register({
    'card.action.trigger': (data) => handleCardAction(runtime, data),
  });
  const wsClient = new lark.WSClient({
    appId,
    appSecret,
    loggerLevel: lark.LoggerLevel.info,
  });
  wsClient.start({ eventDispatcher }).catch((err) => {
    console.error('[review] approval socket failed', err);
  });
  return wsClient;
}

async function onCardAction(runtime, data) {
  const parsed = parseCardAction(data);
  console.log(`[review] card action review=${parsed.reviewId || '-'} decision=${parsed.decision || '-'} operator=${parsed.operatorId || '-'}`);
  const result = await decideReview(runtime, parsed);
  if (result.schedulePublish) {
    setTimeout(() => {
      publishReview(runtime, parsed.reviewId).catch((err) => {
        console.error('[review] publish failed', err);
      });
    }, 0);
  }
  return callbackResponse(result);
}

async function deliverCard(runtime, review, deps = {}) {
  const people = reviewersOf(runtime.config);
  if (!people.length) {
    review.status = 'card_failed';
    review.error = '未配置审核人';
    touch(review);
    await persistReview(runtime);
    console.warn(`[review] ${review.id} not sent: reviewer open_id missing`);
    return;
  }
  if (!Array.isArray(review.messages)) review.messages = [];
  const send = deps.sendCard || sendReviewCard;
  const card = buildReviewCard(review, runtime.config);
  const errors = [];
  for (const person of people) {
    if (review.messages.some((item) => item.openId === person.openId && item.messageId)) continue;
    try {
      const sent = await send(runtime.client, person.openId, card);
      review.messages.push({
        openId: person.openId,
        name: person.name,
        messageId: sent?.message_id || sent?.messageId || '',
      });
    } catch (err) {
      errors.push(`${person.name || person.openId}: ${err.message || err}`);
    }
  }
  review.messageId = review.messages.find((item) => item.messageId)?.messageId || '';
  if (review.messageId) {
    review.status = 'pending';
    review.error = errors.join('; ');
    touch(review);
    await persistReview(runtime);
    console.log(`[review] card sent ${review.id} to ${review.messages.length} reviewer(s), files=${(review.tokens || []).length}`);
    return;
  }
  review.status = 'card_failed';
  review.error = errors.join('; ') || '发送审核卡片失败';
  touch(review);
  await persistReview(runtime);
  console.error(`[review] card send failed ${review.id}: ${review.error}`);
}

async function loadReview(runtime, reviewId) {
  if (!reviewId) return null;
  if (runtime.state?.reviews?.[reviewId]) return runtime.state.reviews[reviewId];
  if (!runtime.state) return null;
  try {
    await mergeRemoteReviewsInto(runtime.state);
  } catch (err) {
    console.warn(`[review] reload remote reviews failed: ${err.message}`);
  }
  return runtime.state.reviews?.[reviewId] || null;
}

async function readReactionDecision(runtime, messageId, reviewerId) {
  if (reactionPollBroken || !messageId) return '';
  try {
    const res = await runtime.client.im.v1.messageReaction.list({
      path: { message_id: messageId },
      params: { page_size: 50, user_id_type: 'open_id' },
    });
    if (res?.code && res.code !== 0) {
      if (isPermissionError(res.code, res.msg)) {
        reactionPollBroken = true;
        console.warn(`[review] reaction poll disabled: code=${res.code} ${res.msg || ''}`);
      }
      return '';
    }
    const items = res?.data?.items || res?.items || [];
    return decisionFromReactions(items, reviewerId);
  } catch (err) {
    if (isPermissionError(err.code, err.message)) {
      reactionPollBroken = true;
      console.warn(`[review] reaction poll disabled: ${err.message}`);
    } else {
      console.warn(`[review] reaction poll failed: ${err.message}`);
    }
    return '';
  }
}

function isPermissionError(code, message) {
  const text = `${code || ''} ${message || ''}`;
  return /99991672|99991663|permission|forbidden/i.test(text);
}

export async function hydrateReviewLinks(client, review, config) {
  const summary = review.spec.summary;
  const files = listedFiles(review);
  const folder = listedFolder(review);
  const docs = [
    ...files.map((file) => ({ token: file.token, type: file.type || 'file' })),
    ...(folder?.token ? [{ token: folder.token, type: 'folder' }] : []),
  ];
  const metas = await loadMetas(client, docs);
  const byToken = new Map(metas.map((meta) => [meta.doc_token, meta]));

  summary.files = files.map((file) => {
    const meta = byToken.get(file.token);
    const type = meta?.doc_type || file.type || 'file';
    return {
      name: file.name || meta?.title || file.token,
      token: file.token,
      type,
      url: resolveDriveFileUrl(meta, file.token, type),
    };
  });
  summary.fileNames = summary.files.map((file) => file.name);
  if (folder?.token) {
    const meta = byToken.get(folder.token);
    summary.folder = {
      name: folder.name || meta?.title || summary.folderPath || '文件夹',
      token: folder.token,
      url: resolveDriveFileUrl(meta, folder.token, 'folder'),
    };
  }
  summary.links = [
    ...summary.files.map((file) => ({ label: file.name, url: file.url })),
    ...(summary.folder?.url ? [{ label: '文件夹', url: summary.folder.url }] : []),
  ];

  for (const person of reviewersOf(config)) {
    for (const file of summary.files) {
      await grantReviewerView(client, file.token, file.type, person.openId);
    }
  }
}

function listedFiles(review) {
  const summary = review.spec?.summary || {};
  const payload = review.spec?.payload || {};
  if (Array.isArray(summary.files) && summary.files.length) return summary.files.filter((file) => file?.token);
  if (Array.isArray(payload.files) && payload.files.length) {
    return payload.files.filter((file) => file?.token).map((file) => ({
      name: file.name,
      token: file.token,
      type: file.type || 'file',
    }));
  }
  if (payload.fileToken) {
    return [{
      name: payload.titleHint || summary.fileNames?.[0] || payload.fileToken,
      token: payload.fileToken,
      type: payload.fileType || 'file',
      url: payload.driveUrl || '',
    }];
  }
  return [];
}

function listedFolder(review) {
  const summary = review.spec?.summary || {};
  const payload = review.spec?.payload || {};
  if (summary.folder?.token) return summary.folder;
  const token = payload.folderToken;
  if (!token) return null;
  return { name: summary.folderPath || payload.folderName || '', token };
}

async function loadMetas(client, docs) {
  const metas = [];
  for (let i = 0; i < docs.length; i += 50) {
    try {
      const batch = await getFileMetas(client, docs.slice(i, i + 50));
      metas.push(...batch);
    } catch (err) {
      console.warn(`[review] drive link meta skipped: ${err.message}`);
    }
  }
  return metas;
}

async function grantReviewerView(client, token, type, openId) {
  if (!client || !token || !openId || !type || type === 'folder' || type === 'shortcut') return;
  try {
    const res = await client.drive.v1.permissionMember.create({
      path: { token },
      params: { type, need_notification: false },
      data: { member_type: 'openid', member_id: openId, perm: 'view' },
    });
    if (res?.code && res.code !== 0) {
      console.warn(`[review] open access ${type}:${token.slice(0, 8)} code=${res.code} ${res.msg || ''}`);
    }
  } catch (err) {
    console.warn(`[review] open access skipped ${type}:${token.slice(0, 8)}: ${err.message}`);
  }
}

function messageNotes(review) {
  if (Array.isArray(review?.messages) && review.messages.length) {
    return review.messages.filter((item) => item?.messageId);
  }
  if (review?.messageId) return [{ openId: '', name: '', messageId: review.messageId }];
  return [];
}

async function patchReviewCards(client, review, config, patch) {
  if (!client || !patch) return;
  const card = buildReviewCard(review, config);
  for (const note of messageNotes(review)) {
    await patch(client, note.messageId, card).catch((err) => {
      console.warn(`[review] patch card failed: ${err.message}`);
    });
  }
}

function reviewerName(config, openId) {
  return reviewersOf(config).find((person) => person.openId === openId)?.name || '';
}

function sendReviewCard(client, openId, card) {
  return sendInteractiveMessage(client, openId, card, { receiveIdType: 'open_id' });
}

function patchReviewCard(client, messageId, card) {
  return patchInteractiveMessage(client, messageId, card);
}

async function persistReview(runtime) {
  try {
    runtime.persist?.();
  } catch (err) {
    console.warn(`[review] local persist failed: ${err.message}`);
  }
  await saveRemoteReviews(runtime.state?.reviews || {});
}

function pruneStoredReviews(runtime) {
  const current = runtime.state?.reviews || {};
  const pruned = pruneReviews(current);
  if (Object.keys(pruned).length === Object.keys(current).length) return;
  runtime.state.reviews = pruned;
  runtime.persist?.();
  saveRemoteReviews(pruned).catch((err) => {
    console.warn(`[review] prune remote reviews failed: ${err.message}`);
  });
}

function findOpenReview(state, tokens) {
  const wanted = new Set(tokens || []);
  for (const review of Object.values(state.reviews || {})) {
    if (!review || review.status === 'sent' || review.status === 'rejected') continue;
    if ((review.tokens || []).some((token) => wanted.has(token))) return review;
  }
  return null;
}

function createReview(spec) {
  const now = new Date().toISOString();
  const tokens = [...(spec.tokens || [])];
  return {
    id: `rv_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    status: 'pending',
    createdAt: now,
    updatedAt: now,
    messageId: '',
    messages: [],
    decidedByName: '',
    error: '',
    groupSent: false,
    tokens,
    spec: {
      kind: spec.kind,
      tokens,
      summary: {
        eventLabel: spec.summary?.eventLabel || '',
        folderPath: spec.summary?.folderPath || '',
        fileNames: [...(spec.summary?.fileNames || [])],
        files: (spec.summary?.files || []).filter((file) => file?.token).map((file) => ({
          name: file.name || file.token,
          token: file.token,
          type: file.type || 'file',
          url: file.url || '',
        })),
        folder: spec.summary?.folder?.token ? {
          name: spec.summary.folder.name || spec.summary.folderPath || '',
          token: spec.summary.folder.token,
          url: spec.summary.folder.url || '',
        } : null,
        links: [],
      },
      payload: spec.payload,
    },
  };
}

function touch(review) {
  review.updatedAt = new Date().toISOString();
}

function reviewAge(review) {
  const at = Date.parse(review?.updatedAt || '');
  if (!Number.isFinite(at)) return Number.POSITIVE_INFINITY;
  return Date.now() - at;
}
