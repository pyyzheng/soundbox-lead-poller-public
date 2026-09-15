"""
URL → 产品型号映射
"""

import re

URL_MODEL_MAP = {
    # ── SR 系列（标准路径）──
    "/soundbox-sr-s/": "SR-S",
    "/soundbox-sr-m/": "SR-M",
    "/soundbox-sr-l/": "SR-L",
    "/soundbox-sr-xl/": "SR-XL",
    "/soundbox-sr-1/": "SR-1",
    "/soundbox-sr-2/": "SR-2",
    "/soundbox-sr/": "SR",
    # ── SR 系列 SEO 页面 ──
    "/meeting-pods/": "SR-L",
    "/small-meeting-pods/": "SR-S",
    "/workpods/": "SR-M",
    "/4-person-meeting-pod/": "SR-L",
    "/sound-proof-meeting-pods/": "SR-XL",
    "/meeting-phone-booth/": "SR-XXL",
    # ── SRP → SR 按尺寸映射 ──
    "/office-private-booth/": "SR-S",
    "/private-phone-booth/": "SR-S",
    "/sound-proof-pods/": "SR-S",
    "/office-quiet-pods/": "SR-L",
    "/phone-booth-soundproof/": "SR-XL",
    "/privacy-booth/": "SR",
    # ── VR 系列（标准路径）──
    "/soundbox-vr-s/": "VR-S",
    "/soundbox-vr-m/": "VR-M",
    "/soundbox-vr-l/": "VR-L",
    "/soundbox-vr/": "VR",
    # ── VR 系列 SEO 页面 ──
    "/silence-box/": "VR-S",
    "/corporate-phone-booth/": "VR-M",
    "/silent-booth-office/": "VR-L",
    "/quiet-booth-office/": "VR-XL",
    "/silen-phone-booth/": "VR-XXL",
    "/silence-booth/": "VR",
    # ── VRP → VR 按尺寸映射 ──
    "/office-booth-vrp-s/": "VR-S",
    "/phone-booth-vrp-m/": "VR-M",
    "/office-booth-vrp-l/": "VR-L",
    "/office-booth-arp-xl/": "VR-XL",
    "/office-booth-vrp-xxl/": "VR-XXL",
    "/office-booth-vrp/": "VR",
    # ── VRT 系列 ──
    "/soundbox-vrt-s/": "VRT-S",
    "/soundbox-vrt-m/": "VRT-M",
    "/soundbox-vrt-l/": "VRT-L",
    "/soundbox-vrt/": "VRT",
    # ── ART 系列 ──
    "/soundbox-art/": "ART",
    "/soundbox-ar/": "ART",
    "/soundproof-office-pod/": "ART",
    "/silence-booth-ar-s/": "ART-S",
    "/office-pods-ar-m/": "ART-M",
    "/office-pods-ar-l/": "ART-L",
    "/office-pods-ar-xl/": "ART-XL",
    "/work-station-pods/": "ART-S",
    "/office-space-pods/": "ART-M",
    "/enclosed-office-pods/": "ART-L",
    "/enclosed-meeting-pods/": "ART-XL",
    "/pod-meeting/": "ART-XXL",
    "/office-phone-call-booth/": "ART-S",
    # ── 家居舱 ──
    "/soundbox-home-pod/": "家居舱",
    "/soundbox-home-silence-pod/": "家居舱",
    "/home-soundproof-booth/": "家居舱",
    "/office-pods-for-home/": "家居舱",
    "/4-person-home-office-pod/": "家居舱",
    # ── 声学产品 ──
    "/acoustic-art-panels/": "声学产品",
    "/acoustic-slat-wall-panel/": "声学产品",
    "/damped-acoustic-flooring/": "声学产品",
    "/noise-insulate-curtain/": "声学产品",
    "/mute-foot-cover-for-chairs/": "声学产品",
    "/wafter-acoustic-holography-odule/": "声学产品",
}


def identify_model_from_url(url: str) -> str:
    """从 Page URL 路径提取具体型号"""
    if not url:
        return ""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    for url_path, model in sorted(URL_MODEL_MAP.items(), key=lambda x: -len(x[0])):
        if url_path.rstrip("/") in path or path.startswith(url_path.rstrip("/")):
            return model
    return ""


def extract_page_url(body: str) -> str:
    m = re.search(r"Page URL\s*[:：]\s*(\S+)", body, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def extract_url_keyword(body: str) -> str:
    m = re.search(r"(?:URL\s*(?:关键[词字]|Keyword))\s*[:：]\s*(.+?)(?=\s*(?:页面|Page|Full|http|$))", body, re.IGNORECASE)
    return m.group(1).strip() if m else ""
