import assert from 'node:assert/strict';
import test from 'node:test';
import { pollCycle } from './poll-cycle.js';

function emptyState() {
  return {
    initialized: false,
    files: {},
    folderChildren: {},
    notified: {},
    folderMeta: {},
    subfolders: {},
  };
}

function makeDeps(client) {
  const sent = [];
  return {
    sent,
    deps: {
      listFolderChildren: async (_c, token) => {
        client.listed.push(token);
        return client.folders[token] || [];
      },
      getFileMetas: async (_c, docs) =>
        docs.map((d) => ({
          doc_token: d.token,
          doc_type: d.type,
          title: client.metas[d.token]?.title || d.token,
          latest_modify_time: client.metas[d.token]?.modify || 0,
        })),
      notifyFileAttachment: async (_c, _cfg, payload) => {
        sent.push({ kind: 'file', ...payload });
        return { skipped: false };
      },
      notifyFilesAsZip: async (_c, _cfg, payload) => {
        sent.push({ kind: 'zip', ...payload });
        return { skipped: false };
      },
      notifyFolderCreated: async (_c, _cfg, payload) => {
        sent.push({ kind: 'folder', ...payload });
        return {};
      },
      maybeAutoSubscribe: async () => {},
    },
  };
}

const ROOT = 'root';
const SUB = 'sub';
const FILE_OLD = 'old';
const FILE_NEW = 'new';
const now = Math.floor(Date.now() / 1000);
const config = { watchFolders: [{ token: ROOT, name: 'root' }], watchFiles: [] };

test('first run notifies recent uploads but seeds old files', async () => {
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: SUB, type: 'folder', name: 'nested' }],
      [SUB]: [
        { token: FILE_OLD, type: 'file', name: 'old.pdf' },
        { token: FILE_NEW, type: 'file', name: 'new.pdf' },
      ],
    },
    metas: {
      [FILE_OLD]: { modify: now - 86400, title: 'old.pdf' },
      [FILE_NEW]: { modify: now - 60, title: 'new.pdf' },
    },
  };
  const { sent, deps } = makeDeps(client);
  const state = emptyState();
  const { notified, isFirstRun } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(isFirstRun, true);
  assert.equal(notified, 1);
  assert.equal(sent[0].fileToken, FILE_NEW);
  assert.deepEqual(state.folderChildren[SUB].sort(), [FILE_OLD, FILE_NEW].sort());
});

test('upload into a never-listed subfolder notifies the recent file', async () => {
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: SUB, type: 'folder', name: 'nested' }],
      [SUB]: [
        { token: FILE_OLD, type: 'file', name: 'old.pdf' },
        { token: FILE_NEW, type: 'file', name: 'new.pdf' },
      ],
    },
    metas: {
      [FILE_OLD]: { modify: now - 86400, title: 'old.pdf' },
      [FILE_NEW]: { modify: now - 60, title: 'new.pdf' },
    },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [SUB] },
    notified: {},
    folderMeta: {},
    subfolders: { [ROOT]: [{ token: SUB, name: 'nested' }] },
  };
  const { sent, deps } = makeDeps(client);
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(notified, 1);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, 'file');
  assert.equal(sent[0].fileToken, FILE_NEW);
});

test('already-listed subfolder notifies a brand new child', async () => {
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: SUB, type: 'folder', name: 'nested' }],
      [SUB]: [
        { token: FILE_OLD, type: 'file', name: 'old.pdf' },
        { token: FILE_NEW, type: 'file', name: 'new.pdf' },
      ],
    },
    metas: {
      [SUB]: { modify: now, title: 'nested' },
      [FILE_OLD]: { modify: now - 86400, title: 'old.pdf' },
      [FILE_NEW]: { modify: now, title: 'new.pdf' },
    },
  };
  const state = {
    initialized: true,
    files: { [FILE_OLD]: { type: 'file', lastModify: now - 86400, title: 'old.pdf' } },
    folderChildren: { [ROOT]: [SUB], [SUB]: [FILE_OLD] },
    notified: {},
    folderMeta: { [SUB]: { lastModify: now - 3600 } },
    subfolders: { [ROOT]: [{ token: SUB, name: 'nested' }] },
  };
  const { sent, deps } = makeDeps(client);
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(notified, 1);
  assert.equal(sent[0].fileToken, FILE_NEW);
});

test('newly uploaded folder notifies all contained files regardless of modify time', async () => {
  const NEW_FOLDER = 'newfolder';
  const FILE1 = 'f1';
  const FILE2 = 'f2';
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: NEW_FOLDER, type: 'folder', name: 'uploaded' }],
      [NEW_FOLDER]: [
        { token: FILE1, type: 'file', name: 'old1.pdf' },
        { token: FILE2, type: 'file', name: 'old2.pdf' },
      ],
    },
    metas: {
      [NEW_FOLDER]: { modify: now, title: 'uploaded' },
      [FILE1]: { modify: now - 86400 * 90, title: 'old1.pdf' },
      [FILE2]: { modify: now - 86400 * 60, title: 'old2.pdf' },
    },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: {},
    folderMeta: {},
    subfolders: {},
  };
  const { sent, deps } = makeDeps(client);
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.ok(sent.some((s) => s.kind === 'zip'));
  assert.ok(sent.some((s) => s.kind === 'folder'));
  assert.equal(sent.find((s) => s.kind === 'zip').files.length, 2);
  assert.ok(notified >= 2);
});

test('does not resend a file already notified within the dedup window', async () => {
  const client = {
    listed: [],
    folders: { [ROOT]: [{ token: FILE_NEW, type: 'file', name: 'new.pdf' }] },
    metas: { [FILE_NEW]: { modify: now, title: 'new.pdf' } },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: { [FILE_NEW]: now - 60 },
    folderMeta: {},
    subfolders: {},
  };
  const { sent, deps } = makeDeps(client);
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(notified, 0);
  assert.equal(sent.length, 0);
});
