import fs from 'node:fs';
import path from 'node:path';
import { PDFDocument } from 'pdf-lib';

/**
 * Split an oversized PDF into multiple smaller PDFs, each under maxBytes.
 * Returns local file paths + display names. Caller cleans them up.
 */
export async function splitPdfUnderLimit(localPath, baseName, maxBytes, tmpDir) {
  const src = await PDFDocument.load(fs.readFileSync(localPath), {
    ignoreEncryption: true,
  });
  const total = src.getPageCount();
  if (total <= 0) {
    throw new Error('PDF 无有效页面，无法拆分');
  }

  // Soft margin so multipart encoding stays under IM 30MB hard limit
  const limit = Math.max(1024 * 1024, Math.floor(maxBytes * 0.92));
  const parts = [];
  let start = 0;
  let partIndex = 1;

  while (start < total) {
    let low = 1;
    let high = total - start;
    let best = null;

    while (low <= high) {
      const mid = Math.floor((low + high) / 2);
      const bytes = await buildPartBytes(src, start, mid);
      if (bytes.length <= limit) {
        best = { pageCount: mid, bytes };
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }

    if (!best) {
      // Single page still too large — emit it anyway so caller can report
      const bytes = await buildPartBytes(src, start, 1);
      best = { pageCount: 1, bytes, oversized: true };
    }

    const stem = path.basename(baseName, path.extname(baseName)) || 'file';
    const outName = `${stem}.part${partIndex}.pdf`;
    const outPath = path.join(tmpDir, `${stem}.part${partIndex}-${Date.now()}.pdf`);
    fs.writeFileSync(outPath, best.bytes);
    parts.push({
      localPath: outPath,
      fileName: outName,
      size: best.bytes.length,
      oversized: Boolean(best.oversized),
      pageFrom: start + 1,
      pageTo: start + best.pageCount,
    });

    start += best.pageCount;
    partIndex += 1;

    if (partIndex > 50) {
      throw new Error('PDF 拆分份数过多，已中止');
    }
  }

  return parts;
}

async function buildPartBytes(src, start, pageCount) {
  const part = await PDFDocument.create();
  const indices = Array.from({ length: pageCount }, (_, i) => start + i);
  const pages = await part.copyPages(src, indices);
  for (const page of pages) part.addPage(page);
  return Buffer.from(await part.save({ useObjectStreams: false }));
}
