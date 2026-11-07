/**
 * Always-on Feishu card callback for drive review buttons.
 * Reject finishes here. Approve records the decision, then starts a short
 * GitHub Actions job to upload the files. The poller no longer has to be online.
 */

import driveConfig from '../../drive-file-notifier/config.json' with { type: 'json' };
import {
  buildReviewCard,
  callbackResponse,
  pruneReviews,
  stageReviewAction,
} from '../../drive-file-notifier/src/review-card.js';

const STATE_PATH = 'drive-file-notifier/.remote-notified.json';
const GITHUB_API = 'https://api.github.com';
const FEISHU_API = 'https://open.feishu.cn/open-apis';

function callbackConfig() {
  return {
    review: driveConfig.review,
    mentions: driveConfig.mentions || [],
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function repoSpec(env) {
  const repo = env.GITHUB_REPO || 'pyyzheng/soundbox-lead-poller-public';
  const token = env.GITHUB_TOKEN;
  if (!token) return null;
  const [owner, name] = String(repo).split('/');
  if (!owner || !name) return null;
  return { owner, name, repo, token };
}

function githubHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
    'User-Agent': 'drive-review',
  };
}

function decodeBase64(b64) {
  const binary = atob(String(b64 || '').replace(/\s/g, ''));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function encodeBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  const size = 0x8000;
  for (let i = 0; i < bytes.length; i += size) {
    binary += String.fromCharCode(...bytes.subarray(i, i + size));
  }
  return btoa(binary);
}

export async function decryptFeishuEncrypt(encrypt, encryptKey) {
  const keyHash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(encryptKey));
  const raw = Uint8Array.from(atob(encrypt), (char) => char.charCodeAt(0));
  const iv = raw.slice(0, 16);
  const data = raw.slice(16);
  const key = await crypto.subtle.importKey('raw', keyHash, { name: 'AES-CBC' }, false, ['decrypt']);
  const plain = await crypto.subtle.decrypt({ name: 'AES-CBC', iv }, key, data);
  return new TextDecoder().decode(plain);
}

function verificationToken(env) {
  return env.DRIVE_REVIEW_VERIFICATION_TOKEN || env.FEISHU_VERIFICATION_TOKEN || '';
}

function encryptKey(env) {
  return env.DRIVE_REVIEW_ENCRYPT_KEY || env.FEISHU_ENCRYPT_KEY || '';
}

function needsPublish(outcome, review) {
  if (!review?.id || review.groupSent || review.status === 'sent' || review.status === 'rejected') return false;
  if (outcome?.schedulePublish) return true;
  // A timed-out retry can observe "sending" after the decision was saved but before the job started.
  return review.status === 'sending';
}

async function loadRemote(spec) {
  const url = `${GITHUB_API}/repos/${spec.owner}/${spec.name}/contents/${STATE_PATH}`;
  const res = await fetch(url, { headers: githubHeaders(spec.token) });
  if (res.status === 404) return { doc: { updatedAt: '', notified: {}, reviews: {} }, sha: null };
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`load reviews HTTP ${res.status}: ${text.slice(0, 180)}`);
  }
  const body = await res.json();
  const raw = JSON.parse(decodeBase64(body.content || ''));
  return {
    doc: {
      updatedAt: raw.updatedAt || '',
      notified: raw.notified || {},
      reviews: raw.reviews || {},
    },
    sha: body.sha || null,
  };
}

async function mutateRemote(env, mutate) {
  const spec = repoSpec(env);
  if (!spec) throw new Error('GITHUB_TOKEN missing');
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const { doc, sha } = await loadRemote(spec);
    const next = mutate(doc);
    if (!next?.changed) return { changed: false, doc };
    const payload = {
      message: 'drive-notifier: review action',
      content: encodeBase64(JSON.stringify(next.doc, null, 2)),
    };
    if (sha) payload.sha = sha;
    const url = `${GITHUB_API}/repos/${spec.owner}/${spec.name}/contents/${STATE_PATH}`;
    const res = await fetch(url, {
      method: 'PUT',
      headers: githubHeaders(spec.token),
      body: JSON.stringify(payload),
    });
    if (res.ok) return { changed: true, doc: next.doc };
    if (res.status === 409) continue;
    const text = await res.text().catch(() => '');
    throw new Error(`save reviews HTTP ${res.status}: ${text.slice(0, 180)}`);
  }
  throw new Error('save reviews exhausted 5 retries');
}

async function dispatchPublish(env, reviewId) {
  const spec = repoSpec(env);
  if (!spec) throw new Error('GITHUB_TOKEN missing');
  const res = await fetch(`${GITHUB_API}/repos/${spec.repo}/dispatches`, {
    method: 'POST',
    headers: githubHeaders(spec.token),
    body: JSON.stringify({
      event_type: 'drive-review-publish',
      client_payload: { review_id: reviewId },
    }),
  });
  if (res.status === 204 || res.status === 202) return;
  const text = await res.text().catch(() => '');
  throw new Error(`dispatch HTTP ${res.status}: ${text.slice(0, 180)}`);
}

