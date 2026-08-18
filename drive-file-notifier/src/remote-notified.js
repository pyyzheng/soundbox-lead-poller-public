/**
 * Persist the notified-token map in the GitHub repo so job cancel/restart
 * does not lose dedup state (Actions cache is only uploaded at job end).
 *
 * Uses GITHUB_TOKEN first (its pushes don't trigger workflows), falls back
 * to GH_TOKEN (PAT). If neither is available, all operations silently succeed
 * so the notifier still works with local-only dedup.
 */

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

/** @returns {{ notified: Record<string, number>, sha: string|null }} */
export async function loadRemoteNotified() {
  const spec = repoSpec();
  if (!spec) return { notified: {}, sha: null };

  const url = `${API}/repos/${spec.owner}/${spec.name}/contents/${STATE_PATH}`;
  const res = await fetch(url, { headers: headers(spec.token) });
  if (res.status === 404) {
    console.log('[remote] .remote-notified.json not found (404), starting fresh');
    return { notified: {}, sha: null };
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`load remote notified HTTP ${res.status}: ${text.slice(0, 200)}`);
  }

  const body = await res.json();
  const raw = JSON.parse(Buffer.from(body.content, body.encoding || 'base64').toString('utf8'));
  const count = Object.keys(raw.notified || {}).length;
  console.log(`[remote] loaded ${count} notified token(s), sha=${body.sha?.slice(0, 8)}`);
  return {
    notified: raw.notified || {},
    sha: body.sha || null,
  };
}

/** Claim a token atomically; retries on SHA conflict (409). Returns false if already claimed. */
export async function claimRemoteNotified(token, nowSec = Math.floor(Date.now() / 1000)) {
  const spec = repoSpec();
  if (!spec) {
    console.log(`[remote] no repo spec, skip claim token=${token.slice(0, 8)}`);
    return true;
  }

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const { notified, sha } = await loadRemoteNotified();
    if (Number(notified[token]) > 0) {
      console.log(`[remote] token=${token.slice(0, 8)} already claimed at=${notified[token]}, rejecting`);
      return false;
    }

    notified[token] = nowSec;
    const payload = {
      message: `drive-notifier: mark notified ${token.slice(0, 8)}`,
      content: Buffer.from(JSON.stringify({
        updatedAt: new Date().toISOString(),
        notified,
      }, null, 2)).toString('base64'),
    };
    if (sha) payload.sha = sha;

    const url = `${API}/repos/${spec.owner}/${spec.name}/contents/${STATE_PATH}`;
    const res = await fetch(url, {
      method: 'PUT',
      headers: headers(spec.token),
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      console.log(`[remote] claimed token=${token.slice(0, 8)} attempt=${attempt + 1}`);
      return true;
    }
    if (res.status === 409) {
      console.warn(`[remote] SHA conflict on attempt ${attempt + 1}, retrying`);
      continue;
    }
    if (res.status === 403 || res.status === 422) {
      const text = await res.text().catch(() => '');
      console.warn(`[remote] claim failed HTTP ${res.status} (permission issue, proceeding with local dedup): ${text.slice(0, 150)}`);
      return true;
    }
    const text = await res.text().catch(() => '');
    throw new Error(`claim remote notified HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  console.warn('[remote] claim exhausted 5 retries, proceeding with local dedup');
  return true;
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
