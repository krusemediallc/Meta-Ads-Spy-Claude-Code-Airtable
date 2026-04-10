# Meta Ad Library API Reference (`ads_archive`)

Quick reference for the Ad Library endpoint used by this template. Adapted from production experience.

**Endpoint:** `GET https://graph.facebook.com/v21.0/ads_archive`

**Auth:** `access_token` query param (any user token with `public_profile` works — the Ad Library is public data).

---

## Required parameters

| Param | Type | Notes |
|---|---|---|
| `access_token` | string | Meta developer access token |
| `ad_reached_countries` | string | Comma-separated ISO country codes, e.g. `US`, `US,CA,GB` |
| `ad_delivery_date_min` | `YYYY-MM-DD` | Start of delivery window. Effectively required even though docs say optional. |
| `ad_delivery_date_max` | `YYYY-MM-DD` | End of delivery window. |
| `search_page_ids` **or** `search_terms` | string | Page IDs (comma-separated, max 10) or keyword search. You must provide one, not both. |

## Useful optional parameters

| Param | Values | Notes |
|---|---|---|
| `ad_active_status` | `ACTIVE`, `INACTIVE`, `ALL` | Default `ALL` |
| `ad_type` | `ALL`, `POLITICAL_AND_ISSUE_ADS`, etc. | Default `ALL` |
| `sort_by` | `impressions_high_to_low`, `longest_running`, `most_recent`, `ad_delivery_start_time_ascending`, `ad_delivery_start_time_descending` | Meta ranks internally even though numeric impressions aren't returned for commercial ads |
| `media_type` | `all`, `video`, `image` | Filter by creative type |
| `fields` | comma-separated field list | See next section |
| `limit` | 1–1000 | Results per page |

## Field availability

| Field | Available for |
|---|---|
| `id`, `page_id`, `page_name`, `ad_creation_time` | All ads |
| `ad_delivery_start_time`, `ad_delivery_stop_time` | All ads |
| `ad_snapshot_url` | All ads — **only way** to get the actual creative (image/video). The URL renders the ad in a browser. |
| `ad_creative_bodies` | All ads — **but** requesting it together with `paging.next` intermittently causes HTTP 500. Fetch creative fields per-ad instead. |
| `ad_creative_link_titles`, `ad_creative_link_descriptions`, `ad_creative_link_captions` | Same caveat as `ad_creative_bodies` |
| `publisher_platforms`, `languages` | All ads |
| `target_ages`, `target_gender`, `target_locations` | All ads (coverage varies) |
| `estimated_audience_size` | Most EU-delivered ads |
| `eu_total_reach` | EU-delivered ads only — useful as an engagement signal |
| `age_country_gender_reach_breakdown` | EU-delivered ads — rich per-country demo breakdown |
| `impressions` | **POLITICAL_AND_ISSUE_ADS only** (numeric ranges, not returned for commercial ads) |
| `spend` | **POLITICAL_AND_ISSUE_ADS only** |
| `currency`, `bylines`, `beneficiary_payers` | Political ads only |

## Resolving page names to IDs

The API takes numeric `page_id`s in `search_page_ids`. To go from a page name or vanity URL to an ID:

1. **Direct lookup** — `GET /{username}?fields=id&access_token={token}`. Works if the page has a vanity URL (e.g. `BenHeath`).
2. **Search fallback** — if step 1 returns an error, call `ads_archive` with `search_terms={name}`, count page occurrences in the results, pick the page whose `page_name` best matches the input string.

Both strategies are implemented in [`lib/meta_ads.py`](./lib/meta_ads.py) as `resolve_page_id()`.

## Creative extraction

The API **never** returns direct image/video URLs for commercial ads. You must:

1. Take the `ad_snapshot_url` from the API response
2. Load it in a headless browser (this template uses Selenium + Chrome)
3. Wait for the page to finish loading (~3 sec)
4. Query the DOM:
   - `img[src*="scontent"], img[src*="fbcdn"], img[src*="cdninstagram"]` for images (filter out avatars/emoji/thumbnails by min dimension)
   - `video[src], video source[src]` for videos (usually `.mp4` on `*.fbcdn.net`)

The returned URLs have short-lived signed tokens (~1 hour) — if you want permanent copies, download them immediately. If you're passing them to Airtable as attachments, Airtable fetches them within seconds so this isn't an issue.

## Gotchas

1. **Rate limits:** 200 calls per hour per app in default tier. 429 responses include `Retry-After`. Retry after the specified seconds.
2. **Pagination + creative fields:** Requesting `ad_creative_bodies` with pagination returns sporadic 500s. Use minimal fields on list calls, fetch creative fields per-ad.
3. **Date range required in practice:** Even though `ad_delivery_date_min/max` are listed as optional, omitting them often returns empty results.
4. **Python `requests` library gets blocked on the `render_ad` snapshot URL** (returns 400) but `curl` and real browsers work. Use Selenium for creative extraction, not `requests`.
5. **`ad_snapshot_url` token:** The URL Meta returns embeds your access token. Don't log it or share it — it grants the same access as the token itself.
