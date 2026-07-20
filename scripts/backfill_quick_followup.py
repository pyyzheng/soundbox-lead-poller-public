#!/usr/bin/env python3
"""Backfill Quick Follow-up / 快捷跟进 from Follow-up Records summaries."""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import time


BASE = "ZpbUb7SP7azsNasniFjc0bWSnHg"
FU_TABLE = "tbl3n8TTJYXHG12q"
LEAD_TABLE = "tbluuuXn9WexH8LV"
QUICK = "Quick Follow-up / 快捷跟进"


def log(msg: str) -> None:
    print(msg, flush=True)


def cli(args: list[str]) -> dict:
    r = subprocess.run(args, capture_output=True, text=True)
    raw = (r.stdout or "").strip() or (r.stderr or "").strip()
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"CLI JSON parse failed: {e}; raw={raw[:500]}") from e


def load_followups() -> dict[str, list[str]]:
    offset = 0
    limit = 200
    by_lead: dict[str, list[str]] = collections.defaultdict(list)
    total = 0
    while True:
        d = cli(
            [
                "lark-cli",
                "base",
                "+record-list",
                "--base-token",
                BASE,
                "--table-id",
                FU_TABLE,
                "--as",
                "user",
                "--format",
                "json",
                "--limit",
                str(limit),
                "--offset",
                str(offset),
                "--field-id",
                "Follow-up Details",
                "--field-id",
                "Follow-up Time",
                "--field-id",
                "Related Lead",
                "--field-id",
                "跟进摘要",
                "--sort-json",
                '[{"field":"Follow-up Time","desc":true}]',
            ]
        )
        if not d.get("ok"):
            raise RuntimeError(f"record-list failed: {d}")
        data = d["data"]
        rows = data.get("data") or []
        fields = data.get("fields") or []
        idx = {name: i for i, name in enumerate(fields)}
        for row in rows:
            total += 1
            related = row[idx["Related Lead"]]
            summary = row[idx["跟进摘要"]]
            details = row[idx["Follow-up Details"]]
            ftime = row[idx["Follow-up Time"]]
            if not related:
                continue
            lead_ids = []
            if isinstance(related, list):
                for x in related:
                    if isinstance(x, dict) and x.get("id"):
                        lead_ids.append(x["id"])
                    elif isinstance(x, str):
                        lead_ids.append(x)
            text = summary
            if not text:
                parts = []
                if ftime:
                    parts.append(str(ftime)[:10].replace("-", "/"))
                if details:
                    parts.append(str(details))
                text = " | ".join(parts)
            text = (text or "").strip()
            if not text:
                continue
            for lid in lead_ids:
                by_lead[lid].append(text)
        log(f"loaded offset={offset} page={len(rows)} total_fu={total} leads={len(by_lead)}")
        if not data.get("has_more"):
            break
        offset += limit
        time.sleep(0.12)
    return by_lead


def build_updates(by_lead: dict[str, list[str]]) -> list[tuple[str, str]]:
    updates = []
    for lid, lines in by_lead.items():
        seen = set()
        uniq = []
        for line in lines:
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            uniq.append(line)
        if uniq:
            updates.append((lid, "\n".join(uniq)))
    return updates


def upsert_all(updates: list[tuple[str, str]], sleep_s: float = 0.05) -> None:
    ok = fail = 0
    for i, (lid, text) in enumerate(updates, 1):
        out = cli(
            [
                "lark-cli",
                "base",
                "+record-upsert",
                "--base-token",
                BASE,
                "--table-id",
                LEAD_TABLE,
                "--record-id",
                lid,
                "--as",
                "user",
                "--json",
                json.dumps({QUICK: text}, ensure_ascii=False),
            ]
        )
        if out.get("ok"):
            ok += 1
        else:
            fail += 1
            if fail <= 8:
                log(f"upsert fail {lid}: {json.dumps(out, ensure_ascii=False)[:300]}")
        if i % 50 == 0 or i == len(updates):
            log(f"progress {i}/{len(updates)} ok={ok} fail={fail}")
        time.sleep(sleep_s)
    log(f"DONE ok={ok} fail={fail} total={len(updates)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-leads", type=int, default=0)
    args = parser.parse_args()

    by_lead = load_followups()
    updates = build_updates(by_lead)
    if args.limit_leads:
        updates = updates[: args.limit_leads]
    log(f"prepared updates={len(updates)}")
    if args.dry_run:
        for lid, text in updates[:3]:
            log(f"sample {lid}:\n{text[:300]}\n---")
        return 0
    upsert_all(updates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
