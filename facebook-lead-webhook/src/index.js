var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/verify.js
async function verifySignature(body, signature, appSecret) {
  if (!signature) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(appSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(body)
  );
  const hex = [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
  const expected = "sha256=" + hex;
  if (expected.length !== signature.length) return false;
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) {
    mismatch |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return mismatch === 0;
}
__name(verifySignature, "verifySignature");
function handleVerification(url, verifyToken) {
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token");
  const challenge = url.searchParams.get("hub.challenge");
  if (mode === "subscribe" && token === verifyToken) {
    return new Response(challenge, { status: 200 });
  }
  return new Response("Forbidden", { status: 403 });
}
__name(handleVerification, "handleVerification");

// src/meta-api.js
var META_API_BASE = "https://graph.facebook.com";
async function metaGet(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15e3);
  let res;
  try {
    res = await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
  const data = await res.json();
  if (data.error) {
    const err = data.error;
    if (err.code === 190) throw new Error(`Meta API token expired: ${err.message}`);
    if (err.code === 17 || err.code === 80004 || res.status === 429) {
      throw new Error(`Meta API rate limited: ${err.message}`);
    }
    throw new Error(`Meta API error ${err.code}: ${err.message}`);
  }
  return data;
}
__name(metaGet, "metaGet");
async function fetchLeadData(leadgenId, accessToken, apiVersion = "v21.0") {
  const url = `${META_API_BASE}/${apiVersion}/${leadgenId}?fields=field_data,created_time,form_id,ad_id&access_token=${accessToken}`;
  const data = await metaGet(url);
  const fields = {};
  if (data.field_data && Array.isArray(data.field_data)) {
    for (const f of data.field_data) {
      const val = f.values && f.values.length > 0 ? f.values[0] : "";
      fields[f.name] = val;
    }
  }
  return {
    leadgen_id: data.id || leadgenId,
    form_id: data.form_id || "",
    ad_id: data.ad_id || "",
    created_time: data.created_time || "",
    fields
  };
}
__name(fetchLeadData, "fetchLeadData");
async function listForms(pageId, accessToken, apiVersion = "v21.0") {
  const forms = [];
  let url = `${META_API_BASE}/${apiVersion}/${pageId}/leadgen_forms?fields=id,name,status,created_time,updated_time&limit=100&access_token=${accessToken}`;
  while (url) {
    const data = await metaGet(url);
    for (const f of data.data || []) {
      if (f.status === "ACTIVE") forms.push({ id: f.id, name: f.name, created_time: f.created_time, updated_time: f.updated_time });
    }
    url = data.paging?.next || null;
  }
  return forms;
}
__name(listForms, "listForms");
async function listLeads(formId, sinceISO, accessToken, apiVersion = "v21.0") {
  const sinceTs = new Date(sinceISO).getTime();
  const leads = [];
  let url = `${META_API_BASE}/${apiVersion}/${formId}/leads?fields=id,created_time&limit=100&access_token=${accessToken}`;
  while (url) {
    const data = await metaGet(url);
    for (const l of data.data || []) {
      const ct = new Date(l.created_time).getTime();
      if (ct < sinceTs) {
        return { leads, debug: { total: leads.length, stopped: true, stoppedAt: l.created_time } };
      }
      leads.push({ leadgen_id: l.id, created_time: l.created_time });
    }
    const next = data.paging?.next;
    if (!next) break;
    url = next;
  }
  return { leads, debug: { total: leads.length, stopped: false } };
}
__name(listLeads, "listLeads");

// src/parser.js
function getField(fields, ...keys) {
  for (const k of keys) {
    if (fields[k]) return fields[k];
  }
  return "";
}
__name(getField, "getField");
var COUNTRY_MAP = {
  "united states": "\u7F8E\u56FD",
  us: "\u7F8E\u56FD",
  usa: "\u7F8E\u56FD",
  "canada": "\u52A0\u62FF\u5927",
  ca: "\u52A0\u62FF\u5927",
  "united kingdom": "\u82F1\u56FD",
  uk: "\u82F1\u56FD",
  britain: "\u82F1\u56FD",
  "germany": "\u5FB7\u56FD",
  de: "\u5FB7\u56FD",
  "france": "\u6CD5\u56FD",
  fr: "\u6CD5\u56FD",
  "italy": "\u610F\u5927\u5229",
  it: "\u610F\u5927\u5229",
  "spain": "\u897F\u73ED\u7259",
  es: "\u897F\u73ED\u7259",
  "australia": "\u6FB3\u5927\u5229\u4E9A",
  au: "\u6FB3\u5927\u5229\u4E9A",
  "new zealand": "\u65B0\u897F\u5170",
  nz: "\u65B0\u897F\u5170",
  "netherlands": "\u8377\u5170",
  nl: "\u8377\u5170",
  "belgium": "\u6BD4\u5229\u65F6",
  be: "\u6BD4\u5229\u65F6",
  "switzerland": "\u745E\u58EB",
  ch: "\u745E\u58EB",
  "austria": "\u5965\u5730\u5229",
  at: "\u5965\u5730\u5229",
  "sweden": "\u745E\u5178",
  se: "\u745E\u5178",
  "norway": "\u632A\u5A01",
  no: "\u632A\u5A01",
  "denmark": "\u4E39\u9EA6",
  dk: "\u4E39\u9EA6",
  "finland": "\u82AC\u5170",
  fi: "\u82AC\u5170",
  "poland": "\u6CE2\u5170",
  pl: "\u6CE2\u5170",
  "czech republic": "\u6377\u514B",
  czechia: "\u6377\u514B",
  cz: "\u6377\u514B",
  "portugal": "\u8461\u8404\u7259",
  pt: "\u8461\u8404\u7259",
  "ireland": "\u7231\u5C14\u5170",
  ie: "\u7231\u5C14\u5170",
  "greece": "\u5E0C\u814A",
  gr: "\u5E0C\u814A",
  "japan": "\u65E5\u672C",
  jp: "\u65E5\u672C",
  "south korea": "\u97E9\u56FD",
  korea: "\u97E9\u56FD",
  kr: "\u97E9\u56FD",
  "china": "\u4E2D\u56FD",
  cn: "\u4E2D\u56FD",
  "india": "\u5370\u5EA6",
  in: "\u5370\u5EA6",
  "singapore": "\u65B0\u52A0\u5761",
  sg: "\u65B0\u52A0\u5761",
  "malaysia": "\u9A6C\u6765\u897F\u4E9A",
  my: "\u9A6C\u6765\u897F\u4E9A",
  "thailand": "\u6CF0\u56FD",
  th: "\u6CF0\u56FD",
  "philippines": "\u83F2\u5F8B\u5BBE",
  ph: "\u83F2\u5F8B\u5BBE",
  "indonesia": "\u5370\u5C3C",
  id: "\u5370\u5C3C",
  "vietnam": "\u8D8A\u5357",
  vn: "\u8D8A\u5357",
  "hong kong": "\u9999\u6E2F",
  hk: "\u9999\u6E2F",
  "taiwan": "\u53F0\u6E7E",
  tw: "\u53F0\u6E7E",
  "united arab emirates": "\u963F\u8054\u914B",
  uae: "\u963F\u8054\u914B",
  ae: "\u963F\u8054\u914B",
  "saudi arabia": "\u6C99\u7279",
  sa: "\u6C99\u7279",
  "qatar": "\u5361\u5854\u5C14",
  qa: "\u5361\u5854\u5C14",
  "ghana": "\u52A0\u7EB3",
  gh: "\u52A0\u7EB3",
  "brazil": "\u5DF4\u897F",
  br: "\u5DF4\u897F",
  "mexico": "\u58A8\u897F\u54E5",
  mx: "\u58A8\u897F\u54E5",
  "turkey": "\u571F\u8033\u5176",
  tr: "\u571F\u8033\u5176",
  "peru": "\u79D8\u9C81",
  pe: "\u79D8\u9C81",
  "colombia": "\u54E5\u4F26\u6BD4\u4E9A",
  co: "\u54E5\u4F26\u6BD4\u4E9A",
  "chile": "\u667A\u5229",
  cl: "\u667A\u5229",
  "argentina": "\u963F\u6839\u5EF7",
  ar: "\u963F\u6839\u5EF7",
  "ecuador": "\u5384\u74DC\u591A\u5C14",
  ec: "\u5384\u74DC\u591A\u5C14",
  "uruguay": "\u4E4C\u62C9\u572D",
  uy: "\u4E4C\u62C9\u572D",
  "albania": "\u963F\u5C14\u5DF4\u5C3C\u4E9A",
  al: "\u963F\u5C14\u5DF4\u5C3C\u4E9A",
  "serbia": "\u585E\u5C14\u7EF4\u4E9A",
  rs: "\u585E\u5C14\u7EF4\u4E9A",
  "croatia": "\u514B\u7F57\u5730\u4E9A",
  hr: "\u514B\u7F57\u5730\u4E9A",
  "romania": "\u7F57\u9A6C\u5C3C\u4E9A",
  ro: "\u7F57\u9A6C\u5C3C\u4E9A",
  "bulgaria": "\u4FDD\u52A0\u5229\u4E9A",
  bg: "\u4FDD\u52A0\u5229\u4E9A",
  "slovakia": "\u65AF\u6D1B\u4F10\u514B",
  sk: "\u65AF\u6D1B\u4F10\u514B",
  "slovenia": "\u65AF\u6D1B\u6587\u5C3C\u4E9A",
  si: "\u65AF\u6D1B\u6587\u5C3C\u4E9A",
  "hungary": "\u5308\u7259\u5229",
  hu: "\u5308\u7259\u5229",
  "ukraine": "\u4E4C\u514B\u5170",
  ua: "\u4E4C\u514B\u5170",
  "russia": "\u4FC4\u7F57\u65AF",
  ru: "\u4FC4\u7F57\u65AF",
  "south africa": "\u5357\u975E",
  za: "\u5357\u975E",
  "nigeria": "\u5C3C\u65E5\u5229\u4E9A",
  ng: "\u5C3C\u65E5\u5229\u4E9A",
  "kenya": "\u80AF\u5C3C\u4E9A",
  ke: "\u80AF\u5C3C\u4E9A",
  "egypt": "\u57C3\u53CA",
  eg: "\u57C3\u53CA",
  "morocco": "\u6469\u6D1B\u54E5",
  ma: "\u6469\u6D1B\u54E5",
  "israel": "\u4EE5\u8272\u5217",
  il: "\u4EE5\u8272\u5217",
  "dominican republic": "\u591A\u7C73\u5C3C\u52A0",
  do: "\u591A\u7C73\u5C3C\u52A0",
  "costa rica": "\u54E5\u65AF\u8FBE\u9ECE\u52A0",
  cr: "\u54E5\u65AF\u8FBE\u9ECE\u52A0",
  "panama": "\u5DF4\u62FF\u9A6C",
  pa: "\u5DF4\u62FF\u9A6C",
  "guatemala": "\u5371\u5730\u9A6C\u62C9",
  gt: "\u5371\u5730\u9A6C\u62C9"
};
var PHONE_PREFIX_MAP = {
  "+1": "\u7F8E\u56FD",
  "+44": "\u82F1\u56FD",
  "+33": "\u6CD5\u56FD",
  "+49": "\u5FB7\u56FD",
  "+81": "\u65E5\u672C",
  "+82": "\u97E9\u56FD",
  "+86": "\u4E2D\u56FD",
  "+91": "\u5370\u5EA6",
  "+852": "\u9999\u6E2F",
  "+853": "\u6FB3\u95E8",
  "+886": "\u53F0\u6E7E",
  "+61": "\u6FB3\u5927\u5229\u4E9A",
  "+64": "\u65B0\u897F\u5170",
  "+31": "\u8377\u5170",
  "+32": "\u6BD4\u5229\u65F6",
  "+41": "\u745E\u58EB",
  "+43": "\u5965\u5730\u5229",
  "+46": "\u745E\u5178",
  "+47": "\u632A\u5A01",
  "+45": "\u4E39\u9EA6",
  "+358": "\u82AC\u5170",
  "+48": "\u6CE2\u5170",
  "+420": "\u6377\u514B",
  "+351": "\u8461\u8404\u7259",
  "+353": "\u7231\u5C14\u5170",
  "+30": "\u5E0C\u814A",
  "+34": "\u897F\u73ED\u7259",
  "+39": "\u610F\u5927\u5229",
  "+65": "\u65B0\u52A0\u5761",
  "+60": "\u9A6C\u6765\u897F\u4E9A",
  "+66": "\u6CF0\u56FD",
  "+63": "\u83F2\u5F8B\u5BBE",
  "+62": "\u5370\u5C3C",
  "+84": "\u8D8A\u5357",
  "+971": "\u963F\u8054\u914B",
  "+966": "\u6C99\u7279",
  "+974": "\u5361\u5854\u5C14",
  "+233": "\u52A0\u7EB3",
  "+55": "\u5DF4\u897F",
  "+52": "\u58A8\u897F\u54E5",
  "+90": "\u571F\u8033\u5176",
  "+51": "\u79D8\u9C81",
  "+57": "\u54E5\u4F26\u6BD4\u4E9A",
  "+56": "\u667A\u5229",
  "+54": "\u963F\u6839\u5EF7",
  "+593": "\u5384\u74DC\u591A\u5C14",
  "+598": "\u4E4C\u62C9\u572D",
  "+355": "\u963F\u5C14\u5DF4\u5C3C\u4E9A",
  "+381": "\u585E\u5C14\u7EF4\u4E9A",
  "+385": "\u514B\u7F57\u5730\u4E9A",
  "+40": "\u7F57\u9A6C\u5C3C\u4E9A",
  "+359": "\u4FDD\u52A0\u5229\u4E9A",
  "+421": "\u65AF\u6D1B\u4F10\u514B",
  "+386": "\u65AF\u6D1B\u6587\u5C3C\u4E9A",
  "+36": "\u5308\u7259\u5229",
  "+380": "\u4E4C\u514B\u5170",
  "+7": "\u4FC4\u7F57\u65AF",
  "+27": "\u5357\u975E",
  "+234": "\u5C3C\u65E5\u5229\u4E9A",
  "+254": "\u80AF\u5C3C\u4E9A",
  "+20": "\u57C3\u53CA",
  "+212": "\u6469\u6D1B\u54E5",
  "+972": "\u4EE5\u8272\u5217",
  "+1-809": "\u591A\u7C73\u5C3C\u52A0",
  "+506": "\u54E5\u65AF\u8FBE\u9ECE\u52A0",
  "+507": "\u5DF4\u62FF\u9A6C",
  "+502": "\u5371\u5730\u9A6C\u62C9"
};
var SR_COUNTRIES = /* @__PURE__ */ new Set([
  "\u963F\u8054\u914B",
  "\u9999\u6E2F",
  "\u5370\u5C3C",
  "\u65E5\u672C",
  "\u97E9\u56FD",
  "\u9A6C\u6765\u897F\u4E9A",
  "\u83F2\u5F8B\u5BBE",
  "\u5361\u5854\u5C14",
  "\u6C99\u7279",
  "\u8D8A\u5357"
]);
var HOMEPOD_COUNTRIES = /* @__PURE__ */ new Set([
  "\u6FB3\u5927\u5229\u4E9A",
  "\u65B0\u897F\u5170",
  "\u5FB7\u56FD",
  "\u6CD5\u56FD",
  "\u610F\u5927\u5229",
  "\u897F\u73ED\u7259",
  "\u82F1\u56FD",
  "\u8377\u5170",
  "\u6BD4\u5229\u65F6",
  "\u745E\u58EB",
  "\u5965\u5730\u5229",
  "\u745E\u5178",
  "\u632A\u5A01",
  "\u4E39\u9EA6",
  "\u82AC\u5170",
  "\u6CE2\u5170",
  "\u6377\u514B",
  "\u8461\u8404\u7259",
  "\u7231\u5C14\u5170",
  "\u5E0C\u814A"
]);
var NA_COUNTRIES = /* @__PURE__ */ new Set(["\u7F8E\u56FD", "\u52A0\u62FF\u5927"]);
var DEFAULT_FORM_CONFIG = {
  "1608388523720167": { form_type: "A", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 香港VRT舱-copy
  "1624015192051737": { form_type: "A", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 美加VRT舱
  "1587419575689695": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 美国VRT表单0305
  "1128129825877107": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 欧洲家居舱表单0820→VRT
  "1129793142376528": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 家庭影音表单→VRT
  "1793721661502930": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 静音舱表单
  "1213096124130728": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 基本表单0115
  "892279930015791": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "SR"] },
  // 香港表单
  "1585049362647622": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "SR"] },
  // 日本表单0112
  "2001756617441724": { form_type: "B", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // CIFF
  "2126023344630740": { form_type: "A", default_product: ["\u9759\u97F3\u8231", "VRT"] },
  // 欧洲VRT舱表0519
  "1681355539546664": { form_type: "A", default_product: ["\u9759\u97F3\u8231", "VRT"] }
  // VRT舱表单0521
};
function resolveCountry(fields) {
  const countryField = getField(fields, "country", "Country");
  if (countryField) {
    const mapped = COUNTRY_MAP[countryField.toLowerCase().trim()];
    if (mapped) return mapped;
  }
  const phone = getField(fields, "phone_number", "Phone Number", "Phone number (verified)", "phone", "\u96FB\u8A71\u756A\u53F7");
  if (phone) {
    const cleanPhone = phone.replace(/[\s\-()]/g, "");
    let normalized = "";
    if (cleanPhone.startsWith("+")) {
      normalized = cleanPhone;
    } else if (cleanPhone.startsWith("00")) {
      normalized = "+" + cleanPhone.slice(2);
    }
    for (const len of [4, 3, 2]) {
      const prefix = normalized.slice(0, len);
      if (PHONE_PREFIX_MAP[prefix]) return PHONE_PREFIX_MAP[prefix];
    }
  }
  return "";
}
__name(resolveCountry, "resolveCountry");
function routeProduct(country, formConfig) {
  if (SR_COUNTRIES.has(country)) return ["\u9759\u97F3\u8231", "SR"];
  if (HOMEPOD_COUNTRIES.has(country)) return ["\u9759\u97F3\u8231", "VRT"];
  if (NA_COUNTRIES.has(country)) return ["\u9759\u97F3\u8231", "VRT"];
  return formConfig.default_product || ["\u65E0\u6CD5\u8BC6\u522B", "\u65E0\u6CD5\u8BC6\u522B"];
}
__name(routeProduct, "routeProduct");
var FIELD_LABELS = {
  "is_this_booth_for_personal_use_or_for_resale?": "Use type",
  "Is this booth for personal use or for resale?": "Use type",
  "how_many_people_does_the_booth_need_to_fit?": "Capacity",
  "How many people does the booth need to fit?": "Capacity",
  "how_can_i_help_you?": "Message",
  "How can I help you?": "Message",
  "leave_your_message": "Message",
  "Leave your message": "Message",
  "acoustic_treatment_problems": "Message",
  "Acoustic treatment problems": "Message",
  "message:": "Message",
  "what_date_are_you_attending_ciff?": "CIFF Date",
  "\u8072\u5B78\u9700\u6C42": "Message",
  "\u304A\u554F\u3044\u5408\u308F\u305B\u5185\u5BB9": "Message"
};
var SKIP_FIELDS = /* @__PURE__ */ new Set([
  "full_name",
  "Full Name",
  "name",
  "full name",
  "email",
  "Email",
  "phone_number",
  "Phone Number",
  "Phone number (verified)",
  "phone_number_verified",
  "phone",
  "\u96FB\u8A71\u756A\u53F7",
  "company_name",
  "Company",
  "\u4F1A\u793E\u540D",
  "country",
  "Country",
  "inbox_url",
  "Is this booth for personal use or for resale?",
  "is_this_booth_for_personal_use_or_for_resale?",
  "How many people does the booth need to fit?",
  "how_many_people_does_the_booth_need_to_fit?",
  "How can I help you?",
  "how_can_i_help_you?",
  "message",
  "message:",
  "leave_your_message",
  "Leave your message",
  "acoustic_treatment_problems",
  "Acoustic treatment problems",
  "\u8072\u5B78\u9700\u6C42",
  "\u304A\u554F\u3044\u5408\u308F\u305B\u5185\u5BB9"
]);
function cleanValue(val) {
  return val.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
__name(cleanValue, "cleanValue");
var MESSAGE_FIELDS = [
  "How can I help you?",
  "how_can_i_help_you?",
  "message",
  "message:",
  "Leave your message",
  "leave_your_message",
  "Acoustic treatment problems",
  "acoustic_treatment_problems",
  "\u8072\u5B78\u9700\u6C42",
  "\u304A\u554F\u3044\u5408\u308F\u305B\u5185\u5BB9"
];
function formatMessage(fields) {
  const useType = getField(fields, "Is this booth for personal use or for resale?", "is_this_booth_for_personal_use_or_for_resale?");
  const capacity = getField(fields, "How many people does the booth need to fit?", "how_many_people_does_the_booth_need_to_fit?");
  if (useType || capacity) {
    const parts = [];
    if (useType) parts.push(cleanValue(useType));
    if (capacity) parts.push(`for ${cleanValue(capacity)}`);
    return parts.join(", ");
  }
  for (const key of MESSAGE_FIELDS) {
    if (fields[key]) return fields[key];
  }
  return "";
}
__name(formatMessage, "formatMessage");
function formatInquiryDetails(parsed) {
  const lines = [
    `Name:${parsed.name || ""}`,
    `Email:${parsed.email || ""}`,
    `Company:${parsed.company || ""}`,
    `Telephone Number:${parsed.phone || ""}`
  ];
  if (parsed.message) {
    lines.push(`Message:${parsed.message}`);
  }
  if (parsed.extra_fields) {
    lines.push(parsed.extra_fields);
  }
  const tag = [
    parsed.country,
    "Facebook",
    parsed.product_category,
    parsed.product_model
  ].filter(Boolean).join("-");
  return lines.join("\n") + "\n\n" + tag;
}
__name(formatInquiryDetails, "formatInquiryDetails");
function parseLead(leadData, env) {
  const { fields, form_id } = leadData;
  const formConfig = DEFAULT_FORM_CONFIG[form_id] || {
    form_type: "A",
    default_product: ["\u9759\u97F3\u8231", "VRT"]
  };
  const name = getField(fields, "full_name", "Full Name", "full name", "name");
  const email = getField(fields, "email", "Email");
  const phone = getField(fields, "phone_number", "Phone Number", "Phone number (verified)", "phone", "\u96FB\u8A71\u756A\u53F7");
  const company = getField(fields, "company_name", "Company", "\u4F1A\u793E\u540D");
  const country = resolveCountry(fields);
  const [product_category, product_model] = routeProduct(country, formConfig);
  const message = formatMessage(fields);
  const extraParts = [];
  for (const [key, val] of Object.entries(fields)) {
    if (SKIP_FIELDS.has(key) || !val) continue;
    const label = FIELD_LABELS[key] || cleanValue(key.replace(/\?/g, ""));
    extraParts.push(`${label}: ${cleanValue(val)}`);
  }
  const parsed = {
    leadgen_id: leadData.leadgen_id,
    form_id,
    ad_id: leadData.ad_id,
    created_time: leadData.created_time,
    form_type: formConfig.form_type,
    name,
    email,
    phone,
    company,
    country,
    product_category,
    product_model,
    sub_channel: "Facebook",
    message,
    extra_fields: extraParts.join("\n")
  };
  parsed.enquiry_details = formatInquiryDetails(parsed);
  return parsed;
}
__name(parseLead, "parseLead");

// src/grader.js
var PERSONAL_DOMAINS = /* @__PURE__ */ new Set([
  "gmail.com",
  "outlook.com",
  "yahoo.com",
  "hotmail.com",
  "aol.com",
  "icloud.com",
  "protonmail.com",
  "mail.com",
  "gmx.com",
  "yandex.com",
  "qq.com",
  "163.com",
  "126.com",
  "sina.com",
  "sohu.com",
  "yeah.net",
  "hotmail.co.uk",
  "live.com",
  "zoho.com",
  "21cn.com",
  "foxmail.com",
  "tom.com",
  "189.cn",
  "139.com",
  "me.com",
  "msn.com"
]);
var QUANTITY_PATTERN = /(\d+)\s*(pcs|units|sets|nos|booths?|pods?|cabins?|cabines?|cabinas?|台|套|个)/gi;
var PARTNERSHIP_KEYWORDS = /\b(distributor|dealer|reseller|partner|wholesal|agent|representative|franchise|collaborat|cooperation|co-operat)\b/i;
var QUOTATION_KEYWORDS = /\b(quote|quotation|price|pricing|cost|budget|offer|proposal|inquiry|enquiry)\b/i;
var SCENARIO_PATTERNS = {
  Office: /\b(for\s+(?:our|the|my|a|an)\b.{0,40}?\boffice\b(?![-\s]*(?:pod|booth|cabin|cabine|phone))|office\s+(building|space|environment|setup)|\boffice\s*(?:pod|booth|cabin|phone)\b)/i,
  Studio: /\b(recording\s+(?:booth|studio|room|pod|space)|music\s+(?:studio|room)|vocal\s+(?:booth|room)|podcast|streaming\s+(?:room|setup|booth))\b/i,
  Home: /\b(for\s+home\b|home\s+(?:office|studio|use|environment)|residential|personal\s+use\s+at\s+home)\b/i,
  Project: /\b(new\s+(?:building|office|headquarters|campus|location|site)|project\s+(?:manager|requirement)|construction|renovation|remodel)\b/i
};
var CAPACITY_PATTERN = /(\d+)\s*(?:-?\s*person|pax|people|seat| occupant)/i;
var CONFIG_KEYWORDS = /\b(color|colour|finish|material|dimension|size|custom|option|accessory|window|ventilation|led|lighting|socket|power|ac|soundproof|acoustic)\b/i;
var PLACEHOLDER_NAMES = /\b(john\s*(doe|smith)|jane\s*doe|test\s*user|anonymous|n\/a|none)\b/i;
var PROMO_ACTION = /\b(review|audit|optimize|improve|suggest|analysis|launch|show you|rank|boost|grow|scale|increase|drive)\b/i;
var PROMO_TARGET = /\b(website|web site|site|webpage|landing page|search|brand|visibility|online|google|traffic|leads|revenue|sales)\b/i;
function isCompanyEmail(email) {
  if (!email || !email.includes("@")) return false;
  const domain = email.toLowerCase().split("@")[1];
  return !PERSONAL_DOMAINS.has(domain);
}
__name(isCompanyEmail, "isCompanyEmail");
function extractQuantity(text) {
  const matches = [...text.matchAll(QUANTITY_PATTERN)];
  if (matches.length === 0) return { min: 0, max: 0, evidence: false };
  const nums = matches.map((m) => parseInt(m[1], 10));
  return { min: Math.min(...nums), max: Math.max(...nums), evidence: true };
}
__name(extractQuantity, "extractQuantity");
function detectScenario(text) {
  for (const [scenario, pattern] of Object.entries(SCENARIO_PATTERNS)) {
    if (pattern.test(text)) return scenario;
  }
  return "Unknown";
}
__name(detectScenario, "detectScenario");
function extractCapacity(text) {
  const m = text.match(CAPACITY_PATTERN);
  return m ? parseInt(m[1], 10) : 0;
}
__name(extractCapacity, "extractCapacity");
function detectIntent(text) {
  if (PARTNERSHIP_KEYWORDS.test(text)) return "Partnership";
  if (QUOTATION_KEYWORDS.test(text)) return "Quotation";
  return "Info";
}
__name(detectIntent, "detectIntent");
function isSpam(name, email, message) {
  let signals = 0;
  if (name) {
    const clean = name.replace(/\s/g, "");
    if (clean.length > 4) {
      const vowels = (clean.match(/[aeiouAEIOU]/g) || []).length;
      if (vowels === 0) signals++;
      const unique = new Set(clean.toLowerCase()).size;
      if (unique / clean.length > 0.9) signals++;
    }
  }
  if (name && PLACEHOLDER_NAMES.test(name)) signals++;
  if (email && /^(test|sample|example|noreply|no-reply)@/i.test(email)) signals++;
  if (message && PROMO_ACTION.test(message) && PROMO_TARGET.test(message)) signals++;
  return signals >= 2;
}
__name(isSpam, "isSpam");
var FORM_SPAM_ALLOWLIST = /* @__PURE__ */ new Set([
  "quote", "pricing", "inquiry", "quotation", "price", "catalog", "brochure", "moq", "rfq"
]);
var FORM_SPAM_INQUIRY_HINTS = /\b(quote|quotation|pricing|price|inquiry|enquiry|interested|looking|catalog|brochure|moq|rfq|need|want|help|office|booth|pod|soundbox|acoustic|soundproof|buy|order|sample|demo|contact|details|info|unit|model|silent|cabin|homepod|vrt|art|sr|vr|partition|meeting|cost|availability|specification|catalogue|distributor|partner|dealer|wholesale|purchase|modular)\b/i;
function isFormSpamSingleWord(parsed) {
  const msg = (parsed.message || "").trim();
  if (!msg || /\s/.test(msg)) return false;
  if (!/^[A-Za-z]+$/.test(msg)) return false;
  if (msg.length < 6 || msg.length > 24) return false;
  const lower = msg.toLowerCase();
  if (FORM_SPAM_ALLOWLIST.has(lower)) return false;
  if (FORM_SPAM_INQUIRY_HINTS.test(msg) || QUOTATION_KEYWORDS.test(msg) || CONFIG_KEYWORDS.test(msg)) return false;
  const phoneDigits = (parsed.phone || "").replace(/\D/g, "");
  const phoneWeak = !phoneDigits || phoneDigits.length < 8;
  const companyEmpty = !(parsed.company || "").trim();
  return phoneWeak && companyEmpty;
}
__name(isFormSpamSingleWord, "isFormSpamSingleWord");
function gradeLead(parsed) {
  const text = [parsed.name, parsed.message].filter(Boolean).join(" ");
  const email = parsed.email || "";
  if (isSpam(parsed.name, email, parsed.message)) {
    return { level: "L4", signals: { spam: true } };
  }
  if (parsed.product_category === "\u65E0\u6CD5\u8BC6\u522B" && !PARTNERSHIP_KEYWORDS.test(text)) {
    return { level: "L4", signals: { noProduct: true } };
  }
  const s1 = isCompanyEmail(email);
  const qty = extractQuantity(text);
  const s3a = qty.evidence && qty.max >= 2;
  const s3b = qty.evidence && qty.max >= 10;
  const scenario = detectScenario(text);
  const s4 = scenario !== "Unknown";
  const capacity = extractCapacity(text);
  const s5 = capacity > 0;
  const s6 = CONFIG_KEYWORDS.test(text);
  const intent = detectIntent(text);
  const signals = { s1, s3a, s3b, s4, s5, s6, scenario, intent, quantity: qty.max };
  if (s3b) return { level: "L1", signals };
  if (intent === "Partnership" && (s1 || s4)) return { level: "L1", signals };
  if (s1 && (s3a || s4 || s5)) return { level: "L2", signals };
  if (s4 && intent === "Quotation") return { level: "L2", signals };
  if (!s1 && (s3a || s5)) return { level: "L2", signals };
  if (s1 && parsed.product_category === "\u9759\u97F3\u8231" && s6) return { level: "L2", signals };
  return { level: "L3", signals };
}
__name(gradeLead, "gradeLead");

// src/feishu.js
var FEISHU_BASE = "https://open.feishu.cn/open-apis";
var FEISHU_FIELDS = {
  EMAIL: "Email\uFF08\u5BA2\u6237\u90AE\u7BB1\uFF09",
  ENQUIRY: "Enquiry details\uFF08\u8BE2\u76D8\u5185\u5BB9\uFF09",
  CLUE_LEVEL: "Clue level\uFF08\u7EBF\u7D22\u7B49\u7EA7\uFF09",
  CHANNELS: "Channels\uFF08\u6E20\u9053\uFF09",
  AUTOREPLY_STATUS: "Auto-Reply Status",
  ASSIGN_METHOD: "\u5206\u914D\u65B9\u5F0F",
  COUNTRY: "Country\uFF08\u56FD\u5BB6\uFF09",
  SUB_CHANNEL: "\u7EC6\u5206\u6E20\u9053\uFF08Channel segmentation\uFF09",
  PRODUCT_CAT: "Product Categories\uFF08\u4EA7\u54C1\u5927\u7C7B\uFF09",
  PRODUCT_MODEL: "Product model\uFF08\u5177\u4F53\u578B\u53F7\uFF09",
  CUSTOMER_NAME: "Customer Name\uFF08\u5BA2\u6237\u540D\u79F0\uFF09",
  PHONE: "Phone\uFF08\u5BA2\u6237\u7535\u8BDD\uFF09",
  WECHAT: "Wechat\uFF08\u5FAE\u4FE1\uFF09",
  ALI_ID: "\u963F\u91CCID",
  LEADGEN_ID: "Facebook Leadgen ID",
  ENTRY_UTC_MS: "Entry_Time_UTC_MS",
  AUTOREPLY_ERROR: "Auto-Reply Error"
};
function messengerChannelLabels(channel) {
  if (channel === "ig") {
    return { channels: "Instagram", subChannel: "Instagram" };
  }
  return { channels: "Facebook-Messenger", subChannel: "Facebook" };
}
__name(messengerChannelLabels, "messengerChannelLabels");
function buildMessengerTranscript(session) {
  const recent = session.messages.slice(-10);
  const undelivered = recent.filter((m) => m.role === "assistant" && m.delivered === false).length;
  const lines = [];
  if (undelivered > 0) {
    lines.push(`[Bot \u81EA\u52A8\u56DE\u590D ${undelivered} \u6761\u672A\u9001\u8FBE\u5BA2\u6237\uFF08Meta API \u53D1\u9001\u5931\u8D25\uFF09\uFF0C\u4EE5\u4E0B\u4EC5\u542B\u5BA2\u6237\u539F\u6587\u4E0E\u5DF2\u6210\u529F\u9001\u8FBE\u7684 Bot \u6D88\u606F]`);
  }
  for (const msg of recent) {
    if (msg.role === "user") {
      lines.push(`Customer: ${msg.content}`);
    } else if (msg.delivered !== false) {
      lines.push(`Bot: ${msg.content}`);
    }
  }
  return lines.join("\n");
}
__name(buildMessengerTranscript, "buildMessengerTranscript");
var CATEGORY_TO_FEISHU = {
  "\u9759\u97F3\u8231": "Silence Booth \u9759\u97F3\u8231",
  "\u5BB6\u5C45\u8231": "Homepod \u5BB6\u5C45\u8231",
  "\u58F0\u5B66\u4EA7\u54C1": "Acoustic products \u58F0\u5B66\u4EA7\u54C1"
};
function feishuProductCategory(category) {
  return CATEGORY_TO_FEISHU[category] || category;
}
__name(feishuProductCategory, "feishuProductCategory");
var tokenCache = { token: "", expiresAt: 0 };
function tableUrl(env, suffix = "") {
  return `${FEISHU_BASE}/bitable/v1/apps/${env.FEISHU_APP_TOKEN}/tables/${env.FEISHU_TABLE_ID}/records${suffix}`;
}
__name(tableUrl, "tableUrl");
function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}
__name(authHeaders, "authHeaders");
async function getTenantToken(env) {
  if (tokenCache.token && Date.now() < tokenCache.expiresAt - 3e5) {
    return tokenCache.token;
  }
  const res = await fetch(`${FEISHU_BASE}/auth/v3/tenant_access_token/internal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app_id: env.FEISHU_APP_ID, app_secret: env.FEISHU_APP_SECRET })
  });
  const data = await res.json();
  if (data.code !== 0 || !data.tenant_access_token) {
    throw new Error(`Feishu auth failed: code=${data.code} msg=${data.msg}`);
  }
  tokenCache = { token: data.tenant_access_token, expiresAt: Date.now() + (data.expire || 7200) * 1e3 };
  return tokenCache.token;
}
__name(getTenantToken, "getTenantToken");
function normalizeMessengerEmail(email) {
  const value = String(email || "").trim().toLowerCase();
  if (!value || value === "n/a" || value === "na") return "";
  if (value.includes(",")) {
    for (const part of value.split(",")) {
      const p = part.trim();
      if (p && p !== "n/a" && p !== "na") return p;
    }
    return "";
  }
  return value;
}
__name(normalizeMessengerEmail, "normalizeMessengerEmail");
async function feishuSearch(token, env, body) {
  const res = await fetch(tableUrl(env, "/search"), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (data.code !== 0) {
    throw new Error(`Feishu search error: code=${data.code} msg=${data.msg}`);
  }
  return data.data;
}
__name(feishuSearch, "feishuSearch");
var MESSENGER_PAIR_WINDOW_MS = 60 * 60 * 1e3;
async function findRecentFacebookLeadAd(token, email, env, windowMs = MESSENGER_PAIR_WINDOW_MS) {
  const normalized = normalizeMessengerEmail(email);
  if (!normalized) return null;
  const cutoff = Date.now() - windowMs;
  const data = await feishuSearch(token, env, {
    filter: {
      conjunction: "and",
      conditions: [
        { field_name: FEISHU_FIELDS.EMAIL, operator: "is", value: [normalized] },
        { field_name: FEISHU_FIELDS.CHANNELS, operator: "is", value: ["Facebook"] }
      ]
    },
    field_names: ["\u7EBF\u7D22ID", FIELD_ENTRY_TIME, FEISHU_FIELDS.CHANNELS, FEISHU_FIELDS.EMAIL],
    sort: [{ field_name: FIELD_ENTRY_TIME, desc: true }],
    page_size: 5
  });
  const items = data?.items || [];
  for (const item of items) {
    const entryMs = item.fields?.[FIELD_ENTRY_TIME];
    if (typeof entryMs === "number" && entryMs >= cutoff) {
      return { record_id: item.record_id, lead_id: extractTextFromField(item.fields?.["\u7EBF\u7D22ID"]) };
    }
  }
  return null;
}
__name(findRecentFacebookLeadAd, "findRecentFacebookLeadAd");
async function searchByEmail(token, email, env) {
  const res = await fetch(tableUrl(env, "/search"), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      filter: {
        conjunction: "and",
        conditions: [{ field_name: FEISHU_FIELDS.EMAIL, operator: "contains", value: [email.trim().toLowerCase()] }]
      },
      field_names: [FEISHU_FIELDS.EMAIL]
    })
  });
  const data = await res.json();
  if (data.code !== 0) {
    throw new Error(`Feishu search error: code=${data.code} msg=${data.msg}`);
  }
  return (data.data?.total || 0) > 0;
}
__name(searchByEmail, "searchByEmail");
async function findByLeadgenId(token, leadgenId, env) {
  if (!leadgenId) return null;
  const data = await feishuSearch(token, env, {
    filter: {
      conjunction: "and",
      conditions: [
        { field_name: FEISHU_FIELDS.LEADGEN_ID, operator: "is", value: [String(leadgenId)] }
      ]
    },
    field_names: [FEISHU_FIELDS.LEADGEN_ID, "\u7EBF\u7D22ID"],
    page_size: 5
  });
  return data?.items?.[0] || null;
}
__name(findByLeadgenId, "findByLeadgenId");
async function findFacebookContactDuplicate(token, email, phone, env, windowMs = 30 * 24 * 3600 * 1e3) {
  const normalized = (email || "").trim().toLowerCase();
  const phoneDigits = (phone || "").replace(/\D/g, "");
  if ((!normalized || normalized === "n/a") && phoneDigits.length < 8) return null;
  const cutoff = Date.now() - windowMs;
  const conditions = [
    { field_name: FEISHU_FIELDS.CHANNELS, operator: "is", value: ["Facebook"] }
  ];
  if (normalized && normalized !== "n/a") {
    conditions.push({ field_name: FEISHU_FIELDS.EMAIL, operator: "contains", value: [normalized] });
  } else {
    conditions.push({ field_name: FEISHU_FIELDS.PHONE, operator: "contains", value: [phoneDigits.slice(-8)] });
  }
  const data = await feishuSearch(token, env, {
    filter: { conjunction: "and", conditions },
    field_names: [FEISHU_FIELDS.EMAIL, FEISHU_FIELDS.PHONE, FIELD_ENTRY_TIME, "\u7EBF\u7D22ID"],
    sort: [{ field_name: FIELD_ENTRY_TIME, desc: true }],
    page_size: 10
  });
  for (const item of data?.items || []) {
    const entryMs = item.fields?.[FIELD_ENTRY_TIME];
    if (typeof entryMs === "number" && entryMs >= cutoff) {
      return item;
    }
  }
  return null;
}
__name(findFacebookContactDuplicate, "findFacebookContactDuplicate");
var FOLLOWUP_TABLE_ID = "tblbrI87BmcvFC5L";
var FIELD_ENTRY_TIME = "Entry Time\uFF08\u5F55\u5165\u65F6\u95F4\uFF09";
var FIELD_PHONE = "Telephone Number";
function extractTextFromField(val) {
  if (!val) return "";
  if (typeof val === "string") return val;
  if (typeof val === "number") return String(val);
  if (Array.isArray(val)) return val.map((v) => v?.text || v?.name || String(v)).join("");
  return val?.text || val?.name || String(val);
}
__name(extractTextFromField, "extractTextFromField");
async function fetchRecentLeads(token, env, days = 3) {
  const cutoff = Date.now() - days * 864e5;
  const res = await fetch(tableUrl(env, "/search"), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      sort: { field_name: FIELD_ENTRY_TIME, desc: true },
      page_size: 100,
      field_names: [FEISHU_FIELDS.EMAIL, FIELD_PHONE, FIELD_ENTRY_TIME, FEISHU_FIELDS.CHANNELS]
    })
  });
  const data = await res.json();
  if (data.code !== 0) return [];
  const items = data.data?.items || [];
  const leads = [];
  for (const it of items) {
    const entry = it.fields?.[FIELD_ENTRY_TIME];
    if (!entry || entry < cutoff) break;
    const rawEmail = it.fields?.[FEISHU_FIELDS.EMAIL];
    const emailList = (Array.isArray(rawEmail) ? rawEmail : [rawEmail]).filter(Boolean).map((e) => extractTextFromField(e).trim().toLowerCase()).flatMap((e) => {
      if (!e) return [];
      if (e.includes(",")) return e.split(",").map((p) => p.trim()).filter(Boolean);
      return [e];
    }).filter(Boolean);
    leads.push({
      record_id: it.record_id,
      emails: emailList,
      phone: extractTextFromField(it.fields?.[FIELD_PHONE]).replace(/\D/g, ""),
      channels: extractTextFromField(it.fields?.[FEISHU_FIELDS.CHANNELS])
    });
  }
  return leads;
}
__name(fetchRecentLeads, "fetchRecentLeads");
function findDuplicateLead(email, phone, leads) {
  const emailLower = normalizeMessengerEmail(email);
  const phoneClean = phone?.replace(/\D/g, "");
  for (const lead of leads) {
    if (emailLower && lead.emails.includes(emailLower)) return lead.record_id;
    if (phoneClean && lead.phone && phoneClean === lead.phone) return lead.record_id;
  }
  return null;
}
__name(findDuplicateLead, "findDuplicateLead");
async function appendMessengerFollowup(token, env, leadRecordId, transcript) {
  const fields = {
    "Related Lead": [{ id: leadRecordId }],
    "Follow-up Details": `Messenger \u5BF9\u8BDD\uFF08\u5408\u5E76\u81EA\u91CD\u590D\u5F55\u5165\uFF09:
${transcript}`,
    "Contact Result": "Contacted - No Reply \u5DF2\u8054\u7CFB\uFF0C\u6682\u65E0\u56DE\u590D",
    "Contact Method": "Email"
  };
  const res = await fetch(`${FEISHU_BASE}/bitable/v1/apps/${env.FEISHU_APP_TOKEN}/tables/${FOLLOWUP_TABLE_ID}/records`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ fields })
  });
  const data = await res.json();
  return data.code === 0;
}
__name(appendMessengerFollowup, "appendMessengerFollowup");
async function markDuplicate(token, env, recordId, mainId) {
  const fields = { "Customer Name\uFF08\u5BA2\u6237\u540D\u79F0\uFF09": `\u91CD\u590D-\u89C1\u8868\u5355 ${mainId}` };
  try {
    await fetch(`${tableUrl(env)}/${recordId}`, {
      method: "PUT",
      headers: authHeaders(token),
      body: JSON.stringify({ fields })
    });
  } catch (e) {
    console.log(`[feishu] markDuplicate failed: ${e.message}`);
  }
}
__name(markDuplicate, "markDuplicate");
async function createRecord(token, parsed, env) {
  const fields = {
    [FEISHU_FIELDS.ENQUIRY]: parsed.enquiry_details,
    [FEISHU_FIELDS.CHANNELS]: "Facebook",
    [FEISHU_FIELDS.AUTOREPLY_STATUS]: "Pending",
    [FEISHU_FIELDS.ASSIGN_METHOD]: "\u81EA\u52A8",
    [FEISHU_FIELDS.SUB_CHANNEL]: parsed.sub_channel || "Facebook",
    [FEISHU_FIELDS.ENTRY_UTC_MS]: Date.now()
  };
  if (parsed.clue_level) fields[FEISHU_FIELDS.CLUE_LEVEL] = parsed.clue_level;
  if (parsed.country) fields[FEISHU_FIELDS.COUNTRY] = parsed.country;
  if (parsed.product_category) {
    fields[FEISHU_FIELDS.PRODUCT_CAT] = feishuProductCategory(parsed.product_category);
  }
  if (parsed.product_model) fields[FEISHU_FIELDS.PRODUCT_MODEL] = parsed.product_model;
  if (parsed.name) fields[FEISHU_FIELDS.CUSTOMER_NAME] = parsed.name;
  if (parsed.phone) fields[FEISHU_FIELDS.PHONE] = parsed.phone;
  if (parsed.email) fields[FEISHU_FIELDS.EMAIL] = parsed.email;
  if (parsed.leadgen_id) fields[FEISHU_FIELDS.LEADGEN_ID] = String(parsed.leadgen_id);
  fields[FEISHU_FIELDS.WECHAT] = "N/A";
  fields[FEISHU_FIELDS.ALI_ID] = "N/A";
  const res = await fetch(tableUrl(env), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ fields })
  });
  const data = await res.json();
  if (data.code !== 0) {
    throw new Error(`Feishu create error: code=${data.code} msg=${data.msg}`);
  }
  const record = data.data?.record;
  if (parsed.leadgen_id && record?.record_id) {
    const saved = record.fields?.[FEISHU_FIELDS.LEADGEN_ID];
    if (!saved) {
      const patchRes = await fetch(`${tableUrl(env)}/${record.record_id}`, {
        method: "PUT",
        headers: authHeaders(token),
        body: JSON.stringify({ fields: { [FEISHU_FIELDS.LEADGEN_ID]: String(parsed.leadgen_id) } })
      });
      const patchData = await patchRes.json();
      if (patchData.code !== 0) {
        console.error(`[lead] leadgen patch failed: ${parsed.leadgen_id} code=${patchData.code} msg=${patchData.msg}`);
      }
    }
  }
  return record;
}
__name(createRecord, "createRecord");
async function writeLead(parsed, env) {
  const token = await getTenantToken(env);
  if (isFormSpamSingleWord(parsed)) {
    console.log(`[lead] form spam single-word skip: ${(parsed.message || "").slice(0, 20)}`);
    return { status: "skipped", reason: "form_spam_single_word" };
  }
  if (parsed.leadgen_id) {
    const byLeadgen = await findByLeadgenId(token, parsed.leadgen_id, env);
    if (byLeadgen) {
      console.log(`[lead] duplicate leadgen_id, skip: ${parsed.leadgen_id} -> ${byLeadgen.record_id}`);
      return { status: "skipped", reason: "duplicate_leadgen_id", record_id: byLeadgen.record_id };
    }
  }
  const contactDup = await findFacebookContactDuplicate(token, parsed.email, parsed.phone, env);
  if (contactDup) {
    console.log(`[lead] duplicate Facebook contact, skip: ${contactDup.record_id}`);
    return { status: "skipped", reason: "duplicate_contact", record_id: contactDup.record_id };
  }
  const leads = await fetchRecentLeads(token, env);
  const dupId = findDuplicateLead(parsed.email, parsed.phone, leads);
  if (dupId) {
    const dup = leads.find((l) => l.record_id === dupId);
    if (dup?.channels === "Facebook-Messenger" || dup?.channels === "Instagram") {
      const record2 = await createRecord(token, parsed, env);
      const newId = record2?.record_id;
      if (newId) {
        const msgRecord = await getBitableRecord(token, env.FEISHU_APP_TOKEN, env.FEISHU_TABLE_ID, dupId);
        const messengerEnquiry = extractTextFromField(msgRecord?.fields?.[FEISHU_FIELDS.ENQUIRY]);
        if (messengerEnquiry) await appendMessengerFollowup(token, env, newId, messengerEnquiry);
        await markDuplicate(token, env, dupId, newId);
      }
      console.log(`[lead] Messenger \u5148\u5165\u91CD\u590D\uFF0C\u8868\u5355\u5EFA\u4E3B + \u8FFD\u52A0: main=${newId} merged_from=${dupId}`);
      return { status: "merged", record_id: newId, merged_from: dupId };
    }
    console.log(`[lead] \u91CD\u590D\u8868\u5355\uFF0C\u8DF3\u8FC7: ${dupId}`);
    return { status: "skipped", reason: "duplicate_email", record_id: dupId };
  }
  if (parsed.email) {
    if (await searchByEmail(token, parsed.email, env)) {
      return { status: "skipped", reason: "duplicate_email", email: parsed.email };
    }
  }
  const record = await createRecord(token, parsed, env);
  return { status: "created", record_id: record?.record_id };
}
__name(writeLead, "writeLead");
async function writeMessengerLead(session, env) {
  const token = await getTenantToken(env);
  const req = session.requirement;
  const transcript = buildMessengerTranscript(session);
  const { channels, subChannel } = messengerChannelLabels(session.channel || "fb");
  const leads = await fetchRecentLeads(token, env);
  const dupId = findDuplicateLead(req.email, req.phone, leads);
  if (dupId) {
    console.log(`[messenger] \u91CD\u590D\u7EBF\u7D22\uFF0C\u8FFD\u52A0\u5BF9\u8BDD\u5230 ${dupId}`);
    await appendMessengerFollowup(token, env, dupId, transcript);
    return { status: "merged", record_id: dupId };
  }
  if (req.email) {
    const existingLeadAd = await findRecentFacebookLeadAd(token, req.email, env);
    if (existingLeadAd) {
      console.log(`[messenger] skip duplicate Lead Ad email=${req.email.slice(0, 4)}*** existing=${existingLeadAd.record_id}`);
      await appendMessengerFollowup(token, env, existingLeadAd.record_id, transcript);
      return { status: "skipped", reason: "facebook_lead_ad_exists", record_id: existingLeadAd.record_id };
    }
    if (await searchByEmail(token, normalizeMessengerEmail(req.email), env)) {
      console.log(`[messenger] Lead skipped: duplicate email (search fallback) ${req.email.slice(0, 4)}***`);
      return { status: "skipped", reason: "duplicate_email" };
    }
  }
  const countryFromPhone = inferCountryFromPhone(req.phone);
  const summary = [
    req.location || countryFromPhone || "Unknown",
    subChannel,
    req.product_category || "",
    req.product_model || ""
  ].filter(Boolean).join("-");
  const lines = [
    `Name:`,
    `Email: ${req.email || ""}`,
    `Company: ${req.company || ""}`,
    `Telephone Number: ${req.phone || ""}`,
    `Message: ${transcript}`,
    "",
    summary
  ];
  const fields = {
    [FEISHU_FIELDS.ENQUIRY]: lines.join("\n"),
    [FEISHU_FIELDS.CHANNELS]: channels,
    [FEISHU_FIELDS.SUB_CHANNEL]: subChannel,
    [FEISHU_FIELDS.EMAIL]: req.email || "",
    [FEISHU_FIELDS.ENTRY_UTC_MS]: Date.now()
  };
  if (session.send_failures?.length) {
    const errSummary = session.send_failures.map((f) => f.error).join("; ");
    fields[FEISHU_FIELDS.AUTOREPLY_ERROR] = `Bot send failed: ${errSummary}`.slice(0, 500);
  }
  const res = await fetch(tableUrl(env), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ fields })
  });
  const data = await res.json();
  if (data.code !== 0) {
    throw new Error(`Feishu messenger lead create error: code=${data.code} msg=${data.msg}`);
  }
  return { status: "created", record_id: data.data?.record?.record_id };
}
__name(writeMessengerLead, "writeMessengerLead");
async function getBitableRecord(token, appToken, tableId, recordId) {
  const url = `${FEISHU_BASE}/bitable/v1/apps/${appToken}/tables/${tableId}/records/${recordId}`;
  const res = await fetch(url, { headers: authHeaders(token) });
  const data = await res.json();
  if (data.code !== 0) {
    throw new Error(`\u83B7\u53D6\u8BB0\u5F55\u5931\u8D25: code=${data.code} msg=${data.msg}`);
  }
  return data.data?.record;
}
__name(getBitableRecord, "getBitableRecord");
function extractSalesName(fields) {
  const raw = fields["\u6700\u7EC8\u5206\u914D\u7684\u4E1A\u52A1\u5458"];
  if (!raw) return null;
  if (Array.isArray(raw)) {
    const texts = raw.filter(Boolean).map((v) => v.text || String(v));
    return texts.join(", ") || null;
  }
  if (typeof raw === "string") return raw || null;
  return null;
}
__name(extractSalesName, "extractSalesName");
function fieldValue(field) {
  if (!field) return "";
  if (typeof field === "string") return field;
  if (typeof field === "number") return String(field);
  if (Array.isArray(field)) {
    return field.map((v) => v.text || v.name || String(v)).join(", ");
  }
  if (field.text) return field.text;
  return String(field);
}
__name(fieldValue, "fieldValue");
function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts));
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}
__name(fmtDate, "fmtDate");
function buildWecomMarkdown(fields, salesName) {
  const customer = fieldValue(fields["Customer Name\uFF08\u5BA2\u6237\u540D\u79F0\uFF09"]);
  const level = fieldValue(fields["\u{1F31F}\u7EBF\u7D22\u5206\u7EA7/Case Level"]);
  const channel = fieldValue(fields["Channels\uFF08\u6E20\u9053\uFF09"]);
  const country = fieldValue(fields["Country\uFF08\u56FD\u5BB6\uFF09"]);
  const entryTime = fmtDate(fields["Entry Time\uFF08\u5F55\u5165\u65F6\u95F4\uFF09"]);
  const firstContact = fieldValue(fields["\u{1F31F}First Contact Completed\uFF08\u662F\u5426\u5DF2\u9996\u8054\uFF09"]);
  const lines = [
    "### \u{1F6A8} \u7EBF\u7D22\u8DDF\u8FDB\u5F3A\u63D0\u9192",
    `> **\u5BA2\u6237**: ${customer || "\u672A\u77E5"}`,
    `> **\u7EBF\u7D22\u7B49\u7EA7**: ${level || "\u672A\u5206\u7EA7"}`,
    `> **\u6E20\u9053**: ${channel || "\u672A\u77E5"}`,
    `> **\u56FD\u5BB6**: ${country || "\u672A\u77E5"}`,
    `> **\u5F55\u5165\u65F6\u95F4**: ${entryTime || "\u672A\u77E5"}`,
    `> **\u9996\u8054\u72B6\u6001**: ${firstContact || "\u672A\u9996\u8054"}`,
    ""
  ];
  if (salesName) {
    lines.push(`**\u4E1A\u52A1\u5458 <font color="warning">${salesName}</font> \u8BF7\u5C3D\u5FEB\u8DDF\u8FDB\uFF01**`);
  } else {
    lines.push("**\u8BF7\u76F8\u5173\u4E1A\u52A1\u5458\u5C3D\u5FEB\u8DDF\u8FDB\uFF01**");
  }
  return lines.join("\n");
}
__name(buildWecomMarkdown, "buildWecomMarkdown");
async function sendWecomReminder(webhookUrl, markdown) {
  const payload = {
    msgtype: "markdown",
    markdown: { content: markdown }
  };
  const res = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.errcode !== 0) {
    throw new Error(`\u4F01\u5FAE Webhook \u5931\u8D25: errcode=${data.errcode} errmsg=${data.errmsg}`);
  }
  return data;
}
__name(sendWecomReminder, "sendWecomReminder");
async function sendUrgentReminder(body, env) {
  const recordId = body.record_id || body.data?.record_id;
  const appToken = body.app_token || body.data?.app_token || env.FEISHU_APP_TOKEN;
  const tableId = body.table_id || body.data?.table_id;
  if (!recordId) return { status: "error", error: "record_id required" };
  if (!tableId) return { status: "error", error: "table_id required" };
  const token = await getTenantToken(env);
  const record = await getBitableRecord(token, appToken, tableId, recordId);
  if (!record?.fields) {
    return { status: "error", error: "\u8BB0\u5F55\u4E0D\u5B58\u5728\u6216\u65E0\u5B57\u6BB5\u6570\u636E" };
  }
  const fields = record.fields;
  const salesName = extractSalesName(fields);
  const webhookUrl = env.WECOM_REMIND_WEBHOOK;
  if (!webhookUrl) {
    return { status: "error", error: "WECOM_REMIND_WEBHOOK \u672A\u914D\u7F6E\uFF08wrangler secret put\uFF09" };
  }
  const markdown = buildWecomMarkdown(fields, salesName);
  await sendWecomReminder(webhookUrl, markdown);
  console.log(`[remind] \u5DF2\u53D1\u9001\u4F01\u5FAE\u63D0\u9192: record=${recordId} \u4E1A\u52A1\u5458=${salesName || "\u672A\u77E5"}`);
  return { status: "ok", record_id: recordId, sales_person: salesName || "\u672A\u77E5" };
}
__name(sendUrgentReminder, "sendUrgentReminder");
function inferCountryFromPhone(phone) {
  if (!phone) return null;
  const digits = phone.replace(/[\s()\-–]/g, "");
  const TABLE = [
    ["+86", "China"],
    ["+852", "Hong Kong"],
    ["+853", "Macau"],
    ["+886", "Taiwan"],
    ["+1", "USA/Canada"],
    ["+44", "UK"],
    ["+61", "Australia"],
    ["+65", "Singapore"],
    ["+60", "Malaysia"],
    ["+66", "Thailand"],
    ["+63", "Philippines"],
    ["+62", "Indonesia"],
    ["+84", "Vietnam"],
    ["+91", "India"],
    ["+971", "UAE"],
    ["+966", "Saudi Arabia"],
    ["+974", "Qatar"],
    ["+968", "Oman"],
    ["+49", "Germany"],
    ["+33", "France"],
    ["+34", "Spain"],
    ["+39", "Italy"],
    ["+31", "Netherlands"],
    ["+46", "Sweden"],
    ["+47", "Norway"],
    ["+48", "Poland"],
    ["+55", "Brazil"],
    ["+52", "Mexico"],
    ["+54", "Argentina"],
    ["+56", "Chile"],
    ["+81", "Japan"],
    ["+82", "South Korea"]
  ];
  for (const [prefix, country] of TABLE) {
    if (digits.startsWith(prefix)) return country;
  }
  if (!digits.startsWith("+") && /^55\d{8}$/.test(digits)) return "Mexico";
  return null;
}
__name(inferCountryFromPhone, "inferCountryFromPhone");

// src/github-dispatch.js
var GITHUB_API = "https://api.github.com";
async function dispatchEvent(recordId, env, eventType, payload = {}) {
  if (!recordId) {
    console.log(`[dispatch] Skipping ${eventType}: no record_id`);
    return { status: "skipped", reason: "no record_id" };
  }
  if (!env.GITHUB_TOKEN) {
    console.log(`[dispatch] Skipping ${eventType}: GITHUB_TOKEN not configured`);
    return { status: "skipped", reason: "no token" };
  }
  const repo = env.GITHUB_REPO || "pyyzheng/soundbox-lead-poller-public";
  const url = `${GITHUB_API}/repos/${repo}/dispatches`;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `token ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "facebook-lead-webhook"
      },
      body: JSON.stringify({
        event_type: eventType,
        client_payload: { record_id: recordId, ...payload }
      })
    });
    if (resp.status === 204 || resp.status === 202) {
      console.log(`[dispatch] Triggered ${eventType} for ${recordId}`);
      return { status: "dispatched", record_id: recordId };
    }
    const text = await resp.text();
    console.error(`[dispatch] ${eventType} failed: ${resp.status} ${text}`);
    return { status: "error", code: resp.status, detail: `GitHub API ${resp.status}` };
  } catch (err) {
    console.error(`[dispatch] ${eventType} error: ${err.message}`);
    return { status: "error", detail: err.message };
  }
}
__name(dispatchEvent, "dispatchEvent");
async function triggerCompanyResearch(recordId, env) {
  return dispatchEvent(recordId, env, "company-research");
}
__name(triggerCompanyResearch, "triggerCompanyResearch");
async function triggerFacebookFirstContact(recordId, env) {
  return dispatchEvent(recordId, env, "facebook-first-contact");
}
__name(triggerFacebookFirstContact, "triggerFacebookFirstContact");
async function triggerAssignmentUnblock(recordId, env) {
  return dispatchEvent(recordId, env, "assignment-unblock");
}
__name(triggerAssignmentUnblock, "triggerAssignmentUnblock");

