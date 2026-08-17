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
import { compressAnyUnderLimit, createZipArchive } from './compress-any.js';

const ONLINE_TYPES = new Set(['doc', 'docx', 'sheet', 'bitable', 'slides']);

/**
 * Notify with IM attachment.
 * Single files under the limit are sent directly; oversized files fall back
 * to compression/zip, while multi-file batches are packed into zip files.
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

  try {
    const prepared = await prepareLocalFile(
      client,
      config,
      { token: fileToken, type: fileType, name: titleHint },
      temps,
    );
    if (!prepared) {
      return { skipped: true, reason: 'empty' };
    }

    let localPath = prepared.localPath;
    let sendName = prepared.sendName;
    let size = prepared.size;
    let note = '';

    if (size > maxBytes) {
      console.log(`[compress] ${sendName} ${formatBytes(size)} > limit, trying all methods`);
      const compressed = await compressAnyUnderLimit(localPath, sendName, maxBytes, config.tmpDir);
      if (!compressed) {
        throw new Error(`${sendName} 压缩后仍超过 ${formatBytes(maxBytes)}`);
      }
      temps.push(compressed.localPath);
      localPath = compressed.localPath;
      sendName = compressed.fileName;
      note = `已自动压缩（${compressed.method}）：${formatBytes(size)} → ${formatBytes(compressed.size)}`;
      size = compressed.size;
    }

    const caption = composeMessage(config, resolveEventLabel(reason), [
      `文件：${sendName}`,
      `大小：${formatBytes(size)}`,
      ...(note ? [note] : []),
    ]);
    const sent = await sendTextMessage(client, config.chatId, caption);
    const fileKey = await uploadImFile(client, localPath, sendName);
    await sendFileMessage(client, config.chatId, fileKey);
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

/**
 * Download one or more Drive files, pack into a zip, then send caption + zip.
 * Splits into multiple zips if a single archive would exceed the IM size limit.
 */
export async function notifyFilesAsZip(client, config, {
  reason,
  files,
  zipNameHint,
  folderName,
}) {
  if (!files?.length) return { skipped: true, reason: 'empty' };

  fs.mkdirSync(config.tmpDir, { recursive: true });
  const maxBytes = config.maxFileBytes || 30 * 1024 * 1024;
  const temps = [];
  const entries = [];

  try {
    for (const item of files) {
      const prepared = await prepareLocalFile(client, config, item, temps);
      if (!prepared) continue;
      entries.push({
        localPath: prepared.localPath,
        entryName: prepared.sendName,
        originalSize: prepared.size,
      });
    }
    if (!entries.length) {
      throw new Error('没有可打包的文件');
    }

    const packs = await packEntriesIntoZips(entries, {
      maxBytes,
      tmpDir: config.tmpDir,
      zipNameHint: zipNameHint || folderName || entries[0].entryName,
      temps,
    });

    let lastMessageId;
    let totalSize = 0;
    for (let i = 0; i < packs.length; i += 1) {
      const pack = packs[i];
      totalSize += pack.size;
      const caption = buildZipCaption(config, {
        reason,
        pack,
        packIndex: i,
        packCount: packs.length,
        folderName,
      });
      const sent = await sendTextMessage(client, config.chatId, caption);
      const fileKey = await uploadImFile(client, pack.localPath, pack.fileName);
      await sendFileMessage(client, config.chatId, fileKey);
      lastMessageId = sent?.message_id;
      console.log(`[sent-zip] ${pack.fileName} ${formatBytes(pack.size)} (${pack.names.length} files)`);
    }

    return {
      skipped: false,
      messageId: lastMessageId,
      fileName: packs.map((p) => p.fileName).join(', '),
      size: totalSize,
      mode: 'zip',
      fileCount: entries.length,
      packCount: packs.length,
    };
  } finally {
    for (const f of temps) cleanup(f);
  }
}

async function prepareLocalFile(client, config, item, temps) {
  const fileToken = item.token;
  const fileType = item.type || 'file';
  const meta = await getFileMeta(client, fileToken, fileType).catch(() => null);
  const title = safeFileName(
    item.name || meta?.title || config.watchFiles.find((f) => f.token === fileToken)?.name,
    fileToken,
  );

  let localPath;
  let sendName = title;

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

  const size = fs.statSync(localPath).size;
  if (size <= 0) {
    console.warn(`[zip] skip empty file ${sendName}`);
    return null;
  }
  return { localPath, sendName, size };
}

