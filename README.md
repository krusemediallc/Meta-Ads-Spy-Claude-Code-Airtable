# Competitor Ads Scraper (Claude Code Template)

Pull your competitors' ads from the Meta Ad Library straight into Airtable — ad copy, headlines, creatives (images + videos), targeting, platforms, and more.

## Getting Started

### 1. Open Claude Code
Open [Claude Code](https://claude.ai/code) (CLI, desktop app, or IDE extension).

### 2. Paste this repo URL and say "set this up"
That's it. Claude will clone the repo, install dependencies, walk you through API key setup, and get everything running.

---

## What you'll need
- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/)
- **Google Chrome** — needed for extracting ad creatives (runs headless, you won't see a browser window)
- **Meta access token** — free, takes 30 seconds: [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer/)
- **Airtable account + Personal Access Token** — free tier works: [airtable.com/create/tokens](https://airtable.com/create/tokens)

## What you get

A `Competitor Ads` Airtable table with 20 fields per ad:

| Field | Description |
|---|---|
| Competitor Ad Name | Auto-generated descriptive name |
| Facebook Page | Page running the ad |
| Creative | Image or video attachment (auto-downloaded) |
| Ad Copy | Full ad text |
| Headline / Description / CTA | Link preview fields |
| Landing Page URL | Where the ad sends traffic |
| Ad Library URL | Direct link to view in Meta's Ad Library |
| Start Date / Last Seen Date | When the ad ran |
| Active Status | Active or Inactive |
| Platforms | Facebook, Instagram, Messenger, Audience Network, Threads |
| Impressions | High / Medium / Low (derived from EU reach data) |
| Target Ages / Locations | Audience targeting |
| ...and more | Languages, EU Total Reach, Page ID, Ad Library ID |

## Two ways to find competitors

- **Direct** — give Claude a list of Facebook page names or IDs
- **Discover** — give Claude a URL (like your website or a competitor's Skool page) and it will suggest relevant competitors automatically

## How it works under the hood

1. `pull_ads.py` calls the Meta Ad Library API (`ads_archive`) for each competitor
2. Parallel headless Chrome workers extract creative images/videos from ad snapshots
3. `pyairtable` batch-inserts all records directly to your Airtable base (~20 seconds for 100+ ads)
4. A JSON backup is saved locally

See [`CLAUDE.md`](./CLAUDE.md) for the full Claude Code runtime flow and [`META_ADS_LIBRARY_API.md`](./META_ADS_LIBRARY_API.md) for the API reference.
