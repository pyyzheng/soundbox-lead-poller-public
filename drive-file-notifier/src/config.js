import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

dotenv.config({ path: path.join(root, '.env') });

function configuredReviewers(review) {
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
  for (const person of review.reviewers || []) add(person);
  add(review.reviewer);
  return people;
}

export function loadConfig() {
  const raw = JSON.parse(
    fs.readFileSync(path.join(root, 'config.json'), 'utf8'),
  );

  const appId = process.env.APP_ID || process.env.FEISHU_APP_ID;
  const appSecret = process.env.APP_SECRET || process.env.FEISHU_APP_SECRET;
  if (!appId || !appSecret) {
    throw new Error('缺少 APP_ID / APP_SECRET，请复制 .env.example 为 .env 并填写');
  }

  const review = raw.review && typeof raw.review === 'object' ? raw.review : {};
  const reviewers = configuredReviewers(review);

  return {
    ...raw,
    appId,
    appSecret,
    chatId: process.env.CHAT_ID || raw.chatId,
    review: {
      enabled: review.enabled === true,
      reviewers,
      reviewer: reviewers[0] || { name: '', openId: '' },
    },
    staleRescanBudget: Number(raw.staleRescanBudget) || 300,
    root,
    tmpDir: path.join(root, 'tmp'),
  };
}
