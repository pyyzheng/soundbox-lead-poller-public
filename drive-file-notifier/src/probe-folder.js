import { loadConfig } from './config.js';
import { createClient, getFileMetas, listFolderChildren } from './feishu.js';

/** Quick probe: list a folder and show the newest files. */
async function main() {
  const folderToken = process.argv[2];
  if (!folderToken) {
    console.error('用法: node src/probe-folder.js <folder_token>');
    process.exit(1);
  }

  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);
  const children = await listFolderChildren(client, folderToken);
  const files = children.filter((c) => c.type !== 'folder' && c.type !== 'shortcut');
  const metas = await getFileMetas(
    client,
    files.map((f) => ({ token: f.token, type: f.type })),
  );
  const ranked = metas
    .map((m) => ({
      name: m.title,
      token: m.doc_token,
      type: m.doc_type,
      modify: Number(m.latest_modify_time) || 0,
      agoMin: Math.round((Date.now() / 1000 - (Number(m.latest_modify_time) || 0)) / 60),
    }))
    .sort((a, b) => b.modify - a.modify);

  console.log(`folder=${folderToken} children=${children.length} files=${files.length}`);
  for (const row of ranked.slice(0, 10)) {
    console.log(`  ${row.agoMin}m ago  ${row.name}  ${row.token}  (${row.type})`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
