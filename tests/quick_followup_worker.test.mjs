import assert from 'node:assert/strict';
import test from 'node:test';
import worker from '../facebook-lead-webhook/src/index.js';

const appToken = 'ZpbUb7SP7azsNasniFjc0bWSnHg';
const leadTable = 'tbluuuXn9WexH8LV';
const followupTable = 'tbl3n8TTJYXHG12q';
const quickField = 'Quick Follow-up / 快捷跟进';
const fixedNow = Date.parse('2026-11-16T16:30:00Z'); // 2026/11/17 in Shanghai.
const env = {
  ADMIN_TOKEN: 'test-admin-token',
  FEISHU_APP_ID: 'test-app-id',
  FEISHU_APP_SECRET: 'test-app-secret',
  FEISHU_APP_TOKEN: appToken,
  FEISHU_TABLE_ID: leadTable,
};

function feishu(data) {
  return Response.json({ code: 0, data });
}

async function withFixture(options, run) {
  const recordId = options.recordId;
  const leadUrl = `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables/${leadTable}/records/${recordId}`;
  const followupUrl = `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables/${followupTable}/records`;
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const originalError = console.error;
  const state = {
    quick: options.quick,
    quickWrites: 0,
    searches: 0,
    created: [],
    errors: [],
  };

  try {
    Date.now = () => fixedNow;
    console.error = (...args) => state.errors.push(args.join(' '));
    globalThis.fetch = async (url, init = {}) => {
      const target = String(url);
      const method = init.method || 'GET';
      if (target.endsWith('/auth/v3/tenant_access_token/internal')) {
        return Response.json({ code: 0, tenant_access_token: 'test-tenant-token', expire: 7200 });
      }
      if (target === leadUrl && method === 'GET') {
        return feishu({ record: { record_id: recordId, fields: { [quickField]: state.quick } } });
      }
      if (target === leadUrl && method === 'PUT') {
        state.quickWrites++;
        state.quick = JSON.parse(init.body).fields[quickField];
        return feishu({ record: { record_id: recordId } });
      }
      if (target === `${followupUrl}/search` && method === 'POST') {
        state.searches++;
        const items = options.staleSearch ? [] : state.created.map((row) => ({
          record_id: row.record_id,
          fields: {
            ...row.fields,
            '跟进摘要': `${row.fields['Follow-up Details'] === 'Earlier call' ? '2026/11/10' : '2026/11/17'} | ${row.fields['Follow-up Details']}`,
          },
        }));
        return feishu({ items, has_more: false });
      }
      if (target === followupUrl && method === 'POST') {
        if (options.failCreate) {
          return Response.json({ code: 1255001, msg: 'test create failure' });
        }
        const row = {
          record_id: `rec_followup_${state.created.length + 1}`,
          fields: JSON.parse(init.body).fields,
        };
        state.created.push(row);
        return feishu({ record: { record_id: row.record_id } });
      }
      throw new Error(`Unexpected ${method} ${target}`);
    };

    const invoke = async () => {
      const response = await worker.fetch(new Request('https://webhook.soundboxbooth.com/sync-quick-followup', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${env.ADMIN_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ direction: 'from_quick', lead_record_id: recordId }),
      }), env, {
        waitUntil() { throw new Error('Quick sync must finish before HTTP response'); },
      });
      return { status: response.status, body: await response.json() };
    };
    await run(state, invoke);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
    console.error = originalError;
  }
}

test('Quick Follow-up Worker', async (t) => {
  await t.test('new records visible immediately: stamp Shanghai date and keep earlier date', async () => {
    await withFixture({
      recordId: 'rec_fresh',
      quick: 'Need proposal\n2026/11/10 | Earlier call',
    }, async (state, invoke) => {
      const result = await invoke();
      assert.equal(result.status, 200);
      assert.equal(result.body.ok, true);
      assert.equal(state.quick, '2026/11/17 | Need proposal\n2026/11/10 | Earlier call');
      assert.equal(state.quickWrites, 1);
      assert.equal(state.created.length, 2);
      const proposal = state.created.find((row) => row.fields['Follow-up Details'] === 'Need proposal');
      const earlier = state.created.find((row) => row.fields['Follow-up Details'] === 'Earlier call');
      assert.equal(proposal.fields['Follow-up Time'], Date.parse('2026-11-17T12:00:00+08:00'));
      assert.equal(earlier.fields['Follow-up Time'], Date.parse('2026-11-10T12:00:00+08:00'));
      assert.ok(state.created.every((row) => row.fields['Related Lead'][0] === 'rec_fresh'));
    });
  });

  await t.test('stale post-create search cannot erase the dated Quick text', async () => {
    await withFixture({
      recordId: 'rec_stale',
      quick: 'Need proposal\n2026/11/10 | Earlier call',
      staleSearch: true,
    }, async (state, invoke) => {
      const result = await invoke();
      assert.equal(result.status, 200);
      assert.equal(result.body.ok, true);
      assert.equal(state.quick, '2026/11/17 | Need proposal\n2026/11/10 | Earlier call');
      assert.equal(state.quickWrites, 1);
      assert.equal(state.created.length, 2);
    });
  });

  await t.test('repeated trigger does not create duplicate Follow-up Records', async () => {
    await withFixture({ recordId: 'rec_retry', quick: 'Call customer' }, async (state, invoke) => {
      assert.equal((await invoke()).status, 200);
      assert.equal((await invoke()).status, 200);
      assert.equal(state.quick, '2026/11/17 | Call customer');
      assert.equal(state.quickWrites, 1);
      assert.equal(state.created.length, 1);
    });
  });

  await t.test('Follow-up create failure returns HTTP 500 and leaves Quick unchanged', async () => {
    await withFixture({
      recordId: 'rec_create_failure',
      quick: 'Call customer',
      failCreate: true,
    }, async (state, invoke) => {
      const result = await invoke();
      assert.equal(result.status, 500);
      assert.equal(result.body.ok, false);
      assert.match(result.body.error, /create followup failed/);
      assert.equal(state.quick, 'Call customer');
      assert.equal(state.quickWrites, 0);
      assert.equal(state.created.length, 0);
    });
  });
});
