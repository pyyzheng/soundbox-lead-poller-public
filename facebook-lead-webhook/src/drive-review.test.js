import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import test from 'node:test';
import driveConfig from '../../drive-file-notifier/config.json' with { type: 'json' };
import { decryptFeishuEncrypt, handleDriveReview } from './drive-review.js';

const reviewerId = driveConfig.review.reviewers[0].openId;

function encodeDoc(doc) {
  return Buffer.from(JSON.stringify(doc)).toString('base64');
}

function pendingDoc() {
  return {
    updatedAt: '2026-11-06T00:00:00.000Z',
    notified: { tok: 1 },
    reviews: {
      rv1: {
        id: 'rv1',
        status: 'pending',
        createdAt: '2026-11-06T00:00:00.000Z',
        updatedAt: '2026-11-06T00:00:00.000Z',
        messageId: 'om_1',
        messages: [{ openId: reviewerId, name: '郑培友', messageId: 'om_1' }],
        error: '',
        groupSent: false,
        tokens: ['file1'],
        spec: { summary: { eventLabel: '文件夹有新上传/新建', fileNames: ['a.jpg'], files: [] } },
      },
    },
  };
}

function mockFetch(doc) {
  const calls = [];
  const saved = [];
  globalThis.fetch = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const target = String(url);
    calls.push({ url: target, method, body: opts.body || '' });
    if (target.includes('/contents/')) {
      if (method === 'PUT') {
        const payload = JSON.parse(opts.body);
        saved.push(JSON.parse(Buffer.from(payload.content, 'base64').toString()));
        return new Response(JSON.stringify({ content: { sha: 'next' } }), { status: 200 });
      }
      return new Response(JSON.stringify({
        sha: 'abc',
        content: encodeDoc(doc),
        encoding: 'base64',
      }), { status: 200 });
    }
    if (target.endsWith('/dispatches')) {
      return new Response(null, { status: 204 });
    }
    if (target.includes('tenant_access_token')) {
      return new Response(JSON.stringify({ code: 0, tenant_access_token: 'tenant' }), { status: 200 });
    }
    if (target.includes('/im/v1/messages/')) {
      return new Response('{}', { status: 200 });
    }
    throw new Error(`unexpected ${method} ${target}`);
  };
  return { calls, saved };
}

test('decrypts a Feishu AES callback', async () => {
  const key = 'test-key';
  const plain = JSON.stringify({ type: 'url_verification', challenge: 'abc' });
  const hash = crypto.createHash('sha256').update(key).digest();
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv('aes-256-cbc', hash, iv);
  const enc = Buffer.concat([iv, cipher.update(plain, 'utf8'), cipher.final()]).toString('base64');
  const decoded = await decryptFeishuEncrypt(enc, key);
  assert.equal(JSON.parse(decoded).challenge, 'abc');
});

test('url verification echoes the challenge', async () => {
  const res = await handleDriveReview(new Request('https://webhook.soundboxbooth.com/drive-review', {
    method: 'POST',
    body: JSON.stringify({ type: 'url_verification', challenge: 'ch_1', token: 't' }),
  }), {}, {});
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { challenge: 'ch_1' });
});

test('reject is stored and does not start a publish job', async () => {
  const original = globalThis.fetch;
  const doc = pendingDoc();
  const { calls, saved } = mockFetch(doc);
  const pending = [];
  try {
    const res = await handleDriveReview(new Request('https://webhook.soundboxbooth.com/drive-review', {
      method: 'POST',
      body: JSON.stringify({
        header: { event_type: 'card.action.trigger', app_id: 'cli_test' },
        event: {
          operator: { open_id: reviewerId },
          action: { value: { action: 'reject', reviewId: 'rv1' } },
        },
      }),
    }), {
      GITHUB_TOKEN: 'tok',
      GITHUB_REPO: 'pyyzheng/soundbox-lead-poller-public',
      FEISHU_APP_ID: 'cli_test',
      FEISHU_APP_SECRET: 'sec',
    }, { waitUntil(promise) { pending.push(promise); } });
    const body = await res.json();
    await Promise.all(pending);
    assert.match(body.toast.content, /不会发到群/);
    assert.equal(saved[0].reviews.rv1.status, 'rejected');
    assert.equal(saved[0].notified.tok, 1);
    assert.equal(calls.some((call) => call.url.endsWith('/dispatches')), false);
  } finally {
    globalThis.fetch = original;
  }
});

test('approve stores sending and dispatches the publish job', async () => {
  const original = globalThis.fetch;
  const { calls, saved } = mockFetch(pendingDoc());
  const pending = [];
  try {
    const res = await handleDriveReview(new Request('https://webhook.soundboxbooth.com/drive-review', {
      method: 'POST',
      body: JSON.stringify({
        header: { event_type: 'card.action.trigger', app_id: 'cli_test' },
        event: {
          operator: { open_id: reviewerId },
          action: { value: { action: 'approve', reviewId: 'rv1' } },
        },
      }),
    }), {
      GITHUB_TOKEN: 'tok',
      GITHUB_REPO: 'pyyzheng/soundbox-lead-poller-public',
      FEISHU_APP_ID: 'cli_test',
      FEISHU_APP_SECRET: 'sec',
    }, { waitUntil(promise) { pending.push(promise); } });
    const body = await res.json();
    await Promise.all(pending);
    assert.match(body.toast.content, /正在推送/);
    assert.equal(saved[0].reviews.rv1.status, 'sending');
    assert.equal(calls.filter((call) => call.url.endsWith('/dispatches')).length, 1);
  } finally {
    globalThis.fetch = original;
  }
});
