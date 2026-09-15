import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveDriveFileUrl } from './feishu.js';

test('resolveDriveFileUrl prefers http meta url', () => {
  assert.equal(
    resolveDriveFileUrl({ url: 'https://example.feishu.cn/file/abc' }, 'abc', 'file'),
    'https://example.feishu.cn/file/abc',
  );
});

test('resolveDriveFileUrl ignores non-http meta.url such as a file name', () => {
  assert.equal(
    resolveDriveFileUrl({ url: '模块宠物舱-折页-B4.pdf' }, 'tok123', 'file'),
    'https://www.feishu.cn/file/tok123',
  );
});

test('resolveDriveFileUrl falls back by file type', () => {
  assert.equal(
    resolveDriveFileUrl(null, 'tok123', 'docx'),
    'https://www.feishu.cn/docx/tok123',
  );
  assert.equal(
    resolveDriveFileUrl(null, 'tok123', 'file'),
    'https://www.feishu.cn/file/tok123',
  );
  assert.equal(
    resolveDriveFileUrl(null, 'tok123', 'folder'),
    'https://www.feishu.cn/drive/folder/tok123',
  );
});
