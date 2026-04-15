#!/usr/bin/env python3
"""Pull competitor ads from the Meta Ad Library, extract creatives, and
optionally write directly to Airtable via pyairtable.

Usage:
    # JSON only (Claude inserts via MCP later):
    python3 pull_ads.py --pages "Ben Heath,Chase Dimond" --output ads_output/ads.json

    # Full pipeline — scrape + write to Airtable in one shot:
    python3 pull_ads.py --pages "Ben Heath,Chase Dimond" --write-to-airtable

    # Parallel creative extraction (4x faster):
    python3 pull_ads.py --pages "Ben Heath" --workers 4 --write-to-airtable
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from lib.creative_extractor import extract_batch
from lib.meta_ads import fetch_ads_for_page, get_token, resolve_page_id

REPO_ROOT = Path(__file__).parent

# ── Airtable field name → value builders ─────────────────────────

PLATFORM_MAP = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "messenger": "Messenger",
    "audience_network": "Audience Network",
    "threads": "Threads",
    "whatsapp": "WhatsApp",
}


def _slugify(text: str, max_len: int = 60) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:max_len]


def _make_ad_name(page_name: str, ad: dict) -> str:
    headlines = ad.get("ad_creative_link_titles") or []
    bodies = ad.get("ad_creative_bodies") or []
    hook = ""
    if headlines and headlines[0]:
        hook = headlines[0]
    elif bodies and bodies[0]:
        hook = bodies[0].split("\n")[0][:80]
    hook = _slugify(hook, 70)
    return f"{page_name} — {hook}" if hook else f"{page_name} — ad {ad.get('id', '')[-6:]}"


def _locations(locs) -> str:
    if not locs:
        return ""
    return ", ".join(
        (f"-{l['name']}" if l.get("excluded") else l.get("name", ""))
        for l in locs
    )


def _ages(ages) -> str:
    return f"{ages[0]}–{ages[-1]}" if ages and len(ages) >= 2 else ""


def build_row(page_input: str, ad: dict, creatives: list) -> dict:
    page_name = ad.get("page_name") or page_input
    ad_id = ad.get("id") or ""
    bodies = ad.get("ad_creative_bodies") or []
    headlines = ad.get("ad_creative_link_titles") or []
    descriptions = ad.get("ad_creative_link_descriptions") or []
    captions = ad.get("ad_creative_link_captions") or []
    caption = captions[0] if captions else ""
    landing = ""
    if caption:
        landing = caption if caption.startswith("http") else f"https://{caption}"
    stop = ad.get("ad_delivery_stop_time") or ""
    langs = ad.get("languages") or []

    # Calculate days running
    days_running = None
    start_raw = ad.get("ad_delivery_start_time") or ""
    if start_raw:
        try:
            start_dt = datetime.strptime(start_raw[:10], "%Y-%m-%d").date()
            end_dt = datetime.strptime(stop[:10], "%Y-%m-%d").date() if stop else date.today()
            days_running = max((end_dt - start_dt).days, 0)
        except (ValueError, TypeError):
            pass

    return {
        "competitor_ad_name": _make_ad_name(page_name, ad),
        "facebook_page": page_name,
        "page_id": ad.get("page_id") or "",
        "ad_copy": bodies[0] if bodies else "",
        "headline": headlines[0] if headlines else "",
        "description": descriptions[0] if descriptions else "",
        "cta_caption": caption,
        "landing_page_url": landing,
        "ad_library_id": ad_id,
        "ad_library_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
        "start_date": ad.get("ad_delivery_start_time") or "",
        "last_seen_date": stop,
        "active_status": "Inactive" if stop else "Active",
        "platforms": [PLATFORM_MAP.get(p, p) for p in (ad.get("publisher_platforms") or [])],
        "languages": ", ".join(langs),
        "days_running": days_running,
        "target_ages": _ages(ad.get("target_ages")),
        "target_locations": _locations(ad.get("target_locations")),
        "creative_urls": [{"url": c.url, "type": c.media_type} for c in creatives],
        "creative_type": "Video" if any(c.media_type == "video" for c in creatives) else ("Image" if creatives else ""),
        "video_transcription": "",
    }


# ── Airtable writer ──────────────────────────────────────────────

# Maps our flat row keys → Airtable field names
FIELD_NAME_MAP = {
    "competitor_ad_name": "Competitor Ad Name",
    "facebook_page": "Facebook Page",
    "page_id": "Page ID",
    "creative_type": "Creative Type",
    "ad_copy": "Ad Copy",
    "headline": "Headline",
    "description": "Description",
    "cta_caption": "CTA Caption",
    "landing_page_url": "Landing Page URL",
    "ad_library_id": "Ad Library ID",
    "ad_library_url": "Ad Library URL",
    "start_date": "Start Date",
    "last_seen_date": "Last Seen Date",
    "active_status": "Active Status",
    "platforms": "Platforms",
    "languages": "Languages",
    "days_running": "Days Running",
    "target_ages": "Target Ages",
    "target_locations": "Target Locations",
    "video_transcription": "Video Transcription",
}

# Fields that should be omitted when empty (Airtable rejects empty URL/date fields)
SKIP_IF_EMPTY = {"landing_page_url", "ad_library_url", "start_date", "last_seen_date", "impressions_tier"}


def _row_to_airtable_fields(row: dict) -> dict:
    """Convert a flat row dict to Airtable {field_name: value}."""
    fields = {}
    for key, field_name in FIELD_NAME_MAP.items():
        val = row.get(key)
        if key in SKIP_IF_EMPTY and (val is None or val == "" or val == []):
            continue
        if val is None or val == "" or val == []:
            continue
        fields[field_name] = val

    # Creative attachment — pass URLs for Airtable to fetch
    creative_urls = row.get("creative_urls") or []
    if creative_urls:
        ad_id = row.get("ad_library_id", "ad")
        fields["Creative"] = [
            {
                "url": c["url"],
                "filename": f"{ad_id}_{i+1}.{'mp4' if c['type'] == 'video' else 'jpg'}",
            }
            for i, c in enumerate(creative_urls)
        ]

    return fields


def write_to_airtable(rows: list[dict], base_id: str) -> None:
    """Write rows to Airtable via pyairtable (fast, direct REST)."""
    from pyairtable import Api

    pat = os.getenv("AIRTABLE_PAT")
    if not pat:
        sys.exit("AIRTABLE_PAT not set in .env")

    api = Api(pat)
    table = api.table(base_id, "Competitor Ads")

    field_dicts = [_row_to_airtable_fields(r) for r in rows]

    # Batch insert in chunks of 10 with per-batch error handling.
    # If one batch fails, log it and continue — don't lose the whole run.
    BATCH_SIZE = 10
    batches = [field_dicts[i:i + BATCH_SIZE] for i in range(0, len(field_dicts), BATCH_SIZE)]
    print(f"\n  writing {len(field_dicts)} records to Airtable ({len(batches)} batches)...", flush=True)
    t0 = time.time()
    created_count = 0
    failed_count = 0
    for i, batch in enumerate(batches, 1):
        try:
            created = table.batch_create(batch, typecast=True)
            created_count += len(created)
            print(f"    batch {i}/{len(batches)}: {len(created)} records ✓", flush=True)
        except Exception as e:
            failed_count += len(batch)
            print(f"    batch {i}/{len(batches)}: FAILED ({e})", flush=True)
    elapsed = time.time() - t0
    print(f"  done! {created_count} records written, {failed_count} failed, {elapsed:.1f}s", flush=True)


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Pull competitor ads from Meta Ad Library")
    parser.add_argument("--pages", required=True, help="Comma-separated page names or IDs")
    parser.add_argument("--countries", default="US")
    parser.add_argument("--ads-per-page", type=int, default=10)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--sort-by",
        default="impressions_high_to_low",
        choices=[
            "impressions_high_to_low",
            "longest_running",
            "most_recent",
            "ad_delivery_start_time_ascending",
            "ad_delivery_start_time_descending",
        ],
    )
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--media-type", default="all", choices=["all", "image", "video"])
    parser.add_argument("--output", default="ads_output/ads.json")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--skip-creatives", action="store_true")
    parser.add_argument("--workers", type=int, default=4, help="Parallel Selenium workers for creative extraction (default 4)")
    parser.add_argument("--write-to-airtable", action="store_true", help="Write results directly to Airtable (needs AIRTABLE_PAT in .env)")
    parser.add_argument("--base-id", type=str, help="Airtable base ID (required with --write-to-airtable)")
    parser.add_argument("--transcribe", action="store_true", help="Transcribe video ads using OpenAI Whisper (local, free, adds ~10-15s per video)")
    args = parser.parse_args()

    token = get_token()
    page_inputs = [p.strip() for p in args.pages.split(",") if p.strip()]
    if not page_inputs:
        sys.exit("No pages provided.")

    # ── Phase 1: Resolve all page IDs + fetch all ads in parallel ──
    def fetch_one_page(page_input: str):
        pid, resolved_name = resolve_page_id(page_input, token)
        if not pid:
            return page_input, None, None, "could not resolve page ID"
        ads, err = fetch_ads_for_page(
            pid, token,
            countries=args.countries,
            days_back=args.days,
            max_ads=args.ads_per_page,
            sort_by=args.sort_by,
            ad_active_status="ACTIVE" if args.active_only else "ALL",
            media_type=args.media_type,
        )
        return page_input, ads or [], resolved_name, err

    t0 = time.time()
    print(f"\n[phase 1] fetching ads for {len(page_inputs)} pages in parallel...", flush=True)
    pages_with_ads: list[tuple[str, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=min(len(page_inputs), 8)) as pool:
        futures = {pool.submit(fetch_one_page, p): p for p in page_inputs}
        for future in as_completed(futures):
            page_input, ads, resolved_name, err = future.result()
            if err:
                print(f"  {page_input}: {err}", flush=True)
                continue
            print(f"  {page_input}: {len(ads)} ads" + (f" ({resolved_name})" if resolved_name else ""), flush=True)
            pages_with_ads.append((page_input, ads))
    print(f"[phase 1] done in {time.time()-t0:.1f}s — {sum(len(a) for _, a in pages_with_ads)} total ads", flush=True)

    # ── Phase 2: Single pooled Selenium extraction across ALL ads ──
    creatives_by_ad: dict = {}
    if not args.skip_creatives:
        all_snapshot_pairs = [
            (a["id"], a["ad_snapshot_url"])
            for _, ads in pages_with_ads
            for a in ads
            if a.get("ad_snapshot_url")
        ]
        if all_snapshot_pairs:
            t0 = time.time()
            print(f"\n[phase 2] extracting {len(all_snapshot_pairs)} creatives...", flush=True)
            creatives_by_ad = extract_batch(
                all_snapshot_pairs,
                headless=not args.no_headless,
                workers=args.workers,
            )
            print(f"[phase 2] done in {time.time()-t0:.1f}s", flush=True)

    # ── Phase 3: Build all rows ──
    all_rows: list[dict] = []
    for page_input, ads in pages_with_ads:
        for a in ads:
            all_rows.append(build_row(page_input, a, creatives_by_ad.get(a["id"], [])))

    # Optionally transcribe video ads
    if args.transcribe:
        from lib.transcriber import transcribe_videos
        all_rows = transcribe_videos(all_rows)

    # Always save JSON
    out_path = REPO_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_rows, indent=2, default=str))
    print(f"\nWrote {len(all_rows)} ads to {out_path}", flush=True)

    # Optionally write to Airtable directly
    if args.write_to_airtable:
        if not args.base_id:
            sys.exit("--base-id is required with --write-to-airtable")
        write_to_airtable(all_rows, args.base_id)


if __name__ == "__main__":
    main()
