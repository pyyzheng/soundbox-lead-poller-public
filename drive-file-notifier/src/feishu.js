import fs from 'node:fs';
import path from 'node:path';
import * as lark from '@larksuiteoapi/node-sdk';

export function createClient(appId, appSecret) {
  const client = new lark.Client({
    appId,
    appSecret,
    appType: lark.AppType.SelfBuild,
    domain: lark.Domain.Feishu,
  });
  // Keep credentials for raw fetch fallbacks
  client.__appId = appId;
  client.__appSecret = appSecret;
  return client;
}

export async function getTenantToken(client) {
  const res = await client.auth.v3.tenantAccessToken.internal({
    data: {
      app_id: client.appId,
      app_secret: client.appSecret,
    },
  });
  if (res.code !== 0) {
    throw new Error(`获取 tenant_access_token 失败: ${res.msg}`);
  }
  return res.tenant_access_token;
}

export async function subscribeResource(client, fileToken, fileType) {
  const params = { file_type: fileType };
  if (fileType === 'folder') {
    params.event_type = 'file.created_in_folder_v1';
  }
  const res = await client.request({
    url: `/open-apis/drive/v1/files/${fileToken}/subscribe`,
    method: 'POST',
    params,
  });
  if (res.code !== 0) {
    throw new Error(`订阅失败 ${fileToken} (${fileType}): code=${res.code} msg=${res.msg}`);
  }
  return res;
}

export async function getFileMeta(client, fileToken, fileType) {
  const res = await client.drive.v1.meta.batchQuery({
    data: {
      request_docs: [{ doc_token: fileToken, doc_type: fileType }],
      with_url: true,
    },
  });
  if (res.code !== 0) {
    throw new Error(`查询元数据失败: ${res.msg}`);
  }
  return res.data?.metas?.[0] || null;
}

export async function getFileMetas(client, docs) {
  if (!docs.length) return [];
  const res = await client.drive.v1.meta.batchQuery({
    data: {
      request_docs: docs.map(({ token, type }) => ({
        doc_token: token,
        doc_type: type || 'file',
      })),
      with_url: true,
    },
  });
  if (res.code !== 0) {
    throw new Error(`批量查询元数据失败: ${res.msg}`);
  }
  return res.data?.metas || [];
}

export async function listFolderChildren(client, folderToken) {
  const items = [];
  let pageToken;
  do {
    const res = await client.drive.v1.file.list({
      params: {
        folder_token: folderToken,
        page_size: 200,
        page_token: pageToken,
      },
    });
    if (res?.code && res.code !== 0) {
      throw new Error(`列出文件夹失败: code=${res.code} msg=${res.msg}`);
    }
    const data = res?.data || res;
    for (const f of data?.files || []) {
      items.push({
        token: f.token,
        type: f.type || 'file',
        name: f.name,
      });
    }
    pageToken = data?.has_more ? data.next_page_token : undefined;
  } while (pageToken);
  return items;
}

export async function downloadDriveFile(client, fileToken, outputPath) {
  const res = await client.drive.v1.file.download(
    { path: { file_token: fileToken } },
    { responseType: 'arraybuffer' },
  );

  // SDK may return Buffer/ArrayBuffer or a writeable stream helper
  if (Buffer.isBuffer(res)) {
    fs.writeFileSync(outputPath, res);
    return outputPath;
  }
  if (res?.data && Buffer.isBuffer(res.data)) {
    fs.writeFileSync(outputPath, res.data);
    return outputPath;
  }
  if (typeof res?.writeFile === 'function') {
    await res.writeFile(outputPath);
    return outputPath;
  }

  // Fallback: raw HTTP
  const tokenRes = await client.auth.v3.tenantAccessToken.internal({
    data: { app_id: client.__appId, app_secret: client.__appSecret },
  });
  if (tokenRes.code !== 0) {
    throw new Error(`下载前取 token 失败: ${tokenRes.msg}`);
  }
  const accessToken = tokenRes.tenant_access_token;
  const resp = await fetch(
    `https://open.feishu.cn/open-apis/drive/v1/files/${fileToken}/download`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`下载失败 HTTP ${resp.status}: ${text.slice(0, 300)}`);
  }
  const buf = Buffer.from(await resp.arrayBuffer());
  fs.writeFileSync(outputPath, buf);
  return outputPath;
}

export async function exportOnlineDoc(client, fileToken, fileType, tmpDir) {
  const typeMap = {
    doc: 'pdf',
    docx: 'pdf',
    sheet: 'xlsx',
    bitable: 'xlsx',
  };
  const exportType = typeMap[fileType];
  if (!exportType) {
    throw new Error(`不支持导出的类型: ${fileType}`);
  }

  const createRes = await client.drive.v1.exportTask.create({
    data: {
      file_extension: exportType,
      token: fileToken,
      type: fileType,
    },
  });
  if (createRes.code !== 0) {
    throw new Error(`创建导出任务失败: ${createRes.msg}`);
  }
  const ticket = createRes.data.ticket;

  let fileExportToken = null;
  for (let i = 0; i < 30; i += 1) {
    await sleep(2000);
    const q = await client.drive.v1.exportTask.get({
      path: { ticket },
      params: { token: fileToken },
    });
    if (q.code !== 0) {
      throw new Error(`查询导出任务失败: ${q.msg}`);
    }
    const result = q.data?.result;
    if (result?.job_status === 0) {
      fileExportToken = result.file_token;
      break;
    }
    if (result?.job_status === 1 || result?.job_status === 2) {
      // pending / processing
      continue;
    }
    throw new Error(`导出失败 status=${result?.job_status} msg=${result?.job_error_msg}`);
  }
  if (!fileExportToken) {
    throw new Error('导出超时');
  }

  const out = path.join(tmpDir, `${fileToken}.${exportType}`);
  await downloadDriveFile(client, fileExportToken, out);
  return { localPath: out, fileName: path.basename(out), exportType };
}

