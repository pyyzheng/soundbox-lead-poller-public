import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildReviewCard,
  decisionFromReactions,
  parseCardAction,
  pruneReviews,
  reviewMode,
} from './review-card.js';
import { decideReview, publishReview, submitReview } from './review.js';

const reviewer = { name: '郑培友', openId: 'ou_me' };
const config = {
  review: { enabled: true, reviewer },
  mentions: [{ name: '西米', openId: 'ou_ximi' }],
};

function pendingReview(status = 'pending') {
  return {
    id: 'rv1',
    status,
    createdAt: '2026-10-28T00:00:00.000Z',
    updatedAt: '2026-10-28T00:00:00.000Z',
    messageId: 'om_1',
    error: '',
    groupSent: false,
    tokens: ['file1'],
    spec: {
      kind: 'file',
      tokens: ['file1'],
      summary: {
        eventLabel: '文件夹有新上传/新建',
        folderPath: '画册',
        fileNames: ['a.pdf'],
        links: [],
      },
      payload: { fileToken: 'file1', fileType: 'file', reason: '文件夹有新上传/新建', titleHint: 'a.pdf' },
    },
  };
}

test('parseCardAction reads a flattened callback and a nested event', () => {
  assert.deepEqual(parseCardAction({
    operator: { open_id: 'ou_me' },
    action: { value: { action: 'approve', reviewId: 'rv1' } },
    context: { open_message_id: 'om_1' },
  }), {
    reviewId: 'rv1',
    decision: 'approve',
    operatorId: 'ou_me',
    messageId: 'om_1',
  });
  assert.equal(parseCardAction({
    event: {
      operator: { open_id: 'ou_me' },
      action: { value: '{"action":"reject","reviewId":"rv2"}' },
    },
  }).decision, 'reject');
});

test('review card exposes a clickable drive link for each file', () => {
  const review = pendingReview();
  review.spec.summary.files = [{
    name: 'a.pdf',
    token: 'file1',
    type: 'file',
    url: 'https://www.feishu.cn/file/file1',
  }];
  review.spec.summary.folder = {
    name: '画册',
    token: 'fold',
    url: 'https://www.feishu.cn/drive/folder/fold',
  };
  const card = JSON.stringify(buildReviewCard(review, config));
  assert.match(card, /\[a\.pdf\]\(https:\/\/www\.feishu\.cn\/file\/file1\)/);
  assert.match(card, /"type":"open_url"/);
  assert.match(card, /https:\/\/www\.feishu\.cn\/drive\/folder\/fold/);
  assert.match(card, /打开所在文件夹/);
});

test('pending card has approve and reject callbacks, decided card does not', () => {
  const pending = JSON.stringify(buildReviewCard(pendingReview(), config));
  assert.match(pending, /通过并推送/);
  assert.match(pending, /不通过/);
  assert.match(pending, /"action":"approve"/);
  assert.match(pending, /"reviewId":"rv1"/);
  const sent = JSON.stringify(buildReviewCard(pendingReview('sent'), config));
  assert.doesNotMatch(sent, /callback/);
  assert.match(sent, /已通过/);
});

test('reaction from the reviewer picks the latest decision', () => {
  const decision = decisionFromReactions([
    { operator: { operator_id: 'ou_me', operator_type: 'user' }, action_time: '10', reaction_type: { emoji_type: 'THUMBSUP' } },
    { operator: { operator_id: 'ou_me', operator_type: 'user' }, action_time: '20', reaction_type: { emoji_type: 'THUMBSDOWN' } },
    { operator: { operator_id: 'ou_other', operator_type: 'user' }, action_time: '30', reaction_type: { emoji_type: 'THUMBSUP' } },
  ], 'ou_me');
  assert.equal(decision, 'reject');
});

