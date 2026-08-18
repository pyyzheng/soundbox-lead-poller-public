import {
  getFileMetas as defaultGetFileMetas,
  listFolderChildren as defaultListFolderChildren,
} from './feishu.js';
import {
  maybeAutoSubscribe as defaultMaybeAutoSubscribe,
  notifyFileAttachment as defaultNotifyFileAttachment,
  notifyFilesAsZip as defaultNotifyFilesAsZip,
  notifyFolderCreated as defaultNotifyFolderCreated,
} from './notify.js';

const META_BATCH_SIZE = 50;
const DEFAULT_MAX_DEPTH = 8;
const DEFAULT_RECENT_UPLOAD_SEC = 6 * 60 * 60;
const DEFAULT_NOTIFY_DEDUP_SEC = 24 * 60 * 60;
const DEFAULT_FOLDER_BUDGET = 80;

/**
 * One detection pass over watched files and folders.
 * Mutates `state` and returns how many notifications were sent.
 * On the very first pass it only establishes a baseline (no spam).
 */
export async function pollCycle({
  client,
  config,
  state,
  tag = 'once',
  deps = {},
}) {
  const isFirstRun = !state.initialized;
  const log = (msg) => console.log(`[${tag}] ${msg}`);
  const warn = (msg) => console.warn(`[${tag}] ${msg}`);
  const ctx = {
    client,
    config,
    state,
    isFirstRun,
    log,
    warn,
    listFolderChildren: deps.listFolderChildren || defaultListFolderChildren,
    getFileMetas: deps.getFileMetas || defaultGetFileMetas,
    notifyFileAttachment: deps.notifyFileAttachment || defaultNotifyFileAttachment,
    notifyFilesAsZip: deps.notifyFilesAsZip || defaultNotifyFilesAsZip,
    notifyFolderCreated: deps.notifyFolderCreated || defaultNotifyFolderCreated,
    maybeAutoSubscribe: deps.maybeAutoSubscribe || defaultMaybeAutoSubscribe,
  };

  let notified = 0;
  notified += await checkContentChanges(ctx);
  notified += await checkFolderAdditions(ctx);
  if (!isFirstRun) {
    notified += await backfillUnnotifiedRecent(ctx);
  }
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
  const nowSec = Math.floor(Date.now() / 1000);
  for (let i = 0; i < docs.length; i += META_BATCH_SIZE) {
    let metas;
    try {
      metas = await ctx.getFileMetas(client, docs.slice(i, i + META_BATCH_SIZE));
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
      if (wasRecentlyNotified(state, token, config, nowSec)) {
        log(`skip duplicate notify ${meta.title}`);
        continue;
      }

      log(`changed ${meta.title}`);
      await ctx.notifyFileAttachment(client, config, {
        fileToken: token,
        fileType: type,
        reason: '文件内容已更新',
        titleHint: meta.title,
      });
      markNotified(state, token, nowSec);
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

const DEFAULT_STALE_RESCAN_SEC = 180;

/**
 * Walk watched folders. After the tree is known, only re-list folders whose
 * metadata changed, plus any folder we have never listed. Stale folders are
 * re-listed on a timer so uploads are not missed when modify_time is unchanged.
 */
async function checkFolderAdditions(ctx) {
  const { config, state, isFirstRun, log, warn } = ctx;
  const maxDepth = config.maxFolderDepth ?? DEFAULT_MAX_DEPTH;
  const budget = { left: config.folderScanBudget ?? DEFAULT_FOLDER_BUDGET };
  const staleBudget = { left: config.staleRescanBudget ?? 50 };
  const nowSec = Math.floor(Date.now() / 1000);
  ensureFolderMeta(state);

  let notified = 0;
  const visited = new Set();
  const queue = (config.watchFolders || []).map((f) => ({
    token: f.token,
    name: f.name || f.token,
    path: f.name || f.token,
    parentName: '',
    depth: 0,
    force: true, // roots always listed so new top-level files are never missed
  }));

  while (queue.length) {
    const folder = queue.shift();
    if (!folder.token || visited.has(folder.token)) continue;
    visited.add(folder.token);

    const listed = state.folderChildren[folder.token] !== undefined;
    let needsRescan = false;
    if (listed && !folder.force) {
      needsRescan = await folderNeedsRescan(ctx, folder.token, listed);
    }
    const stale = listed && staleBudget.left > 0 && folderIsStale(state, folder.token, config, nowSec);
    if (stale) staleBudget.left -= 1;
    const shouldList = folder.force || !listed || needsRescan || stale;
    if (!shouldList) {
      enqueueKnownChildren(state, folder, maxDepth, queue);
      continue;
    }
    // Never defer roots, first-time folders, stale folders, or folders whose metadata changed.
    if (budget.left <= 0 && listed && !needsRescan && !folder.force && !stale) {
      enqueueKnownChildren(state, folder, maxDepth, queue);
      continue;
    }
    budget.left -= 1;

    let children;
    try {
      children = await ctx.listFolderChildren(ctx.client, folder.token);
    } catch (err) {
      warn(`list folder ${folder.name} failed: ${err.message}`);
      continue;
    }

    const known = state.folderChildren[folder.token];
    const seedOnly = isFirstRun;
    const firstSeen = !listed && !isFirstRun;
    const prevSet = new Set(known || []);

    const result = await scanChildren(ctx, {
      folder,
      children,
      prevSet,
      seedOnly,
      firstSeen,
    });
    notified += result.notified;
    state.folderChildren[folder.token] = result.tokens;
    rememberSubfolders(state, folder, result.subfolders);
    if (!state.folderListedAt) state.folderListedAt = {};
    state.folderListedAt[folder.token] = nowSec;

    if (folder.depth < maxDepth) {
      for (const sub of result.subfolders) {
        queue.push({
          token: sub.token,
          name: sub.name,
          path: `${folder.path}/${sub.name}`,
          parentName: folder.name,
          depth: folder.depth + 1,
          // Newly discovered folders must be listed immediately so files already
          // inside them (the usual "upload into a nested folder" case) are seen.
          force: firstSeen || !state.folderChildren[sub.token],
        });
      }
    } else if (result.subfolders.length) {
      warn(`depth limit ${maxDepth} reached at ${folder.name}, skipping subfolders`);
    }
  }

  if (budget.left <= 0) {
    log(`folder scan budget exhausted; remaining folders wait for later cycles`);
  }
  return notified;
}

async function folderNeedsRescan(ctx, token, listed) {
  if (!listed) return true;
  const { client, state } = ctx;
  try {
    const [meta] = await ctx.getFileMetas(client, [{ token, type: 'folder' }]);
    const modify = Number(meta?.latest_modify_time) || 0;
    const prev = Number(state.folderMeta[token]?.lastModify) || 0;
    if (modify && modify === prev) return false;
    state.folderMeta[token] = { lastModify: modify || prev };
    return true;
  } catch {
    return true;
  }
}

function enqueueKnownChildren(state, folder, maxDepth, queue) {
  if (folder.depth >= maxDepth) return;
  for (const sub of state.subfolders?.[folder.token] || []) {
    queue.push({
      token: sub.token,
      name: sub.name,
      path: `${folder.path}/${sub.name}`,
      parentName: folder.name,
      depth: folder.depth + 1,
      force: state.folderChildren[sub.token] === undefined,
    });
  }
}

function rememberSubfolders(state, folder, subfolders) {
  if (!state.subfolders) state.subfolders = {};
  state.subfolders[folder.token] = subfolders.map((s) => ({ token: s.token, name: s.name }));
}

function ensureFolderMeta(state) {
  if (!state.folderMeta) state.folderMeta = {};
  if (!state.subfolders) state.subfolders = {};
  if (!state.notified) state.notified = {};
  if (!state.folderListedAt) state.folderListedAt = {};
}

function folderIsStale(state, token, config, nowSec) {
  const staleSec = config.staleRescanSec ?? DEFAULT_STALE_RESCAN_SEC;
  const last = Number(state.folderListedAt?.[token]) || 0;
  return last > 0 && nowSec - last >= staleSec;
}

async function scanChildren(ctx, { folder, children, prevSet, seedOnly, firstSeen }) {
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
      if (isNew && !seedOnly && !firstSeen) {
        log(`new folder: ${child.name}`);
        await ctx.notifyFolderCreated(client, config, {
          folderName: child.name,
          parentName: folder.name,
        });
        notified += 1;
      }
      subfolders.push({ token: child.token, name: child.name });
      continue;
    }

    if (!isNew) continue;
    if (seedOnly || firstSeen) {
      bootstrapCandidates.push(child);
      continue;
    }
    newFiles.push(child);
  }

  if (bootstrapCandidates.length) {
    const metaByToken = await loadModifyTimes(ctx, bootstrapCandidates);
    for (const child of bootstrapCandidates) {
      const modify = Number(metaByToken.get(child.token)) || 0;
      if (modify >= nowSec - recentWindowSec) {
        log(`${seedOnly ? 'baseline' : 'bootstrap'} notify recent ${child.name}`);
        newFiles.push(child);
      } else {
        log(`${seedOnly ? 'baseline' : 'bootstrap'} seed old ${child.name}`);
        await trackNewChild(ctx, { child, metaModify: modify });
      }
    }
  }

  notified += await sendNewFiles(ctx, folder, newFiles, nowSec);
  return { tokens, subfolders, notified };
}

async function sendNewFiles(ctx, folder, newFiles, nowSec) {
  const { client, config, state, log } = ctx;
  const toNotify = [];
  for (const child of newFiles) {
    if (wasRecentlyNotified(state, child.token, config, nowSec)) {
      log(`skip duplicate notify ${child.name}`);
      await trackNewChild(ctx, { child });
      continue;
    }
    toNotify.push(child);
  }
  if (!toNotify.length) return 0;

  if (toNotify.length === 1) {
    const child = toNotify[0];
    log(`notify ${child.name} in ${folder.name}`);
    await ctx.maybeAutoSubscribe(client, config, child.token, child.type);
    await ctx.notifyFileAttachment(client, config, {
      fileToken: child.token,
      fileType: child.type,
      reason: '文件夹有新上传/新建',
      titleHint: child.name,
      folderPath: folder.path,
    });
    await trackNewChild(ctx, { child });
    markNotified(state, child.token, nowSec);
    return 1;
  }

  log(`zip ${toNotify.length} new file(s) in ${folder.name}`);
  for (const child of toNotify) {
    await ctx.maybeAutoSubscribe(client, config, child.token, child.type);
    await trackNewChild(ctx, { child });
    markNotified(state, child.token, nowSec);
  }
  await ctx.notifyFilesAsZip(client, config, {
    reason: '文件夹有新上传/新建',
    folderName: folder.path,
    zipNameHint: folder.name,
    files: toNotify.map((c) => ({ token: c.token, type: c.type, name: c.name })),
  });
  return 1;
}

async function loadModifyTimes(ctx, children) {
  const map = new Map();
  for (let i = 0; i < children.length; i += META_BATCH_SIZE) {
    const chunk = children.slice(i, i + META_BATCH_SIZE);
    try {
      const metas = await ctx.getFileMetas(
        ctx.client,
        chunk.map((c) => ({ token: c.token, type: c.type })),
      );
      for (const meta of metas) {
        map.set(meta.doc_token, Number(meta.latest_modify_time) || 0);
      }
    } catch {
      // missing entries stay 0 → treated as not recent
    }
  }
  return map;
}

async function trackNewChild(ctx, { child, metaModify, metaTitle }) {
  let lastModify = metaModify || 0;
  let title = metaTitle || child.name;
  if (metaModify == null) {
    try {
      const [meta] = await ctx.getFileMetas(ctx.client, [{ token: child.token, type: child.type }]);
      lastModify = Number(meta?.latest_modify_time) || 0;
      title = meta?.title || child.name;
    } catch {
      // keep defaults
    }
  }
  ctx.state.files[child.token] = { type: child.type, lastModify, title };
}

function wasRecentlyNotified(state, token, config, nowSec) {
  const dedupSec = config.notifyDedupSec ?? DEFAULT_NOTIFY_DEDUP_SEC;
  const at = Number(state.notified?.[token]) || 0;
  return at > 0 && nowSec - at < dedupSec;
}

function markNotified(state, token, nowSec) {
  if (!state.notified) state.notified = {};
  state.notified[token] = nowSec;
}

/** Recover files that were silently seeded during an earlier baseline pass. */
async function backfillUnnotifiedRecent(ctx) {
  const { client, config, state, log } = ctx;
  const nowSec = Math.floor(Date.now() / 1000);
  const window = config.recentUploadWindowSec ?? DEFAULT_RECENT_UPLOAD_SEC;
  let notified = 0;
  for (const [token, info] of Object.entries(state.files || {})) {
    if (!info?.type || info.type === 'folder' || info.type === 'shortcut') continue;
    if (wasRecentlyNotified(state, token, config, nowSec)) continue;
    const modify = Number(info.lastModify) || 0;
    if (modify < nowSec - window) continue;
    log(`backfill notify ${info.title || token}`);
    await ctx.notifyFileAttachment(client, config, {
      fileToken: token,
      fileType: info.type,
      reason: '文件夹有新上传/新建',
      titleHint: info.title || token,
    });
    markNotified(state, token, nowSec);
    notified += 1;
  }
  return notified;
}
