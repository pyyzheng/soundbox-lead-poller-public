import { loadConfig } from './config.js';
import { createClient, getFileMetas, listFolderChildren } from './feishu.js';

const MAX_DEPTH = 4;
const MAX_FOLDERS = 200;

/** Walk watch roots and print files modified within the last N minutes. */
async function main() {
  const withinMin = Number(process.argv[2] || 30);
  const sinceSec = Math.floor(Date.now() / 1000) - withinMin * 60;
  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);
  const recent = [];
  let foldersScanned = 0;

  async function walk(folderToken, path, depth) {
    if (depth > MAX_DEPTH || foldersScanned >= MAX_FOLDERS) return;
    foldersScanned += 1;
    let children;
    try {
      children = await listFolderChildren(client, folderToken);
    } catch (err) {
      console.warn(`list failed ${path}: ${err.message}`);
      return;
    }
    const files = children.filter((c) => c.type !== 'folder' && c.type !== 'shortcut');
    if (files.length) {
      const metas = await getFileMetas(
        client,
        files.map((f) => ({ token: f.token, type: f.type })),
      );
      for (const meta of metas) {
        const modify = Number(meta.latest_modify_time) || 0;
        if (modify >= sinceSec) {
          recent.push({
            path,
            name: meta.title,
            token: meta.doc_token,
            agoMin: Math.round((Date.now() / 1000 - modify) / 60),
          });
        }
      }
    }
    for (const sub of children.filter((c) => c.type === 'folder')) {
      await walk(sub.token, `${path}/${sub.name}`, depth + 1);
    }
  }

  for (const root of config.watchFolders || []) {
    await walk(root.token, root.name, 0);
  }

  recent.sort((a, b) => a.agoMin - b.agoMin);
  console.log(`recent uploads within ${withinMin}m (scanned ${foldersScanned} folders):`);
  if (!recent.length) {
    console.log('  (none found)');
    return;
  }
  for (const row of recent) {
    console.log(`  ${row.agoMin}m ago  ${row.path}/${row.name}  ${row.token}`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
