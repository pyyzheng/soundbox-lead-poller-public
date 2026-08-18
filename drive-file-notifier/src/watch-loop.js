import { loadConfig } from './config.js';
import { createClient } from './feishu.js';
import { pollCycle } from './poll-cycle.js';
import { loadState, resolveStatePath, saveState } from './state.js';

const DEFAULT_INTERVAL_MS = 30_000;
const DEFAULT_DURATION_MS = 5 * 60 * 60 * 1000;
// Large Drive trees can take several minutes; never start the next cycle on top.
const DEFAULT_CYCLE_TIMEOUT_MS = 20 * 60 * 1000;

/**
 * Long-running poll loop for hosted runners.
 */
async function main() {
  const statePath = resolveStatePath(loadConfig());
  const state = loadState(statePath);

  let config = loadConfig();
  let client = createClient(config.appId, config.appSecret);
  const intervalMs = Number(process.env.POLL_INTERVAL_MS) || config.pollIntervalMs || DEFAULT_INTERVAL_MS;
  const durationMs = Number(process.env.MAX_RUN_MS) || DEFAULT_DURATION_MS;
  const cycleTimeoutMs = Number(process.env.CYCLE_TIMEOUT_MS) || config.cycleTimeoutMs || DEFAULT_CYCLE_TIMEOUT_MS;
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
    const cycleStart = Date.now();
    try {
      config = loadConfig();
      client = createClient(config.appId, config.appSecret);
      const { notified } = await withTimeout(
        pollCycle({ client, config, state, tag: 'loop' }),
        cycleTimeoutMs,
        `poll cycle exceeded ${Math.round(cycleTimeoutMs / 1000)}s`,
      );
      totalNotified += notified;
      saveState(statePath, state);
    } catch (err) {
      console.error(`[loop] cycle ${cycles} failed: ${err.message}`);
    }

    const elapsed = Date.now() - cycleStart;
    if (stopping || Date.now() >= deadline) break;
    const waitMs = Math.max(0, intervalMs - elapsed);
    if (waitMs > 0) await sleep(waitMs);
  }

  console.log(`[loop] done cycles=${cycles} notified=${totalNotified} state=${statePath}`);
}

function withTimeout(promise, ms, message) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
