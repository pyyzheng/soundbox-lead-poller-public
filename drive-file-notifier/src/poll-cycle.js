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
import { claimRemoteNotified } from './remote-notified.js';

const META_BATCH_SIZE = 50;
const DEFAULT_MAX_DEPTH = 8;
const DEFAULT_RECENT_UPLOAD_SEC = 6 * 60 * 60;
const DEFAULT_FOLDER_BUDGET = 80;
const DEFAULT_EMPTY_FOLDER_NOTIFY_GRACE_SEC = 10 * 60;

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

  const notifiedCount = Object.keys(state.notified || {}).length;
  const filesCount = Object.keys(state.files || {}).length;
  log(`cycle start | isFirstRun=${isFirstRun} notified=${notifiedCount} files=${filesCount}`);

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
    persistState: deps.persistState,
    claimRemoteNotified: deps.claimRemoteNotified || claimRemoteNotified,
  };

  let notified = 0;
  notified += await checkContentChanges(ctx);
  notified += await checkFolderAdditions(ctx);

  if (notified > 0) {
    log(`cycle end | sent ${notified} notification(s) this cycle`);
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

      const filledModify = !(prev.lastModify);
      state.files[token] = { type, lastModify: modify, title: meta.title };
      if (isFirstRun || filledModify) {
        log(`baseline update ${meta.title} (no notify)`);
        continue;
      }
      if (wasEverNotified(state, token)) {
        log(`skip duplicate content-change ${meta.title} token=${token.slice(0, 8)}`);
        continue;
      }

      log(`changed ${meta.title} token=${token.slice(0, 8)} modify=${modify} prev=${prev.lastModify}`);
      if (!(await markNotified(ctx, token, nowSec))) continue;
      await ctx.notifyFileAttachment(client, config, {
        fileToken: token,
        fileType: type,
        reason: '文件内容已更新',
        titleHint: meta.title,
        driveUrl: meta.url,
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

const DEFAULT_STALE_RESCAN_SEC = 180;

/**
 * Walk watched folders. After the tree is known, only re-list folders whose
 * metadata changed, plus any folder we have never listed. Stale folders are
 * re-listed on a timer so uploads are not missed when modify_time is unchanged.
 */
async function checkFolderAdditions(ctx) {
  const { client, config, state, isFirstRun, log, warn } = ctx;
  const maxDepth = config.maxFolderDepth ?? DEFAULT_MAX_DEPTH;
  const budget = { left: config.folderScanBudget ?? DEFAULT_FOLDER_BUDGET };
  const staleBudget = { left: config.staleRescanBudget ?? 300 };
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
    force: true,
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
      notifyAllNewFiles: Boolean(folder.isNewFolder && !seedOnly),
    });
    notified += result.notified;

    if (folder.isNewFolder && !seedOnly) {
      const folderRecent = await folderRecentlyChanged(ctx, folder.token, config, nowSec);
      if (!folderRecent) {
        log(`skip empty-folder flow for old folder ${folder.path}`);
      } else {
      const hasDirectFile = result.fileCount > 0;
      const hasSubfolder = result.subfolders.length > 0;
      const pending = state.emptyFolderPending?.[folder.token];
      const graceSec = config.emptyFolderNotifyGraceSec ?? DEFAULT_EMPTY_FOLDER_NOTIFY_GRACE_SEC;
      if (hasDirectFile || hasSubfolder) {
        if (pending) {
          delete state.emptyFolderPending[folder.token];
          log(`clear empty-folder pending ${folder.path} token=${folder.token.slice(0, 8)} (children detected)`);
        }
      } else if (!pending) {
        state.emptyFolderPending[folder.token] = { firstSeenAt: nowSec, path: folder.path };
        log(`defer empty-folder notify ${folder.path} token=${folder.token.slice(0, 8)} grace=${graceSec}s`);
      } else if ((nowSec - Number(pending.firstSeenAt || nowSec)) >= graceSec) {
        if (!wasEverNotified(state, folder.token)) {
          if (await markNotified(ctx, folder.token, nowSec)) {
            log(`notify empty new folder ${folder.path} token=${folder.token.slice(0, 8)} graceElapsed=${nowSec - pending.firstSeenAt}s`);
            await ctx.notifyFolderCreated(client, config, {
              folderToken: folder.token,
              folderPath: folder.path,
            });
            notified += 1;
          }
        }
        delete state.emptyFolderPending[folder.token];
      }
      }
    }

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
          force: firstSeen || !state.folderChildren[sub.token],
          isNewFolder: sub.isNew && (Boolean(folder.isNewFolder) || folder.depth === 0),
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

async function folderRecentlyChanged(ctx, token, config, nowSec) {
  const recentSec = config.recentUploadWindowSec ?? DEFAULT_RECENT_UPLOAD_SEC;
  try {
    const [meta] = await ctx.getFileMetas(ctx.client, [{ token, type: 'folder' }]);
    const modify = Number(meta?.latest_modify_time) || 0;
    return modify >= nowSec - recentSec;
  } catch {
    return false;
  }
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
      isNewFolder: false,
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
  if (!state.emptyFolderPending) state.emptyFolderPending = {};
}

function folderIsStale(state, token, config, nowSec) {
  const staleSec = config.staleRescanSec ?? DEFAULT_STALE_RESCAN_SEC;
  const last = Number(state.folderListedAt?.[token]) || 0;
  return last > 0 && nowSec - last >= staleSec;
}

async function scanChildren(ctx, { folder, children, prevSet, seedOnly, firstSeen, notifyAllNewFiles }) {
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
      subfolders.push({ token: child.token, name: child.name, isNew });
      continue;
    }

    if (!isNew) continue;

    // All paths check wasEverNotified before queueing for notification
    if (wasEverNotified(state, child.token)) {
      log(`skip already-notified ${child.name} token=${child.token.slice(0, 8)} path=${folder.path}`);
      await trackNewChild(ctx, { child });
      continue;
    }

    if (notifyAllNewFiles) {
      bootstrapCandidates.push(child);
      continue;
    }
    if (seedOnly || firstSeen) {
      bootstrapCandidates.push(child);
      continue;
    }
    log(`new child ${child.name} token=${child.token.slice(0, 8)} in ${folder.path}`);
    newFiles.push(child);
  }

  if (bootstrapCandidates.length) {
    const metaByToken = await loadModifyTimes(ctx, bootstrapCandidates);
    for (const child of bootstrapCandidates) {
      const modify = Number(metaByToken.get(child.token)) || 0;
      if (modify >= nowSec - recentWindowSec) {
        if (wasEverNotified(state, child.token)) {
          log(`bootstrap skip duplicate ${child.name} token=${child.token.slice(0, 8)}`);
          await trackNewChild(ctx, { child, metaModify: modify });
          continue;
        }
        log(`${seedOnly ? 'baseline' : 'bootstrap'} notify recent ${child.name} token=${child.token.slice(0, 8)} age=${nowSec - modify}s`);
        newFiles.push(child);
      } else {
        log(`${seedOnly ? 'baseline' : 'bootstrap'} seed old ${child.name} token=${child.token.slice(0, 8)}`);
        await trackNewChild(ctx, { child, metaModify: modify });
      }
    }
  }

  const fileNotifyCount = await sendNewFiles(ctx, folder, newFiles, nowSec);
  notified += fileNotifyCount;

  return {
    tokens,
    subfolders,
    notified,
    fileNames: newFiles.map((c) => c.name),
    fileCount: newFiles.length,
  };
}

