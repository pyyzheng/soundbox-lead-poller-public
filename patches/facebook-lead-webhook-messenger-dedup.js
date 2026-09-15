/**
 * Patch for facebook-lead-webhook/src/feishu.js
 *
 * Problem: writeLead (Lead Ad) + writeMessengerLead both insert for the same email.
 * Fix: skip Messenger write when a recent Facebook Lead Ad row already exists.
 *
 * Steps:
 *   1. Paste findRecentFacebookLeadAd below into feishu.js (reuse existing feishuSearch).
 *   2. At top of writeMessengerLead(), after lead.email is known, call the guard below.
 *   3. cd facebook-lead-webhook && npx wrangler deploy
 */

const LEAD_AD_CHANNEL = 'Facebook';
const MESSENGER_PAIR_WINDOW_MS = 60 * 60 * 1000; // sync with MESSENGER_DEDUP_PAIR_MINUTES

function normalizeMessengerEmail(email) {
  const value = String(email || '').trim().toLowerCase();
  if (!value || value === 'n/a' || value === 'na') return '';
  if (value.includes(',')) {
    for (const part of value.split(',')) {
      const p = part.trim();
      if (p && p !== 'n/a' && p !== 'na') return p;
    }
    return '';
  }
  return value;
}

/**
 * Uses existing feishuSearch(token, env, body) from feishu.js.
 * @returns {Promise<{record_id: string, lead_id?: string}|null>}
 */
export async function findRecentFacebookLeadAd(token, email, env, windowMs = MESSENGER_PAIR_WINDOW_MS) {
  const normalized = normalizeMessengerEmail(email);
  if (!normalized) return null;

  const cutoff = Date.now() - windowMs;
  const body = {
    filter: {
      conjunction: 'and',
      conditions: [
        { field_name: 'Email（客户邮箱）', operator: 'is', value: [normalized] },
        { field_name: 'Channels（渠道）', operator: 'is', value: [LEAD_AD_CHANNEL] },
      ],
    },
    field_names: ['线索ID', 'Entry Time（录入时间）', 'Channels（渠道）', 'Email（客户邮箱）'],
    sort: [{ field_name: 'Entry Time（录入时间）', desc: true }],
    page_size: 5,
  };

  const data = await feishuSearch(token, env, body);
  const items = data?.items || [];
  for (const item of items) {
    const entryMs = item.fields?.['Entry Time（录入时间）'];
    if (typeof entryMs === 'number' && entryMs >= cutoff) {
      return {
        record_id: item.record_id,
        lead_id: item.fields?.['线索ID'],
      };
    }
  }
  return null;
}

// --- paste into writeMessengerLead() after email is parsed ---
//
//   const existingLeadAd = await findRecentFacebookLeadAd(token, lead.email, env);
//   if (existingLeadAd) {
//     console.log(
//       `[writeMessengerLead] skip duplicate email=${lead.email} existing=${existingLeadAd.record_id}`
//     );
//     return { skipped: true, reason: 'facebook_lead_ad_exists', existing: existingLeadAd };
//   }
//
// Return shape matches other early-exit paths in writeMessengerLead; callers should
// treat skipped=true as success (no Feishu row needed).
