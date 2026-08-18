/**
 * Persist the notified-token map in the GitHub repo so job cancel/restart
 * does not lose dedup state (Actions cache is only uploaded at job end).
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
  if (res.status === 404) return { notified: {}, sha: null };
  if (!res.ok) {
    throw new Error(`load remote notified failed: HTTP ${res.status}`);
  }

  const body = await res.json();
  const raw = JSON.parse(Buffer.from(body.content, body.encoding || 'base64').toString('utf8'));
  return {
    notified: raw.notified || {},
    sha: body.sha || null,
  };
}

/** Merge and save; retries on SHA conflict (409). */
export async function claimRemoteNotified(token, nowSec = Math.floor(Date.now() / 1000)) {
  const spec = repoSpec();
  if (!spec) return true;

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const { notified, sha } = await loadRemoteNotified();
    if (Number(notified[token]) > 0) return false;

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

    if (res.ok) return true;
    if (res.status === 409) continue;
    const text = await res.text();
    throw new Error(`claim remote notified failed: HTTP ${res.status} ${text.slice(0, 200)}`);
  }
  return false;
}

export async function mergeRemoteNotifiedInto(state) {
  if (!remoteNotifiedEnabled()) return;
  try {
    const { notified } = await loadRemoteNotified();
    if (!state.notified) state.notified = {};
    for (const [token, at] of Object.entries(notified)) {
      const prev = Number(state.notified[token]) || 0;
      const remote = Number(at) || 0;
      if (remote > prev) state.notified[token] = remote;
    }
    if (Object.keys(notified).length > 0 && !state.initialized) {
      state.initialized = true;
      console.log(`[state] remote has notified tokens → marking initialized to skip bootstrap`);
    }
    console.log(`[state] merged ${Object.keys(notified).length} remote notified token(s)`);
  } catch (err) {
    console.warn(`[state] merge remote notified failed: ${err.message}`);
  }
}
