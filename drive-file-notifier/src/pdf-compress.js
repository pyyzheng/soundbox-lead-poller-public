import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const MAGICK = process.env.MAGICK_BIN || 'magick';

/**
 * Compress a PDF into a single file under maxBytes using ImageMagick.
 * Tries several quality/density steps; returns null if none fit.
 */
export async function compressPdfUnderLimit(localPath, baseName, maxBytes, tmpDir) {
  const stem = path.basename(baseName, path.extname(baseName)) || 'file';
  const attempts = [
    { density: 150, quality: 70 },
    { density: 120, quality: 55 },
    { density: 100, quality: 45 },
    { density: 90, quality: 35 },
    { density: 72, quality: 30 },
  ];

  for (const [i, opt] of attempts.entries()) {
    const outPath = path.join(tmpDir, `${stem}.compressed-${i}-${Date.now()}.pdf`);
    try {
      await runMagick([
        '-density', String(opt.density),
        localPath,
        '-quality', String(opt.quality),
        '-compress', 'jpeg',
        '-alpha', 'remove',
        outPath,
      ]);
      if (!fs.existsSync(outPath)) continue;
      const size = fs.statSync(outPath).size;
      console.log(
        `[compress] try density=${opt.density} quality=${opt.quality} -> ${formatBytes(size)}`,
      );
      if (size > 0 && size <= maxBytes) {
        return {
          localPath: outPath,
          fileName: `${stem}.pdf`,
          size,
          density: opt.density,
          quality: opt.quality,
        };
      }
      fs.unlinkSync(outPath);
    } catch (err) {
      console.warn(`[compress] attempt failed: ${err.message}`);
      try {
        if (fs.existsSync(outPath)) fs.unlinkSync(outPath);
      } catch {
        // ignore
      }
    }
  }
  return null;
}

function runMagick(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(MAGICK, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (d) => {
      stderr += d.toString();
    });
    child.on('error', (err) => reject(err));
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `magick exit ${code}`));
    });
  });
}

function formatBytes(n) {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}
