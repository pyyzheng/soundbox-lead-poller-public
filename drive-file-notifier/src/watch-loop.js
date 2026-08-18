import { loadConfig } from './config.js';
import { createClient } from './feishu.js';
import { pollCycle } from './poll-cycle.js';
import { mergeRemoteNotifiedInto, remoteNotifiedEnabled } from './remote-notified.js';
import { loadState, resolveStatePath, saveState } from './state.js';

const DEFAULT_INTERVAL_MS = 30_000;
const DEFAULT_DURATION_MS = 5 * 60 * 60 * 1000;

function stateDigest(state) {
  const notifiedCount = Object.keys(state.notified || {}).length;
  const filesCount = Object.keys(state.files || {}).length;
  const foldersCount = Object.keys(state.folderChildren || {}).length;
  return `initialized=${!!state.initialized} notified=${notifiedCount} files=${filesCount} folders=${foldersCount}`;
}

async function main() {
  const statePath = resolveStatePath(loadConfig());
  const state = loadState(statePath);
  console.log(`[boot] state loaded from ${statePath}: ${stateDigest(state)}`);

  console.log(`[boot] GITHUB_REPOSITORY=${process.env.GITHUB_REPOSITORY || '(unset)'}`);
  console.log(`[boot] GITHUB_TOKEN=${process.env.GITHUB_TOKEN ? 'set' : 'unset'} GH_TOKEN=${process.env.GH_TOKEN ? 'set' : 'unset'}`);
  console.log(`[boot] remoteNotifiedEnabled=${remoteNotifiedEnabled()}`);

  if (remoteNotifiedEnabled()) {
    await mergeRemoteNotifiedInto(state);
    saveState(statePath, state);
    console.log(`[boot] state after remote merge: ${stateDigest(state)}`);
  } else {
    console.warn('[boot] remote notified NOT enabled — dedup depends only on local cache');
  }

  let config = loadConfig();
  let client = createClient(config.appId, config.appSecret);
  const intervalMs = Number(process.env.POLL_INTERVAL_MS) || config.pollIntervalMs || DEFAULT_INTERVAL_MS;
  const durationMs = Number(process.env.MAX_RUN_MS) || DEFAULT_DURATION_MS;
  const deadline = Date.now() + durationMs;
  const persistState = () => saveState(statePath, state);

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
    const cycleStart = Date.now();
    try {
      config = loadConfig();
      client = createClient(config.appId, config.appSecret);
      const { notified } = await pollCycle({
        client,
        config,
        state,
        tag: 'loop',
        deps: { persistState },
      });
      totalNotified += notified;
      persistState();
    } catch (err) {
      console.error(`[loop] cycle ${cycles} failed: ${err.message}`);
      console.error(err.stack);
      persistState();
    }

    const elapsed = Date.now() - cycleStart;
    if (stopping || Date.now() >= deadline) break;
    const waitMs = Math.max(0, intervalMs - elapsed);
    if (waitMs > 0) await sleep(waitMs);
  }

  console.log(`[loop] done cycles=${cycles} notified=${totalNotified} state=${statePath}`);
  console.log(`[loop] final state: ${stateDigest(state)}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
