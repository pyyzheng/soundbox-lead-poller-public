import { loadConfig } from './config.js';
import { createClient } from './feishu.js';
import { pollCycle } from './poll-cycle.js';
import { loadState, resolveStatePath, saveState } from './state.js';

const DEFAULT_INTERVAL_MS = 30_000;
// Stay well under the 6h GitHub Actions job ceiling; the workflow chains a successor.
const DEFAULT_DURATION_MS = 5 * 60 * 60 * 1000;

/**
 * Long-running poll loop for hosted runners.
 * GitHub's `schedule` events are unreliable and capped at 5 minutes, so a
 * single job polls on a short interval and the workflow starts the next one.
 */
async function main() {
  const statePath = resolveStatePath(loadConfig());
  const state = loadState(statePath);

  let config = loadConfig();
  let client = createClient(config.appId, config.appSecret);
  const intervalMs = Number(process.env.POLL_INTERVAL_MS) || config.pollIntervalMs || DEFAULT_INTERVAL_MS;
  const durationMs = Number(process.env.MAX_RUN_MS) || DEFAULT_DURATION_MS;
  const deadline = Date.now() + durationMs;

  console.log(
    `[loop] polling every ${Math.round(intervalMs / 1000)}s for ${Math.round(durationMs / 60000)}min`,
  );

  let cycles = 0;
  let totalNotified = 0;
  let stopping = false;
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      console.log(`[loop] ${sig} received, finishing current cycle`);
      stopping = true;
    });
  }

  while (!stopping && Date.now() < deadline) {
    cycles += 1;
    try {
      // Pick up watchFolders / config edits without waiting for job restart.
      config = loadConfig();
      client = createClient(config.appId, config.appSecret);
      const { notified } = await pollCycle({ client, config, state, tag: 'loop' });
      totalNotified += notified;
      saveState(statePath, state);
    } catch (err) {
      console.error(`[loop] cycle ${cycles} failed: ${err.message}`);
    }
    if (stopping || Date.now() >= deadline) break;
    await sleep(intervalMs);
  }

  console.log(`[loop] done cycles=${cycles} notified=${totalNotified} state=${statePath}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
