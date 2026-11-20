import assert from 'node:assert/strict';
import { createCipheriv, createHash, randomBytes } from 'node:crypto';
import test from 'node:test';
import { handleDriveReview } from './drive-review.js';

const worker = 'https://worker.example/drive-review';
const backend = 'https://api.soundbox-pod.com/soundbox/showrooms/feishu/card';
const env = { FEISHU_APP_ID: 'cli_fixture1234', FEISHU_APP_SECRET: 'fixture-secret', FEISHU_VERIFICATION_TOKEN: 'fixture-token' };
const ok = { toast: { type: 'success', content: '已处理' } };
const authCard = { toast: { type: 'info', content: '请授权' },
  card: { type: 'raw', data: { schema: '2.0', body: { elements: [] } } } };
const verified = body => Response.json(body, { headers: { 'X-Sbx-Showroom-Callback': 'verified' } });
function payload(action) {
  return {
    header: { event_type: 'card.action.trigger', app_id: env.FEISHU_APP_ID, token: 'fixture-token' },
    event: {
      context: { open_message_id: 'om_fixture_msg' }, operator: { open_id: 'ou_fixture1234' },
      action: { tag: 'button', value: { action, noticeId: '00112233445566778899aabbccddeeff' },
        form_value: { remark: '第 1 行\n第 2 行' } },
    },
  };
}

test('GET reports the installed showroom route without taking any action', async t => {
  t.mock.method(globalThis, 'fetch', () => assert.fail('unexpected network'));
  const response = await handleDriveReview(new Request(worker), {}, {});
  assert.deepEqual(await response.json(), { ok: true, service: 'drive-review', showroomRouter: 1 });
});

for (const action of ['approve_booking', 'cancel_booking', 'add_calendar', 'confirm_calendar']) {
  test(`forwards ${action} with the exact body and only Feishu authentication headers`, async t => {
    const data = payload(action);
    if (action === 'confirm_calendar') data.event.action.value = JSON.stringify(data.event.action.value);
    const raw = JSON.stringify(data, null, 1) + '\n';
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, opts) => {
      calls.push(url);
      assert.equal(opts.method, 'POST');
      assert.equal(opts.redirect, 'error');
      assert.deepEqual([...opts.headers.keys()].sort(), [
        'content-type', 'x-lark-request-nonce', 'x-lark-request-timestamp', 'x-lark-signature',
      ]);
      assert.equal(opts.headers.get('X-Lark-Signature'), 'sig');
      assert.deepEqual(Buffer.from(opts.body), Buffer.from(raw));
      return verified(ok);
    });
    const res = await handleDriveReview(new Request(worker, { method: 'POST', body: raw,
      headers: { 'X-Lark-Signature': 'sig', 'X-Lark-Request-Timestamp': '123', 'X-Lark-Request-Nonce': 'abc',
        'X-Sbx-Relay-Signature': 'spoofed', Authorization: 'ignored', Cookie: 'ignored' } }), env,
    { waitUntil: () => assert.fail('fast response should return synchronously') });
    assert.deepEqual(await res.json(), ok);
    assert.deepEqual(calls, [backend]);
  });
}

test('original encrypted envelope survives dispatch', async t => {
  const key = 'encrypt-fixture';
  const iv = randomBytes(16);
  const cipher = createCipheriv('aes-256-cbc', createHash('sha256').update(key).digest(), iv);
  const encrypted = Buffer.concat([iv, cipher.update(JSON.stringify(payload('approve_booking'))), cipher.final()]);
  const raw = JSON.stringify({ encrypt: encrypted.toString('base64') });
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    assert.equal(url, backend);
    assert.deepEqual(Buffer.from(opts.body), Buffer.from(raw));
    return verified(ok);
  });
  const timestamp = String(Math.floor(Date.now() / 1000)), nonce = 'nonce';
  const signature = createHash('sha256').update(timestamp + nonce + key + raw).digest('hex');
  const headers = { 'X-Lark-Request-Timestamp': timestamp, 'X-Lark-Request-Nonce': nonce, 'X-Lark-Signature': signature };
  const response = await handleDriveReview(new Request(worker, { method: 'POST', body: raw, headers }),
    { ...env, FEISHU_ENCRYPT_KEY: key }, {});
  assert.deepEqual(await response.json(), ok);
});

