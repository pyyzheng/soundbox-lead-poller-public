import fs from 'node:fs';
import path from 'node:path';

const EMPTY_STATE = {
  initialized: false,
  files: {},
  folderChildren: {},
  notified: {},
  folderMeta: {},
  subfolders: {},
};

export function resolveStatePath(config) {
  return process.env.STATE_PATH || path.join(config.root, '.drive-notifier-state.json');
}

export function loadState(statePath) {
  try {
    if (fs.existsSync(statePath)) {
      return { ...EMPTY_STATE, ...JSON.parse(fs.readFileSync(statePath, 'utf8')) };
    }
  } catch (err) {
    console.warn(`[state] bad state file, resetting: ${err.message}`);
  }
  return { ...EMPTY_STATE };
}

export function saveState(statePath, state) {
  state.initialized = true;
  state.updatedAt = new Date().toISOString();
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2));
}
