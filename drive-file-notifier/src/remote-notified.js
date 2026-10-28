/**
 * Persist the notified-token map in the GitHub repo so job cancel/restart
 * does not lose dedup state (Actions cache is only uploaded at job end).
 *
 * Uses GITHUB_TOKEN first (its pushes don't trigger workflows), falls back
 * to GH_TOKEN (PAT). If neither is available, all operations silently succeed
 * so the notifier still works with local-only dedup.
 */

import { pruneReviews } from './review-card.js';

const STATE_PATH = 'drive-file-notifier/.remote-notified.json';
const API = 'https://api.github.com';

function repoSpec() {
  const repo = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN;
  if (!repo || !token) return null;
  const [owner, name] = repo.split('/');
  if (!owner || !name) return null;
  return { owner, name, token };
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
  };
}

export function remoteNotifiedEnabled() {
  return Boolean(repoSpec());
}

function emptyDoc() {
  return { updatedAt: '', notified: {}, reviews: {} };
}

/** @returns {{ doc: { updatedAt: string, notified: Record<string, number>, reviews: object }, sha: string|null }} */
export async function loadRemoteDoc() {
  const spec = repoSpec();
  if (!spec) return { doc: emptyDoc(), sha: null };

  const url = `${API}/repos/${spec.owner}/${spec.name}/contents/${STATE_PATH}`;
  const res = await fetch(url, { headers: headers(spec.token) });
  if (res.status === 404) {
    console.log('[remote] .remote-notified.json not found (404), starting fresh');
    return { doc: emptyDoc(), sha: null };
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`load remote notified HTTP ${res.status}: ${text.slice(0, 200)}`);
  }

  const body = await res.json();
  const raw = JSON.parse(Buffer.from(body.content, body.encoding || 'base64').toString('utf8'));
  const notified = raw.notified || {};
  const reviews = raw.reviews || {};
  console.log(`[remote] loaded ${Object.keys(notified).length} notified token(s), ${Object.keys(reviews).length} review(s), sha=${body.sha?.slice(0, 8)}`);
  return {
    doc: {
      updatedAt: raw.updatedAt || '',
      notified,
      reviews,
    },
    sha: body.sha || null,
  };
}

/** @returns {{ notified: Record<string, number>, sha: string|null }} */
export async function loadRemoteNotified() {
  const { doc, sha } = await loadRemoteDoc();
  return { notified: doc.notified || {}, sha };
}

/**
 * Read-modify-write the remote JSON. `mutate` returns `{ changed, doc }`.
 * `changed: false` leaves the file untouched (for example a duplicate claim).
 */
async function updateRemoteDoc(mutate, message) {
  const spec = repoSpec();
  if (!spec) return { changed: false, disabled: true };

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const { doc, sha } = await loadRemoteDoc();
    const next = mutate(doc);
    if (!next?.changed) return { changed: false, doc };

    const payload = {
      message,
      content: Buffer.from(JSON.stringify(next.doc, null, 2)).toString('base64'),
    };
    if (sha) payload.sha = sha;
    const url = `${API}/repos/${spec.owner}/${spec.name}/contents/${STATE_PATH}`;
    const res = await fetch(url, {
      method: 'PUT',
      headers: headers(spec.token),
      body: JSON.stringify(payload),
    });
    if (res.ok) return { changed: true, doc: next.doc };
    if (res.status === 409) {
      console.warn(`[remote] SHA conflict on attempt ${attempt + 1}, retrying`);
      continue;
    }
    const text = await res.text().catch(() => '');
    const error = new Error(`update remote doc HTTP ${res.status}: ${text.slice(0, 200)}`);
    error.status = res.status;
    throw error;
  }
  const error = new Error('update remote doc exhausted 5 retries');
  error.status = 409;
  throw error;
}

/** Claim a token atomically; retries on SHA conflict (409). Returns false if already claimed. */
export async function claimRemoteNotified(token, nowSec = Math.floor(Date.now() / 1000)) {
  const spec = repoSpec();
  if (!spec) {
    console.log(`[remote] no repo spec, skip claim token=${token.slice(0, 8)}`);
    return true;
  }

  try {
    const result = await updateRemoteDoc((doc) => {
      if (Number(doc.notified?.[token]) > 0) return { changed: false, doc };
      return {
        changed: true,
        doc: {
          updatedAt: new Date().toISOString(),
          notified: { ...(doc.notified || {}), [token]: nowSec },
          reviews: doc.reviews || {},
        },
      };
    }, `drive-notifier: mark notified ${token.slice(0, 8)}`);
    if (!result.changed) {
      console.log(`[remote] token=${token.slice(0, 8)} already claimed, rejecting`);
      return false;
    }
    console.log(`[remote] claimed token=${token.slice(0, 8)}`);
    return true;
  } catch (err) {
    if (err.status === 403 || err.status === 422) {
      console.warn(`[remote] claim failed HTTP ${err.status} (permission issue, proceeding with local dedup): ${err.message.slice(0, 150)}`);
      return true;
    }
    if (err.status === 409) {
      console.warn('[remote] claim exhausted 5 retries, proceeding with local dedup');
      return true;
    }
    throw err;
  }
}

export async function saveRemoteReviews(reviews) {
  if (!remoteNotifiedEnabled()) return;
  try {
    await updateRemoteDoc((doc) => ({
      changed: true,
      doc: {
        updatedAt: new Date().toISOString(),
        notified: doc.notified || {},
        reviews: pruneReviews(reviews),
      },
    }), 'drive-notifier: update reviews');
  } catch (err) {
    console.warn(`[remote] save reviews failed: ${err.message}`);
  }
}

export async function mergeRemoteReviewsInto(state) {
  if (!remoteNotifiedEnabled()) return;
  try {
    const { doc } = await loadRemoteDoc();
    if (!state.reviews) state.reviews = {};
    let merged = 0;
    for (const [id, remote] of Object.entries(doc.reviews || {})) {
      const local = state.reviews[id];
      const remoteAt = Date.parse(remote?.updatedAt || remote?.createdAt || '');
      const localAt = Date.parse(local?.updatedAt || local?.createdAt || '');
      if (!local || !(localAt > remoteAt)) {
        state.reviews[id] = remote;
        merged += 1;
      }
    }
    console.log(`[state] merged ${merged} review(s) from remote (remote total: ${Object.keys(doc.reviews || {}).length})`);
  } catch (err) {
    console.warn(`[state] merge remote reviews failed: ${err.message}`);
  }
}

export async function mergeRemoteNotifiedInto(state) {
  if (!remoteNotifiedEnabled()) return;
  try {
    const { notified } = await loadRemoteNotified();
    if (!state.notified) state.notified = {};
    let merged = 0;
    for (const [token, at] of Object.entries(notified)) {
      const prev = Number(state.notified[token]) || 0;
      const remote = Number(at) || 0;
      if (remote > prev) {
        state.notified[token] = remote;
        merged += 1;
      }
    }
    const remoteCount = Object.keys(notified).length;
    if (remoteCount > 0 && !state.initialized) {
      state.initialized = true;
      console.log(`[state] remote has ${remoteCount} notified tokens → marking initialized to skip bootstrap`);
    }
    console.log(`[state] merged ${merged} new token(s) from remote (remote total: ${remoteCount}, local total: ${Object.keys(state.notified).length})`);
  } catch (err) {
    console.warn(`[state] merge remote notified failed: ${err.message}`);
  }
}
