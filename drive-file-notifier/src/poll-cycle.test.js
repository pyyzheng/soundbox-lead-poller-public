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

test('first run seeds nested files without notifying', async () => {
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: SUB, type: 'folder', name: 'nested' }],
      [SUB]: [{ token: FILE_OLD, type: 'file', name: 'old.pdf' }],
    },
    metas: { [FILE_OLD]: { modify: now - 86400, title: 'old.pdf' } },
  };
  const { sent, deps } = makeDeps(client);
  const state = emptyState();
  const { notified, isFirstRun } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(isFirstRun, true);
  assert.equal(notified, 0);
  assert.equal(sent.length, 0);
  assert.deepEqual(state.folderChildren[SUB], [FILE_OLD]);
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
