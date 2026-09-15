import { loadConfig } from './config.js';
import { createClient, subscribeResource } from './feishu.js';

async function main() {
  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);

  console.log('开始订阅云盘资源...');

  for (const folder of config.watchFolders || []) {
    try {
      await subscribeResource(client, folder.token, 'folder');
      console.log(`✓ 文件夹已订阅: ${folder.name || folder.token}`);
    } catch (err) {
      console.error(`✗ 文件夹订阅失败: ${folder.name || folder.token}`);
      console.error(`  ${err.message}`);
      console.error('  处理：打开该文件夹 → 「...」→「添加文档应用」→ 选本应用并给「可管理」权限后重试');
    }
  }

  for (const file of config.watchFiles || []) {
    try {
      await subscribeResource(client, file.token, file.type || 'file');
      console.log(`✓ 文件已订阅: ${file.name || file.token} (${file.type || 'file'})`);
    } catch (err) {
      console.error(`✗ 文件订阅失败: ${file.name || file.token}`);
      console.error(`  ${err.message}`);
    }
  }

  console.log('完成。请确保开发者后台已添加事件：');
  console.log('  - drive.file.created_in_folder_v1');
  console.log('  - drive.file.edit_v1');
  console.log('并使用「长连接」或把本服务的 Webhook 配好。');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
