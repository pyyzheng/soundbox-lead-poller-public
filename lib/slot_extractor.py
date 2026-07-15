#!/usr/bin/env python3
"""
slot_extractor.py — LLM 语义槽位提取（轻量 prompt）

仅用 LLM 提取需要语义理解的槽位：
  - intent_slot (Quotation/Partnership/Unclear)
  - product_slot (AcousticPod/NonPod_Acoustics/NoMatch/Unclear)
  - scenario_slot (Office/Home/Studio/Project/Unknown)
  - timeline_slot (TimeSensitive/NotMentioned)
  - config_slot (ConfigMentioned/NotMentioned)
  - language_hint

所有规则逻辑（数量、容量、身份、信号、Level 判定）在 lead_grader.py 中用代码实现。
"""

import os
import json
import logging
import requests

log = logging.getLogger("slot-extractor")

ZHIPU_API_KEY  = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL    = os.environ.get("ZHIPU_MODEL", "glm-4-flash")
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


# ── 轻量 Prompt（~300 tokens）────────────────────────────────────────────────

SLOT_EXTRACTION_PROMPT = """Analyze this business inquiry and extract semantic slots. Output ONLY valid JSON, no other text.

JSON schema: {"intent":"...","product":"...","scenario":"...","timeline":"...","config":"...","language":"..."}

Slot rules:

intent (Quotation | Partnership | Unclear):
- Quotation: ANY buying signal — price/quote/cost/catalog/brochure/spec/dimension/lead time/shipping/availability/order/purchase/buy/interested in/looking for/need/want (even vague interest like "send catalog" = Quotation)
- Partnership: distributor/dealer/agent/partner/representative/reseller/OEM/ODM/long-term cooperation
- Unclear: truly impossible to determine

product (AcousticPod | NonPod_Acoustics | NoMatch | Unclear):
- AcousticPod: pod/booth/cabin/cabina/cabine/phone booth/meeting pod/office pod/soundproof booth/acoustic pod/soundbox/sound box + product model names (model S/model M/model L/model XL/SRP-M/VR-S/ART-L/etc.)
- NonPod_Acoustics: acoustic panel/soundproofing/acoustic foam/sound absorption/diffusion/隔音板/吸音棉
- NoMatch: smoking booth/smoking shelter/明显不相关产品 (ONLY when clearly NOT acoustic-related)
- Unclear: cannot determine

product examples:
{"product":"AcousticPod"}  ← "I need a phone booth for my office"
{"product":"AcousticPod"}  ← "quote for 2 meeting pods model S"
{"product":"AcousticPod"}  ← "Interested in your soundproof cabin"
{"product":"NonPod_Acoustics"} ← "We need acoustic panels for our restaurant ceiling"
{"product":"NonPod_Acoustics"} ← "looking for sound absorption foam for recording room walls"
{"product":"NoMatch"}      ← "smoking shelter for airport terminal"
{"product":"NoMatch"}      ← "need a vending machine"
{"product":"AcousticPod"}  ← "cabina insonorizada para oficina" (Spanish: soundproof cabin)
{"product":"NoMatch"}      ← "need to make room soundproof 15 by 15 feet" (DIY room treatment for medical reasons, not buying a product)
{"product":"NoMatch"}      ← "looking for sound insulation material for my apartment wall" (general insulation material, not a booth product)
{"product":"AcousticPod"}  ← "need sound proof room with 2 side door" (customer wants a soundproof enclosure with doors = buying a product)

CRITICAL RULES for product:
- AcousticPod: standalone soundproof enclosure with walls/doors, even if customer says "room" instead of "pod/booth"
- NoMatch for DIY/medical: "soundproof my room" (home renovation), "hyperacusis patient" (medical)
- NonPod_Acoustics = buying acoustic panels/foam/materials; NoMatch = non-acoustic request or DIY home renovation

scenario (Office | Home | Studio | Project | Unknown):
- Office: ONLY when customer explicitly describes WHERE the product will be used — "for our office", "in our office", "office use", "workplace", "coworking"
- Home: home/residential/bedroom/living room
- Studio: studio/recording/dubbing/voice over/podcast
- Project: new building/new office/renovation/fit-out/tender/RFQ/RFI/project/expansion/headquarters
- Unknown: everything else — including when only the product name is mentioned without usage context

CRITICAL RULES for scenario:
1. "office pod" or "meeting pod" or "phone booth" is a PRODUCT NAME, NOT a scenario. Do NOT set scenario=Office just because the product name contains "office" or "meeting".
2. A customer must EXPLICITLY describe their usage context (where/how they will use it) for you to set a scenario.
3. If unsure, set scenario=Unknown.

scenario examples:
{"scenario":"Office"}    ← "I need pods for our office" (explicit "for our office")
{"scenario":"Office"}    ← "looking for a booth for workplace" (explicit workplace)
{"scenario":"Office"}    ← "source booths for our office in Sydney" (explicit "for our office")
{"scenario":"Project"}   ← "we are building a new office and need booths" (new building = Project)
{"scenario":"Project"}   ← "upcoming project, need 15 acoustic pods" (explicit project)
{"scenario":"Studio"}    ← "recording booth for my dubbing work" (dubbing = Studio)
{"scenario":"Studio"}    ← "I need a recording booth" (recording = Studio)
{"scenario":"Home"}      ← "home Pod for my living room" (home + living room)
{"scenario":"Unknown"}   ← "Please quote for XL Meeting Pod" (only product name)
{"scenario":"Unknown"}   ← "I need a phone booth" (no usage context)
{"scenario":"Unknown"}   ← "I need price list" (no context at all)
{"scenario":"Unknown"}   ← "A small pod with a window" (no usage context)
{"scenario":"Unknown"}   ← "May I get a quote for single booth" (no usage context)
{"scenario":"Unknown"}   ← "How long to get installed?" (no context)
{"scenario":"Unknown"}   ← "i am requerd phone booth" (no usage context)
{"scenario":"Unknown"}   ← "We need 2 office phone booths" ("office phone booth" is product name, not scenario)
{"scenario":"Unknown"}   ← "quote for soundproof office pod" ("office pod" is product name, not scenario)
{"scenario":"Unknown"}   ← "I sell office furniture, want your booths" (describes their business, not usage)
{"scenario":"Unknown"}   ← "price for 10 phone pods of 1 person" (no usage context at all)
{"scenario":"Unknown"}   ← "need acoustic pods for company with specs" ("for company" is not a scenario)

timeline (TimeSensitive | NotMentioned):
- TimeSensitive: customer shows genuine urgency or needs delivery to a specific place — ASAP/urgent/need by [date]/within [N] weeks/ship to [city]/deliver to [location]/尽快/交期/运输/发货/enviar/enviam/立即/levertermijn/lieferzeit/délai de livraison/plazo de entrega
- NotMentioned: no urgency AND no delivery destination — "how long to install" (process question, not urgency), "pricing including shipping" (asking about cost, not urgency), "lead time?" (just browsing, no deadline pressure)

config (ConfigMentioned | NotMentioned):
- ConfigMentioned: custom/customised/customized/configuration/color/frame/finish/材质/颜色/配色/门向/OEM/ODM
- NotMentioned: none of above

language: primary language of the inquiry text (English/Spanish/Portuguese/French/German/Arabic/Chinese/Other)"""


