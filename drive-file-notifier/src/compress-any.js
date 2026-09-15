import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { compressPdfUnderLimit } from './pdf-compress.js';

const MAGICK = process.env.MAGICK_BIN || 'magick';
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.tif', '.tiff', '.bmp']);

/**
 * Compress any oversized file into a single payload under maxBytes.
 * Strategy:
 * 1) PDF → ImageMagick quality compress
 * 2) Images → ImageMagick quality compress
 * 3) Fallback for all types → zip
 * Returns null if nothing fits under the limit.
 */
export async function compressAnyUnderLimit(localPath, baseName, maxBytes, tmpDir) {
  const ext = path.extname(baseName || localPath).toLowerCase();

  if (ext === '.pdf') {
    const pdf = await compressPdfUnderLimit(localPath, baseName, maxBytes, tmpDir);
    if (pdf) return { ...pdf, method: 'pdf' };
  }

  if (IMAGE_EXTS.has(ext)) {
    const img = await compressImageUnderLimit(localPath, baseName, maxBytes, tmpDir);
    if (img) return { ...img, method: 'image' };
  }

  const zipped = await zipUnderLimit(localPath, baseName, maxBytes, tmpDir);
  if (zipped) return { ...zipped, method: 'zip' };

  return null;
}

async function compressImageUnderLimit(localPath, baseName, maxBytes, tmpDir) {
  const stem = path.basename(baseName, path.extname(baseName)) || 'image';
  const ext = path.extname(baseName).toLowerCase() || '.jpg';
  const outExt = ['.png', '.gif', '.webp'].includes(ext) ? ext : '.jpg';
  const qualities = [80, 60, 45, 30, 20];

  for (const [i, quality] of qualities.entries()) {
    const outPath = path.join(tmpDir, `${stem}.img-${i}-${Date.now()}${outExt}`);
    try {
      const args = [localPath, '-strip', '-quality', String(quality)];
      if (outExt === '.jpg' || outExt === '.jpeg') {
        args.push('-sampling-factor', '4:2:0');
      }
      args.push(outPath);
      await runCmd(MAGICK, args);
      if (!fs.existsSync(outPath)) continue;
      const size = fs.statSync(outPath).size;
      console.log(`[compress] image quality=${quality} -> ${formatBytes(size)}`);
      if (size > 0 && size <= maxBytes) {
        return {
          localPath: outPath,
          fileName: `${stem}${outExt}`,
          size,
        };
      }
      fs.unlinkSync(outPath);
    } catch (err) {
      console.warn(`[compress] image attempt failed: ${err.message}`);
      try {
        if (fs.existsSync(outPath)) fs.unlinkSync(outPath);
      } catch {
        // ignore
      }
    }
  }
  return null;
}

async function zipUnderLimit(localPath, baseName, maxBytes, tmpDir) {
  const stem = path.basename(baseName, path.extname(baseName)) || 'file';
  const outPath = path.join(tmpDir, `${stem}-${Date.now()}.zip`);
  const entryName = path.basename(baseName) || path.basename(localPath);

  try {
    const packed = await createZipArchive(
      [{ localPath, entryName }],
      outPath,
      tmpDir,
    );
    if (!packed) return null;
    console.log(`[compress] zip -> ${formatBytes(packed.size)}`);
    if (packed.size > 0 && packed.size <= maxBytes) {
      return {
        localPath: packed.localPath,
        fileName: `${stem}.zip`,
        size: packed.size,
      };
    }
    fs.unlinkSync(outPath);
    return null;
  } catch (err) {
    console.warn(`[compress] zip failed: ${err.message}`);
    try {
      if (fs.existsSync(outPath)) fs.unlinkSync(outPath);
    } catch {
      // ignore
    }
    return null;
  }
}

/**
 * Pack one or more local files into a zip.
 * Entries: [{ localPath, entryName }]
 * Returns { localPath, size } or null.
 */
export async function createZipArchive(entries, outPath, tmpDir) {
  if (!entries?.length) return null;
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  if (fs.existsSync(outPath)) fs.unlinkSync(outPath);

  // zip -j needs real filenames on disk; stage unique copies when names collide
  const stageDir = path.join(tmpDir, `zip-stage-${Date.now()}`);
  fs.mkdirSync(stageDir, { recursive: true });
  const staged = [];
  const usedNames = new Set();

  try {
    for (const entry of entries) {
      if (!entry?.localPath || !fs.existsSync(entry.localPath)) continue;
      let name = path.basename(entry.entryName || entry.localPath) || 'file';
      if (usedNames.has(name)) {
        const ext = path.extname(name);
        const stem = path.basename(name, ext) || 'file';
        let i = 2;
        while (usedNames.has(`${stem}_${i}${ext}`)) i += 1;
        name = `${stem}_${i}${ext}`;
      }
      usedNames.add(name);
      const alias = path.join(stageDir, name);
      fs.copyFileSync(entry.localPath, alias);
      staged.push(alias);
    }
    if (!staged.length) return null;

    await runCmd('zip', ['-j', '-9', outPath, ...staged]);
    if (!fs.existsSync(outPath)) return null;
    return { localPath: outPath, size: fs.statSync(outPath).size };
  } finally {
    for (const f of staged) {
      try {
        fs.unlinkSync(f);
      } catch {
        // ignore
      }
    }
    try {
      fs.rmdirSync(stageDir);
    } catch {
      // ignore
    }
  }
}

function runCmd(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], ...opts });
    let stderr = '';
    child.stderr.on('data', (d) => {
      stderr += d.toString();
    });
    child.on('error', (err) => reject(err));
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `${cmd} exit ${code}`));
    });
  });
}

function formatBytes(n) {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}
