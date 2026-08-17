import fs from 'node:fs';
import path from 'node:path';
import {
  downloadDriveFile,
  exportOnlineDoc,
  getFileMeta,
  safeFileName,
  sendFileMessage,
  sendTextMessage,
  subscribeResource,
  uploadImFile,
} from './feishu.js';
import { compressAnyUnderLimit } from './compress-any.js';

const ONLINE_TYPES = new Set(['doc', 'docx', 'sheet', 'bitable', 'slides']);

/**
 * Notify with IM attachment:
 * - ≤30MB: send original as attachment
 * - >30MB: compress any type (PDF/image quality, else zip); if still >30MB → text only
 * Always @ configured mentions.
 */
export async function notifyFileAttachment(client, config, {
  fileToken,
  fileType,
  reason,
  titleHint,
}) {
  fs.mkdirSync(config.tmpDir, { recursive: true });
  const maxBytes = config.maxFileBytes || 30 * 1024 * 1024;
  const temps = [];

  const meta = await getFileMeta(client, fileToken, fileType).catch(() => null);
  const title = safeFileName(
    titleHint || meta?.title || config.watchFiles.find((f) => f.token === fileToken)?.name,
    fileToken,
  );

  let localPath;
  let sendName = title;

  try {
    if (ONLINE_TYPES.has(fileType)) {
      const exported = await exportOnlineDoc(client, fileToken, fileType, config.tmpDir);
      localPath = exported.localPath;
      temps.push(localPath);
      if (!path.extname(sendName)) {
        sendName = `${sendName}.${exported.exportType}`;
      } else {
        sendName = `${path.basename(sendName, path.extname(sendName))}.${exported.exportType}`;
      }
    } else {
      const extFromTitle = path.extname(title);
      localPath = path.join(config.tmpDir, `${fileToken}${extFromTitle || ''}`);
      await downloadDriveFile(client, fileToken, localPath);
      temps.push(localPath);
      sendName = title.includes('.') ? title : path.basename(localPath);
    }

    let size = fs.statSync(localPath).size;
    if (size <= 0) {
      throw new Error(`文件为空: ${sendName}`);
    }

    let note = '';
    if (size > maxBytes) {
      console.log(`[compress] ${sendName} ${formatBytes(size)} > limit, trying all methods`);
      const compressed = await compressAnyUnderLimit(
        localPath,
        sendName,
        maxBytes,
        config.tmpDir,
      );
      if (!compressed) {
        return sendTextOnly(client, config, {
          reason,
          title: sendName,
          size,
          detail: `原大小 ${formatBytes(size)}，自动压缩后仍超过 30MB，请人工处理。`,
        });
      }
      temps.push(compressed.localPath);
      localPath = compressed.localPath;
      sendName = compressed.fileName;
      note = `\n已自动压缩（${compressed.method}）：${formatBytes(size)} → ${formatBytes(compressed.size)}`;
      size = compressed.size;
    }

    const mentionLine = formatMentions(config.mentions);
    const caption = [
      mentionLine,
      `${config.captionPrefix}${reason}`,
      `文件：${sendName}`,
      `大小：${formatBytes(size)}${note}`,
    ].filter(Boolean).join('\n');

    await sendTextMessage(client, config.chatId, caption);
    const fileKey = await uploadImFile(client, localPath, sendName);
    const sent = await sendFileMessage(client, config.chatId, fileKey);
    console.log(`[sent-file] ${sendName} ${formatBytes(size)}`);

    return {
      skipped: false,
      messageId: sent?.message_id,
      fileName: sendName,
      size,
      mode: 'attachment',
    };
  } finally {
    for (const f of temps) cleanup(f);
  }
}

async function sendTextOnly(client, config, { reason, title, size, detail }) {
  const mentionLine = formatMentions(config.mentions);
  const text = [
    mentionLine,
    `${config.captionPrefix}${reason}`,
    `文件：${title}`,
    detail,
  ].filter(Boolean).join('\n');
  const sent = await sendTextMessage(client, config.chatId, text);
  console.log(`[sent-text-only] ${title} ${formatBytes(size)}`);
  return {
    skipped: true,
    reason: 'too_large',
    messageId: sent?.message_id,
    fileName: title,
    size,
    mode: 'text',
  };
}

function formatMentions(mentions) {
  if (!Array.isArray(mentions) || !mentions.length) return '';
  return mentions
    .filter((m) => m?.openId)
    .map((m) => `<at user_id="${m.openId}">${m.name || '用户'}</at>`)
    .join(' ');
}

function cleanup(filePath) {
  try {
    if (filePath && fs.existsSync(filePath)) fs.unlinkSync(filePath);
  } catch {
    // ignore
  }
}

function formatBytes(n) {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

export async function maybeAutoSubscribe(client, config, fileToken, fileType) {
  if (!config.autoSubscribeNewFiles) return;
  if (!fileType || fileType === 'folder' || fileType === 'shortcut') return;
  try {
    await subscribeResource(client, fileToken, fileType);
    console.log(`[subscribe] auto-subscribed ${fileType}:${fileToken}`);
  } catch (err) {
    console.warn(`[subscribe] auto-subscribe failed ${fileType}:${fileToken}: ${err.message}`);
  }
}