# ═══════════════════════════════════════════════════════════════════════════════
# LLM 调用
# ═══════════════════════════════════════════════════════════════════════════════

def extract_semantic_slots(raw_body: str) -> dict | None:
    """调用 LLM 提取语义槽位。

    Returns:
        dict with keys: intent, product, scenario, timeline, config, language
        失败返回 None
    """
    if not ZHIPU_API_KEY:
        log.error("ZHIPU_API_KEY 未设置")
        return None

    if not raw_body or not raw_body.strip():
        return None

    # 截断过长文本
    text = raw_body.strip()
    if len(text) > 2000:
        text = text[:2000] + "\n...(truncated)"

    try:
        resp = requests.post(
            f"{ZHIPU_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {ZHIPU_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": ZHIPU_MODEL,
                "messages": [
                    {"role": "system", "content": SLOT_EXTRACTION_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.1,
                "max_tokens": 256,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not content:
            log.warning("LLM 语义提取返回空")
            return None

        # 解析 JSON（可能被 ```json 包裹，含截断容错）
        from auto_fix_utils import parse_ai_response
        slots = parse_ai_response(content)

        # 标准化值
        result = {
            "intent": _normalize_enum(slots.get("intent", "Unclear"),
                        ["Quotation", "Partnership", "Unclear"], "Unclear"),
            "product": _normalize_enum(slots.get("product", "Unclear"),
                         ["AcousticPod", "NonPod_Acoustics", "NoMatch", "Unclear"], "Unclear"),
            "scenario": _normalize_enum(slots.get("scenario", "Unknown"),
                          ["Office", "Home", "Studio", "Project", "Unknown"], "Unknown"),
            "timeline": _normalize_enum(slots.get("timeline", "NotMentioned"),
                          ["TimeSensitive", "NotMentioned"], "NotMentioned"),
            "config": _normalize_enum(slots.get("config", "NotMentioned"),
                        ["ConfigMentioned", "NotMentioned"], "NotMentioned"),
            "language": slots.get("language", "Other"),
        }

        log.info("语义槽位: intent=%s | product=%s | scenario=%s | timeline=%s | config=%s",
                 result["intent"], result["product"], result["scenario"],
                 result["timeline"], result["config"])
        return result

    except json.JSONDecodeError as e:
        log.warning("语义槽位 JSON 解析失败: %s | raw: %s", e, content[:200])
        return None
    except requests.Timeout:
        log.warning("语义槽位提取超时（30s）")
        return None
    except Exception as e:
        log.error("语义槽位提取异常: %s", e)
        return None


def _normalize_enum(value: str, valid: list[str], default: str) -> str:
    """标准化枚举值（模糊匹配）。"""
    if not value:
        return default
    v = value.strip()
    # 精确匹配
    if v in valid:
        return v
    # 大小写模糊
    for item in valid:
        if v.lower() == item.lower():
            return item
    return default


# ── 独立测试入口 ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python slot_extractor.py '<text>'")
        print("       echo 'text' | python slot_extractor.py -")
        sys.exit(1)

    text = sys.stdin.read() if sys.argv[1] == "-" else sys.argv[1]
    result = extract_semantic_slots(text)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Extraction failed", file=sys.stderr)
        sys.exit(1)