async function sendNewFiles(ctx, folder, newFiles, nowSec) {
  const { client, config, state, log } = ctx;
  const toNotify = [];
  for (const child of newFiles) {
    if (wasEverNotified(state, child.token)) {
      log(`sendNewFiles skip duplicate ${child.name} token=${child.token.slice(0, 8)}`);
      await trackNewChild(ctx, { child });
      continue;
    }
    toNotify.push(child);
  }
  if (!toNotify.length) return 0;

  if (toNotify.length === 1) {
    const child = toNotify[0];
    log(`notify single ${child.name} token=${child.token.slice(0, 8)} in ${folder.path}`);
    if (!(await markNotified(ctx, child.token, nowSec))) return 0;
    await ctx.maybeAutoSubscribe(client, config, child.token, child.type);
    await trackNewChild(ctx, { child });
    await ctx.notifyFileAttachment(client, config, {
      fileToken: child.token,
      fileType: child.type,
      reason: '文件夹有新上传/新建',
      titleHint: child.name,
      folderPath: folder.path,
      folderToken: folder.token,
      driveUrl: child.url,
    });
    return 1;
  }

  log(`zip ${toNotify.length} new file(s) in ${folder.path}: ${toNotify.map((c) => c.name).join(', ')}`);
  const zipBatch = [];
  for (const child of toNotify) {
    if (await markNotified(ctx, child.token, nowSec)) {
      zipBatch.push(child);
      await ctx.maybeAutoSubscribe(client, config, child.token, child.type);
      await trackNewChild(ctx, { child });
    }
  }
  if (!zipBatch.length) return 0;
  await ctx.notifyFilesAsZip(client, config, {
    reason: '文件夹有新上传/新建',
    folderName: folder.path,
    folderToken: folder.token,
    zipNameHint: folder.name,
    files: zipBatch.map((c) => ({ token: c.token, type: c.type, name: c.name })),
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

function wasEverNotified(state, token) {
  return Number(state.notified?.[token]) > 0;
}

async function markNotified(ctx, token, nowSec) {
  const { state, log, warn } = ctx;
  if (!state.notified) state.notified = {};

  if (Number(state.notified[token]) > 0) {
    log(`markNotified BLOCKED (local) token=${token.slice(0, 8)} notifiedAt=${state.notified[token]}`);
    return false;
  }

  let claimed = true;
  try {
    claimed = await ctx.claimRemoteNotified(token, nowSec);
  } catch (err) {
    warn(`remote claim error (proceeding with local-only dedup): ${err.message}`);
  }
  if (!claimed) {
    state.notified[token] = state.notified[token] || nowSec;
    log(`markNotified BLOCKED (remote) token=${token.slice(0, 8)}`);
    try {
      ctx.persistState?.();
    } catch (err) {
      warn(`persist state failed: ${err.message}`);
    }
    return false;
  }

  state.notified[token] = nowSec;
  log(`markNotified OK token=${token.slice(0, 8)} at=${nowSec}`);
  try {
    ctx.persistState?.();
  } catch (err) {
    warn(`persist state failed: ${err.message}`);
  }
  return true;
}