async function markSendFailed(env, review, message) {
  const updatedAt = new Date().toISOString();
  const error = String(message || 'dispatch failed').slice(0, 180);
  await mutateRemote(env, (doc) => {
    const reviews = { ...(doc.reviews || {}) };
    const current = reviews[review.id];
    if (!current || current.groupSent || current.status === 'sent') return { changed: false };
    reviews[review.id] = { ...current, status: 'send_failed', error, updatedAt };
    return {
      changed: true,
      doc: {
        updatedAt,
        notified: doc.notified || {},
        reviews: pruneReviews(reviews),
      },
    };
  });
  return { ...review, status: 'send_failed', error, updatedAt };
}

async function patchReviewMessages(env, review, card) {
  const appId = env.DRIVE_REVIEW_APP_ID || env.FEISHU_APP_ID;
  const appSecret = env.DRIVE_REVIEW_APP_SECRET || env.FEISHU_APP_SECRET;
  if (!appId || !appSecret) {
    console.warn('[drive-review] missing app credentials, other cards stay unchanged');
    return;
  }
  const tokenRes = await fetch(`${FEISHU_API}/auth/v3/tenant_access_token/internal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
  });
  const tokenBody = await tokenRes.json().catch(() => ({}));
  if (!tokenRes.ok || tokenBody.code !== 0 || !tokenBody.tenant_access_token) {
    throw new Error(tokenBody.msg || `tenant token HTTP ${tokenRes.status}`);
  }
  const notes = Array.isArray(review.messages) && review.messages.length
    ? review.messages
    : (review.messageId ? [{ messageId: review.messageId }] : []);
  for (const note of notes) {
    if (!note?.messageId) continue;
    const res = await fetch(`${FEISHU_API}/im/v1/messages/${note.messageId}`, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${tokenBody.tenant_access_token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ content: JSON.stringify(card) }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      console.warn(`[drive-review] patch ${note.messageId} HTTP ${res.status} ${text.slice(0, 160)}`);
    }
  }
}

async function readPayload(request, env) {
  const raw = await request.text();
  let payload = raw ? JSON.parse(raw) : {};
  const key = encryptKey(env);
  if (!payload.encrypt) return payload;
  if (!key) {
    const error = new Error('encrypted callback without key');
    error.publicMessage = '回调已加密，但服务未配置密钥';
    throw error;
  }
  try {
    payload = JSON.parse(await decryptFeishuEncrypt(payload.encrypt, key));
  } catch {
    const error = new Error('decrypt failed');
    error.publicMessage = '回调无法解密';
    throw error;
  }
  return payload;
}

export async function handleDriveReview(request, env, ctx) {
  if (request.method === 'GET') return json({ ok: true, service: 'drive-review' });
  if (request.method !== 'POST') return json({ toast: { type: 'error', content: '不支持的请求' } }, 405);

  let payload;
  try {
    payload = await readPayload(request, env);
  } catch (err) {
    console.error('[drive-review] payload failed', err);
    return json({ toast: { type: 'error', content: err.publicMessage || '无法识别的回调' } });
  }

  if (payload?.type === 'url_verification') {
    const expected = verificationToken(env);
    if (expected && payload.token && payload.token !== expected) {
      return new Response('verification token mismatch', { status: 401 });
    }
    return json({ challenge: payload.challenge });
  }

  const expected = verificationToken(env);
  const got = payload?.header?.token || '';
  if (expected && got && got !== expected) {
    return json({ toast: { type: 'error', content: '回调校验失败' } });
  }
  const appId = env.DRIVE_REVIEW_APP_ID || env.FEISHU_APP_ID || '';
  if (appId && payload?.header?.app_id && payload.header.app_id !== appId) {
    return json({ toast: { type: 'error', content: '回调应用不匹配' } });
  }

  const config = callbackConfig();
  let staged;
  try {
    await mutateRemote(env, (doc) => {
      staged = stageReviewAction(doc, payload, config);
      if (!staged.outcome.changed) return { changed: false };
      return {
        changed: true,
        doc: {
          updatedAt: staged.review?.updatedAt || new Date().toISOString(),
          notified: doc.notified || {},
          reviews: pruneReviews(staged.reviews),
        },
      };
    });
  } catch (err) {
    console.error('[drive-review] save failed', err);
    return json({ toast: { type: 'error', content: '处理失败，请再点一次' } });
  }

  const outcome = staged?.outcome || { toast: { type: 'error', content: '无法识别的操作' } };
  const review = staged?.review;
  console.log(`[drive-review] review=${review?.id || '-'} decision=${staged?.parsed?.decision || '-'} status=${review?.status || '-'} changed=${!!outcome.changed}`);

  let card = outcome.card;
  let toast = outcome.toast;
  if (needsPublish(outcome, review)) {
    try {
      await dispatchPublish(env, review.id);
      console.log(`[drive-review] dispatched publish ${review.id}`);
    } catch (err) {
      console.error('[drive-review] dispatch failed', err);
      let failed = { ...review, status: 'send_failed', error: err.message };
      try {
        failed = await markSendFailed(env, review, err.message);
      } catch (saveErr) {
        console.error('[drive-review] mark send_failed failed', saveErr);
      }
      card = buildReviewCard(failed, config);
      toast = { type: 'error', content: '通过了，但推送任务没能启动，请再点一次' };
    }
  }

  if (review && card) {
    const patch = patchReviewMessages(env, review, card).catch((err) => {
      console.warn(`[drive-review] patch cards failed: ${err.message}`);
    });
    if (ctx?.waitUntil) ctx.waitUntil(patch);
  }
  return json(callbackResponse({ toast, card }));
}
