import { loadConfig } from './config.js';
import { createClient } from './feishu.js';
import { notifyFileAttachment } from './notify.js';

/**
 * Manual one-shot send for debugging:
 *   node src/send-once.js HubRbexqdo5VRqxh220c1TUpn0f file
 */
async function main() {
  const fileToken = process.argv[2];
  const fileType = process.argv[3] || 'file';
  if (!fileToken) {
    console.error('用法: node src/send-once.js <file_token> [file_type]');
    process.exit(1);
  }

  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);
  const result = await notifyFileAttachment(client, config, {
    fileToken,
    fileType,
    reason: '手动测试发送',
  });
  console.log(result);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
