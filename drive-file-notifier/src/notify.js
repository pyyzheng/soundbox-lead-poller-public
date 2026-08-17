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
 * Notify with IM attachment.
 * Send caption and file as two independent messages (@mentions last in caption).
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

    const caption = buildCaption(config, {
      reason,
      sendName,
      size,
      note,
    });

    const fileKey = await uploadImFile(client, localPath, sendName);
    // 文案与附件分开发送；@ 在文案末尾
    const sent = await sendTextMessage(client, config.chatId, caption);
    await sendFileMessage(client, config.chatId, fileKey);
    console.log(`[sent-caption+file] ${sendName} ${formatBytes(size)}`);

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
  const text = buildCaption(config, {
    reason,
    sendName: title,
    size,
    note: `\n${detail}`,
  });
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

function buildCaption(config, { reason, sendName, size, note = '' }) {
  const prefix = config.captionPrefix || '【云盘更新】';
  const action = config.captionAction || '麻烦安排超级图册更新。';
  const eventLine = resolveEventLabel(reason);
  // 例：【云盘更新】文件内容已更新，麻烦安排超级图册更新。
  const lines = [
    `${prefix}${eventLine}，${action}`,
    `文件：${sendName}`,
    `大小：${formatBytes(size)}${note || ''}`,
  ];
  const mentionLine = formatMentions(config.mentions);
  if (mentionLine) lines.push(mentionLine);
  return lines.join('\n');
}

function resolveEventLabel(reason = '') {
  if (/新上传|新建|new/i.test(reason)) return '文件夹有新上传/新建';
  if (/更新|changed|内容/i.test(reason)) return '文件内容已更新';
  // 手动测试等兜底：按「内容更新」表述
  return reason || '文件内容已更新';
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