// src/failure.js
var FAILURE_TTL = 7 * 24 * 3600;
async function sha256Hex(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
__name(sha256Hex, "sha256Hex");
function sanitizeError(msg) {
  return String(msg || "").replace(/\b\d{10,16}\b/g, "[REDACTED-ID]").slice(0, 200);
}
__name(sanitizeError, "sanitizeError");
async function recordFailure(env, leadgenId, errorMsg, source = "webhook") {
  try {
    const key = `fail:${Date.now()}:${leadgenId}:${Math.random().toString(36).slice(2, 8)}`;
    await env.FAILED_LEADS.put(key, JSON.stringify({
      leadgen_id: leadgenId,
      error: errorMsg,
      time: (/* @__PURE__ */ new Date()).toISOString(),
      source
    }), { expirationTtl: FAILURE_TTL });
  } catch (e) {
    console.error(`[webhook] KV write failed: ${e.message}`);
  }
}
__name(recordFailure, "recordFailure");
async function recordMessengerFailure(env, psid, stage, err) {
  try {
    const safe = String(psid || "unknown");
    const psidPart = safe.slice(0, 4);
    const hash = (await sha256Hex(safe)).slice(0, 6);
    const dedupKey = `fail_dedup:messenger:${psidPart}:${hash}`;
    if (await env.MESSENGER_SESSIONS.get(dedupKey)) return;
    const key = `fail:messenger:${Date.now()}:${psidPart}:${hash}`;
    await env.FAILED_LEADS.put(key, JSON.stringify({
      source: "messenger",
      stage,
      psid_prefix: psidPart,
      psid_hash: hash,
      error: sanitizeError(err?.message || err),
      time: (/* @__PURE__ */ new Date()).toISOString()
    }), { expirationTtl: FAILURE_TTL });
    await env.MESSENGER_SESSIONS.put(dedupKey, "1", { expirationTtl: 300 });
  } catch (e) {
    console.error(`[messenger] recordMessengerFailure failed: ${e.message}`);
  }
}
__name(recordMessengerFailure, "recordMessengerFailure");

// src/messenger.js
var META_API_BASE2 = "https://graph.facebook.com";
var GLM_API_BASE = "https://open.bigmodel.cn/api/paas/v4";
var GLM_TIMEOUT_MS = 15e3;
var GLM_FAST_TIMEOUT_MS = 8e3;
var GLM_SEED_TIMEOUT_MS = 1e4;
var MODEL_FAST = "glm-4.5-flash";
var MODEL_FALLBACK = "glm-4-flash";
function isDeflect(text) {
  const t = (text || "").trim().toLowerCase();
  const deflectSet = /* @__PURE__ */ new Set([
    "ok",
    "yes",
    "no",
    "maybe",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank you",
    "thx",
    "thk",
    "obrigado",
    "obrigada",
    "gracias",
    "gracio",
    "\u597D\u7684",
    "\u662F\u7684",
    "\u4E0D\u662F",
    "\u53EF\u80FD",
    "\u4F60\u597D",
    "\u8C22\u8C22",
    "\u611F\u8C22",
    "just the booths"
  ]);
  return deflectSet.has(t) || t.length < 3;
}
__name(isDeflect, "isDeflect");
function isRepeatForm(text) {
  const t = (text || "").toLowerCase();
  return t.includes("filled out") || t.includes("i filled");
}
__name(isRepeatForm, "isRepeatForm");
function shouldQualifyDepth(currentMsg, userMsgCount) {
  if (userMsgCount < 2) return false;
  if (isRepeatForm(currentMsg)) return false;
  if (isDeflect(currentMsg)) return false;
  return true;
}
__name(shouldQualifyDepth, "shouldQualifyDepth");
async function fetchGLM(env, endpoint, body, { timeoutMs = GLM_TIMEOUT_MS, retry = true } = {}) {
  const MAX_ATTEMPTS = retry ? 2 : 1;
  let lastErr;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(`${GLM_API_BASE}/${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${env.GLM_API_KEY}`
        },
        body: JSON.stringify(body),
        signal: controller.signal
      });
      const data = await resp.json();
      clearTimeout(timer);
      if (data.error) throw new Error(`GLM API error (${endpoint}): ${data.error.message}`);
      return data;
    } catch (err) {
      clearTimeout(timer);
      lastErr = err;
      const name = err?.name || "";
      const isBizError = err.message?.startsWith?.("GLM API error");
      const isTransient = name === "TimeoutError" || name === "AbortError" || name === "TypeError" || /fetch failed|network/i.test(err.message || "");
      if (isBizError || !isTransient || attempt === MAX_ATTEMPTS - 1) throw err;
      console.warn(`[fetchGLM] retry ${endpoint} after ${name}: ${err.message}`);
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  throw lastErr;
}
__name(fetchGLM, "fetchGLM");
async function callGLM(env, messages, options = {}, fetchOpts = {}) {
  const model = options.model || MODEL_FAST;
  const makeBody = /* @__PURE__ */ __name((m) => ({
    model: m,
    messages,
    max_tokens: options.max_tokens || 200,
    temperature: options.temperature ?? 0.3,
    ...m.startsWith("glm-4.5") ? { thinking: { type: options.thinking ?? "disabled" } } : {}
  }), "makeBody");
  try {
    const data = await fetchGLM(env, "chat/completions", makeBody(model), fetchOpts);
    return data.choices?.[0]?.message?.content?.trim() || "";
  } catch (err) {
    if (/速率限制|rate limit|1302/i.test(err.message) && model !== MODEL_FALLBACK) {
      console.warn(`[callGLM] rate-limited on ${model}, fallback ${MODEL_FALLBACK}: ${err.message}`);
      const data = await fetchGLM(env, "chat/completions", makeBody(MODEL_FALLBACK), fetchOpts);
      return data.choices?.[0]?.message?.content?.trim() || "";
    }
    throw err;
  }
}
__name(callGLM, "callGLM");
async function getEmbedding(env, texts, opts = {}) {
  const data = await fetchGLM(env, "embeddings", {
    model: "embedding-3",
    input: texts,
    dimensions: 1024
  }, opts);
  return data.data.map((d) => d.embedding);
}
__name(getEmbedding, "getEmbedding");
var TZ_OFFSET_MS = 8 * 3600 * 1e3;
function isBusinessHours() {
  const utc8 = new Date(Date.now() + TZ_OFFSET_MS);
  const hour = utc8.getUTCHours();
  const day = utc8.getUTCDay();
  if (day === 0) return false;
  if (day === 6) return hour >= 8 && hour < 12;
  return hour >= 8 && hour < 18;
}
__name(isBusinessHours, "isBusinessHours");
var MESSAGE_INTERVAL_MS = 250;
var lastSentAt = /* @__PURE__ */ new Map();
function checkRateLimit(psid) {
  const now = Date.now();
  const last = lastSentAt.get(psid) || 0;
  if (now - last < MESSAGE_INTERVAL_MS) {
    return false;
  }
  lastSentAt.set(psid, now);
  return true;
}
__name(checkRateLimit, "checkRateLimit");
async function sendTypingIndicator(psid, env, channel = "fb") {
  try {
    await fetch(
      `${META_API_BASE2}/${env.META_API_VERSION}/me/messages?access_token=${env.META_PAGE_ACCESS_TOKEN}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          recipient: { id: psid },
          sender_action: "typing_on"
        })
      }
    );
  } catch {
  }
}
__name(sendTypingIndicator, "sendTypingIndicator");
async function fetchUserProfile(psid, env) {
  try {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 5e3);
    const url = `${META_API_BASE2}/${env.META_API_VERSION}/${psid}?fields=first_name&access_token=${env.META_PAGE_ACCESS_TOKEN}`;
    const resp = await fetch(url, { signal: controller.signal });
    clearTimeout(t);
    if (!resp.ok) {
      console.warn(`[messenger] UserProfile API ${resp.status} for ${psid}`);
      return null;
    }
    const data = await resp.json();
    return data.first_name || null;
  } catch (err) {
    console.warn(`[messenger] UserProfile fetch failed: ${err.message}`);
    return null;
  }
}
__name(fetchUserProfile, "fetchUserProfile");
function buildNeedSummary(req, language) {
  if (!req) return "";
  const parts = [];
  const q = String(req.quantity || "").match(/(\d+)/);
  if (q) parts.push(language === "zh" ? `${q[1]}\u4EBA` : `${q[1]}-person`);
  const scenario = req.use_scenario ? String(req.use_scenario).trim() : "";
  const model = req.product_model ? String(req.product_model).trim() : "";
  if (language === "zh") {
    const segs = [];
    if (scenario) segs.push(scenario);
    if (model) segs.push(model);
    if (segs.length) parts.push(segs.join("\u3001"));
    return parts.length ? parts.join("") + "\u9759\u97F3\u8231\u9700\u6C42" : "";
  }
  if (scenario) parts.push(scenario.toLowerCase());
  if (model) parts.push(model);
  return parts.length ? parts.join(" ") : "";
}
__name(buildNeedSummary, "buildNeedSummary");
async function sendAPI(psid, messagePayload, env, channel = "fb") {
  if (!checkRateLimit(psid)) {
    console.warn(`[${channel}] Rate limited for ${psid}`);
    return { status: "rate_limited" };
  }
  const resp = await fetch(
    `${META_API_BASE2}/${env.META_API_VERSION}/me/messages?access_token=${env.META_PAGE_ACCESS_TOKEN}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recipient: { id: psid },
        messaging_type: "RESPONSE",
        message: messagePayload
      })
    }
  );
  const data = await resp.json();
  if (data.error) {
    console.error(`[${channel}] Send error: ${data.error.message}`);
    await recordMessengerFailure(env, psid, channel + "_send", new Error(data.error.message)).catch(() => {
    });
    return { status: "error", error: data.error.message };
  }
  return { status: "sent", message_id: data.message_id };
}
__name(sendAPI, "sendAPI");
async function sendTextMessage(psid, text, env, channel = "fb") {
  return sendAPI(psid, { text }, env, channel);
}
__name(sendTextMessage, "sendTextMessage");
async function deliverAssistantReply(session, psid, text, env, channel) {
  const sendResult = await sendTextMessage(psid, text, env, channel);
  const delivered = sendResult.status === "sent";
  if (!delivered) {
    session.send_failures = session.send_failures || [];
    session.send_failures.push({
      error: sendResult.error || sendResult.status || "unknown",
      at: (/* @__PURE__ */ new Date()).toISOString(),
      channel
    });
    console.warn(`[${channel}] Bot reply not delivered to ${psid}: ${sendResult.error || sendResult.status}`);
    await recordMessengerFailure(env, psid, `${channel}_send`, new Error(sendResult.error || sendResult.status || "send failed")).catch(() => {
    });
  }
  session.messages.push({ role: "assistant", content: text, delivered });
  return sendResult;
}
__name(deliverAssistantReply, "deliverAssistantReply");
async function sendImageMessage(psid, imageUrl, env, channel = "fb") {
  return sendAPI(psid, { attachment: { type: "image", payload: { url: imageUrl } } }, env, channel);
}
__name(sendImageMessage, "sendImageMessage");
async function classifyIntent(messageText, env) {
  const prompt = `Classify this customer message intent. Reply with ONLY a JSON object, no other text.

Message: "${messageText}"

Possible intents:
- "greeting" \u2014 hello, hi, greetings
- "product_inquiry" \u2014 asking about products, specs, dimensions, models, features
- "shipping_inquiry" \u2014 asking about delivery, shipping, logistics, lead time
- "support" \u2014 after-sales, warranty, installation, issues
- "quote_request" \u2014 asking for a quote, price, quotation, cost, budget, "how much"
- "complaint" \u2014 complaints, refund, return, cancellation, bad experience, angry, frustrated
- "custom_order" \u2014 asking for customization, non-standard, special requirements
- "other" \u2014 anything else

Also detect the language (en, zh, es, ar, etc).

Reply format: {"intent":"...","language":"..."}`;
  try {
    const text = await callGLM(env, [{ role: "user", content: prompt }], {
      max_tokens: 100,
      temperature: 0.1
    }, { timeoutMs: GLM_FAST_TIMEOUT_MS, retry: false });
    const jsonMatch = text.match(/\{[^}]+\}/);
    if (jsonMatch) {
      return JSON.parse(jsonMatch[0]);
    }
  } catch (err) {
    console.error(`[messenger] Intent classification failed: ${err.message}`);
  }
  return { intent: "other", language: "en" };
}
__name(classifyIntent, "classifyIntent");
var DOMAIN_EXPANSIONS = {
  // 产品系列
  "booth": "silent booth acoustic pod phone booth meeting pod",
  "pod": "acoustic pod silent pod work pod meeting pod",
  "\u9694\u97F3": "\u9694\u97F3\u8231 \u9694\u97F3\u677F \u9694\u97F3\u95E8 \u9694\u97F3\u68C9 \u9694\u97F3\u6750\u6599",
  "\u9759\u97F3": "\u9759\u97F3\u8231 \u9759\u97F3\u8231 \u9694\u97F3\u8231 \u9759\u97F3\u623F",
  // 产品系列名
  "VRT": "VRT'Pod transparent silent booth glass pod",
  "VRT'Pod": "VRT'Pod transparent silent booth acoustic glass pod",
  "SR": "SR Series standard silent booth cost-effective",
  "VR": "VR Series VIP silent booth premium executive",
  "ART": "ART'Pod artistic silent booth design decor",
  // 功能需求
  "home office": "home office pod booth quiet workspace residential",
  "meeting": "meeting pod booth conference room team discussion",
  "recording": "recording studio sound booth acoustic treatment"
};
function expandQuery(query) {
  if (query.length >= 20) return query;
  for (const [key, expansion] of Object.entries(DOMAIN_EXPANSIONS)) {
    if (query.toLowerCase().includes(key.toLowerCase())) {
      return `${query} ${expansion}`;
    }
  }
  return query;
}
__name(expandQuery, "expandQuery");
var PRODUCT_IMAGES = Object.fromEntries([
  // [关键词列表, 图片 URL]
  [["vrt", "\u9759\u97F3\u8231", "\u9694\u97F3\u8231", "booth", "pod", "\u7535\u8BDD\u4EAD"], "https://images.soundboxbooth.com/vrt-series-overview.jpg"],
  [["homepod", "home pod", "home", "hush", "art"], "https://images.soundboxbooth.com/hush-art-series.jpg"]
].flatMap(([keys, url]) => keys.map((k) => [k, url])));
function matchProductImage(messageText) {
  const lower = messageText.toLowerCase();
  for (const [keyword, url] of Object.entries(PRODUCT_IMAGES)) {
    if (lower.includes(keyword.toLowerCase())) return url;
  }
  return null;
}
__name(matchProductImage, "matchProductImage");
async function retrieveContext(query, env) {
  try {
    const expandedQuery = expandQuery(query);
    console.log(`[messenger] Query: "${query}" \u2192 Expanded: "${expandedQuery}"`);
    const [queryVector] = await getEmbedding(env, [expandedQuery], { timeoutMs: GLM_FAST_TIMEOUT_MS, retry: false });
    const results = await env.VECTORIZE.query(queryVector, {
      topK: 3,
      returnMetadata: "all"
    });
    if (results.matches && results.matches.length > 0) {
      return results.matches.map((m) => m.metadata?.text || "").filter(Boolean);
    }
  } catch (err) {
    console.error(`[messenger] KB retrieval failed: ${err.message}`);
  }
  return [];
}
__name(retrieveContext, "retrieveContext");
var SESSION_TTL = 1800;
var MAX_MESSAGES = 20;
function createSession(psid, language, channel = "fb") {
  return {
    psid,
    language,
    created_at: (/* @__PURE__ */ new Date()).toISOString(),
    messages: [],
    send_failures: [],
    requirement: {
      product_category: null,
      product_model: null,
      use_scenario: null,
      location: null,
      quantity: null,
      budget: null,
      company: null,
      email: null,
      phone: null
    },
    stage: "collecting",
    handoff_sent: false,
    sent_images: [],
    customer_name: null,
    // 客户名（fb User Profile API 获取，handoff 个性化称呼用）
    is_form_lead: false,
    // Lead Form 表单客户（邮件单独跟进，Messenger 需确认意向才转交）
    qualification_confirmed: false,
    // 表单客户在 Messenger 回复有价值内容后置 true
    channel
    // 渠道：fb=Messenger, ig=Instagram（send 按 channel 选 endpoint）
  };
}
__name(createSession, "createSession");
function detectFormLead(text) {
  return /filled out (your|the) form|submitted.*form|i filled.*form|filled.*formulario|填写.*表单/i.test(text || "");
}
__name(detectFormLead, "detectFormLead");
async function loadSession(psid, env) {
  try {
    const data = await env.MESSENGER_SESSIONS.get(`session:${psid}`, "json");
    return data;
  } catch {
    return null;
  }
}
__name(loadSession, "loadSession");
async function saveSession(session, env) {
  if (session.messages.length > MAX_MESSAGES) {
    session.messages = session.messages.slice(-MAX_MESSAGES);
  }
  await env.MESSENGER_SESSIONS.put(`session:${session.psid}`, JSON.stringify(session), {
    expirationTtl: SESSION_TTL
  });
}
__name(saveSession, "saveSession");
var CONV_LOG_TTL = 30 * 24 * 3600;
async function saveConversationLog(session, env) {
  const req = session.requirement;
  const log = {
    psid: session.psid,
    channel: session.channel || "fb",
    language: session.language,
    created_at: session.created_at,
    logged_at: (/* @__PURE__ */ new Date()).toISOString(),
    stage: session.stage,
    handoff_sent: session.handoff_sent,
    send_failures: session.send_failures || [],
    requirement: req,
    message_count: session.messages.length,
    messages: session.messages
  };
  const key = `conv-log:${(/* @__PURE__ */ new Date()).toISOString().slice(0, 10)}:${session.psid}`;
  await env.MESSENGER_SESSIONS.put(key, JSON.stringify(log), {
    expirationTtl: CONV_LOG_TTL
  });
  console.log(`[messenger] Conversation log saved: ${key} (${session.messages.length} messages)`);
}
__name(saveConversationLog, "saveConversationLog");
function buildRequirementStatusBlock(req) {
  const required = [
    ["Product category\uFF08\u4EA7\u54C1\u7C7B\u522B\uFF09", req.product_category],
    ["Use scenario\uFF08\u4F7F\u7528\u573A\u666F\uFF09", req.use_scenario],
    ["Delivery location\uFF08\u6536\u8D27\u5730\uFF09", req.location],
    ["Quantity\uFF08\u6570\u91CF\uFF09", req.quantity]
  ];
  const contactCollected = req.email || req.phone;
  const contactStatus = contactCollected ? `"email=${req.email || "-"} phone=${req.phone || "-"}" (collected)` : "NOT COLLECTED (MANDATORY \u2014 must collect before handoff)";
  const productDetailCollected = req.product_model || req.quantity || req.use_scenario;
  const productDetailStatus = productDetailCollected ? "collected" : "NOT COLLECTED (need at least one: product model, quantity, or use scenario)";
  const optional = [
    ["Product model\uFF08\u4EA7\u54C1\u578B\u53F7\uFF09", req.product_model],
    ["Budget\uFF08\u9884\u7B97\uFF09", req.budget],
    ["Company\uFF08\u516C\u53F8\uFF09", req.company]
  ];
  const fmt = /* @__PURE__ */ __name((label, val) => val ? `${label}: "${val}" (collected)` : `${label}: NOT COLLECTED`, "fmt");
  const lines = [
    "Required fields (must collect before handoff):",
    ...required.map(([l, v]) => fmt(l, v)),
    `Contact info\uFF08\u8054\u7CFB\u65B9\u5F0F, at least email or phone\uFF09: ${contactStatus}`,
    `Product details\uFF08\u578B\u53F7/\u6570\u91CF/\u573A\u666F, at least one\uFF09: ${productDetailStatus}`,
    "",
    "Optional fields (collect if mentioned naturally):",
    ...optional.map(([l, v]) => fmt(l, v))
  ];
  return lines.join("\n");
}
__name(buildRequirementStatusBlock, "buildRequirementStatusBlock");
function canHandoff(req, messageCount = 0, formFlags = {}) {
  if (formFlags.is_form_lead && !formFlags.qualification_confirmed) return false;
  const hasContact = req.email || req.phone;
  const hasProductDetail = req.product_model || req.quantity || req.use_scenario;
  if (hasContact && hasProductDetail) return true;
  if (hasContact && messageCount >= 6) return true;
  return false;
}
__name(canHandoff, "canHandoff");
function parseLLMResponse(raw) {
  let extracted = {};
  try {
    const jsonMatch = raw.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      const parsed = JSON.parse(jsonMatch[0]);
      if (parsed.reply) {
        return { extracted: parsed.extracted || {}, reply: parsed.reply };
      }
      if (parsed.extracted) extracted = parsed.extracted;
    }
  } catch {
  }
  const emailMatch = raw.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
  const phoneMatch = raw.match(/(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}/);
  if (emailMatch) extracted.email = emailMatch[0];
  if (phoneMatch && phoneMatch[0].replace(/\D/g, "").length >= 7) extracted.phone = phoneMatch[0];
  return { extracted, reply: raw };
}
__name(parseLLMResponse, "parseLLMResponse");
function mergeExtractedFields(requirement, extracted) {
  for (const [key, value] of Object.entries(extracted)) {
    if (value && !requirement[key]) {
      requirement[key] = String(value);
    }
  }
}
__name(mergeExtractedFields, "mergeExtractedFields");
function applyGroundTruthFields(requirement, messages) {
  const customerText = messages.filter((m) => m.role === "user").map((m) => m.content).join("\n");
  const emailMatch = customerText.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
  const phoneMatch = customerText.match(/(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}/);
  if (emailMatch) requirement.email = emailMatch[0];
  if (phoneMatch && phoneMatch[0].replace(/\D/g, "").length >= 7) requirement.phone = phoneMatch[0];
  const allText = messages.map((m) => m.content).join("\n");
  if (!requirement.product_model) {
    const models = [...allText.matchAll(/\b(VRT[‐-―-\s'‘’]?(?:S|M|L|SM|ML))\b/g)].map((m) => m[1].replace(/[\s‐-―-'‘’]/g, "-"));
    if (models.length) requirement.product_model = models[models.length - 1];
  }
  if (!requirement.quantity) {
    const qtyRe = /(?<!\d)(?!19\d{2}|20\d{2})(\d{1,3}\+?)\s*(?:[-\s]?(?:people|persons?|persona|persone|seats?|cabins?|cabine?|booths?|pods?|units?|pcs?)\b|(个|人|台|套|间|座|件))(?![a-z])/gi;
    const qtys = [...allText.matchAll(new RegExp(qtyRe.source, qtyRe.flags))].map((m) => parseInt(m[1].replace("+", ""), 10));
    if (qtys.length) requirement.quantity = String(qtys[qtys.length - 1]);
  }
}
__name(applyGroundTruthFields, "applyGroundTruthFields");
var REPLY_FILTERS = [
  {
    name: "PRICE",
    detect: /* @__PURE__ */ __name((reply, userMsg) => {
      const PRICE = /(\$|¥|€|£|USD|CNY|RMB|人民币)\s?[\d,]+(\.\d{1,2})?|[\d,]+(\.\d{1,2})?\s?(元|美元|美金|块)/i;
      const PLACEHOLDER = /\[insert.*price|priced at\s*\[|价格[是为]\s*[\[【]/i;
      const HINT = /\b(priced at|costs?|price is|our price|报价[是为]|价格[是为])\b/i;
      if (!PRICE.test(reply) && !PLACEHOLDER.test(reply) && !HINT.test(reply)) return false;
      const CONTEXT = /\b(your budget|you mentioned|you said|your price|您的预算|您提到|您说的|您报的)/i;
      if (CONTEXT.test(reply) || PRICE.test(userMsg)) return false;
      return true;
    }, "detect"),
    zh: "\u4EF7\u683C\u7531\u6211\u4EEC\u9500\u552E\u56E2\u961F\u6839\u636E\u5177\u4F53\u914D\u7F6E\u62A5\u4EF7\uFF0C\u6211\u5E2E\u60A8\u8F6C\u4EA4\u2014\u2014\u8BF7\u95EE\u60A8\u7684\u6536\u8D27\u5730\u5728\u54EA\u91CC\uFF1F",
    en: "Pricing depends on your exact configuration. I'll have our team send you an accurate quote \u2014 where should we ship to?"
  },
  {
    name: "CROSS-STANDARD",
    detect: /* @__PURE__ */ __name((reply) => {
      const DSA = /Ds\.A\s*[\d.]+\s*dB/i;
      const RAW = /\d{2}[-–]\d{2}\s*dB|\d{2}\s*dB\s*±/;
      const FALLBACK = /not (?:directly )?comparable|different (?:test|testing|measurement) standard|不可直接比较|不同.*标准/i;
      return DSA.test(reply) && RAW.test(reply) && !FALLBACK.test(reply);
    }, "detect"),
    zh: "\u8FD9\u4E24\u4E2A\u7CFB\u5217\u4F7F\u7528\u4E0D\u540C\u7684\u6D4B\u8BD5\u6807\u51C6\uFF0C\u6570\u636E\u4E0D\u53EF\u76F4\u63A5\u6BD4\u8F83\u3002\u6211\u53EF\u4EE5\u8BA9\u6211\u4EEC\u7684\u6280\u672F\u56E2\u961F\u4E3A\u60A8\u8BE6\u7EC6\u8BF4\u660E\u2014\u2014\u65B9\u4FBF\u7559\u4E0B\u90AE\u7BB1\u5417\uFF1F",
    en: "These two use different testing standards, so the numbers aren't directly comparable. I'll have our team clarify \u2014 could you share your email so they can reach out?"
  },
  {
    name: "PERMISSION",
    detect: /* @__PURE__ */ __name((reply) => {
      const TAIL = /(would you like|do you want|shall i|any other questions|let me know if|how can I (?:assist|help)|anything else|需要了解更多|还想了解|还有其他问题|需要我.*吗)\b/i;
      const PROGRESS = /\b(how many|what.*size|which city|where.*ship|how much|what.*use|share.*email|your email|how soon|方便.*邮箱|收货.*在哪|几台|多少台|哪个城市|什么场景)\b/i;
      return TAIL.test(reply) && !PROGRESS.test(reply);
    }, "detect"),
    zh: "\u8FD9\u4E9B\u4FE1\u606F\u6211\u5E2E\u60A8\u8F6C\u7ED9\u9500\u552E\u56E2\u961F\u6765\u89E3\u7B54\u3002\u8BF7\u95EE\u60A8\u7684\u6536\u8D27\u5730\u5728\u54EA\u4E2A\u57CE\u5E02\uFF1F",
    en: "I'll have our team follow up on that. Where should we ship to?"
  }
];
function applyReplyFilters(reply, userMessage, language) {
  for (const f of REPLY_FILTERS) {
    if (f.detect(reply, userMessage)) {
      console.warn(`[messenger] ${f.name} INTERCEPTED in reply: "${reply.slice(0, 100)}"`);
      return language === "zh" ? f.zh : f.en;
    }
  }
  return reply;
}
__name(applyReplyFilters, "applyReplyFilters");
var LANG_MAP = { zh: "Reply in Simplified Chinese.", es: "Reply in Spanish.", ar: "Reply in Arabic.", pt: "Reply in Portuguese.", it: "Reply in Italian.", fr: "Reply in French.", de: "Reply in German." };
var STAGE_CONFIG = {
  DISCOVER: {
    maxTokens: 150,
    rules: `DISCOVER stage:
- Reply in 2-3 short sentences.
- Help identify the customer's use scenario and candidate model.
- Ask ONE progressing question targeting use_scenario or candidate_model.`
  },
  QUALIFY: {
    maxTokens: 200,
    rules: `QUALIFY stage:
- Scenario and model are known. Focus on qualification.
- Ask for the next missing slot: quantity \u2192 location \u2192 contact.
- Ask no more than TWO items at once.
- When quantity or location is captured, go for handoff.`
  },
  CLOSING: {
    maxTokens: 120,
    rules: `CLOSING stage:
- Handoff conditions met. Send the handoff message and wrap up.`
  }
};
function getStage(requirement) {
  if (canHandoff(requirement)) return "CLOSING";
  if (requirement.use_scenario && requirement.product_model) return "QUALIFY";
  return "DISCOVER";
}
__name(getStage, "getStage");
function getMaxTokens(stage) {
  return (STAGE_CONFIG[stage] || STAGE_CONFIG.CLOSING).maxTokens;
}
__name(getMaxTokens, "getMaxTokens");
function buildSystemPrompt(language, context, requirement, stage, formFlags = {}) {
  const formQualBlock = formFlags.is_form_lead && !formFlags.qualification_confirmed ? `## FORM LEAD QUALIFICATION (PRIORITY \u2014 overrides ADVANCEMENT RULE)
This customer came from a Lead Form \u2014 they already gave email/phone in the form, so contact info alone is NOT real intent (sales team emails ALL form leads separately). Your job: qualify whether they have real intent HERE in Messenger.
1. First reply: restate their stated need in one sentence + ask ONE specific confirming question (people count / use case / delivery timeline / specific model interest).
2. You MUST set extracted.qualification_confirmed = true when the customer's reply contains ANY of the following (this is a hard rule, not a judgment call):
   - A specific use case or scenario: recording, podcast, calls, meetings, study, work, vocal, music, etc.
   - A specific location, city, or country: "San Jose", "Tokyo", "Malta", "Hong Kong", "Costa Rica", "Brazil", etc.
   - A model name or number: VRT-S, VRT-M, VRT-L, ART-S, etc.
   - A quantity: "1", "2 units", "5 pods", etc.
   - A pricing or shipping question: "how much", "delivery time", "shipping cost", etc.
   The ONLY replies that do NOT qualify: "ok", "yes", "no", "maybe", "hello", "hi", "thanks", "thank you".
3. Until qualification_confirmed, do NOT go for handoff \u2014 keep qualifying one question per turn.
4. NEVER ask for information already in CURRENT REQUIREMENT STATUS above (email, phone, quantity, etc.). If the requirement status shows email/phone already collected, do NOT ask for it again.
5. If the customer deflects or refuses to answer a question twice, set qualification_confirmed = true and proceed to handoff \u2014 do not trap the customer in an endless qualification loop.` : "";
  const langInstruction = LANG_MAP[language] || "Reply in the same language the customer is using.";
  const contextBlock = context.length > 0 ? `
Product knowledge:
${context.map((c, i) => `${i + 1}. ${c}`).join("\n")}` : "";
  const statusBlock = buildRequirementStatusBlock(requirement);
  const stageRules = (STAGE_CONFIG[stage] || STAGE_CONFIG.CLOSING).rules;
  return `You are the Soundbox Acoustic Messenger chatbot on Facebook Messenger. You help customers learn about acoustic products AND collect their requirements for a sales quote.

## CURRENT REQUIREMENT STATUS
${statusBlock}

## CONVERSATION STAGE: ${stage}
${stageRules}

## FIRST SENTENCE RULE
The first sentence of your reply must be simple and low-density. It should only do ONE of these:
1. Confirm understanding: "Got it!" or "I see."
2. Directly answer the customer's question: "Yes, we have 4-person meeting pods."
3. Reassure: "I can help with that."
Do NOT include product features, conditions, pricing logic, or follow-up questions in the first sentence.

## QUESTION-TYPE RULE (strict)
End every reply with a PROGRESSING question, never a PERMISSION question.
- PERMISSION question (FORBIDDEN): any question the customer can end by saying "no".
  Blacklist: "Would you like to know more?" / "Need details on other models?" /
  "Any other questions?" / "Let me know if you need..." / "\u9700\u8981\u4E86\u89E3\u66F4\u591A\u5417\uFF1F" /
  "\u9700\u8981\u4E86\u89E3\u5176\u4ED6\u578B\u53F7\u5417\uFF1F" / "\u8FD8\u6709\u5176\u4ED6\u95EE\u9898\u5417\uFF1F"
- PROGRESSING question (REQUIRED): asks for ONE missing qualification field,
  or narrows between specific models.
- REWRITE EXAMPLES (follow these patterns):
  WRONG \u2192 "Would you like to know more about the VRT-S?"
  RIGHT \u2192 "How many seats do you need \u2014 just for yourself or for a team?"
  WRONG \u2192 "Any other questions?"
  RIGHT \u2192 "Which city should we ship to?"
  WRONG \u2192 "Let me know if you need more details."
  RIGHT \u2192 "What's your preferred use \u2014 phone calls or meetings?"

${formQualBlock}
## SHOWROOM GUIDE (when location is captured)
If the customer mentions they are in or near one of these cities, proactively invite them to visit:
- New York, USA: 1120 Avenue of the Americas, New York, NY 10036. Contact: +1 626 298 5263 (Rita)
- Los Angeles / Pasadena, USA: 45 S Arroyo Parkway, Pasadena, CA 91105. Contact: +1 626 298 5263 (Rita)
- Toronto, Canada: Suite 900, 26 Wellington Street East, Toronto, ON M5E 1S2. Contact: +1 437 917 9482 (Mike)
- Tokyo, Japan: Mita Kokusai Bldg. 2F, 1-4-28 Mita, Minato-ku, Tokyo 108-0073. Contact: +81 80 5313 7666 (Shirley)
- Sydney, Australia: 85 William St, Darlinghurst NSW 2010. Contact: +61 0481 261 538 (Feoney)
- Hong Kong: Room 3610, 36/F, Hong Kong Plaza, 188 Connaught Road West. Contact: +852 6212 7169 (Anne)
- Guangzhou, China: No. 12 Huashan Road, Shilou Town, Panyu District. Contact: hello@soundbox-sys.com (Shay)
How to mention it: "Great news \u2014 we have a showroom in [city]! You're welcome to visit and experience the booths in person: [address]. Our local contact [name] ([phone]) can arrange a tour." Then CONTINUE the normal qualification/handoff flow \u2014 do NOT stop or pause.

## ADVANCEMENT RULE (strict)
1. Primary goal: reach HANDOFF (capture email/phone). Product info is the means, not the end.
2. Slot priority (high\u2192low): use_scenario > candidate_model > quantity > location > contact.
   Follow-up question MUST target the highest-priority slot still missing.
3. FIT-BASED recommendation (NOT series-based):
   - single-person/phone-call \u2192 {VRT-S, VRT-SM, VRT-M} only
   - 2-4 people \u2192 VRT-ML. 4-6 people \u2192 VRT-L.
   Do NOT recommend outside the fit set.
4. STOP-EXPANDING: once {scenario + candidate model} are known, do NOT list more models
   unless customer asks or rejects. Switch to qualification.
5. When quantity OR location is captured, proactively go for handoff:
   "Could you share your email so the team can send a quote?"

## COLLECTION RULES
- Handoff requires TWO conditions: (1) Contact info (email or phone) AND (2) at least one product detail (product model, quantity, or use scenario).
- NEVER ask for information already in CURRENT REQUIREMENT STATUS above. If email/phone/quantity/location is already collected, do NOT re-ask \u2014 move to the next missing slot or proceed to handoff.
- If the customer refuses to provide a piece of information (says "no", "don't want to", "prefer not to", or changes subject), do NOT ask again. Accept what you have and proceed to handoff with available info.

## PRODUCT SPEC TABLE \u2014 SINGLE SOURCE OF TRUTH (acoustic pods)
For ANY pod dimension/isolation/capacity question, quote ONLY from this table.
Ignore retrieved snippets for these numbers.

| Model        | Capacity | W\xD7D\xD7H (mm)          | Isolation                        | Positioning              |
|--------------|----------|---------------------|----------------------------------|--------------------------|
| VRT-S        | 1        | 1000\xD7940\xD72100       | ISO 23351-1 Class A, Ds.A 30.4dB | phone booth              |
| VRT-SM       | 1        | 1100\xD71040\xD72100      | ISO 23351-1 Class A, Ds.A 30.2dB | compact work pod         |
| VRT-M        | 1        | 1550\xD71240\xD72100      | ISO 23351-1 Class A, Ds.A 30.3dB | full-size single office  |
| VRT-ML       | 2-4      | 2200\xD71240\xD72100      | ISO 23351-1 Class A, Ds.A 30.6dB | meeting pod              |
| VRT-L        | 4-6      | 2200\xD71640\xD72100      | ISO 23351-1 Class A, Ds.A 31.0dB | large meeting pod        |
| VRT+         | same     | same as VRT         | 35-40dB (measured differently)   | upgraded isolation       |
| SR-S~XXL     | 1-8+     | 1200\xB2~2500\xB2\xD72200   | 25-30dB (measured differently)   | value line               |
| VR-S~XXL     | 1-8+     | 1200\xB2~2500\xB2\xD72400   | 30-35dB (measured differently)   | premium VIP              |
| ART-S        | 1        | 1000\xD7940\xD72100       | ISO 23351-1 Class A, Ds.A 30.4dB | art phone booth          |
| ART-SM       | 1        | 1100\xD71040\xD72100      | ISO 23351-1 Class A, Ds.A 30.2dB | art work pod             |
| ART-M        | 1        | 1550\xD71240\xD72100      | ISO 23351-1 Class A, Ds.A 30.3dB | art focus booth          |
| ART-ML       | 2-4      | 2200\xD71240\xD72100      | ISO 23351-1 Class A, Ds.A 30.6dB | art meeting pod          |
| ART-L        | 4-6      | 2200\xD71640\xD72100      | ISO 23351-1 Class A, Ds.A 31.0dB | art large meeting        |
| ART+-S       | 1        | 1000\xD7940\xD72100       | ISO 23351-1 Class B, Ds.A 25.1dB | budget art phone booth   |
| ART+-SM      | 1        | 1100\xD71040\xD72100      | ISO 23351-1 Class B, Ds.A 26.4dB | budget art work pod      |
| ART+-M       | 1        | 1550\xD71240\xD72100      | ISO 23351-1 Class C, Ds.A 23.8dB | budget art focus booth   |
| ART+-ML      | 2-4      | 2200\xD71240\xD72100      | ISO 23351-1 Class B, Ds.A 27.0dB | budget art meeting pod   |
| ART+-L       | 4-6      | 2200\xD71640\xD72100      | ISO 23351-1 Class B, Ds.A 25.1dB | budget art large meeting |
| HOUSE NO.1   | 1        | ~1200\xD71200\xD72200    | 25dB \xB13                          | residential, basic       |
| HOUSE NO.2   | 1        | ~1500\xD71500\xD72200    | 26dB \xB13                          | residential, flat-top    |
| HOUSE NO.2.4 | 4        | ~2000\xD72000\xD72200    | 25dB \xB13                          | residential, 4-person    |

## ISOLATION-COMMUNICATION RULE
Two isolation groups. ONLY compare within the SAME group.
- GROUP A (ISO 23351-1 Ds.A, comparable): VRT-S/SM/M/ML/L, ART-S/SM/M/ML/L, ART+-S/SM/M/ML/L
- GROUP B (raw dB, NOT comparable to Group A): VRT+, SR-*, VR-*, HOUSE NO.*
- Cross-group comparison \u2192 say: "These use different testing standards, so the numbers aren't directly comparable. I'll have our team clarify."
- ART+ is the budget line: lower Ds.A than ART, at a lower price point.

For door / engineering-acoustics / NVH products, use retrieved "Product knowledge" snippets.

## FORMAT RULES
1. Friendly, conversational chat tone. No "Dear [Name]" or formal email language.
2. Never sign off with a name. No placeholders.
3. VARY your phrasing every reply; never repeat the same sentence pattern.
4. Use short sentences. No bullet lists.

## TRUTH RULES
1. NEVER fabricate or estimate prices.
2. If unsure about any spec, say: "Let me connect you with our team."

${langInstruction}
${contextBlock}

## OUTPUT FORMAT \u2014 CRITICAL
You MUST reply with ONLY a valid JSON object. No markdown, no code fences, no extra text.
{"extracted": {"field_name": "value"}, "reply": "your reply text here"}

- "reply" is ALWAYS required \u2014 this is what the customer sees.
- "extracted" contains fields you can confidently infer. If the customer mentions email/phone, you MUST extract it.
- Valid fields: product_category, product_model, use_scenario, location, quantity, budget, company, email, phone, qualification_confirmed (boolean: true only when form-lead customer shows concrete intent in Messenger)
- Nothing to extract: {"extracted": {}, "reply": "..."}`;
}
__name(buildSystemPrompt, "buildSystemPrompt");
var MID_TTL = 300;
async function handleUserMessages(psid, messages, env, channel = "fb") {
  const checks = await Promise.all(
    messages.map(({ mid }) => env.MESSENGER_SESSIONS.get(`mid:${mid}`))
  );
  const fresh = messages.filter((_, i) => {
    if (checks[i]) {
      console.log(`[messenger] Skipping duplicate mid: ${messages[i].mid}`);
      return false;
    }
    return true;
  });
  if (fresh.length === 0) return;
  await Promise.all(
    fresh.map(
      ({ mid }) => env.MESSENGER_SESSIONS.put(`mid:${mid}`, "1", { expirationTtl: MID_TTL })
    )
  );
  if (env.MESSENGER_FORCE_REPLY !== "1" && isBusinessHours()) {
    console.log(`[messenger] Business hours (Mon-Fri 8-18, Sat 8-12 GMT+8) \u2014 skipping auto-reply for ${psid}, ${fresh.length} msgs`);
    return;
  }
  const freshTexts = fresh.map((m) => m.text);
  let session = await loadSession(psid, env);
  for (const text of freshTexts) {
    const lang = session?.language || "en";
    if (!session) session = createSession(psid, lang, channel);
    else {
      session.channel = channel;
      if (!session.send_failures) session.send_failures = [];
    }
    try {
      await _processOneMessage(psid, text, session, env);
    } catch (err) {
      console.error(`[messenger] processOne failed for ${psid}: ${err.message}`);
      await recordMessengerFailure(env, psid, "process_one", err).catch(() => {
      });
    }
  }
  await saveSession(session, env);
  if (session.handoff_sent || session.messages.length >= 3) {
    await saveConversationLog(session, env).catch(
      (err) => console.error(`[messenger] Conversation log save failed: ${err.message}`)
    );
  }
}
__name(handleUserMessages, "handleUserMessages");
async function _processOneMessage(psid, messageText, session, env) {
  const channel = session.channel || "fb";
  console.log(`[${channel}] Processing message from ${psid}: "${messageText?.slice(0, 80)}"`);
  await sendTypingIndicator(psid, env, channel);
  if (channel === "fb" && !session.customer_name) {
    const name = await fetchUserProfile(psid, env);
    if (name) session.customer_name = name;
  }
  const { intent, language } = await classifyIntent(messageText, env);
  console.log(`[messenger] Intent: ${intent}, Language: ${language}`);
  session.language = language;
  const lastUserMsg = session.messages.filter((m) => m.role === "user").pop();
  if (lastUserMsg && lastUserMsg.content === messageText) {
    console.log(`[messenger] Skipping duplicate content from ${psid}`);
    return;
  }
  session.messages.push({ role: "user", content: messageText });
  const userMsgCount = session.messages.filter((m) => m.role === "user").length;
  if (userMsgCount === 1 && session.is_form_lead !== true) {
    session.is_form_lead = detectFormLead(messageText);
    if (session.is_form_lead) console.log(`[messenger] Form lead detected for ${psid} (qualify before handoff)`);
  }
  if (intent === "greeting" && !session.handoff_sent) {
    const greeting = language === "zh" ? "\u60A8\u597D\uFF01\u611F\u8C22\u60A8\u8054\u7CFB Soundbox Acoustic\u3002\u6211\u53EF\u4EE5\u5E2E\u60A8\u4E86\u89E3\u9759\u97F3\u8231\u3001\u9694\u97F3\u4EA7\u54C1\u548C\u58F0\u5B66\u89E3\u51B3\u65B9\u6848\u3002\u8BF7\u95EE\u60A8\u6709\u4EC0\u4E48\u9700\u8981\uFF1F" : "Hello! Thanks for reaching out to Soundbox Acoustic. I can help you with information about our silent booths, acoustic pods, and acoustic treatment products. What are you looking for?";
    await deliverAssistantReply(session, psid, greeting, env, channel);
    return;
  }
  if (session.handoff_sent) {
    const postHandoffSystem = language === "zh" ? "\u4F60\u662F Soundbox Acoustic \u7684\u5BA2\u670D\u52A9\u624B\u3002\u8BE5\u5BA2\u6237\u7684\u9700\u6C42\u5DF2\u8F6C\u4EA4\u9500\u552E\u56E2\u961F\u3002\u8BF7\u6839\u636E\u5BA2\u6237\u7684\u65B0\u6D88\u606F\u7B80\u77ED\u56DE\u590D\uFF082-3\u53E5\uFF09\uFF1A\u5982\u679C\u5BA2\u6237\u8865\u5145\u4E86\u9700\u6C42\uFF08\u5982\u7279\u5B9A\u7528\u9014\u3001\u578B\u53F7\u3001\u5C3A\u5BF8\uFF09\uFF0C\u786E\u8BA4\u5DF2\u8BB0\u5F55\u5E76\u544A\u77E5\u9500\u552E\u4F1A\u4E00\u5E76\u5904\u7406\uFF1B\u5982\u679C\u5BA2\u6237\u95EE\u4EA7\u54C1/\u89C4\u683C/\u7269\u6D41\u95EE\u9898\uFF0C\u57FA\u4E8E\u77E5\u8BC6\u5E93\u56DE\u7B54\uFF1B\u6700\u540E\u63D0\u9192\u9500\u552E\u56E2\u961F\u4F1A\u57281-3\u4E2A\u5DE5\u4F5C\u65E5\u5185\u8054\u7CFB\u3002\u4E0D\u8981\u91CD\u590D\u7D22\u53D6\u8054\u7CFB\u65B9\u5F0F\u3002\u7EDD\u5BF9\u4E0D\u80FD\u7F16\u9020\u4EF7\u683C\u2014\u2014\u5982\u679C\u5BA2\u6237\u95EE\u4EF7\u683C\uFF0C\u8BF4\u9500\u552E\u56E2\u961F\u4F1A\u5728\u62A5\u4EF7\u4E2D\u63D0\u4F9B\u3002" : "You are a Soundbox Acoustic assistant. This customer's requirements have been passed to the sales team. Respond briefly (2-3 sentences) to their new message: if they add requirements (specific use case, model, size), acknowledge and say the sales team will include it; if they ask product/spec/shipping questions, answer from knowledge; end by reminding the sales team will contact them within 1-3 business days. Do NOT re-ask for contact info. NEVER fabricate prices \u2014 if asked about pricing, say the sales team will provide a quote.";
    const postHandoffContext = await retrieveContext(messageText, env);
    try {
      const raw = await callGLM(env, [
        { role: "system", content: postHandoffSystem + "\n\n## Product Knowledge\n" + postHandoffContext },
        ...session.messages.slice(-MAX_MESSAGES)
      ], { max_tokens: 200, temperature: 0.3 }, { timeoutMs: GLM_FAST_TIMEOUT_MS });
      const reply2 = raw.trim() || (language === "zh" ? "\u597D\u7684\uFF0C\u6211\u5DF2\u8BB0\u5F55\u60A8\u7684\u8865\u5145\u4FE1\u606F\uFF0C\u9500\u552E\u56E2\u961F\u4F1A\u4E00\u5E76\u5904\u7406\u3002" : "Got it, I've noted your additional details. Our sales team will include this in your quote.");
      await deliverAssistantReply(session, psid, reply2, env, channel);
    } catch (err) {
      console.error(`[${channel}] Post-handoff reply failed: ${err.message}`);
      const fallback = language === "zh" ? "\u597D\u7684\uFF0C\u5DF2\u8BB0\u5F55\u3002\u9500\u552E\u56E2\u961F\u4F1A\u5C3D\u5FEB\u8054\u7CFB\u60A8\uFF01" : "Got it! Our sales team will reach out to you shortly with all the details.";
      await deliverAssistantReply(session, psid, fallback, env, channel);
    }
    return;
  }
  const context = await retrieveContext(messageText, env);
  const stage = getStage(session.requirement);
  const systemPrompt = buildSystemPrompt(language, context, session.requirement, stage, { is_form_lead: session.is_form_lead, qualification_confirmed: session.qualification_confirmed });
  const maxTokens = getMaxTokens(stage);
  const llmMessages = [
    { role: "system", content: systemPrompt },
    ...session.messages.slice(-MAX_MESSAGES)
  ];
  let reply;
  try {
    const raw = await callGLM(env, llmMessages, { max_tokens: maxTokens, temperature: 0.3 });
    const fullRaw = raw;
    if (env.DEBUG_LLM) console.log(`[messenger] LLM raw response (FULL): ${fullRaw}`);
    const parsed = parseLLMResponse(fullRaw);
    if (env.DEBUG_LLM) console.log(`[messenger] parseLLMResponse output: extracted=${JSON.stringify(parsed.extracted)} reply="${parsed.reply.slice(0, 100)}"`);
    if (Object.keys(parsed.extracted).length > 0) {
      mergeExtractedFields(session.requirement, parsed.extracted);
      const userMsgCount2 = session.messages.filter((m) => m.role === "user").length;
      const llmQualified = parsed.extracted.qualification_confirmed === true;
      const codeQualified = shouldQualifyDepth(messageText, userMsgCount2);
      const isDeflectMsg = isDeflect(messageText);
      if ((llmQualified || codeQualified) && !isDeflectMsg && !session.qualification_confirmed) {
        session.qualification_confirmed = true;
        console.log(`[messenger] Form lead qualified (llm=${llmQualified} code=${codeQualified} deflect=${isDeflectMsg}) for ${psid}`);
      }
      if (!session.requirement.product_category && session.requirement.product_model) {
        const model = session.requirement.product_model.toUpperCase();
        if (/^(VRT|ART)/.test(model)) session.requirement.product_category = "silent booth";
        else if (/^(SR|VR)/.test(model)) session.requirement.product_category = "silent booth";
        else if (/HOUSE/.test(model)) session.requirement.product_category = "home pod";
        else if (/G\d/.test(model)) session.requirement.product_category = "acoustic door";
      }
      console.log(`[messenger] Extracted fields: ${JSON.stringify(parsed.extracted)}`);
    }
    applyGroundTruthFields(session.requirement, session.messages);
    reply = parsed.reply;
    reply = applyReplyFilters(reply, messageText, language);
  } catch (err) {
    console.error(`[messenger] Reply generation failed: ${err.message}`);
    await recordMessengerFailure(env, psid, "reply_generation", err).catch(() => {
    });
    const fallback = language === "zh" ? "\u611F\u8C22\u60A8\u7684\u54A8\u8BE2\uFF01\u6211\u5DF2\u8BB0\u5F55\u60A8\u7684\u6D88\u606F\uFF0C\u6211\u4EEC\u7684\u9500\u552E\u56E2\u961F\u4F1A\u5C3D\u5FEB\u56DE\u590D\u60A8\u3002" : "Thanks for your inquiry! I've noted your message and our sales team will get back to you shortly.";
    await deliverAssistantReply(session, psid, fallback, env, channel);
    applyGroundTruthFields(session.requirement, session.messages);
    return;
  }
  if (!session.handoff_sent && canHandoff(session.requirement, session.messages.length, { is_form_lead: session.is_form_lead, qualification_confirmed: session.qualification_confirmed })) {
    console.log(`[messenger] Requirement complete, triggering handoff for ${psid}`);
    try {
      await writeMessengerLead(session, env);
      console.log(`[messenger] Feishu lead created for ${psid}`);
    } catch (err) {
      console.error(`[messenger] Feishu write failed (still confirming to user): ${err.message}`);
      await recordMessengerFailure(env, psid, "feishu_write", err).catch(() => {
      });
    }
    session.handoff_sent = true;
    const custName = session.customer_name || "";
    const need = buildNeedSummary(session.requirement, language);
    const handoffMsg = language === "zh" ? `\u597D\u7684${custName ? `\uFF0C${custName}` : ""}\uFF0C\u6211\u5DF2\u7ECF\u6536\u96C6\u4E86\u60A8\u7684\u9700\u6C42${need ? `\uFF08${need}\uFF09` : "\u4FE1\u606F"}\uFF0C\u5DF2\u7ECF\u8F6C\u7ED9\u6211\u4EEC\u7684\u9500\u552E\u56E2\u961F\uFF0C\u4ED6\u4EEC\u4F1A\u901A\u8FC7\u90AE\u4EF6 soundboxbooth@gmail.com \u5728 1-3 \u4E2A\u5DE5\u4F5C\u65E5\u5185\u7ED9\u60A8\u62A5\u4EF7\u3002\u5982\u6709\u5176\u4ED6\u95EE\u9898\u968F\u65F6\u95EE\u6211\uFF01` : `${custName ? `Hi ${custName}! ` : "Great, "}I've got everything I need${need ? ` \u2014 ${need}` : ""}! I've passed your requirements to our sales team. They'll send you a quote via soundboxbooth@gmail.com within 1-3 business days. Feel free to ask me anything else in the meantime!`;
    reply = handoffMsg;
  }
  const imageUrl = matchProductImage(messageText) || matchProductImage(reply);
  const isNewImage = imageUrl && !(session.sent_images || []).includes(imageUrl);
  if (isNewImage) {
    session.sent_images = [...session.sent_images || [], imageUrl];
  }
  const trimmed = reply.length > 1950 ? reply.slice(0, 1950) + "..." : reply;
  await deliverAssistantReply(session, psid, trimmed, env, channel);
  if (isNewImage) {
    await sendImageMessage(psid, imageUrl, env, channel).catch(
      (err) => console.error(`[${channel}] Image send failed: ${err.message}`)
    );
  }
}
__name(_processOneMessage, "_processOneMessage");
var KB_ENTRIES = [
  // --- 企业信息 ---
  { id: "kb-company-overview", text: "Soundbox Acoustic (\u58F0\u535A\u58EB) is an acoustic technology company founded in 2008, specializing in sound absorption, sound insulation, vibration damping, and acoustic diffusion. With over 500 employees including 60+ technical engineers, Soundbox delivers comprehensive acoustic solutions for commercial, industrial, and residential applications. Core product lines include soundproof pods (silent cabins), acoustic doors, architectural acoustic panels, vibration isolation systems, and custom noise control enclosures. All products designed, engineered, and manufactured in-house at Guangzhou headquarters.", metadata: { category: "company", product: "all" } },
  { id: "kb-company-credentials", text: "Soundbox holds nationally recognized qualifications: National Specialized 'Little Giant' Enterprise, National High-Tech Enterprise, ISO 9001/14001/45001 triple certifications. In-house CNAS-accredited laboratory for independent acoustic testing. Over 200 patents covering acoustic materials, structural designs, and manufacturing processes. Every acoustic claim backed by certified test reports.", metadata: { category: "company", product: "all" } },
  { id: "kb-company-clients", text: "Over 5 years, Soundbox has supplied Tesla, Apple, Amazon, Boeing, Huawei, Alibaba, ByteDance. Designated acoustic provider for Beijing Winter Olympics, Hangzhou Asian Games, Harbin Asian Winter Games. Co-built world's third anechoic chamber at -21 dB with Tsinghua University. Only Asian company in German EASE acoustic software database.", metadata: { category: "company", product: "all" } },
  { id: "kb-company-contact", text: "Contact: Project hotline +86 136-3213-0080. Complaint hotline 4006-43-4006. Email: acoustic@soundbox.hk. Website: www.soundbox.hk. Address: No.12 Huashan Road, Shilou Town, Panyu District, Guangzhou, China. Response within 2 business hours (GMT+8, 9:00-18:00).", metadata: { category: "company", product: "all" } },
  // --- VRT 系列（真实规格） ---
  { id: "kb-vrt-selling-points", text: "VRT'Pod series: 5 models \u2014 VRT'Pod-S, VRT'Pod-SM, VRT'Pod-M, VRT'Pod-ML, VRT'Pod-L. Acoustic rating: ISO 23351-1 Class A, Ds.A 30.2-31.0 dB per model. GREENGUARD Gold certified. Unique selling points: (1) Privacy without isolation \u2014 90% transparent acoustic glass, maintains open-plan feel. (2) Plug-and-play \u2014 modular assembly in 30 minutes, no construction, relocatable. (3) Certified \u2014 CNAS-lab test report with every unit. (4) Healthy \u2014 carbon-plastic panels, zero benzene, E0 formaldehyde. (5) Versatile \u2014 from solo phone booth to 6-person meeting room.", metadata: { category: "product", product: "vrt", series: "VRT" } },
  { id: "kb-vrt-s", text: "VRT'Pod-S: single-person phone booth. W1000 x D940 x H2100mm (height clearance 2170mm). Acoustic rating: ISO 23351-1 Class A, Ds.A 30.4 dB. Carbon-plastic acoustic panels, zero benzene, E0 formaldehyde. For quick calls, private conversations in open-plan offices. Includes ventilation, LED lighting, power outlet. Modular assembly in ~30 min. https://www.soundbox.hk/products/detail/vrtpod-phone-booth-s", metadata: { category: "product", product: "vrt", series: "VRT", size: "S" } },
  { id: "kb-vrt-sm", text: "VRT'Pod-SM: compact single-person work pod. W1100 x D1040 x H2100mm (height clearance 2170mm). Acoustic rating: ISO 23351-1 Class A, Ds.A 30.2 dB. More desk space than VRT-S, better for laptop work and video calls. Carbon-plastic panels, E0 formaldehyde. For focused tasks and short video conferences. https://www.soundbox.hk/products/detail/vrtpod-office-pod-sm", metadata: { category: "product", product: "vrt", series: "VRT", size: "SM" } },
  { id: "kb-vrt-m", text: "VRT'Pod-M: full single-person office pod. W1550 x D1240 x H2100mm (height clearance 2170mm). Acoustic rating: ISO 23351-1 Class A, Ds.A 30.3 dB. Replaces traditional cubicles and small private offices. Ergonomic design for long-term daily use. Spacious work surface with monitor space. Carbon-plastic panels, E0 formaldehyde. https://www.soundbox.hk/products/detail/vrtpod-work-booth-m", metadata: { category: "product", product: "vrt", series: "VRT", size: "M" } },
  { id: "kb-vrt-ml", text: "VRT'Pod-ML: meeting pod for 2-4 persons. W2200 x D1240 x H2100mm (height clearance 2170mm). Acoustic rating: ISO 23351-1 Class A, Ds.A 30.6 dB. For team discussions, client meetings, video conferences. Carbon-plastic panels, E0 formaldehyde. Eliminates need to book traditional meeting rooms. https://www.soundbox.hk/products/detail/vrtpod-meeting-pod-ml", metadata: { category: "product", product: "vrt", series: "VRT", size: "ML" } },
  { id: "kb-vrt-l", text: "VRT'Pod-L: large meeting pod for 4-6 persons. W2200 x D1640 x H2100mm (height clearance 2170mm). Acoustic rating: ISO 23351-1 Class A, Ds.A 31.0 dB. For team meetings, brainstorming, client presentations. Extra depth provides more room for larger groups. Carbon-plastic panels, E0 formaldehyde. https://www.soundbox.hk/products/detail/vrtpod-silent-pod-l", metadata: { category: "product", product: "vrt", series: "VRT", size: "L" } },
  // --- VRT+ 系列（升级版） ---
  { id: "kb-vrt-plus", text: "VRT+ Series: upgraded VRT'Pod with 35-40dB sound insulation (vs VRT's 30-35dB). Features: dual-fan silent ventilation with adjustable speed, Bluetooth connectivity, built-in speaker system. Same 5 sizes (S/SM/M/ML/L) with identical external dimensions. Carbon-plastic panels, E0 formaldehyde. Ideal for premium offices and executive environments. https://www.soundbox.hk/products/detail/vrtpod-single-pod-s", metadata: { category: "product", product: "vrt-plus", series: "VRT+" } },
  // --- SR 系列 ---
  { id: "kb-sr-overview", text: "SR Series (Standard Silent Booth): cost-effective line for standard office use. Sizes: SR-S (1-2 persons, 1.44m\xB2), SR-M (2-4 persons, 2.25m\xB2), SR-L (4-6 persons, 4m\xB2), SR-XL (6-8 persons, 6.25m\xB2), SR-XXL (8+ persons). Sound insulation 25-30dB. Steel frame + acoustic panels. Modular, movable. Includes LED lighting, silent ventilation, power outlets. Optional AC. Best value for standard office needs.", metadata: { category: "product", product: "sr", series: "SR" } },
  { id: "kb-sr-dimensions", text: "SR Series dimensions: SR-S exterior 1200x1200x2200mm. SR-M exterior 1500x1500x2200mm. SR-L exterior 2000x2000x2200mm. SR-XL exterior 2500x2500x2200mm. Internal dimensions approximately 200mm smaller per side. Ceiling height 2200mm. Weight ranges ~300kg (S) to ~800kg (XL).", metadata: { category: "product", product: "sr", series: "SR" } },
  // --- VR 系列 ---
  { id: "kb-vr-overview", text: "VR Series (VIP Silent Booth): premium line with superior 30-35dB sound insulation. Features automatic doors, triple-glazed windows, smart dimmable LED lighting, climate control, leather/fabric interior options, gigabit ethernet. Sizes: VR-S through VR-XXL. Taller profile at 2400mm. Ideal for executive offices, VIP meeting rooms, premium environments.", metadata: { category: "product", product: "vr", series: "VR" } },
  { id: "kb-vr-dimensions", text: "VR Series dimensions: VR-S exterior 1200x1200x2400mm. VR-M exterior 1500x1500x2400mm. VR-L exterior 2000x2000x2400mm. VR-XL exterior 2500x2500x2400mm. All 2400mm tall. Interior ~200mm smaller per side. Premium surface treatment: genuine leather or high-end fabric.", metadata: { category: "product", product: "vr", series: "VR" } },
  // --- Home Pod 详细 ---
  { id: "kb-homepod-detail", text: "Home Pod series for residential use. HOUSE NO.1 (1 person, 25dB \xB13dB, natural ventilation, standard LED), HOUSE NO.2 (1 person, flat-top design, 26dB \xB13dB, silent fan, dimmable LED), HOUSE NO.2.4 (4 persons, 25dB \xB13dB). Space-efficient footprint. Tool-free simplified assembly. For home office, music practice, reading, gaming, remote work.", metadata: { category: "product", product: "home", series: "HomePod" } },
  // --- 全系对比 ---
  { id: "kb-series-comparison", text: "Quick comparison: SR Series = best value, 25-30dB, standard office. VR Series = premium, 30-35dB, executive/VIP. VRT Series = transparent glass design, ISO 23351-1 Class A, Ds.A 30.2-31.0 dB per model, open offices. VRT+ = upgraded VRT, 35-40dB. ART Series = artistic customization, ISO 23351-1 Class A, Ds.A 30.2-31.0 dB, high-end spaces. ART+ Series = budget artistic, ISO 23351-1 Class B-C, Ds.A 23.8-27.0 dB per model. Home Pod = residential, HOUSE NO.1 (25dB) / HOUSE NO.2 (26dB) / HOUSE NO.2.4 (25dB). All include ventilation, LED lighting, power. All modular and relocatable.", metadata: { category: "product", product: "all" } },
  // --- ART 系列 ---
  { id: "kb-art-plus", text: "ART+ Series: budget-friendly artistic silent booths. Per-model Ds.A: ART+-S Ds.A 25.1dB (Class B), ART+-SM Ds.A 26.4dB (Class B), ART+-M Ds.A 23.8dB (Class C), ART+-ML Ds.A 27.0dB (Class B), ART+-L Ds.A 25.1dB (Class B). ISO 23351-1 Class B-C. Same artistic exterior design as ART series at a lower price point. For budget-conscious buyers who still want aesthetic appeal. Carbon-plastic panels, E0 formaldehyde.", metadata: { category: "product", product: "art-plus", series: "ART+" } },
  { id: "kb-art-small", text: "ART'Pod Series: artistic design silent booths. ART-S: W1000 x D940 x H2100mm, Ds.A 30.4 dB. ART-SM: W1100 x D1040 x H2100mm, Ds.A 30.2 dB. ISO 23351-1 Class A. Customizable exterior finishes for design studios, luxury retail, hotel lobbies. Carbon-plastic panels, E0 formaldehyde. https://www.soundbox.hk/products/detail/artpod-small-pod-s", metadata: { category: "product", product: "art", series: "ART" } },
  { id: "kb-art-medium", text: "ART'Pod-M: W1550 x D1240 x H2100mm, Ds.A 30.3 dB. ART'Pod-ML: W2200 x D1240 x H2100mm (2-4 persons), Ds.A 30.6 dB. ART'Pod-L: W2200 x D1640 x H2100mm (4-6 persons), Ds.A 31.0 dB. All ISO 23351-1 Class A. Customizable colors, patterns, exterior finishes. For boutique hotels, premium offices, creative agencies. Carbon-plastic panels, E0 formaldehyde. https://www.soundbox.hk/products/detail/artpod-art-pod-ml", metadata: { category: "product", product: "art", series: "ART" } },
  { id: "kb-art-home", text: "Home Pod series for residential use. Models: HOUSE NO.1 (25dB), HOUSE NO.2 (26dB, flat-top design), HOUSE NO.2.4 (4-person, 25dB). Environmentally friendly, zero formaldehyde. Simplified tool-free assembly. Compact footprint fits apartments. https://www.soundbox.hk/sem/silence-booth-series", metadata: { category: "product", product: "home" } },
  // --- 声学产品 ---
  { id: "kb-acoustic-panels-v2", text: "Acoustic panels: sound-absorbing panels (NRC 0.75-0.90), slat wood panels, perforated panels, ceiling absorbers, fabric-wrapped panels. Materials: fiberglass, polyester fiber, wood, metal. For walls, ceilings, studios, auditoriums. Standard and custom sizes, thickness 15-100mm.", metadata: { category: "product", product: "acoustic-panels" } },
  { id: "kb-acoustic-doors", text: "Acoustic doors: 80mm thick, sound insulation up to 35dB. Residential (bedroom, study), commercial (meeting rooms, hotels), professional (recording studios, hospitals). Custom sizes, fire-rated options. Engineered seals minimize sound leakage at perimeter.", metadata: { category: "product", product: "acoustic-doors" } },
  { id: "kb-acoustic-diffusers-v2", text: "Acoustic diffusers: MLS, QRD, 2D and 3D types. Break up reflected sound, distribute acoustic energy evenly, eliminate flutter echoes. For concert halls, theaters, studios, music classrooms. Materials: solid wood, MDF, custom composites.", metadata: { category: "product", product: "acoustic-diffusers" } },
  // --- 解决方案 ---
  { id: "kb-solution-building", text: "Building acoustic solutions: 10 categories \u2014 residential, hotels, healthcare, venues, concert halls, education, offices, restaurants, multi-function halls, entertainment. Techniques: sound insulation (doors/windows/walls), absorption (panels/wool), diffusion, silent booths. Expected: 15-35dB reduction, reverberation 0.5-2.0s. https://www.soundbox.hk/solution/commercial-office-acoustic", metadata: { category: "solution", type: "building" } },
  { id: "kb-solution-environmental", text: "Environmental noise control: hydroelectric plants, factories, cooling towers, piping, subways, tunnels, highways. Techniques: source control (damping/enclosures), path blocking (barriers), receiver protection (insulated windows/doors). Expected: 10-25dB reduction. https://www.soundbox.hk/solution/hydroelectric-power-stations-acoustic", metadata: { category: "solution", type: "environmental" } },
  { id: "kb-solution-industrial", text: "Industrial NVH solutions: automotive, high-speed rail, marine, warships, armored vehicles. Techniques: vibration control, noise isolation, sound absorption, structural damping. Expected: 8-20dB noise reduction, 50%+ vibration reduction. Clients: Tesla, Boeing. https://www.soundbox.hk/html/product-car", metadata: { category: "solution", type: "industrial" } },
  // --- FAQ ---
  { id: "kb-faq-soundproofing", text: "Soundproof pod acoustic performance varies by series: VRT/ART = ISO 23351-1 Class A (Ds.A 30.2-31.0 dB per model), ART+ = Class B-C (Ds.A 23.8-27.0 dB per model), SR = 25-30dB, Home Pod = 25-26dB. Each unit ships with CNAS-lab-certified test report with precise STC rating and frequency-band curve. Multi-layer composite wall structure. Integrated low-noise fresh air system with silencers maintains ventilation without compromising isolation.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-installation", text: "Modular bolt-together design. Standard pod assembled by 2 technicians in ~4 hours \u2014 no welding, no wet construction, no permanent modifications. Freestanding, can be relocated. Requirements: flat floor, 220V outlet, min ceiling clearance 2170mm. Remote video-guided installation support available for overseas clients.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-materials", text: "Wall panels: carbon-plastic composite acoustic boards (not traditional plywood) \u2014 higher density, moisture resistant. Interior: PET acoustic absorption panels, polyester fiber acoustic fabric. Frame: aluminum profiles, 304 stainless steel connectors. Zero benzene, E0 formaldehyde, TVOC compliant with GB/T 18883.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-competition", text: "Key differentiators: (1) R&D and national standards participation with CNAS lab. (2) Transparent certified test data for every product. (3) Carbon-plastic composite boards outperform plywood in density, moisture resistance, fire rating. (4) 5-year warranty + lifetime spare parts vs competitors' typical 1-2 years.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-pricing", text: "Acoustic insulation is systems engineering \u2014 cannot be quoted per square meter. Depends on: noise environment, target dB reduction, room dimensions, structural constraints, construction method. For accurate budget we need: room dimensions/photos, noise source description, target noise level, any certification requirements. Proposal within 1-3 business days.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-payment", text: "Standard terms: payment before shipment. Built-to-order manufacturing. Custom configurations: confirm specs and lead time first, then quotation. Volume pricing and annual framework agreements available for larger procurement programs.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-aftersales", text: "5-year warranty, lifetime spare parts. Multi-location service network with 60+ engineers. Remote diagnostics, on-site support, scheduled maintenance guidance. Overseas clients: troubleshooting guides, video tutorials, remote video-assisted support. Service requests tracked in CRM with response-time SLAs.", metadata: { category: "faq", product: "all" } },
  { id: "kb-faq-solution-process", text: "Three proposal tiers: (1) Feasibility Estimate \u2014 fast budget-range assessment. (2) Standard Proposal \u2014 3D modeling, acoustic drawings, specs, pricing (3-7 days). (3) Deep Engineering \u2014 simulation, collaborative design, compliance docs (fee-based). To start: provide scenario, target noise level, room dimensions, budget, timeline.", metadata: { category: "faq", product: "all" } },
  // === 隔声门（补充） ===
  { id: "kb-doors-overview", text: "Soundbox soundproof door series: three lines covering residential, commercial, and industrial applications. All doors use carbon-plastic composite panels with graphene sealing strips, fire-retardant cores, and precision-engineered frames. Sound insulation ranges from 25dB (civil) to 45dB (industrial). Civil line: G50M/G50MX/G50MP/G50MPX for bedrooms, studies, home offices (25-39dB). Commercial line: G50S/G60S/G65S for meeting rooms, hotels, studios (30-35dB, 50-65mm thick). Industrial line: G55J/G55Y for factories, hospitals, recording studios (35-45dB, custom sizes). All E0 environmental standard, zero formaldehyde. https://www.soundbox.hk/sem/soundproof-door-series", metadata: { category: "product", product: "doors" } },
  { id: "kb-doors-civil", text: "Civil soundproof doors (G50 series): 50mm carbon-plastic composite door panels, graphene smoke-sealing strips, servo-expanding carbon-plastic frames, magnetic acoustic seals, hidden 3D hinges, silent locks. G50M: STC 39dB, auto acoustic gate. G50MX: fixed gate variant. G50MP: with observation window option. G50MPX: fixed gate with window. Frame size H2200xW900xD120/388mm. For bedrooms, studies, home offices, small offices. E0 environmental standard.", metadata: { category: "product", product: "doors" } },
  { id: "kb-doors-commercial", text: "Commercial soundproof doors: G50S (50mm, with observation window, auto gate), G60S (60mm, dual-seal airtight frame, airtight lock), G65S (65mm, circular observation window, triple-tooth panel, German heavy-duty hinges). Frame sizes H2200xW900mm, thickness 120-448mm. Sound insulation 30-35dB. Applications: meeting rooms, negotiation rooms, retail shops, hotel rooms. Fire-rated B1 materials. E0 standard. Korean PP anti-scratch surface options.", metadata: { category: "product", product: "doors" } },
  { id: "kb-doors-engineering", text: "Engineering soundproof doors: G55J (standard industrial), G55Y (hospital/entertainment). 55mm carbon-plastic composite panels, graphene sealing strips, fire-retardant cores, auto acoustic gates. Custom sizes up to H2350xW950mm. Sound insulation 35-45dB. For factories, machine rooms, recording studios, broadcast rooms, hospital operating rooms. Supports OEM/custom configurations. Fire-rated options available.", metadata: { category: "product", product: "doors" } },
  // === 工程建声（补充） ===
  { id: "kb-wafer-module", text: "WAFER Holographic Acoustic Module (WF-16SA): advanced silica-based technology using inorganic mineral fiber micro-foamed 'absorption substrate' with 80\u03BCm nano-porous absorption coating. Class A fire retardant, base material melting point 1000\xB0C. Covers 50-100m\xB2 per module, ceiling-mounted. For large conference rooms, auditoriums, concert halls, theaters. Provides full-frequency holographic sound field control. https://www.soundbox.hk/products/detail/holographic-acoustic-module-wf16sa", metadata: { category: "product", product: "architectural-acoustics" } },
  { id: "kb-acoustic-baffles-overview", text: "Acoustic baffles: array and matrix series for suspended ceiling/wall applications. Array series (M30/50/100, M50X/100X, MT30/50/60/100) \u2014 for stadiums, public venues, exhibition halls, gyms, bars, equipment rooms. Matrix series (MA30/50, MA30&/50&) \u2014 for classrooms, multi-function halls, offices, commercial spaces. Materials: white fire-resistant acoustic fiberglass cloth. Formaldehyde emissions <0.005mg/m\xB3, exceeding national ENF standards. Thickness 30-100mm, widths 600-1200mm.", metadata: { category: "product", product: "architectural-acoustics" } },
  { id: "kb-perforated-panels", text: "Perforated and slotted absorption panels: CK-1 (5mm digital holes), CK-2 (3-10mm gradient holes for broadband absorption), CK-3/A (plum blossom holes), CK-3/B (straight rows), CK-5 (5 mixed diameters, bubble pattern). Carbon-plastic or calcium-carbonate boards with wood grain or matte finishes. Sizes: 300/600x600mm and 600x1200mm. Slotted panels: 2440x128x15mm. For multi-function halls, exhibition venues, offices, stadiums. NRC 0.7-0.8.", metadata: { category: "product", product: "architectural-acoustics" } },
  { id: "kb-acoustic-cotton", text: "Acoustic cotton/materials: White Fiber (ME64-30T/ME48-50T) \u2014 zero formaldehyde, fire-resistant, no slag balls, low dust, high absorption. Yellow Fiber (MG64-30T/MG48-50T) \u2014 fire-resistant, non-toxic, corrosion-resistant, low thermal conductivity. Bayer Acoustic Cotton (BE20PP/BE500) \u2014 medical-grade PP melt-blown, fiber diameter 1-5\u03BCm, labyrinth micro-pores for broadband absorption, ultra-thin yet effective. Sizes: 600x1200mm, thickness 20-50mm. For cavity filling in acoustic constructions.", metadata: { category: "product", product: "architectural-acoustics" } },
  { id: "kb-acoustic-fabric-panels", text: "Acoustic fabric-wrapped panels: Eco Series (CM28E/30E/60E/120E) \u2014 zero formaldehyde, patented easy-install, eliminates traditional resin-cured frame issues. Standard Series (CM28/30/60/120) \u2014 for larger spaces. Sizes: 200x200mm to 1200x2400mm, thickness 28/30/60/120mm. Strong Absorption Blocks (MD48) \u2014 for bars, restaurants, equipment rooms. Shaped Baffles (MT30R/MT60R) \u2014 round ceiling-mounted, diameter 600-1200mm. A/B/C grade acoustic fabric options.", metadata: { category: "product", product: "architectural-acoustics" } },
  // === 便捷建声（新增） ===
  { id: "kb-quick-install-overview", text: "Quick-install acoustic solutions: no complex construction needed. 6 product lines: Imagine Acoustic Art (custom image panels), Acoustic Modules (free-combination blocks), EQ Series Absorption Panels (diverse styles: Rococo, Baroque, Versailles, Phantom, Cobblestone, Geometry, Bass Traps), AQ Intelligent Modules (Chopin/Haydn/Bach acoustic arrays with movable design), QRD 2D Diffusers (solid wood), MLS 3D Diffusers (Peak/Cloud/Arc/Wave/Pyramid). All E0 environmental standard, tool-free or minimal-tool installation. For offices, meeting rooms, home theaters, recording studios.", metadata: { category: "product", product: "quick-acoustics" } },
  { id: "kb-acoustic-art-modules", text: "Imagine Acoustic Art and Acoustic Modules: decorative acoustic products combining art and sound absorption. Acoustic Art panels support custom images (photos, posters, artwork) with NRC 0.7-0.8, hang like regular paintings. Acoustic Modules: modular blocks (NRC 0.8-0.9), freely combinable into any shape, lightweight (~1kg each). Sizes: S 600x900x30mm, M 650x1000x30mm, L 800x1200x30mm. For living rooms, bedrooms, offices, hotel rooms. Quick 10-minute installation.", metadata: { category: "product", product: "quick-acoustics" } },
  { id: "kb-eq-series", text: "EQ Series Absorption Panels: stylish acoustic panels with diverse designs. Rococo (EQ50R) \u2014 curved combination, Baroque (EQ50B) \u2014 linen dual-density, Versailles (EQ50Q) \u2014 QRD-style, Phantom (EQ3.1-3.4) \u2014 with LED backlight option, Cobblestone (EQ4.1-4.4) \u2014 rounded organic shape, Geometry (EQ5.1-5.2) \u2014 square/round combinations. Plus Bass Traps: Slim Waist (EQ101) and Pyramid (EQ102) for corner low-frequency control, H1500xW400xD250mm. All E0 standard. For offices, meeting rooms, home theaters.", metadata: { category: "product", product: "quick-acoustics" } },
  { id: "kb-aq-modules", text: "AQ Intelligent Acoustic Modules: movable acoustic panels for professional and home use. Chopin (AQ1000S) \u2014 for HiFi rooms, QRD diffusion + absorption. Haydn (AQ1000SL) \u2014 tall array, H2400mm. Bach (AQ1000SW) \u2014 composite QRD with Helmholtz resonator for mid-low frequencies. AQ1000M \u2014 adjustable diffusion angle. AQ1000H/HD \u2014 movable stage baffles. AQ150W \u2014 compact movable baffle for offices. E0 standard. For HiFi rooms, recording studios, home theaters, churches, stages.", metadata: { category: "product", product: "quick-acoustics" } },
  { id: "kb-qrd-diffusers", text: "QRD and MLS diffusers: QRD 2D series \u2014 solid wood diffusers (D64, D30, N8, N29, D80, D60, D80W low-frequency trap, D60W bass trap). MLS 3D series \u2014 Peak (PEAK 5Q), Cloud (CLOUD 2C), Arc (ARC 300), Wave (WAVE 600), Pyramid (PYRAMID 4Q). Scatters reflected sound, eliminates flutter echoes, distributes acoustic energy evenly. For concert halls, theaters, recording studios, home theaters, HiFi rooms. Materials: solid wood, PU lacquer finishes.", metadata: { category: "product", product: "quick-acoustics" } },
  // === 隔声减振（新增） ===
  { id: "kb-insulation-overview", text: "Sound insulation and vibration control products: 7 categories covering walls, floors, ceilings, and equipment. WAFER Holographic Insulation Module (WF-24SI) \u2014 upgrades 100mm brick wall to 56dB. Damping coatings (F-500 floor, F-1000 spray). Insulation boards (G15E-G22, OEM13/15) \u2014 12-20mm thick, sandwich structure with polymer damping. Insulation mats (F15/F30, F100B/F100) \u2014 self-adhesive, 1.5-3mm thick. Vibration isolators for ceiling/wall/floor. Damping floor tiles (FIC-S/FIC-W). Noise control curtains (NIC-PLUS/NIC-UT). For residential, hotels, KTV, recording studios, equipment rooms.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-insulation-boards", text: "Sound insulation boards: eco-friendly damping boards using sandwich construction with different-density inorganic panels + polymer damping adhesive. G15E (12mm, for residential/hotels/piano rooms), G16E (13mm), G17E (13mm, for home theaters/HiFi rooms), G18 (16mm, for equipment rooms/KTV), G19E (15mm), G20J (18mm, metal variant for bars/nightclubs with high SPL). G22 (20mm, composite for KTV/home theaters). OEM13/15 (13/15mm, 2440x1220mm, for large-area commercial use). All sizes 1220x1220mm except OEM series. Changes material resonance frequency, multiplies air and structure sound insulation.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-insulation-mats", text: "Sound insulation felt/mats: IIR polymer mats (F15/F30/F10E/F20E) \u2014 high-performance, flexible, fire-resistant, cut-to-size. F15: 9000x610x1.5mm. F30: 4000x610x3mm. Damping insulation sheets with single-side aluminum foil (F100B/F150Ei/F200Ei) \u2014 2mm, self-adhesive, density 4.2kg/m\xB2. Double-sided self-adhesive (F100/F150E/F200E) \u2014 same 2mm, both sides adhesive, improves insulation \u2265250%. Polycarbonate damping mats (J04P/J06P/J08P) \u2014 floor impact sound reduction 18-23dB, green building compliant. For pipes, equipment enclosures, walls, ceilings, vehicle cabins.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-vibration-isolators", text: "Vibration isolators and damping components: Ceiling isolators (G25TX/G50T/G50TX/G100TX) \u2014 cut ceiling-floor structure-borne sound. Wall isolators (G50Q/G50QX) \u2014 decouple wall acoustic layers from structure. Floor isolators (G100D) \u2014 for low-frequency vibration in bars/equipment rooms. Ultra-thin dampers (G20F) \u2014 23x50x50mm. Damping pads (G3P/G6P/G12P, G3PX/G6PX/G12PX) \u2014 closed-cell, waterproof. Polymer vibration blocks (G25D/G50D) \u2014 floating floor construction, impact improvement 15dB. Acoustic sealant (G600) and channel damping compound (F1E/F2E) for metal stud resonance control.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-damping-coatings", text: "Damping coatings: F-500 Floor Damping Coating \u2014 polymer-modified, high elasticity (99.5% solid content), absorbs floor impact vibration, creates acoustic bridge shear effect between floor finish and base slab. F-1000 Water-based Damping Paint \u2014 excellent adhesion to metals, fire-retardant, non-toxic, spray or roller application. For airports, stations, stadiums, steel structures, vehicle cabins, engine compartments, mechanical equipment housings. Temperature and frequency range: broad spectrum.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-insulation-floor", text: "Damping acoustic flooring: FIC-S (stone pattern, 914x914x7mm) and FIC-W (wood pattern, 185x1220x7mm). LVT+IXPE composite high-performance flooring. High-elasticity energy-absorbing layer absorbs and disperses impact forces. Polymer damping layer converts vibration energy to heat. Reduces both airborne and impact sound transmission. Impact sound improvement 20-25dB, airborne sound insulation 35-40dB. Dry installation, no glue needed. For residential, hotel rooms, offices, hospital wards.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-noise-curtains", text: "Noise control curtains: NIC-PLUS (1000x2000x9mm) \u2014 heavy-duty curtain for construction sites, equipment noise, road works. NIC-UT (2000x3000x0.9mm) \u2014 larger format. Both provide 25-30dB mid/high-frequency noise isolation. Portable, can be used at any time without disturbing neighbors. For construction noise control, equipment noise isolation, temporary sound barriers between spaces. Fire-retardant and waterproof options available.", metadata: { category: "product", product: "insulation" } },
  { id: "kb-wafer-insulation-module", text: "WAFER Holographic Sound Insulation Module (WF-24SI): inorganic silicate damping technology with modified elastic modulus, dramatically improves conversion of acoustic mechanical energy to heat. Upgrades standard 100mm light brick wall to 56dB airborne sound insulation. Can be applied to floors, walls, and ceilings. For premium residential, hotels, hospitals, recording studios, equipment rooms. https://www.soundbox.hk/products/detail/sound-insulation-module-wf24si", metadata: { category: "product", product: "insulation" } },
  { id: "kb-damping-blocks", text: "Polymer vibration damping blocks (G25D/G50D): point-distribution floating floor construction. Micro-stroke elastic design absorbs and rebounds vibration waves from radiating surfaces, isolating low-frequency vibration and impact sound. Impact sound improvement 15dB (national Grade 1 standard). Damping characteristics cut acoustic bridge transmission. Ideal floating floor substrate between vibration source and base floor. G25D: 150x150x25mm. G50D: 150x150x50mm. For recording studios, KTV, home theaters, equipment rooms.", metadata: { category: "product", product: "insulation" } },
  // === 汽车NVH（新增） ===
  { id: "kb-automotive-nvh", text: "Automotive NVH (Noise, Vibration, Harshness) solutions: universal kits covering doors (AU0101), wheels (AU0201), chassis (AU0301), trunk (AU0401), hood (AU0501), and sealing (AU0601). Each kit includes damping sheets, acoustic cotton, and installation tools. Reduces engine noise, road noise, and wind noise by 10-15dB. Applied to doors, floor, roof, trunk, hood. Suitable for private cars, taxis, trucks.", metadata: { category: "product", product: "automotive-nvh" } },
  { id: "kb-tesla-nvh", text: "Tesla Model Y specific NVH kits: precision 1:1 fit for Model Y body structure, no cutting required. 5 kits: doors (AZ0101), wheels (AZ0201), chassis (AZ0301), front/rear trunk (AZ0401), sealing (AZ0501). Lightweight materials (total \u22645kg). Targets EV-specific noise: tire noise, wind noise, motor whine. Reduces frameless door wind noise at highway speeds. Improves audio system quality and cabin comfort.", metadata: { category: "product", product: "automotive-nvh" } },
  // === FAQ 补充 ===
  { id: "kb-faq-differentiators", text: "Soundbox key differentiators vs competitors: (1) R&D and national standards participation with CNAS-accredited lab (one of only 3 anechoic chambers globally at -21dB, co-built with Tsinghua University). (2) Transparent certified test data for every product \u2014 CNAS lab reports included. (3) Carbon-plastic composite boards outperform traditional plywood in density, moisture resistance, fire rating. (4) 5-year warranty + lifetime spare parts vs competitors' typical 1-2 years. (5) Only Asian company in German EASE acoustic software database. (6) 200+ patents covering acoustic materials and structural designs.", metadata: { category: "faq", product: "all" } }
];
var MAX_CHUNK = 25;
async function seedKnowledgeBase(env) {
  const results = { total: KB_ENTRIES.length, success: 0, failed: 0, errors: [] };
  for (let offset = 0; offset < KB_ENTRIES.length; offset += MAX_CHUNK) {
    const chunk = KB_ENTRIES.slice(offset, offset + MAX_CHUNK);
    const chunkResult = await seedChunk(env, chunk);
    results.success += chunkResult.success;
    results.failed += chunkResult.failed;
    results.errors.push(...chunkResult.errors);
    if (offset + MAX_CHUNK < KB_ENTRIES.length) {
      await new Promise((r) => setTimeout(r, 1e3));
    }
  }
  return results;
}
__name(seedKnowledgeBase, "seedKnowledgeBase");
async function seedChunk(env, entries) {
  const results = { success: 0, failed: 0, errors: [] };
  const BATCH_SIZE = 10;
  for (let i = 0; i < entries.length; i += BATCH_SIZE) {
    const batch = entries.slice(i, i + BATCH_SIZE);
    try {
      const vectors = await getEmbedding(env, batch.map((e) => e.text), { timeoutMs: GLM_SEED_TIMEOUT_MS });
      const records = batch.map((entry, j) => ({
        id: entry.id,
        values: vectors[j],
        metadata: { ...entry.metadata, text: entry.text }
      }));
      await env.VECTORIZE.upsert(records);
      results.success += batch.length;
      console.log(`[seed] Inserted ${batch.length} vectors`);
    } catch (err) {
      results.failed += batch.length;
      results.errors.push(err.message);
      console.error(`[seed] Batch failed: ${err.message}`);
    }
    if (i + BATCH_SIZE < entries.length) {
      await new Promise((r) => setTimeout(r, 1e3));
    }
  }
  return results;
}
__name(seedChunk, "seedChunk");

// src/cron.js
var SUBREQUEST_LIMIT = 500;
var LOOKBACK_MS = 6 * 60 * 60 * 1e3;
var PROCESSED_TTL = 7 * 24 * 3600;
var FORM_LOOKBACK_MS = 120 * 24 * 3600 * 1e3;
var LAST_RUN_KEY = "cron:last_run";
var PROCESSED_PREFIX = "cron:processed:";
async function handleScheduled(controller, env, ctx) {
  const now = Date.now();
  const stats = {
    forms_scanned: 0,
    leads_fetched: 0,
    leads_skipped_kv: 0,
    leads_created: 0,
    leads_deduped: 0,
    leads_failed: 0,
    subrequests: 0
  };
  let subs = 0;
  const lastRunStr = await env.FAILED_LEADS.get(LAST_RUN_KEY);
  subs++;
  const since = new Date(
    Math.max(now - LOOKBACK_MS, lastRunStr ? new Date(lastRunStr).getTime() - 30 * 60 * 1e3 : now - LOOKBACK_MS)
  ).toISOString();
  console.log(`[cron] since=${since}, lastRun=${lastRunStr || "never"}`);
  let forms;
  try {
    forms = await listForms(env.META_PAGE_ID, env.META_PAGE_ACCESS_TOKEN, env.META_API_VERSION);
    subs += forms.length;
    stats.forms_scanned = forms.length;
  } catch (err) {
    console.error(`[cron] listForms failed: ${err.message}`);
    return { status: "error", phase: "listForms", error: err.message };
  }
  // 用 120 天 updated_time，覆盖仍在出单的老表单（如香日/欧洲）
  const formCutoff = new Date(now - FORM_LOOKBACK_MS);
  const recentForms = forms.filter((f) => {
    const refTime = f.updated_time || f.created_time;
    if (!refTime) return true;
    return new Date(refTime) >= formCutoff;
  });
  console.log(`[cron] forms=${forms.length} recent=${recentForms.length}`);
  for (const form of recentForms) {
    if (subs >= SUBREQUEST_LIMIT - 100) break;
    let leadsResult;
    try {
      leadsResult = await listLeads(form.id, since, env.META_PAGE_ACCESS_TOKEN, env.META_API_VERSION);
      subs++;
    } catch (err) {
      console.error(`[cron] listLeads(${form.name}): ${err.message}`);
      continue;
    }
    const leads = leadsResult.leads;
    stats.leads_fetched += leads.length;
    for (const lead of leads) {
      if (subs >= SUBREQUEST_LIMIT) break;
      try {
        const pKey = `${PROCESSED_PREFIX}${lead.leadgen_id}`;
        const done = await env.FAILED_LEADS.get(pKey);
        subs++;
        if (done) {
          let status = "";
          try {
            status = JSON.parse(done)?.s || "";
          } catch {
            status = "unknown";
          }
          const permanent = /* @__PURE__ */ new Set([
            "created",
            "updated",
            "skipped",
            "merged",
            "duplicate_leadgen_id",
            "duplicate_contact",
            "duplicate_email",
            "form_spam_single_word",
            "kv_processed"
          ]);
          if (permanent.has(status) || String(status).startsWith("duplicate")) {
            stats.leads_skipped_kv++;
            continue;
          }
        }
        await env.FAILED_LEADS.put(
          pKey,
          JSON.stringify({ t: (/* @__PURE__ */ new Date()).toISOString(), s: "processing" }),
          { expirationTtl: 600 }
        );
        subs++;
        const leadData = await fetchLeadData(lead.leadgen_id, env.META_PAGE_ACCESS_TOKEN, env.META_API_VERSION);
        subs++;
        const parsed = parseLead(leadData, env);
        parsed.clue_level = gradeLead(parsed).level;
        const result = await writeLead(parsed, env);
        subs += 3;
        const okStatuses = /* @__PURE__ */ new Set(["created", "updated", "skipped", "merged"]);
        const reason = result.reason || "";
        const permanentOk = okStatuses.has(result.status) || String(reason).includes("duplicate");
        if (permanentOk) {
          await env.FAILED_LEADS.put(
            pKey,
            JSON.stringify({
              t: (/* @__PURE__ */ new Date()).toISOString(),
              s: result.status === "skipped" ? reason || "skipped" : result.status
            }),
            { expirationTtl: PROCESSED_TTL }
          );
        } else {
          await env.FAILED_LEADS.delete(pKey).catch(() => {
          });
        }
        subs++;
        if (result.status === "created" && result.record_id) {
          triggerCompanyResearch(result.record_id, env).catch(() => {
          });
          triggerFacebookFirstContact(result.record_id, env).catch(() => {
          });
          triggerAssignmentUnblock(result.record_id, env).catch(() => {
          });
        }
        if (result.status === "created") {
          stats.leads_created++;
          console.log(`[cron] Created ${lead.leadgen_id} (${parsed.country}-${parsed.product_category})`);
        } else {
          stats.leads_deduped++;
        }
      } catch (err) {
        stats.leads_failed++;
        console.error(`[cron] Failed ${lead.leadgen_id}: ${err.message}`);
        await env.FAILED_LEADS.delete(`${PROCESSED_PREFIX}${lead.leadgen_id}`).catch(() => {
        });
        await recordFailure(env, lead.leadgen_id, err.message, "cron");
      }
    }
  }
  stats.subrequests = subs;
  await env.FAILED_LEADS.put(LAST_RUN_KEY, (/* @__PURE__ */ new Date()).toISOString());
  subs++;
  console.log(`[cron] Done: ${JSON.stringify(stats)}`);
  return { status: "ok", stats };
}
__name(handleScheduled, "handleScheduled");

// src/auth.js
async function sha256Hex2(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
__name(sha256Hex2, "sha256Hex");
function constTimeEqualHex(a, b) {
  let m = 0;
  for (let i = 0; i < 64; i++) m |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return m === 0;
}
__name(constTimeEqualHex, "constTimeEqualHex");
async function requireAuth(request, env, { allowBodyToken = false } = {}) {
  const token = env.ADMIN_TOKEN;
  if (!token) {
    return { ok: false, response: Response.json({ error: "ADMIN_TOKEN not configured" }, { status: 503 }) };
  }
  const header = request.headers.get("Authorization") || "";
  let provided = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!provided && allowBodyToken && request.method === "POST") {
    try {
      provided = (await request.clone().json())._token || "";
    } catch {
    }
  }
  const ok = provided && constTimeEqualHex(await sha256Hex2(provided), await sha256Hex2(token));
  return ok ? { ok: true } : { ok: false, response: Response.json({ error: "Unauthorized" }, { status: 401 }) };
}
__name(requireAuth, "requireAuth");

// src/index.js
var index_default = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST",
          "Access-Control-Allow-Headers": "Content-Type"
        }
      });
    }
    if (request.method === "GET" && url.pathname === "/webhook") {
      return handleVerification(url, env.META_WEBHOOK_VERIFY_TOKEN);
    }
    if (request.method === "POST" && url.pathname === "/webhook") {
      return handleWebhook(request, env, ctx);
    }
    if (request.method === "GET" && url.pathname === "/instagram") {
      const mode = url.searchParams.get("hub.mode");
      const token = url.searchParams.get("hub.verify_token");
      const challenge = url.searchParams.get("hub.challenge");
      if (mode === "subscribe" && token && token === env.META_WEBHOOK_VERIFY_TOKEN) {
        return new Response(challenge || "", { status: 200 });
      }
      return new Response("Forbidden", { status: 403 });
    }
    if (request.method === "POST" && url.pathname === "/instagram") {
      const rawBody = await request.text();
      const sig = request.headers.get("x-hub-signature-256") || "";
      const secrets = [["META_APP_SECRET", env.META_APP_SECRET], ["IG_APP_SECRET", env.IG_APP_SECRET]].filter(([, s]) => s);
      let matchedSecret = null;
      for (const [name, secret] of secrets) {
        if (await verifySignature(rawBody, sig, secret)) {
          matchedSecret = name;
          break;
        }
      }
      if (!matchedSecret) {
        console.warn("[instagram] Invalid signature (tried META_APP_SECRET + IG_APP_SECRET)");
        return new Response("Unauthorized", { status: 401 });
      }
      console.log(`[instagram] Signature valid via ${matchedSecret}`);
      const payload = JSON.parse(rawBody);
      const msgQueue = /* @__PURE__ */ new Map();
      for (const entry of payload.entry || []) {
        for (const event of entry.messaging || []) {
          if (!event.message || event.message.is_echo || !event.message.text || !event.sender?.id) continue;
          const igsid = event.sender.id;
          const mid = event.message.mid;
          if (!mid) continue;
          const queue = msgQueue.get(igsid) || [];
          if (queue.some((m) => m.mid === mid)) continue;
          queue.push({ mid, text: event.message.text });
          msgQueue.set(igsid, queue);
        }
      }
      const tasks = [];
      for (const [igsid, messages] of msgQueue) {
        tasks.push(
          (async () => {
            try {
              await handleUserMessages(igsid, messages, env, "ig");
            } catch (err) {
              console.error(`[instagram] Error for ${igsid}: ${err.message}`);
            }
          })()
        );
      }
      ctx.waitUntil(Promise.all(tasks).catch((err) => console.error(`[instagram] bg error: ${err.message}`)));
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    if (request.method === "GET" && url.pathname === "/privacy") {
      return new Response(PRIVACY_POLICY_HTML, {
        headers: { "Content-Type": "text/html; charset=utf-8" }
      });
    }
    if (request.method === "GET" && url.pathname === "/diag/failures") {
      const auth = await requireAuth(request, env);
      if (!auth.ok) return auth.response;
      return handleDiagFailures(url, env);
    }
    if (request.method === "POST" && url.pathname === "/reprocess") {
      const auth = await requireAuth(request, env);
      if (!auth.ok) return auth.response;
      return handleReprocess(url, env);
    }
    if (request.method === "POST" && url.pathname === "/trigger-research") {
      const auth = await requireAuth(request, env, { allowBodyToken: true });
      if (!auth.ok) return auth.response;
      return handleTriggerResearch(request, env);
    }
    if (request.method === "POST" && url.pathname === "/remind-lead") {
      const auth = await requireAuth(request, env, { allowBodyToken: true });
      if (!auth.ok) return auth.response;
      return handleRemindLead(request, env);
    }
    if (request.method === "POST" && url.pathname === "/seed-kb") {
      const auth = await requireAuth(request, env);
      if (!auth.ok) return auth.response;
      const result = await seedKnowledgeBase(env);
      return new Response(JSON.stringify(result), {
        headers: { "Content-Type": "application/json" }
      });
    }
    if (request.method === "GET" && url.pathname === "/diag/conversations") {
      const auth = await requireAuth(request, env);
      if (!auth.ok) return auth.response;
      const prefix = url.searchParams.get("prefix") || "conv-log:";
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "20", 10), 100);
      const list = await env.MESSENGER_SESSIONS.list({ prefix, limit });
      const logs = [];
      for (const key of list.keys) {
        const val = await env.MESSENGER_SESSIONS.get(key.name, "json");
        if (val) logs.push(val);
      }
      return new Response(JSON.stringify({ total: logs.length, logs }, null, 2), {
        headers: { "Content-Type": "application/json" }
      });
    }
    if (request.method === "GET" && url.pathname === "/") {
      return new Response(JSON.stringify({ status: "ok", service: "facebook-lead-webhook" }), {
        headers: { "Content-Type": "application/json" }
      });
    }
    return new Response("Not Found", { status: 404 });
  },
  // Cloudflare Cron Triggers：GHA 偶发延迟时由 Worker 每 10 分钟补扫 Lead Ads
  async scheduled(controller, env, ctx) {
    return handleScheduled(controller, env, ctx);
  }
};
async function handleWebhook(request, env, ctx) {
  const body = await request.text();
  const signature = request.headers.get("X-Hub-Signature-256") || "";
  const isValid = await verifySignature(body, signature, env.META_APP_SECRET);
  if (!isValid) {
    console.error("[webhook] Invalid signature");
    return new Response("Forbidden", { status: 403 });
  }
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    console.error("[webhook] Invalid JSON");
    return new Response("Bad Request", { status: 400 });
  }
  if (payload.object !== "page") {
    return new Response("OK", { status: 200 });
  }
  const hasLeadgen = payload.entry?.some(
    (e) => e.changes?.some((c) => c.field === "leadgen" && c.value?.leadgen_id)
  );
  const msgCount = payload.entry?.reduce((n, e) => n + (e.messaging?.length || 0), 0) || 0;
  if (msgCount > 0) {
    console.log(`[webhook] entries=${payload.entry?.length} msgs=${msgCount}`);
  }
  const msgQueue = /* @__PURE__ */ new Map();
  const dbg = env.DEBUG_WEBHOOK;
  if (!hasLeadgen) {
    let skipReasons = { echo: 0, no_text: 0, no_sender: 0, page_self: 0, no_mid: 0, no_msg: 0 };
    for (const entry of payload.entry || []) {
      for (const event of entry.messaging || []) {
        if (!event.message) {
          skipReasons.no_msg++;
          continue;
        }
        if (event.message.is_echo) {
          skipReasons.echo++;
          continue;
        }
        if (!event.message.text) {
          skipReasons.no_text++;
          continue;
        }
        if (!event.sender?.id) {
          skipReasons.no_sender++;
          continue;
        }
        const psid = event.sender.id;
        if (psid === env.META_PAGE_ID) {
          skipReasons.page_self++;
          continue;
        }
        const mid = event.message.mid;
        if (!mid) {
          skipReasons.no_mid++;
          continue;
        }
        const queue = msgQueue.get(psid) || [];
        if (queue.some((m) => m.mid === mid)) continue;
        if (!msgQueue.has(psid)) msgQueue.set(psid, queue);
        queue.push({ mid, text: event.message.text });
      }
    }
    const totalSkipped = Object.values(skipReasons).reduce((a, b) => a + b, 0);
    if (totalSkipped > 0 || dbg) {
      console.log(`[webhook] Filtered: ${totalSkipped} skipped ${JSON.stringify(skipReasons)}, ${msgQueue.size} queued`);
    }
  }
  const allTasks = [];
  for (const [psid, messages] of msgQueue) {
    allTasks.push(
      (async () => {
        try {
          await handleUserMessages(psid, messages, env);
        } catch (err) {
          console.error(`[messenger] Error handling messages for ${psid}: ${err.message}`);
          await recordMessengerFailure(env, psid, "top_level", err).catch(() => {
          });
        }
      })()
    );
  }
  for (const entry of payload.entry || []) {
    for (const change of entry.changes || []) {
      if (change.field !== "leadgen" || !change.value?.leadgen_id) continue;
      allTasks.push(processLead(change.value, env).then(
        async (result) => {
          console.log(`[webhook] Processed lead ${change.value.leadgen_id}: ${result.status}`);
          if (result.status === "error") {
            await recordFailure(env, change.value.leadgen_id, result.error || "unknown");
          }
          return result;
        },
        async (err) => {
          console.error(`[webhook] Failed lead ${change.value.leadgen_id}: ${err.message}`);
          await recordFailure(env, change.value.leadgen_id, err.message);
          return { status: "error", leadgen_id: change.value.leadgen_id, error: err.message };
        }
      ));
    }
  }
  const task = Promise.all(allTasks).catch(
    (err) => console.error(`[webhook] Background task error: ${err.message}`)
  );
  if (ctx?.waitUntil) {
    ctx.waitUntil(task);
  } else {
    await task;
  }
  return new Response(JSON.stringify({ status: "ok" }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}
__name(handleWebhook, "handleWebhook");
async function processLead(value, env) {
  const { leadgen_id, form_id } = value;
  const pKey = `${PROCESSED_PREFIX}${leadgen_id}`;
  const done = await env.FAILED_LEADS.get(pKey);
  if (done) {
    let status = "";
    try {
      status = JSON.parse(done)?.s || "";
    } catch {
      status = "unknown";
    }
    // 仅对成功/已去重的永久跳过；processing / error 允许重试（否则漏单后永不补录）
    const permanent = new Set([
      "created",
      "updated",
      "skipped",
      "duplicate_leadgen_id",
      "duplicate_contact",
      "kv_processed"
    ]);
    if (permanent.has(status)) {
      return { status: "skipped", reason: "kv_processed", leadgen_id };
    }
  }
  // 短 TTL 处理锁，避免并发双写；失败后删除或到期后可重试
  await env.FAILED_LEADS.put(
    pKey,
    JSON.stringify({ t: (/* @__PURE__ */ new Date()).toISOString(), s: "processing" }),
    { expirationTtl: 600 }
  );
  try {
    const leadData = await fetchLeadData(leadgen_id, env.META_PAGE_ACCESS_TOKEN, env.META_API_VERSION);
    const parsed = parseLead(leadData, env);
    const grading = gradeLead(parsed);
    parsed.clue_level = grading.level;
    const result = await writeLead(parsed, env);
    const okStatuses = new Set(["created", "updated", "skipped"]);
    const reason = result.reason || "";
    const permanentOk = okStatuses.has(result.status) || String(reason).includes("duplicate");
    if (permanentOk) {
      await env.FAILED_LEADS.put(
        pKey,
        JSON.stringify({ t: (/* @__PURE__ */ new Date()).toISOString(), s: result.status === "skipped" ? reason || "skipped" : result.status }),
        { expirationTtl: PROCESSED_TTL }
      );
    } else {
      await env.FAILED_LEADS.delete(pKey);
    }
    if (result.status === "created" || result.status === "updated") {
      const recordId = result.record_id || result.data?.record?.record_id;
      if (recordId) {
        triggerCompanyResearch(recordId, env).catch(
          (err) => console.error(`[dispatch] Company research error: ${err.message}`)
        );
        triggerFacebookFirstContact(recordId, env).catch(
          (err) => console.error(`[dispatch] FB first-contact error: ${err.message}`)
        );
        triggerAssignmentUnblock(recordId, env).catch(
          (err) => console.error(`[dispatch] Assignment unblock error: ${err.message}`)
        );
      }
    }
    return {
      status: result.status,
      leadgen_id,
      level: grading.level,
      country: parsed.country,
      product: `${parsed.product_category}-${parsed.product_model}`
    };
  } catch (err) {
    await env.FAILED_LEADS.delete(pKey).catch(() => {
    });
    throw err;
  }
}
__name(processLead, "processLead");
async function handleDiagFailures(url, env) {
  const failures = [];
  let cursor;
  do {
    const list = await env.FAILED_LEADS.list({ prefix: "fail:", limit: 1e3, cursor });
    for (const key of list.keys) {
      try {
        const val = await env.FAILED_LEADS.get(key.name, "json");
        if (val) failures.push(val);
      } catch (err) {
        failures.push({ key: key.name, error: `JSON parse failed: ${err.message}` });
      }
    }
    cursor = list.list_complete ? null : list.cursor;
  } while (cursor);
  return new Response(JSON.stringify({ total: failures.length, failures }, null, 2), {
    headers: { "Content-Type": "application/json" }
  });
}
__name(handleDiagFailures, "handleDiagFailures");
async function handleReprocess(url, env) {
  const leadgenId = url.searchParams.get("leadgen_id");
  if (!leadgenId) {
    return new Response(JSON.stringify({ error: "leadgen_id required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }
  try {
    // 强制重试：清掉 KV 处理标记，避免 kv_processed 挡住补录
    await env.FAILED_LEADS.delete(`${PROCESSED_PREFIX}${leadgenId}`).catch(() => {
    });
    const result = await processLead({ leadgen_id: leadgenId }, env);
    return new Response(JSON.stringify({ status: "ok", result }, null, 2), {
      headers: { "Content-Type": "application/json" }
    });
  } catch (err) {
    await recordFailure(env, leadgenId, err.message);
    return new Response(JSON.stringify({ status: "error", error: err.message }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }
}
__name(handleReprocess, "handleReprocess");
async function handleTriggerResearch(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }
  const recordId = body.record_id || body.data?.record_id;
  if (!recordId) {
    return new Response(JSON.stringify({ error: "record_id required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }
  const result = await triggerCompanyResearch(recordId, env);
  const status = result.status === "dispatched" ? 200 : 502;
  return new Response(JSON.stringify(result), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
__name(handleTriggerResearch, "handleTriggerResearch");
async function handleRemindLead(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ status: "error", error: "Invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }
  try {
    const result = await sendUrgentReminder(body, env);
    const httpStatus = result.status === "ok" ? 200 : 400;
    return new Response(JSON.stringify(result), {
      status: httpStatus,
      headers: { "Content-Type": "application/json" }
    });
  } catch (err) {
    console.error(`[remind] Error: ${err.message}`);
    return new Response(JSON.stringify({ status: "error", error: err.message }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }
}
__name(handleRemindLead, "handleRemindLead");
var PRIVACY_POLICY_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy - Soundbox Booth</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:720px;margin:0 auto;padding:40px 20px;color:#333;line-height:1.7}
h1{font-size:1.8em;border-bottom:2px solid #e0e0e0;padding-bottom:12px}
h2{font-size:1.3em;margin-top:32px;color:#1a1a1a}
p{margin:12px 0}
ul{padding-left:24px}
li{margin:6px 0}
.update{color:#888;font-size:0.9em;margin-top:40px;border-top:1px solid #e0e0e0;padding-top:16px}
</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p><strong>Effective Date:</strong> May 18, 2026</p>
<p><strong>Data Controller:</strong> Soundbox Booth (\u5343\u725B\u667A\u80FD\u79D1\u6280\u6709\u9650\u516C\u53F8)</p>

<h2>1. Information We Collect</h2>
<p>When you submit a lead form on our Facebook Lead Ads, we collect the following information through the Facebook Marketing API:</p>
<ul>
<li>Full name</li>
<li>Email address</li>
<li>Phone number</li>
<li>Company name (if provided)</li>
<li>Country/region</li>
<li>Message or inquiry details</li>
<li>Form responses (product interest, use case, quantity, etc.)</li>
</ul>
<p>This data is collected directly from you when you voluntarily fill out and submit a lead form on Facebook or Instagram.</p>

<h2>2. How We Use Your Information</h2>
<p>We use the collected information to:</p>
<ul>
<li>Respond to your product inquiry and provide relevant information</li>
<li>Assess the nature and priority of your inquiry for efficient follow-up</li>
<li>Manage our sales pipeline and customer relationship</li>
<li>Improve our products and marketing based on inquiry patterns</li>
</ul>

<h2>3. Data Storage and Security</h2>
<p>Your data is stored securely in our internal CRM system (Feishu/Lark Bitable) hosted on servers operated by ByteDance/Lark. We implement appropriate technical and organizational measures to protect your personal data against unauthorized access, alteration, disclosure, or destruction.</p>

<h2>4. Data Retention</h2>
<p>We retain your personal data for as long as necessary to fulfill the purposes for which it was collected, or as required by applicable law. You may request deletion of your data at any time (see Section 7).</p>

<h2>5. Third-Party Services</h2>
<p>We use the following third-party services to process your data:</p>
<ul>
<li><strong>Meta (Facebook):</strong> Lead ad form submission and webhook delivery</li>
<li><strong>Cloudflare:</strong> Secure data transmission infrastructure</li>
<li><strong>Feishu/Lark:</strong> Internal CRM data storage</li>
</ul>
<p>Each service provider processes your data in accordance with their own privacy policies and applicable data protection laws.</p>

<h2>6. Your Rights</h2>
<p>Depending on your jurisdiction, you may have the right to:</p>
<ul>
<li>Access the personal data we hold about you</li>
<li>Request correction of inaccurate data</li>
<li>Request deletion of your data</li>
<li>Object to or restrict processing of your data</li>
<li>Data portability</li>
</ul>

<h2>7. Contact Us</h2>
<p>If you have any questions about this Privacy Policy or wish to exercise your data rights, please contact us:</p>
<ul>
<li>Email: info@soundboxbooth.com</li>
<li>Website: https://www.soundboxbooth.com</li>
</ul>

<h2>8. Changes to This Policy</h2>
<p>We may update this Privacy Policy from time to time. We will notify you of any material changes by posting the updated policy on this page with a revised effective date.</p>

<p class="update">Last updated: May 18, 2026</p>
</body>
</html>`;
export {
  index_default as default
};
//# sourceMappingURL=index.js.map
