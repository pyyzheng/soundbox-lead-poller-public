import { loadConfig } from './config.js';
import { createClient } from './feishu.js';
import { pollCycle } from './poll-cycle.js';
import { loadState, resolveStatePath, saveState } from './state.js';

/** Single detection pass, for cron-style runs. */
async function main() {
  const config = loadConfig();
  const client = createClient(config.appId, config.appSecret);
  const statePath = resolveStatePath(config);
  const state = loadState(statePath);

  const { notified, isFirstRun } = await pollCycle({ client, config, state, tag: 'once' });
  saveState(statePath, state);
  console.log(`[once] done notified=${notified} firstRun=${isFirstRun} state=${statePath}`);
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
