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

test('newly uploaded folder only notifies recent files inside', async () => {

  const NEW_FOLDER = 'newfolder';
  const FILE1 = 'f1';
  const FILE2 = 'f2';
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: NEW_FOLDER, type: 'folder', name: 'uploaded' }],
      [NEW_FOLDER]: [
        { token: FILE1, type: 'file', name: 'old1.pdf' },
        { token: FILE2, type: 'file', name: 'new2.pdf' },
      ],
    },
    metas: {
      [NEW_FOLDER]: { modify: now, title: 'uploaded' },
      [FILE1]: { modify: now - 86400 * 90, title: 'old1.pdf' },
      [FILE2]: { modify: now - 60, title: 'new2.pdf' },
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
  assert.equal(notified, 1);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, 'file');
  assert.equal(sent[0].fileToken, FILE2);
});

test('files in two sibling folders send two separate notices', async () => {
  const A = 'projA';
  const B = 'projB';
  const A1 = 'a1';
  const A2 = 'a2';
  const B1 = 'b1';
  const B2 = 'b2';
  const client = {
    listed: [],
    folders: {
      [ROOT]: [
        { token: A, type: 'folder', name: 'project-a' },
        { token: B, type: 'folder', name: 'project-b' },
      ],
      [A]: [
        { token: A1, type: 'file', name: '图层 1.jpg' },
        { token: A2, type: 'file', name: '图层 2.jpg' },
      ],
      [B]: [
        { token: B1, type: 'file', name: '图层 1.jpg' },
        { token: B2, type: 'file', name: '图层 2.jpg' },
      ],
    },
    metas: {
      [A]: { modify: now, title: 'project-a' },
      [B]: { modify: now, title: 'project-b' },
      [A1]: { modify: now, title: '图层 1.jpg' },
      [A2]: { modify: now, title: '图层 2.jpg' },
      [B1]: { modify: now, title: '图层 1.jpg' },
      [B2]: { modify: now, title: '图层 2.jpg' },
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
  const zips = sent.filter((s) => s.kind === 'zip');
  assert.equal(zips.length, 2);
  assert.equal(notified, 2);
  assert.equal(zips[0].folderToken, A);
  assert.equal(zips[1].folderToken, B);
  assert.equal(zips[0].files.length, 2);
  assert.equal(zips[1].files.length, 2);
});

test('never resends a new-file notice for a token already in notified', async () => {
  const client = {
    listed: [],
    folders: { [ROOT]: [{ token: FILE_NEW, type: 'file', name: 'huge.pdf' }] },
    metas: { [FILE_NEW]: { modify: now, title: 'huge.pdf' } },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: { [FILE_NEW]: now - 3 * 86400 },
    folderMeta: {},
    subfolders: {},
  };
  const { sent, deps } = makeDeps(client);
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(notified, 0);
  assert.equal(sent.length, 0);
});

test('second poll does not resend a file notified in the previous cycle', async () => {
  const client = {
    listed: [],
    folders: { [ROOT]: [{ token: FILE_NEW, type: 'file', name: 'new.pdf' }] },
    metas: { [FILE_NEW]: { modify: now, title: 'new.pdf' } },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: {},
    folderMeta: {},
    subfolders: {},
  };
  const claimRemote = async (token, at) => {
    if (state.notified[token]) return false;
    state.notified[token] = at;
    return true;
  };
  const first = makeDeps(client);
  first.deps.claimRemoteNotified = claimRemote;
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps: first.deps });
  assert.equal(notified, 1);
  client.listed = [];
  const second = makeDeps(client);
  second.deps.claimRemoteNotified = claimRemote;
  const again = await pollCycle({ client, config, state, tag: 't', deps: second.deps });
  assert.equal(again.notified, 0);
  assert.equal(second.sent.length, 0);
});

test('remote claim failure skips duplicate notify across job restarts', async () => {
  const client = {
    listed: [],
    folders: { [ROOT]: [{ token: FILE_NEW, type: 'file', name: 'huge.pdf' }] },
    metas: { [FILE_NEW]: { modify: now, title: 'huge.pdf' } },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: {},
    folderMeta: {},
    subfolders: {},
  };
  const remote = { [FILE_NEW]: now - 100 };
  const { sent, deps } = makeDeps(client);
  deps.claimRemoteNotified = async (token) => !remote[token];
  const { notified } = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(notified, 0);
  assert.equal(sent.length, 0);
});

test('never notifies an empty newly created folder', async () => {
  const NEW_FOLDER = 'new-empty-folder';
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: NEW_FOLDER, type: 'folder', name: 'new-empty' }],
      [NEW_FOLDER]: [],
    },
    metas: {
      [NEW_FOLDER]: { modify: now, title: 'new-empty' },
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
  const result = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(result.notified, 0);
  assert.equal(sent.length, 0);
});

test('does not notify empty folder when it has subfolders', async () => {
  const NEW_FOLDER = 'new-parent';
  const SUB_FOLDER = 'new-child';
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: NEW_FOLDER, type: 'folder', name: 'new-parent' }],
      [NEW_FOLDER]: [{ token: SUB_FOLDER, type: 'folder', name: 'new-child' }],
      [SUB_FOLDER]: [],
    },
    metas: {
      [NEW_FOLDER]: { modify: now, title: 'new-parent' },
      [SUB_FOLDER]: { modify: now, title: 'new-child' },
    },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: {},
    folderMeta: {},
    subfolders: {},
    emptyFolderPending: {},
  };
  const { sent, deps } = makeDeps(client);
  const cfg = { ...config, emptyFolderNotifyGraceSec: 0 };
  const result = await pollCycle({ client, config: cfg, state, tag: 't', deps });
  assert.equal(result.notified, 0);
  assert.equal(sent.some((s) => s.kind === 'folder'), false);
});

test('eventually notifies deep uploaded file after transient subfolder scan failure', async () => {
  const L1 = 'level1';
  const L2 = 'level2';
  const IMG = 'img1';
  let l2FailedOnce = false;
  const client = {
    listed: [],
    folders: {
      [ROOT]: [{ token: L1, type: 'folder', name: 'level1' }],
      [L1]: [{ token: L2, type: 'folder', name: 'level2' }],
      [L2]: [{ token: IMG, type: 'file', name: 'photo.png' }],
    },
    metas: {
      [L1]: { modify: now, title: 'level1' },
      [L2]: { modify: now, title: 'level2' },
      [IMG]: { modify: now, title: 'photo.png' },
    },
  };
  const state = {
    initialized: true,
    files: {},
    folderChildren: { [ROOT]: [] },
    notified: {},
    folderMeta: {},
    subfolders: {},
    emptyFolderPending: {},
  };
  const { sent, deps } = makeDeps(client);
  deps.listFolderChildren = async (_c, token) => {
    client.listed.push(token);
    if (token === L2 && !l2FailedOnce) {
      l2FailedOnce = true;
      throw new Error('transient 502');
    }
    return client.folders[token] || [];
  };
  const claimRemote = async (token, at) => {
    if (state.notified[token]) return false;
    state.notified[token] = at;
    return true;
  };
  deps.claimRemoteNotified = claimRemote;

  const first = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(first.notified, 0);
  assert.equal(sent.length, 0);

  const second = await pollCycle({ client, config, state, tag: 't', deps });
  assert.equal(second.notified, 1);
  assert.equal(sent.filter((s) => s.kind === 'file').length, 1);
  assert.equal(sent.find((s) => s.kind === 'file').fileToken, IMG);
});
