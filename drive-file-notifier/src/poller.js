import { getFileMetas } from './feishu.js';

/**
 * Feishu only pushes edit events for online docs (docx/sheet/bitable/slides).
 * Plain Drive files (.md/.pdf/...) change silently, so poll their metadata
 * and notify when latest_modify_time moves forward.
 */
export function createPoller({ client, config, onChange }) {
  const tracked = new Map(); // token -> { type, lastModify, title }
  let timer = null;

  async function seed(entries) {
    const fresh = entries.filter(({ token }) => !tracked.has(token));
    for (const { token, type } of fresh) {
      tracked.set(token, { type: type || 'file', lastModify: null });
    }
    if (!fresh.length) return;
    try {
      const metas = await getFileMetas(client, fresh);
      for (const meta of metas) {
        const entry = tracked.get(meta.doc_token);
        if (entry) {
          entry.lastModify = Number(meta.latest_modify_time) || 0;
          entry.title = meta.title;
        }
      }
    } catch (err) {
      console.warn(`[poll] seed failed: ${err.message}`);
    }
  }

  async function tick() {
    const docs = [...tracked.entries()].map(([token, v]) => ({ token, type: v.type }));
    if (!docs.length) return;

    let metas;
    try {
      metas = await getFileMetas(client, docs);
    } catch (err) {
      console.warn(`[poll] query failed: ${err.message}`);
      return;
    }

    for (const meta of metas) {
      const entry = tracked.get(meta.doc_token);
      if (!entry) continue;
      const modify = Number(meta.latest_modify_time) || 0;
      if (entry.lastModify === null) {
        entry.lastModify = modify;
        entry.title = meta.title;
        continue;
      }
      if (modify > entry.lastModify) {
        entry.lastModify = modify;
        entry.title = meta.title;
        console.log(`[poll] changed ${meta.title} (${meta.doc_token})`);
        try {
          await onChange({ token: meta.doc_token, type: entry.type, meta });
        } catch (err) {
          console.error(`[poll] notify failed ${meta.doc_token}: ${err.message}`);
        }
      }
    }
  }

  return {
    async track(entries) {
      await seed(entries);
    },
    /** Refresh baseline after an event-driven notification to avoid duplicates. */
    async markSeen(token) {
      const entry = tracked.get(token);
      if (!entry) return;
      try {
        const [meta] = await getFileMetas(client, [{ token, type: entry.type }]);
        if (meta) entry.lastModify = Number(meta.latest_modify_time) || entry.lastModify;
      } catch {
        // ignore
      }
    },
    async start() {
      const interval = config.pollIntervalMs || 60000;
      await tick();
      timer = setInterval(() => {
        tick().catch((err) => console.error('[poll] tick error', err));
      }, interval);
      console.log(`[poll] polling every ${Math.round(interval / 1000)}s for ${tracked.size} file(s)`);
    },
    stop() {
      if (timer) clearInterval(timer);
    },
  };
}
