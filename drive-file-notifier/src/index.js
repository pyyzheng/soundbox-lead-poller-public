import * as lark from '@larksuiteoapi/node-sdk';
import { loadConfig } from './config.js';
import { createClient, getFileMeta } from './feishu.js';
import { maybeAutoSubscribe, notifyFileAttachment } from './notify.js';
import { matchSkipTitleKeyword } from './poll-cycle.js';
import { createPoller } from './poller.js';

async function shouldSkipByTitle(client, config, fileToken, fileType, titleHint = '') {
  let title = titleHint;
  if (!title) {
    try {
      const meta = await getFileMeta(client, fileToken, fileType || 'file');
      title = meta?.title || '';
    } catch {
      // keep empty
    }
  }
  const hit = matchSkipTitleKeyword(title, config.skipTitleKeywords);
  if (hit) {
    console.log(`[skip] blocked title "${title}" keyword=${hit} token=${fileToken}`);
    return true;
  }
  return false;
}

async function main() {
  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);

  const watchedFolders = new Set((config.watchFolders || []).map((f) => f.token));
  const acceptedFiles = new Set((config.watchFiles || []).map((f) => f.token));
  const pendingEdits = new Map(); // fileToken -> timer
  const recentEvents = new Set();

  console.log('[boot] drive-file-notifier starting');
  console.log(`[boot] chat=${config.chatId}`);
  console.log(`[boot] folders=${[...watchedFolders].join(',') || '(none)'}`);
  console.log(`[boot] files=${[...acceptedFiles].join(',') || '(none)'}`);

  const poller = createPoller({
    client,
    config,
    onChange: async ({ token, type, meta }) => {
      if (await shouldSkipByTitle(client, config, token, type, meta?.title)) return;
      await notifyFileAttachment(client, config, {
        fileToken: token,
        fileType: type,
        reason: '文件内容已更新',
        titleHint: meta?.title,
      });
    },
  });

  const handleFileUpdated = async (data, eventName, reason) => {
    try {
      const event = unwrap(data);
      const fileToken = event.file_token;
      const fileType = event.file_type || 'file';
      if (!acceptedFiles.has(fileToken)) {
        console.log(`[skip] ${eventName} not in watch list: ${fileToken}`);
        return;
      }
      if (dedupe(recentEvents, `${data?.header?.event_id || fileToken}:${eventName}`)) return;

      console.log(`[event] ${eventName} type=${fileType} token=${fileToken}`);
      scheduleEdit(pendingEdits, config.editDebounceMs, fileToken, async () => {
        if (await shouldSkipByTitle(client, config, fileToken, fileType)) {
          await poller.markSeen(fileToken);
          return;
        }
        const result = await notifyFileAttachment(client, config, {
          fileToken,
          fileType,
          reason,
        });
        await poller.markSeen(fileToken);
        console.log(`[done] ${eventName}`, result);
      });
    } catch (err) {
      console.error(`[error] ${eventName}`, err);
    }
  };

  const eventDispatcher = new lark.EventDispatcher({}).register({
    'drive.file.created_in_folder_v1': async (data) => {
      try {
        const event = unwrap(data);
        const folderToken = event.folder_token;
        const fileToken = event.file_token;
        const fileType = event.file_type;
        if (!watchedFolders.has(folderToken)) {
          console.log(`[skip] created outside watch folder: ${folderToken}`);
          return;
        }
        if (dedupe(recentEvents, `${data?.header?.event_id || fileToken}:created`)) return;

        console.log(`[event] created_in_folder type=${fileType} token=${fileToken}`);
        if (await shouldSkipByTitle(client, config, fileToken, fileType)) {
          acceptedFiles.add(fileToken);
          await poller.track([{ token: fileToken, type: fileType }]);
          return;
        }
        acceptedFiles.add(fileToken);
        await maybeAutoSubscribe(client, config, fileToken, fileType);
        const result = await notifyFileAttachment(client, config, {
          fileToken,
          fileType,
          reason: '文件夹有新上传/新建',
        });
        await poller.track([{ token: fileToken, type: fileType }]);
        console.log('[done] created', result);
      } catch (err) {
        console.error('[error] created_in_folder', err);
      }
    },

    // 在线文档（docx/sheet/bitable/slides）内容编辑
    'drive.file.edit_v1': (data) => handleFileUpdated(data, 'edit', '文档内容已更新'),

    // 文件重命名
    'drive.file.title_updated_v1': (data) => handleFileUpdated(data, 'title_updated', '文件已重命名'),
  });

  const wsClient = new lark.WSClient({
    appId: config.appId,
    appSecret: config.appSecret,
    loggerLevel: lark.LoggerLevel.info,
  });

  wsClient.start({ eventDispatcher }).then(() => {
    console.log('[ready] websocket connected; waiting for Drive events');
  }).catch((err) => {
    console.error('[fatal] websocket failed', err);
    process.exit(1);
  });

  await poller.track(
    (config.watchFiles || []).map((f) => ({ token: f.token, type: f.type || 'file' })),
  );
  await poller.start();
}

function unwrap(data) {
  return data?.event || data || {};
}

function dedupe(set, key) {
  if (set.has(key)) return true;
  set.add(key);
  setTimeout(() => set.delete(key), 10 * 60 * 1000);
  return false;
}

function scheduleEdit(map, delayMs, key, fn) {
  const prev = map.get(key);
  if (prev) clearTimeout(prev);
  const timer = setTimeout(async () => {
    map.delete(key);
    try {
      await fn();
    } catch (err) {
      console.error('[error] debounced edit', key, err);
    }
  }, delayMs);
  map.set(key, timer);
  console.log(`[debounce] ${key} in ${delayMs}ms`);
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
