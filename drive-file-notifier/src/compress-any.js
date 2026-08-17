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
    // -j: junk paths; -9: max compression
    await runCmd('zip', ['-j', '-9', outPath, localPath], {
      // Ensure zip stores the preferred filename when possible
      cwd: path.dirname(localPath),
    });

    // Re-pack with desired entry name if needed
    if (path.basename(localPath) !== entryName && fs.existsSync(outPath)) {
      fs.unlinkSync(outPath);
      const alias = path.join(tmpDir, entryName);
      fs.copyFileSync(localPath, alias);
      await runCmd('zip', ['-j', '-9', outPath, alias]);
      fs.unlinkSync(alias);
    }

    if (!fs.existsSync(outPath)) return null;
    const size = fs.statSync(outPath).size;
    console.log(`[compress] zip -> ${formatBytes(size)}`);
    if (size > 0 && size <= maxBytes) {
      return {
        localPath: outPath,
        fileName: `${stem}.zip`,
        size,
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
