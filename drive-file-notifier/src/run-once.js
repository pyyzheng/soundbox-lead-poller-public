import fs from 'node:fs';
import path from 'node:path';
import { loadConfig } from './config.js';
import { createClient, getFileMetas, listFolderChildren } from './feishu.js';
import { maybeAutoSubscribe, notifyFileAttachment } from './notify.js';

/**
 * One-shot poll for GitHub Actions / cron.
 * Detects:
 * - watched file content changes (modify time)
 * - new files under watched folders
 * Persists state so the first run only establishes a baseline (no spam).
 */
async function main() {
  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);
  const statePath =
    process.env.STATE_PATH ||
    path.join(config.root, '.drive-notifier-state.json');

  const state = loadState(statePath);
  const isFirstRun = !state.initialized;
  let notified = 0;

  // 1) Watched files
  const watchDocs = (config.watchFiles || []).map((f) => ({
    token: f.token,
    type: f.type || 'file',
  }));
  if (watchDocs.length) {
    const metas = await getFileMetas(client, watchDocs);
    for (const meta of metas) {
      const token = meta.doc_token;
      const type = meta.doc_type || 'file';
      const modify = Number(meta.latest_modify_time) || 0;
      const prev = state.files[token];

      if (!prev) {
        state.files[token] = { type, lastModify: modify, title: meta.title };
        console.log(`[once] seed file ${meta.title}`);
        continue;
      }

      if (modify > (prev.lastModify || 0)) {
        state.files[token] = { type, lastModify: modify, title: meta.title };
        if (!isFirstRun) {
          console.log(`[once] changed ${meta.title}`);
          await notifyFileAttachment(client, config, {
            fileToken: token,
            fileType: type,
            reason: '文件内容已更新',
            titleHint: meta.title,
          });
          notified += 1;
        } else {
          console.log(`[once] baseline update ${meta.title} (no notify on first run)`);
        }
      }
    }
  }

  // 2) Watched folders — new children
  for (const folder of config.watchFolders || []) {
    let children;
    try {
      children = await listFolderChildren(client, folder.token);
    } catch (err) {
      console.warn(`[once] list folder ${folder.name || folder.token} failed: ${err.message}`);
      continue;
    }

    const prevSet = new Set(state.folderChildren[folder.token] || []);
    const nextTokens = [];

    for (const child of children) {
      if (child.type === 'folder' || child.type === 'shortcut') {
        nextTokens.push(child.token);
        continue;
      }
      nextTokens.push(child.token);

      if (!prevSet.has(child.token)) {
        if (!isFirstRun && state.folderChildren[folder.token]) {
          console.log(`[once] new in folder: ${child.name} (${child.type})`);
          await maybeAutoSubscribe(client, config, child.token, child.type);
          await notifyFileAttachment(client, config, {
            fileToken: child.token,
            fileType: child.type,
            reason: '文件夹有新上传/新建',
            titleHint: child.name,
          });
          // Track for future edit detection
          try {
            const [meta] = await getFileMetas(client, [
              { token: child.token, type: child.type },
            ]);
            state.files[child.token] = {
              type: child.type,
              lastModify: Number(meta?.latest_modify_time) || 0,
              title: meta?.title || child.name,
            };
          } catch {
            state.files[child.token] = {
              type: child.type,
              lastModify: 0,
              title: child.name,
            };
          }
          notified += 1;
        } else {
          console.log(`[once] seed folder child ${child.name}`);
        }
      }
    }

    state.folderChildren[folder.token] = nextTokens;
  }

  state.initialized = true;
  state.updatedAt = new Date().toISOString();
  saveState(statePath, state);
  console.log(`[once] done notified=${notified} firstRun=${isFirstRun} state=${statePath}`);
}

function loadState(statePath) {
  try {
    if (fs.existsSync(statePath)) {
      return {
        initialized: false,
        files: {},
        folderChildren: {},
        ...JSON.parse(fs.readFileSync(statePath, 'utf8')),
      };
    }
  } catch (err) {
    console.warn(`[once] bad state file, resetting: ${err.message}`);
  }
  return { initialized: false, files: {}, folderChildren: {} };
}

function saveState(statePath, state) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2));
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
