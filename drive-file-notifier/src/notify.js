import fs from 'node:fs';
import path from 'node:path';
import {
  downloadDriveFile,
  exportOnlineDoc,
  getFileMeta,
  probeDriveFileSize,
  resolveDriveFileUrl,
  safeFileName,
  sendFileMessage,
  sendTextMessage,
  subscribeResource,
  uploadImFile,
  guessImMsgType,
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
  folderPath,
  folderToken,
  driveUrl,
}) {
  fs.mkdirSync(config.tmpDir, { recursive: true });
  const maxBytes = config.maxFileBytes || 30 * 1024 * 1024;
  const temps = [];

  try {
    const metaHint = await getFileMeta(client, fileToken, fileType).catch(() => null);
    const metaForLink = { ...metaHint, url: driveUrl || metaHint?.url };
    const hintedSize = Number(metaHint?.size) || await probeDriveFileSize(client, fileToken).catch(() => 0);
    if (hintedSize > maxBytes) {
      return notifyOversizedLink(client, config, {
        fileToken,
        fileType,
        reason,
        titleHint: titleHint || metaHint?.title,
        folderPath,
        sizeBytes: hintedSize,
        meta: metaForLink,
      });
    }

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
        console.log(`[oversized] ${sendName} still over limit after compress, sending link only`);
        return notifyOversizedLink(client, config, {
          fileToken,
          fileType,
          reason,
          titleHint: prepared.sendName,
          folderPath,
          sizeBytes: prepared.size,
          meta: { ...prepared.meta, url: driveUrl || prepared.meta?.url },
        });
      }
      temps.push(compressed.localPath);
      localPath = compressed.localPath;
      sendName = compressed.fileName;
      note = `已自动压缩（${compressed.method}）：${formatBytes(prepared.size)} → ${formatBytes(compressed.size)}`;
      size = compressed.size;
    }

    const folderUrl = await resolveFolderUrl(client, folderToken);
    const caption = composeMessage(config, resolveEventLabel(reason), [
      ...(folderPath ? [`文件夹：${folderPath}`] : []),
      `文件：${sendName}`,
      `大小：${formatBytes(size)}`,
      ...(note ? [note] : []),
      ...(folderUrl ? [`链接：${folderUrl}`] : []),
    ]);
    const sent = await sendTextMessage(client, config.chatId, caption);
    const fileKey = await uploadImFile(client, localPath, sendName);
    await sendFileMessage(client, config.chatId, fileKey, { msgType: guessImMsgType(sendName) });
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
 * Same-folder batch: pack files into one zip when it stays under the IM limit.
 * Files that cannot fit (or are already oversized) are sent as separate messages.
 * Different folders are handled by the caller (one call per folder).
 */
