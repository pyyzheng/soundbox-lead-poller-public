import { getFileMetas, listFolderChildren } from './feishu.js';
import {
  maybeAutoSubscribe,
  notifyFileAttachment,
  notifyFilesAsZip,
  notifyFolderCreated,
} from './notify.js';

// Drive meta API accepts at most 50 tokens per request.
const META_BATCH_SIZE = 50;
const DEFAULT_MAX_DEPTH = 5;
const DEFAULT_RECENT_UPLOAD_SEC = 6 * 60 * 60;

/**
 * One detection pass over watched files and folders.
 * Mutates `state` and returns how many notifications were sent.
 * On the very first pass it only records a baseline so we don't spam the chat.
 */
export async function pollCycle({ client, config, state, tag = 'once' }) {
  const isFirstRun = !state.initialized;
  const log = (msg) => console.log(`[${tag}] ${msg}`);
  const warn = (msg) => console.warn(`[${tag}] ${msg}`);

  const ctx = { client, config, state, isFirstRun, log, warn };
  let notified = 0;
  notified += await checkContentChanges(ctx);
  notified += await checkFolderAdditions(ctx);
  return { notified, isFirstRun };
}

/**
 * Plain Drive files emit no edit events, so compare `latest_modify_time`
 * for explicitly watched files plus every folder child we've seen before.
 */
async function checkContentChanges(ctx) {
  const { client, config, state, isFirstRun, log, warn } = ctx;
  const docs = collectTrackedDocs(config, state);
  if (!docs.length) return 0;

  let notified = 0;
  for (let i = 0; i < docs.length; i += META_BATCH_SIZE) {
    let metas;
    try {
      metas = await getFileMetas(client, docs.slice(i, i + META_BATCH_SIZE));
    } catch (err) {
      warn(`getFileMetas failed: ${err.message}`);
      continue;
    }

    for (const meta of metas) {
      const token = meta.doc_token;
      const type = meta.doc_type || state.files[token]?.type || 'file';
      const modify = Number(meta.latest_modify_time) || 0;
      const prev = state.files[token];

      if (!prev) {
        state.files[token] = { type, lastModify: modify, title: meta.title };
        log(`seed file ${meta.title}`);
        continue;
      }
      if (modify <= (prev.lastModify || 0)) continue;

      state.files[token] = { type, lastModify: modify, title: meta.title };
      if (isFirstRun) {
        log(`baseline update ${meta.title} (no notify on first run)`);
        continue;
      }

      log(`changed ${meta.title}`);
      await notifyFileAttachment(client, config, {
        fileToken: token,
        fileType: type,
        reason: '文件内容已更新',
        titleHint: meta.title,
      });
      notified += 1;
    }
  }
  return notified;
}

function collectTrackedDocs(config, state) {
  const docs = [];
  const seen = new Set();

  for (const f of config.watchFiles || []) {
    if (!f?.token || seen.has(f.token)) continue;
    seen.add(f.token);
    docs.push({ token: f.token, type: f.type || 'file' });
  }
  for (const [token, info] of Object.entries(state.files || {})) {
    if (!token || seen.has(token)) continue;
    if (!info?.type || info.type === 'folder' || info.type === 'shortcut') continue;
    seen.add(token);
    docs.push({ token, type: info.type });
  }
  return docs;
}

/** Walk every watched folder and its subfolders, breadth-first. */
async function checkFolderAdditions(ctx) {
  const { config, state, isFirstRun, warn } = ctx;
  const maxDepth = config.maxFolderDepth ?? DEFAULT_MAX_DEPTH;

  let notified = 0;
  const visited = new Set();
  const queue = (config.watchFolders || []).map((f) => ({
    token: f.token,
    name: f.name || f.token,
    depth: 0,
    isRoot: true,
  }));

  while (queue.length) {
    const folder = queue.shift();
    if (!folder.token || visited.has(folder.token)) continue;
    visited.add(folder.token);

    let children;
    try {
      children = await listFolderChildren(ctx.client, folder.token);
    } catch (err) {
      warn(`list folder ${folder.name} failed: ${err.message}`);
      continue;
    }

    const known = state.folderChildren[folder.token];
    // 全局首次运行只建基线；新加入监听的文件夹/子目录则按修改时间判断是否要通知
    const seedOnly = isFirstRun;
    const bootstrapNewWatch = !known && !isFirstRun;
    const prevSet = new Set(known || []);

    const result = await scanChildren(ctx, {
      folder,
      children,
      prevSet,
      seedOnly,
      bootstrapNewWatch,
    });
    notified += result.notified;
    state.folderChildren[folder.token] = result.tokens;

    if (folder.depth < maxDepth) {
      for (const sub of result.subfolders) {
        queue.push({ ...sub, depth: folder.depth + 1 });
      }
    } else if (result.subfolders.length) {
      warn(`depth limit ${maxDepth} reached at ${folder.name}, skipping subfolders`);
    }
  }

  return notified;
}

