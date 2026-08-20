import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

dotenv.config({ path: path.join(root, '.env') });

export function loadConfig() {
  const raw = JSON.parse(
    fs.readFileSync(path.join(root, 'config.json'), 'utf8'),
  );

  const appId = process.env.APP_ID || process.env.FEISHU_APP_ID;
  const appSecret = process.env.APP_SECRET || process.env.FEISHU_APP_SECRET;
  if (!appId || !appSecret) {
    throw new Error('缺少 APP_ID / APP_SECRET，请复制 .env.example 为 .env 并填写');
  }

  return {
    ...raw,
    appId,
    appSecret,
    chatId: process.env.CHAT_ID || raw.chatId,
    staleRescanBudget: Number(raw.staleRescanBudget) || 300,
    root,
    tmpDir: path.join(root, 'tmp'),
  };
}