export async function notifyFilesAsZip(client, config, {
  reason,
  files,
  zipNameHint,
  folderName,
  folderToken,
}) {
  if (!files?.length) return { skipped: true, reason: 'empty' };

  fs.mkdirSync(config.tmpDir, { recursive: true });
  const maxBytes = config.maxFileBytes || 30 * 1024 * 1024;
  const temps = [];
  const oversized = [];
  const packable = [];

  try {
    for (const item of files) {
      const fileType = item.type || 'file';
      const meta = await getFileMeta(client, item.token, fileType).catch(() => null);
      const title = safeFileName(item.name || meta?.title, item.token);
      const hintedSize = Number(meta?.size)
        || await probeDriveFileSize(client, item.token).catch(() => 0);
      if (hintedSize > maxBytes) {
        console.log(`[zip] oversized skip download ${title} ${formatBytes(hintedSize)}`);
        oversized.push({
          token: item.token,
          type: fileType,
          name: title,
          size: hintedSize,
          meta,
        });
        continue;
      }

      const prepared = await prepareLocalFile(client, config, { ...item, name: title }, temps);
      if (!prepared) continue;
      packable.push({
        token: item.token,
        type: fileType,
        localPath: prepared.localPath,
        entryName: prepared.sendName,
        originalSize: prepared.size,
        meta: prepared.meta,
      });
    }
    if (!packable.length && !oversized.length) {
      throw new Error('没有可打包的文件');
    }

    const { pack, leftovers } = await packOneZipIfFits(packable, {
      maxBytes,
      tmpDir: config.tmpDir,
      zipNameHint: zipNameHint || folderName || packable[0]?.entryName || 'cloud-update',
      temps,
    });

    let lastMessageId;
    let totalSize = 0;
    let packCount = 0;
    const folderUrl = await resolveFolderUrl(client, folderToken);

    if (pack) {
      totalSize += pack.size;
      packCount = 1;
      const caption = buildZipCaption(config, {
        reason,
        pack,
        folderName,
        folderPath: folderName,
        folderUrl,
      });
      const sent = await sendTextMessage(client, config.chatId, caption);
      const fileKey = await uploadImFile(client, pack.localPath, pack.fileName);
      await sendFileMessage(client, config.chatId, fileKey, { msgType: guessImMsgType(pack.fileName) });
      lastMessageId = sent?.message_id;
      console.log(`[sent-zip] ${pack.fileName} ${formatBytes(pack.size)} (${pack.names.length} files)`);
    }

    for (const item of leftovers) {
      const sent = await notifyFileAttachment(client, config, {
        fileToken: item.token,
        fileType: item.type,
        reason,
        titleHint: item.entryName,
        folderPath: folderName,
        folderToken,
        driveUrl: item.meta?.url,
      });
      lastMessageId = sent?.messageId || lastMessageId;
    }

    for (const item of oversized) {
      const sent = await notifyOversizedLink(client, config, {
        fileToken: item.token,
        fileType: item.type,
        reason,
        titleHint: item.name,
        folderPath: folderName,
        sizeBytes: item.size,
        meta: item.meta,
      });
      lastMessageId = sent?.messageId || lastMessageId;
    }

    return {
      skipped: false,
      messageId: lastMessageId,
      fileName: [
        ...(pack ? [pack.fileName] : []),
        ...leftovers.map((e) => e.entryName),
        ...oversized.map((e) => e.name),
      ].join(', '),
      size: totalSize,
      mode: packCount && !leftovers.length && !oversized.length ? 'zip' : 'mixed',
      fileCount: packable.length + oversized.length,
      packCount,
      linkOnlyCount: oversized.length,
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
  return { localPath, sendName, size, meta };
}

/** Text-only notice when attachment cannot be sent within the IM size limit. */
export async function notifyOversizedLink(client, config, {
  fileToken,
  fileType,
  reason,
  titleHint,
  folderPath,
  sizeBytes,
  meta: metaIn,
}) {
  const meta = metaIn || await getFileMeta(client, fileToken, fileType).catch(() => null);
  const fileName = safeFileName(titleHint || meta?.title, fileToken);
  const driveUrl = resolveDriveFileUrl(meta, fileToken, fileType);
  const caption = composeMessage(config, resolveEventLabel(reason), [
    ...(folderPath ? [`文件夹：${folderPath}`] : []),
    `文件：${fileName}`,
    ...(sizeBytes ? [`大小：${formatBytes(sizeBytes)}`] : []),
    '说明：文件过大请去云盘查看',
    `链接：${driveUrl}`,
  ]);
  const sent = await sendTextMessage(client, config.chatId, caption);
  console.log(`[sent-link] ${fileName} ${sizeBytes ? formatBytes(sizeBytes) : ''}`);
  return {
    skipped: false,
    messageId: sent?.message_id,
    fileName,
    size: sizeBytes || 0,
    mode: 'link-only',
    driveUrl,
  };
}

/** Pack as many same-folder files as fit in a single <30MB zip. The rest stay leftovers. */
async function packOneZipIfFits(entries, { maxBytes, tmpDir, zipNameHint, temps }) {
  if (entries.length < 2) return { pack: null, leftovers: entries };

  const stem = sanitizeZipStem(zipNameHint);
  const tryZip = async (batch) => {
    const outPath = path.join(tmpDir, `${stem}-${Date.now()}.zip`);
    const packed = await createZipArchive(batch, outPath, tmpDir);
    if (!packed) throw new Error(`打包失败: ${batch.map((e) => e.entryName).join(', ')}`);
    temps.push(packed.localPath);
    return packed;
  };

  const sorted = [...entries].sort((a, b) => (a.originalSize || 0) - (b.originalSize || 0));
  const batch = [];
  let batchBytes = 0;
  const leftovers = [];
  for (const entry of sorted) {
    if (batchBytes + (entry.originalSize || 0) <= maxBytes * 0.9) {
      batch.push(entry);
      batchBytes += entry.originalSize || 0;
    } else {
      leftovers.push(entry);
    }
  }

  if (batch.length < 2) {
    console.log(`[zip] no same-folder batch under ${formatBytes(maxBytes)}, sending files separately`);
    return { pack: null, leftovers: entries };
  }

  const packed = await tryZip(batch);
  if (packed.size <= maxBytes) {
    return {
      pack: {
        localPath: packed.localPath,
        fileName: `${stem}.zip`,
        size: packed.size,
        names: batch.map((e) => e.entryName),
      },
      leftovers,
    };
  }

  console.log(`[zip] ${stem}.zip ${formatBytes(packed.size)} over limit, sending files separately`);
  return { pack: null, leftovers: entries };
}

function sanitizeZipStem(name) {
  const raw = String(name || 'cloud-update')
    .replace(/\.[^.]+$/, '')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .trim();
  return (raw || 'cloud-update').slice(0, 80);
}

function buildZipCaption(config, { reason, pack, folderName, folderPath, folderUrl }) {
  const detail = [];
  if (folderPath || folderName) detail.push(`文件夹：${folderPath || folderName}`);
  detail.push(`压缩包：${pack.fileName}`);
  detail.push(`大小：${formatBytes(pack.size)}`);
  detail.push(`包含 ${pack.names.length} 个文件：`);
  const listed = pack.names.slice(0, 20);
  for (const n of listed) detail.push(`· ${n}`);
  if (pack.names.length > listed.length) {
    detail.push(`· …另有 ${pack.names.length - listed.length} 个文件`);
  }
  if (pack.note) detail.push(pack.note);
  if (folderUrl) detail.push(`链接：${folderUrl}`);
  return composeMessage(config, resolveEventLabel(reason), detail);
}

async function resolveFolderUrl(client, folderToken) {
  if (!folderToken) return null;
  const meta = await getFileMeta(client, folderToken, 'folder').catch(() => null);
  return resolveDriveFileUrl(meta, folderToken, 'folder');
}

/** Text notice for an empty newly created folder — same caption layout as file notices. */
export async function notifyFolderCreated(client, config, { folderToken, folderPath }) {
  const meta = folderToken
    ? await getFileMeta(client, folderToken, 'folder').catch(() => null)
    : null;
  const driveUrl = folderToken
    ? resolveDriveFileUrl(meta, folderToken, 'folder')
    : null;
  const folderName = folderPath || meta?.title || folderToken || '新建文件夹';
  const detail = [
    `文件夹：${folderName}`,
    '说明：新建空文件夹，请打开链接在云盘查看',
    ...(driveUrl ? [`链接：${driveUrl}`] : []),
  ];
  const text = composeMessage(config, '文件夹有新上传/新建', detail);
  const sent = await sendTextMessage(client, config.chatId, text);
  console.log(`[sent-folder] ${folderName}`);
  return { messageId: sent?.message_id, mode: 'text', driveUrl };
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
