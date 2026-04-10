"""Meta Ad Library API client.

Handles:
- Page ID resolution (direct lookup + ad_archive search fallback)
- Paginated ad fetching with retry on 429/5xx
- Per-ad detail fetch for creative fields (avoids pagination+creative 500 bug)
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

LIST_FIELDS = ",".join([
    "id",
    "page_id",
    "page_name",
    "ad_creation_time",
    "ad_creative_bodies",
    "ad_creative_link_titles",
    "ad_creative_link_descriptions",
    "ad_creative_link_captions",
    "ad_snapshot_url",
    "ad_delivery_start_time",
    "ad_delivery_stop_time",
    "publisher_platforms",
    "languages",
    "eu_total_reach",
    "estimated_audience_size",
    "target_ages",
    "target_gender",
    "target_locations",
])


def get_token() -> str:
    token = os.getenv("META_ACCESS_TOKEN")
    if not token:
        sys.exit("META_ACCESS_TOKEN not set. Put it in .env first.")
    return token


def verify_token() -> None:
    """Smoke-test the token by fetching 1 ad. Prints result and exits with code 0/1."""
    token = get_token()
    r = requests.get(
        f"{BASE_URL}/ads_archive",
        params={
            "access_token": token,
            "search_terms": "nike",
            "ad_reached_countries": "['US']",
            "ad_type": "ALL",
            "fields": "id",
            "limit": 1,
        },
        timeout=15,
    )
    if r.status_code == 200 and "data" in r.json():
        print("Token OK")
        return
    sys.exit(f"Token verification failed: {r.status_code} {r.text[:200]}")


def resolve_page_id(identifier: str, token: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a page name / vanity / numeric ID to (page_id, page_name).

    Strategy:
    1. If identifier is all digits, return it as-is.
    2. Try GET /{identifier}?fields=id,name — works for vanity URLs.
    3. Fall back to ads_archive search_terms and pick the best-matching page.
    """
    identifier = str(identifier).strip()
    if identifier.isdigit():
        return identifier, None

    # Try direct lookup
    try:
        r = requests.get(
            f"{BASE_URL}/{identifier}",
            params={"access_token": token, "fields": "id,name"},
            timeout=10,
        )
        data = r.json()
        if "error" not in data and data.get("id"):
            return str(data["id"]), data.get("name")
    except Exception:
        pass

    # Fall back to search
    now = datetime.now(timezone.utc)
    params = {
        "access_token": token,
        "search_terms": identifier,
        "ad_reached_countries": "['US']",
        "ad_delivery_date_min": (now - timedelta(days=365)).strftime("%Y-%m-%d"),
        "ad_delivery_date_max": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        "ad_active_status": "ALL",
        "ad_type": "ALL",
        "fields": "page_id,page_name",
        "limit": 50,
    }
    try:
        r = requests.get(f"{BASE_URL}/ads_archive", params=params, timeout=30)
        data = r.json()
        if "error" in data:
            return None, None
        ads = data.get("data", [])
        counts = Counter(ad.get("page_id") for ad in ads if ad.get("page_id"))
        names = {ad.get("page_id"): ad.get("page_name") for ad in ads}
        ident_lower = identifier.lower().replace(".", " ").replace("_", " ")
        # Prefer pages whose name contains the search term
        for pid, _ in counts.most_common(10):
            pname = (names.get(pid) or "").lower()
            parts = [p for p in ident_lower.split() if len(p) > 2]
            if ident_lower in pname or (parts and all(p in pname for p in parts)):
                return pid, names.get(pid)
        if counts:
            pid = counts.most_common(1)[0][0]
            return pid, names.get(pid)
    except Exception:
        pass
    return None, None


def _request_with_retry(url: str, params: Optional[dict], max_retries: int = 4) -> dict:
    """GET with retry on 429 (honor Retry-After) and 500s."""
    for attempt in range(max_retries):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 30))
            print(f"    rate limited, waiting {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if 500 <= r.status_code < 600:
            wait = 2 ** attempt
            print(f"    server error {r.status_code}, retrying in {wait}s...", flush=True)
            time.sleep(wait)
            continue
        return r.json()
    return {"error": {"message": f"max retries exceeded"}}


def fetch_ads_for_page(
    page_id: str,
    token: str,
    countries: str = "US",
    days_back: int = 365,
    limit_per_page: int = 100,
    max_ads: int = 100,
    sort_by: str = "impressions_high_to_low",
    ad_active_status: str = "ALL",
    media_type: str = "all",
) -> tuple[list[dict], Optional[str]]:
    """Fetch up to max_ads ads for a page. Returns (ads, error_message)."""
    now = datetime.now(timezone.utc)
    params = {
        "access_token": token,
        "search_page_ids": f"['{page_id}']",
        "ad_reached_countries": f"['{countries}']",
        "ad_delivery_date_min": (now - timedelta(days=days_back)).strftime("%Y-%m-%d"),
        "ad_delivery_date_max": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        "ad_active_status": ad_active_status,
        "ad_type": "ALL",
        "sort_by": sort_by,
        "fields": LIST_FIELDS,
        "limit": min(limit_per_page, max_ads),
    }
    if media_type != "all":
        params["media_type"] = media_type

    url = f"{BASE_URL}/ads_archive"
    all_ads: list[dict] = []
    seen = set()

    while True:
        data = _request_with_retry(url, params)
        if "error" in data:
            return all_ads, data["error"].get("message", "Unknown API error")

        for ad in data.get("data", []):
            ad_id = ad.get("id")
            if ad_id and ad_id not in seen:
                seen.add(ad_id)
                all_ads.append(ad)

        if len(all_ads) >= max_ads:
            break

        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        url = next_url
        params = None

    return all_ads[:max_ads], None