test('reviewMode stays off unless enabled with an open id', () => {
  assert.equal(reviewMode({}), 'off');
  assert.equal(reviewMode({ review: { enabled: true, reviewer: {} } }), 'misconfigured');
  assert.equal(reviewMode(config), 'on');
});

test('reject does not schedule a group send', async () => {
  const review = pendingReview();
  const runtime = {
    config,
    state: { reviews: { rv1: review } },
    persist() {},
  };
  const result = await decideReview(runtime, {
    reviewId: 'rv1',
    decision: 'reject',
    operatorId: 'ou_me',
  });
  assert.equal(review.status, 'rejected');
  assert.equal(result.schedulePublish, undefined);
  assert.match(result.toast.content, /不会发到群/);
});

test('米拉 can approve when she is a configured reviewer', async () => {
  const review = pendingReview();
  const cfg = {
    ...config,
    review: {
      enabled: true,
      reviewers: [
        { name: '郑培友', openId: 'ou_me' },
        { name: '米拉', openId: 'ou_mila' },
      ],
    },
  };
  const runtime = { config: cfg, state: { reviews: { rv1: review } }, persist() {} };
  assert.match(JSON.stringify(buildReviewCard(review, cfg)), /郑培友、米拉都可审核/);
  const result = await decideReview(runtime, {
    reviewId: 'rv1',
    decision: 'approve',
    operatorId: 'ou_mila',
  });
  assert.equal(result.schedulePublish, true);
  assert.equal(review.decidedByName, '米拉');
});

test('someone else cannot approve', async () => {
  const review = pendingReview();
  const runtime = { config, state: { reviews: { rv1: review } }, persist() {} };
  const result = await decideReview(runtime, {
    reviewId: 'rv1',
    decision: 'approve',
    operatorId: 'ou_other',
  });
  assert.equal(review.status, 'pending');
  assert.match(result.toast.content, /只有审核人/);
});

test('approve marks the review for publish and publish sends once', async () => {
  const review = pendingReview();
  const runtime = {
    client: {},
    config,
    state: { reviews: { rv1: review } },
    persist() {},
  };
  const decision = await decideReview(runtime, {
    reviewId: 'rv1',
    decision: 'approve',
    operatorId: 'ou_me',
  });
  assert.equal(decision.schedulePublish, true);
  assert.equal(review.status, 'sending');

  let sends = 0;
  await publishReview(runtime, 'rv1', {
    dispatchNotice: async () => {
      sends += 1;
    },
    patchCard: async () => {},
  });
  assert.equal(sends, 1);
  assert.equal(review.status, 'sent');
  await publishReview(runtime, 'rv1', {
    dispatchNotice: async () => {
      sends += 1;
    },
    patchCard: async () => {},
  });
  assert.equal(sends, 1);
});

test('submitReview sends a card to the reviewer and does not post to the group', async () => {
  const state = { reviews: {} };
  let card;
  const review = await submitReview({
    client: {},
    config,
    state,
    persist() {},
    spec: pendingReview().spec,
  }, null, {
    sendCard: async (_client, openId, nextCard) => {
      assert.equal(openId, 'ou_me');
      card = nextCard;
      return { message_id: 'om_new' };
    },
  });
  assert.equal(review.status, 'pending');
  assert.equal(review.messageId, 'om_new');
  assert.match(JSON.stringify(card), /通过并推送/);
  assert.equal(Object.keys(state.reviews).length, 1);
});

test('pruneReviews drops old terminal reviews and keeps pending ones', () => {
  const now = Date.parse('2026-10-28T00:00:00.000Z');
  const pruned = pruneReviews({
    old: { status: 'sent', updatedAt: '2026-10-01T00:00:00.000Z' },
    fresh: { status: 'rejected', updatedAt: '2026-10-27T00:00:00.000Z' },
    wait: { status: 'pending', updatedAt: '2026-09-01T00:00:00.000Z' },
  }, now);
  assert.deepEqual(Object.keys(pruned).sort(), ['fresh', 'wait']);
});