export async function uploadImFile(client, localPath, fileName) {
  const fileType = guessImFileType(fileName);
  const stat = fs.statSync(localPath);
  const res = await client.im.v1.file.create({
    data: {
      file_type: fileType,
      file_name: fileName,
      file: fs.createReadStream(localPath),
    },
  });

  // SDK may return either { code, data: { file_key } } or a flat { file_key }
  const fileKey = res?.data?.file_key || res?.file_key;
  if (!fileKey) {
    throw new Error(
      `上传 IM 文件失败(${stat.size} bytes): ${JSON.stringify(res).slice(0, 300)}`,
    );
  }
  return fileKey;
}

export async function sendFileMessage(client, chatId, fileKey) {
  const res = await client.im.v1.message.create({
    params: { receive_id_type: 'chat_id' },
    data: {
      receive_id: chatId,
      msg_type: 'file',
      content: JSON.stringify({ file_key: fileKey }),
    },
  });
  const messageId = res?.data?.message_id || res?.message_id;
  if (!messageId && res?.code && res.code !== 0) {
    throw new Error(`发送文件消息失败: code=${res.code} msg=${res.msg}`);
  }
  if (!messageId) {
    throw new Error(`发送文件消息失败: ${JSON.stringify(res).slice(0, 300)}`);
  }
  return res.data || res;
}

export async function sendTextMessage(client, chatId, text) {
  const res = await client.im.v1.message.create({
    params: { receive_id_type: 'chat_id' },
    data: {
      receive_id: chatId,
      msg_type: 'text',
      content: JSON.stringify({ text }),
    },
  });
  const messageId = res?.data?.message_id || res?.message_id;
  if (!messageId && res?.code && res.code !== 0) {
    throw new Error(`发送文本失败: code=${res.code} msg=${res.msg}`);
  }
  if (!messageId) {
    throw new Error(`发送文本失败: ${JSON.stringify(res).slice(0, 300)}`);
  }
  return res.data || res;
}

export async function ensureFileOnlyShareAccess(client, fileToken, fileType, mentions = []) {
  // Never operate on folders — only the single file being notified.
  if (!fileType || fileType === 'folder' || fileType === 'shortcut') {
    return { skipped: true, reason: 'unsupported_type' };
  }

  const result = { publicPatched: false, membersAdded: [], errors: [] };

  try {
    const res = await client.drive.v1.permissionPublic.patch({
      params: { token: fileToken, type: fileType },
      data: {
        external_access: true,
        link_share_entity: 'anyone_readable',
        security_entity: 'anyone_can_view', // 可阅读者可下载
        share_entity: 'only_full_access', // 禁止收信人再加协作者
        invite_external: false,
      },
    });
    if (res?.code && res.code !== 0) {
      result.errors.push(`public.patch code=${res.code} msg=${res.msg || res.message || ''}`);
    } else {
      result.publicPatched = true;
    }
  } catch (err) {
    result.errors.push(`public.patch ${err.message || err}`);
  }

  for (const m of mentions || []) {
    if (!m?.openId) continue;
    try {
      const res = await client.drive.v1.permissionMember.create({
        params: { token: fileToken, type: fileType },
        data: {
          member_type: 'openid',
          member_id: m.openId,
          perm: 'view',
        },
      });
      if (res?.code && res.code !== 0) {
        // 1062999 / already exists may appear as error — treat duplicate as ok-ish
        result.errors.push(`member ${m.name || m.openId}: code=${res.code} msg=${res.msg || ''}`);
      } else {
        result.membersAdded.push(m.openId);
      }
    } catch (err) {
      result.errors.push(`member ${m.name || m.openId}: ${err.message || err}`);
    }
  }

  return result;
}

export function guessImFileType(fileName) {
  const ext = path.extname(fileName).toLowerCase();
  if (ext === '.pdf') return 'pdf';
  if (ext === '.doc' || ext === '.docx') return 'doc';
  if (ext === '.xls' || ext === '.xlsx') return 'xls';
  if (ext === '.ppt' || ext === '.pptx') return 'ppt';
  if (ext === '.mp4') return 'mp4';
  if (ext === '.opus') return 'opus';
  return 'stream';
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function safeFileName(name, fallback) {
  const base = (name || fallback || 'file').replace(/[\\/:*?"<>|]/g, '_').trim();
  return base || 'file';
}
