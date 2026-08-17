import { getFileMetas, listFolderChildren } from './feishu.js';
import { maybeAutoSubscribe, notifyFileAttachment } from './notify.js';

// Drive meta API accepts at most 50 tokens per request.
const META_BATCH_SIZE = 50;

/**
 * One detection pass over watched files and folders.
 * Mutates `state` and returns how many notifications were sent.
 * On the very first pass it only records a baseline so we don't spam the chat.
 */
export async function pollCycle({ client, config, state, tag = 'once' }) {
  const isFirstRun = !state.initialized;
  const log = (msg) => console.log(`[${tag}] ${msg}`);
  const warn = (msg) => console.warn(`[${tag}] ${msg}`);

  let notified = 0;
  notified += await checkContentChanges({ client, config, state, isFirstRun, log, warn });
  notified += await checkFolderAdditions({ client, config, state, isFirstRun, log, warn });
  return { notified, isFirstRun };
}

/**
 * Plain Drive files emit no edit events, so compare `latest_modify_time`
 * for explicitly watched files plus every folder child we've seen before.
 */
async function checkContentChanges({ client, config, state, isFirstRun, log, warn }) {
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

async function checkFolderAdditions({ client, config, state, isFirstRun, log, warn }) {
  let notified = 0;

  for (const folder of config.watchFolders || []) {
    let children;
    try {
      children = await listFolderChildren(client, folder.token);
    } catch (err) {
      warn(`list folder ${folder.name || folder.token} failed: ${err.message}`);
      continue;
    }

    const known = state.folderChildren[folder.token];
    const prevSet = new Set(known || []);
    const nextTokens = [];

    for (const child of children) {
      nextTokens.push(child.token);
      if (child.type === 'folder' || child.type === 'shortcut') continue;
      if (prevSet.has(child.token)) continue;

      if (isFirstRun || !known) {
        log(`seed folder child ${child.name}`);
        continue;
      }

      log(`new in folder: ${child.name} (${child.type})`);
      await maybeAutoSubscribe(client, config, child.token, child.type);
      await notifyFileAttachment(client, config, {
        fileToken: child.token,
        fileType: child.type,
        reason: '文件夹有新上传/新建',
        titleHint: child.name,
      });
      await trackNewChild({ client, state, child });
      notified += 1;
    }

    state.folderChildren[folder.token] = nextTokens;
  }
  return notified;
}

/** Record the new file so later passes can detect edits to it. */
async function trackNewChild({ client, state, child }) {
  let lastModify = 0;
  let title = child.name;
  try {
    const [meta] = await getFileMetas(client, [{ token: child.token, type: child.type }]);
    lastModify = Number(meta?.latest_modify_time) || 0;
    title = meta?.title || child.name;
  } catch {
    // keep defaults; next pass will seed the real modify time
  }
  state.files[child.token] = { type: child.type, lastModify, title };
}