async function scanChildren(ctx, { folder, children, prevSet, seedOnly, bootstrapNewWatch }) {
  const { client, config, state, log } = ctx;
  const tokens = [];
  const subfolders = [];
  const newFiles = [];
  const bootstrapCandidates = [];
  let notified = 0;
  const recentWindowSec = config.recentUploadWindowSec ?? DEFAULT_RECENT_UPLOAD_SEC;
  const nowSec = Math.floor(Date.now() / 1000);

  for (const child of children) {
    tokens.push(child.token);
    if (child.type === 'shortcut') continue;
    const isNew = !prevSet.has(child.token);

    if (child.type === 'folder') {
      if (isNew && !seedOnly && !bootstrapNewWatch) {
        log(`new folder: ${child.name}`);
        await notifyFolderCreated(client, config, {
          folderName: child.name,
          parentName: folder.name,
        });
        notified += 1;
      }
      subfolders.push({ token: child.token, name: child.name, isRoot: false });
      continue;
    }

    if (!isNew) continue;
    if (seedOnly) {
      log(`seed folder child ${child.name}`);
      continue;
    }
    if (bootstrapNewWatch) {
      bootstrapCandidates.push(child);
      continue;
    }

    newFiles.push(child);
  }

  if (bootstrapCandidates.length) {
    const metaByToken = await loadModifyTimes(client, bootstrapCandidates);
    for (const child of bootstrapCandidates) {
      const modify = metaByToken.get(child.token) || 0;
      if (modify >= nowSec - recentWindowSec) {
        log(`bootstrap notify recent ${child.name}`);
        newFiles.push(child);
      } else {
        log(`bootstrap seed old ${child.name}`);
        await trackNewChild({
          client,
          state,
          child,
          metaModify: metaByToken.get(child.token),
        });
      }
    }
  }

  // 同一文件夹本轮新增：单文件直发，多文件打 zip
  if (newFiles.length === 1) {
    const child = newFiles[0];
    log(`notify ${child.name} in ${folder.name}`);
    await maybeAutoSubscribe(client, config, child.token, child.type);
    await notifyFileAttachment(client, config, {
      fileToken: child.token,
      fileType: child.type,
      reason: '文件夹有新上传/新建',
      titleHint: child.name,
    });
    await trackNewChild({ client, state, child });
    notified += 1;
  } else if (newFiles.length > 1) {
    log(`zip ${newFiles.length} new file(s) in ${folder.name}`);
    for (const child of newFiles) {
      await maybeAutoSubscribe(client, config, child.token, child.type);
      await trackNewChild({ client, state, child });
    }
    await notifyFilesAsZip(client, config, {
      reason: '文件夹有新上传/新建',
      folderName: folder.name,
      zipNameHint: folder.name,
      files: newFiles.map((c) => ({ token: c.token, type: c.type, name: c.name })),
    });
    notified += 1;
  }

  return { tokens, subfolders, notified };
}

async function loadModifyTimes(client, children) {
  const map = new Map();
  for (let i = 0; i < children.length; i += META_BATCH_SIZE) {
    const chunk = children.slice(i, i + META_BATCH_SIZE);
    try {
      const metas = await getFileMetas(
        client,
        chunk.map((c) => ({ token: c.token, type: c.type })),
      );
      for (const meta of metas) {
        map.set(meta.doc_token, Number(meta.latest_modify_time) || 0);
      }
    } catch {
      // leave missing entries at 0
    }
  }
  return map;
}

/** Record the new file so later passes can detect edits to it. */
async function trackNewChild({ client, state, child, metaModify, metaTitle }) {
  let lastModify = metaModify || 0;
  let title = metaTitle || child.name;
  if (!metaModify) {
    try {
      const [meta] = await getFileMetas(client, [{ token: child.token, type: child.type }]);
      lastModify = Number(meta?.latest_modify_time) || 0;
      title = meta?.title || child.name;
    } catch {
      // keep defaults; next pass will seed the real modify time
    }
  }
  state.files[child.token] = { type: child.type, lastModify, title };
}
