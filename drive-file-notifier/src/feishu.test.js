import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveDriveFileUrl } from './feishu.js';

test('resolveDriveFileUrl prefers meta url', () => {
  assert.equal(
    resolveDriveFileUrl({ url: 'https://example.feishu.cn/file/abc' }, 'abc', 'file'),
    'https://example.feishu.cn/file/abc',
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
});
