import { loadConfig } from './config.js';
import { createClient } from './feishu.js';
import { publishReview } from './review.js';
import { mergeRemoteReviewsInto } from './remote-notified.js';

const reviewId = process.env.REVIEW_ID || process.argv[2] || '';

async function main() {
  if (!reviewId) {
    console.error('[publish] missing REVIEW_ID');
    process.exit(1);
  }
  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);
  const state = { reviews: {} };
  await mergeRemoteReviewsInto(state);
  const review = state.reviews[reviewId];
  if (!review) {
    console.error(`[publish] ${reviewId} not found`);
    process.exit(1);
  }
  if (review.groupSent || review.status === 'sent' || review.status === 'rejected') {
    console.log(`[publish] ${reviewId} already ${review.status}, skip`);
    return;
  }
  if (review.status !== 'sending' && review.status !== 'send_failed') {
    console.log(`[publish] ${reviewId} status=${review.status}, skip`);
    return;
  }
  const runtime = {
    client,
    config,
    state,
    persist() {},
  };
  const result = await publishReview(runtime, reviewId);
  const status = state.reviews[reviewId]?.status || '-';
  console.log(`[publish] ${reviewId} result=${result || 'unknown'} status=${status}`);
  if (result === 'failed') process.exit(1);
}

main().catch((err) => {
  console.error('[publish] fatal', err);
  process.exit(1);
});