async function packEntriesIntoZips(entries, { maxBytes, tmpDir, zipNameHint, temps }) {
  const stem = sanitizeZipStem(zipNameHint);
  const packs = [];
  let part = 1;

  const makeZip = async (batch) => {
    if (!batch.length) return;
    const suffix = entries.length > batch.length || part > 1 ? `-part${part}` : '';
    const outPath = path.join(tmpDir, `${stem}${suffix}-${Date.now()}.zip`);
    const packed = await createZipArchive(batch, outPath, tmpDir);
    if (!packed) throw new Error(`打包失败: ${batch.map((e) => e.entryName).join(', ')}`);
    temps.push(packed.localPath);

    if (packed.size <= maxBytes) {
      packs.push({
        localPath: packed.localPath,
        fileName: `${stem}${suffix}.zip`,
        size: packed.size,
        names: batch.map((e) => e.entryName),
      });
      part += 1;
      return;
    }

    // Archive too big: split the batch, or compress the lone remaining file
    if (batch.length === 1) {
      const one = batch[0];
      const compressed = await compressAnyUnderLimit(
        one.localPath,
        one.entryName,
        maxBytes,
        tmpDir,
      );
      if (!compressed) {
        throw new Error(`${one.entryName} 压缩后仍超过 ${formatBytes(maxBytes)}`);
      }
      temps.push(compressed.localPath);
      packs.push({
        localPath: compressed.localPath,
        fileName: compressed.fileName,
        size: compressed.size,
        names: [one.entryName],
        note: `单文件压缩（${compressed.method}）`,
      });
      part += 1;
      return;
    }

    const mid = Math.ceil(batch.length / 2);
    await makeZip(batch.slice(0, mid));
    await makeZip(batch.slice(mid));
  };

  // First-cut batches by raw size so we rarely need to split after zipping
  let batch = [];
  let batchBytes = 0;
  for (const entry of entries) {
    if (batch.length && batchBytes + (entry.originalSize || 0) > maxBytes * 0.9) {
      await makeZip(batch);
      batch = [];
      batchBytes = 0;
    }
    batch.push(entry);
    batchBytes += entry.originalSize || 0;
  }
  await makeZip(batch);
  return packs;
}

function sanitizeZipStem(name) {
  const raw = String(name || 'cloud-update')
    .replace(/\.[^.]+$/, '')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .trim();
  return (raw || 'cloud-update').slice(0, 80);
}

function buildZipCaption(config, { reason, pack, packIndex, packCount, folderName }) {
  const detail = [];
  if (folderName) detail.push(`文件夹：${folderName}`);
  detail.push(`压缩包：${pack.fileName}`);
  detail.push(`大小：${formatBytes(pack.size)}`);
  detail.push(`包含 ${pack.names.length} 个文件：`);
  const listed = pack.names.slice(0, 20);
  for (const n of listed) detail.push(`· ${n}`);
  if (pack.names.length > listed.length) {
    detail.push(`· …另有 ${pack.names.length - listed.length} 个文件`);
  }
  if (packCount > 1) detail.push(`（分卷 ${packIndex + 1}/${packCount}）`);
  if (pack.note) detail.push(pack.note);
  return composeMessage(config, resolveEventLabel(reason), detail);
}

/** A new folder has no downloadable body, so it only gets a text notice. */
export async function notifyFolderCreated(client, config, { folderName, parentName }) {
  const detail = [`文件夹：${folderName}`];
  if (parentName) detail.push(`位置：${parentName}`);
  const text = composeMessage(config, '文件夹有新上传/新建', detail);
  const sent = await sendTextMessage(client, config.chatId, text);
  console.log(`[sent-folder] ${folderName}`);
  return { messageId: sent?.message_id, mode: 'text' };
}

// 例：【云盘更新】文件内容已更新，麻烦安排超级图册更新。
function composeMessage(config, eventLabel, detailLines) {
  const prefix = config.captionPrefix || '【云盘更新】';
  const action = config.captionAction || '麻烦安排超级图册更新。';
  const lines = [`${prefix}${eventLabel}，${action}`, ...detailLines];
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
