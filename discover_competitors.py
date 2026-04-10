#!/usr/bin/env python3
"""Discover Facebook pages that advertise in the same space as a given website.

Flow:
1. Fetch the input URL and extract a short description of the business
2. Generate a handful of Ad Library search keywords from that description
3. Run `ads_archive` with `search_terms` for each keyword (small pages for speed)
4. Count page occurrences across all searches, return top candidates

Claude should treat the output as a starting list and combine it with its own
knowledge of the user's market before presenting to the user.

Usage:
    python3 discover_competitors.py --url https://example.com
    python3 discover_competitors.py --keywords "meta ads course,facebook ads community"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from lib.meta_ads import BASE_URL, get_token, _request_with_retry  # noqa: F401


def extract_page_description(url: str) -> tuple[str, str]:
    """Return (title, short description) for a URL. Best-effort."""
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            },
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        sys.exit(f"Failed to fetch {url}: {e}")

    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string if soup.title else "") or ""
    meta_desc = ""
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").lower()
        if name in ("description", "og:description", "twitter:description"):
            meta_desc = tag.get("content", "") or meta_desc
            if meta_desc:
                break

    if not meta_desc:
        # Fall back to the first decent paragraph
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if len(txt) > 60:
                meta_desc = txt[:300]
                break

    return title.strip(), meta_desc.strip()


def keywords_from_text(title: str, description: str) -> list[str]:
    """Extract noun-phrase-ish keywords. Kept simple on purpose — Claude can
    override this by passing --keywords directly."""
    text = f"{title} {description}".lower()
    # Strip punctuation
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if len(w) > 3]
    stop = {
        "with", "that", "this", "from", "your", "into", "about", "will", "have",
        "they", "been", "more", "make", "most", "best", "just", "like", "what",
        "when", "where", "https", "http", "www", "com", "website",
    }
    words = [w for w in words if w not in stop]

    # Generate bigrams as keyword candidates
    bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    counts = Counter(bigrams)
    top_bigrams = [b for b, _ in counts.most_common(6)]

    # Also include top single words as single-keyword searches
    top_words = [w for w, _ in Counter(words).most_common(4)]

    return top_bigrams + top_words


def search_pages(keyword: str, token: str, countries: str = "US", limit: int = 50) -> list[dict]:
    now = datetime.now(timezone.utc)
    params = {
        "access_token": token,
        "search_terms": keyword,
        "ad_reached_countries": f"['{countries}']",
        "ad_delivery_date_min": (now - timedelta(days=180)).strftime("%Y-%m-%d"),
        "ad_delivery_date_max": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        "ad_active_status": "ACTIVE",
        "ad_type": "ALL",
        "sort_by": "impressions_high_to_low",
        "fields": "page_id,page_name",
        "limit": limit,
    }
    data = _request_with_retry(f"{BASE_URL}/ads_archive", params)
    if "error" in data:
        print(f"    search '{keyword}': error {data['error'].get('message')}", flush=True)
        return []
    return data.get("data", [])


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Website URL to analyze")
    group.add_argument("--keywords", help="Comma-separated keywords to search instead of a URL")
    parser.add_argument("--countries", default="US")
    parser.add_argument("--top", type=int, default=15, help="Max candidates to return")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    token = get_token()

    if args.url:
        print(f"Fetching {args.url}...", flush=True)
        title, desc = extract_page_description(args.url)
        print(f"  title: {title[:120]}", flush=True)
        print(f"  desc:  {desc[:200]}", flush=True)
        keywords = keywords_from_text(title, desc)
    else:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    if not keywords:
        sys.exit("No keywords to search. Pass --keywords explicitly.")

    print(f"\nSearching Ad Library for {len(keywords)} keyword(s):")
    for k in keywords:
        print(f"  - {k}")

    page_counts: Counter[str] = Counter()
    page_names: dict[str, str] = {}

    for kw in keywords:
        ads = search_pages(kw, token, args.countries)
        for ad in ads:
            pid = ad.get("page_id")
            if pid:
                page_counts[pid] += 1
                if ad.get("page_name"):
                    page_names[pid] = ad["page_name"]

    candidates = [
        {"page_id": pid, "page_name": page_names.get(pid, ""), "keyword_hits": n}
        for pid, n in page_counts.most_common(args.top)
    ]

    print(f"\nTop {len(candidates)} candidates:")
    for i, c in enumerate(candidates, 1):
        print(f"  {i:2d}. [{c['keyword_hits']} hits] {c['page_id']} — {c['page_name']}")

    if args.output:
        from pathlib import Path
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(candidates, indent=2))
        print(f"\nWrote candidates to {args.output}", flush=True)


if __name__ == "__main__":
    main()