test('missing config, missing or wrong token and invalid or stale signature never reach the website', async t => {
  const network = t.mock.method(globalThis, 'fetch', () => assert.fail('unauthenticated callback reached the network'));
  const withoutToken = { ...env, FEISHU_VERIFICATION_TOKEN: '' };
  const rawData = payload('cancel_booking');
  delete rawData.header.token;
  const raw = JSON.stringify(rawData);
  const key = 'fixture-encrypt';
  const now = String(Math.floor(Date.now() / 1000));
  function signed(timestamp, signedRaw = raw) {
    return { 'X-Lark-Request-Timestamp': timestamp, 'X-Lark-Request-Nonce': 'n',
      'X-Lark-Signature': createHash('sha256').update(timestamp + 'n' + key + signedRaw).digest('hex') };
  }
  for (const [config, headers, body] of [
    [withoutToken, {}, raw],
    [env, {}, raw],
    [env, {}, raw.replace('"event_type"', '"token":"wrong","event_type"')],
    [{ ...withoutToken, FEISHU_ENCRYPT_KEY: key }, {}, raw],
    [{ ...withoutToken, FEISHU_ENCRYPT_KEY: key }, signed(now, raw + ' '), raw],
    [{ ...withoutToken, FEISHU_ENCRYPT_KEY: key }, signed(String(Number(now) - 700)), raw],
    [{ ...env, FEISHU_ENCRYPT_KEY: key }, signed(now), raw],
    [{ ...env, FEISHU_ENCRYPT_KEY: key }, {}, JSON.stringify(payload('cancel_booking'))],
  ]) {
    const resp = await handleDriveReview(new Request(worker, { method: 'POST', body, headers }), config, {});
    assert.equal((await resp.json()).toast.type, 'error');
  }
  assert.equal(network.mock.callCount(), 0);
});

test('valid signature alone authenticates a tokenless Feishu callback', async t => {
  const key = 'fixture-key';
  const data = payload('approve_booking');
  delete data.header.token;
  const raw = JSON.stringify(data);
  const ts = String(Math.floor(Date.now() / 1000));
  const headers = { 'X-Lark-Request-Timestamp': ts, 'X-Lark-Request-Nonce': 'n',
    'X-Lark-Signature': createHash('sha256').update(ts + 'n' + key + raw).digest('hex') };
  t.mock.method(globalThis, 'fetch', async url => { assert.equal(url, backend); return verified(ok); });
  const res = await handleDriveReview(new Request(worker, { method: 'POST', body: raw, headers }),
    { ...env, FEISHU_ENCRYPT_KEY: key, FEISHU_VERIFICATION_TOKEN: '' }, {});
  assert.deepEqual(await res.json(), ok);
});

test('website auth configuration failure returns an actionable card message', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('unavailable', { status: 503 }));
  const res = await handleDriveReview(new Request(worker, { method: 'POST', body: JSON.stringify(payload('cancel_booking')) }), env, {});
  assert.match((await res.json()).toast.content, /网站服务和飞书回调验证配置/);
});

test('malformed showroom identity is rejected before traffic', async t => {
  const network = t.mock.method(globalThis, 'fetch', () => assert.fail('unexpected network'));
  for (const change of [
    data => { data.event.action.value.noticeId = 'invalid'; },
    data => { data.header.app_id = 'cli_another12'; },
    data => { delete data.event.context.open_message_id; },
    data => { data.event.action.value.action = 'other'; },
    data => { data.header.event_type = 'other'; },
    data => { data.event.action.value = '{noticeId: malformed}'; },
  ]) {
    const data = payload('cancel_booking');
    change(data);
    const response = await handleDriveReview(new Request(worker, { method: 'POST', body: JSON.stringify(data) }), env, {});
    assert.equal((await response.json()).toast.type, 'error');
  }
  assert.equal(network.mock.callCount(), 0);
});

for (const outcome of ['authorization', 'declined', 'unauthorized', 'legacy-backend']) {
  test(`delayed ${outcome} finishes in waitUntil, sends only authenticated results`, async t => {
    const calls = [], pending = [];
    let complete;
    t.mock.method(globalThis, 'fetch', async (url, opts) => {
      calls.push({ url, opts });
      if (url === backend) return new Promise(resolve => { complete = resolve; });
      if (url.endsWith('/tenant_access_token/internal')) return Response.json({ code: 0, tenant_access_token: 'test-token' });
      return Response.json({ code: 0 });
    });
    const data = payload('approve_booking');
    const response = await handleDriveReview(new Request(worker, { method: 'POST', body: JSON.stringify(data) }),
      env, { waitUntil(promise) { pending.push(promise); } });
    assert.equal((await response.json()).toast.type, 'info');
    assert.equal(pending.length, 1);
    assert.equal(calls.length, 1);
    if (outcome === 'authorization') complete(verified(authCard));
    else if (outcome === 'declined') complete(verified({ toast: { type: 'error', content: '预约已处理' } }));
    else if (outcome === 'unauthorized') complete(new Response('unauthorized', { status: 401 }));
    else complete(Response.json(authCard));
    await Promise.all(pending);
    if (outcome === 'unauthorized' || outcome === 'legacy-backend') {
      assert.equal(calls.length, 1);
      return;
    }
    assert.equal(calls.length, 3);
    const sent = JSON.parse(calls[2].opts.body);
    assert.equal(calls[2].opts.headers.Authorization, 'Bearer test-token');
    if (outcome === 'authorization') {
      assert.match(calls[2].url, /\/om_fixture_msg$/);
      assert.equal(calls[2].opts.method, 'PATCH');
      assert.deepEqual(JSON.parse(sent.content), authCard.card.data);
    } else {
      assert.equal(sent.receive_id, data.event.operator.open_id);
      assert.match(JSON.parse(sent.content).text, /预约已处理/);
    }
  });
}
