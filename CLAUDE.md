# Competitor Ads Scraper Template

This template helps users pull competitor ads from the Meta Ad Library into an Airtable base. It's designed to be driven by you (Claude Code) interactively.

## What this template does
1. Helps the user copy a starter Airtable base into their workspace
2. Creates a `Competitor Ads` table inside that base with a rich schema
3. Sets up the user's Meta Ad Library access token in `.env`
4. Optionally discovers competitors from a website URL
5. Pulls real ads (including creative images/videos) and writes them to Airtable

## Prerequisites the user must have
- Python 3.10+
- Google Chrome installed (Selenium uses headless Chrome for creative extraction)

---

## When to start this flow

If the user pastes a GitHub URL for this repo, or says anything like "get started", "set up", "help me scrape", "run the scraper", or asks what this project does:

1. **If the repo hasn't been cloned yet** (you're not inside the project directory), clone it first:
   ```bash
   git clone <REPO_URL>
   cd competitor-ads-scraper
   ```
2. Then **immediately begin Step 0 below**. Don't wait for the user to ask for each step individually; drive the process forward proactively. Walk them through every step one at a time, confirming each before moving on.

## Step-by-step flow you (Claude) should follow

Do not skip steps. Do them in order. After each step, briefly confirm completion before moving on.

### Step 0 — Install Python dependencies
```bash
pip install -r requirements.txt
```
If `pip` isn't available, try `pip3` or `python3 -m pip install -r requirements.txt`.

### Step 1 — Have the user copy the starter Airtable base
Tell the user to click this link to copy the starter base into their Airtable workspace:

**https://airtable.com/addBaseFromShare/appbv243OgAopA4f9/shr5ZglusPoPRzfua**

After they click and copy it, ask them for the **base ID** of the new copy. (It's the string starting with `app...` in the Airtable URL, e.g. `appXXXXXXXXXX`.)

Remember this base ID — you'll pass it to scripts via `--base-id` in later steps.

### Step 2 — Set up API keys
If `.env` doesn't exist, copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Then **always open `.env` for the user** — never make them search for it:
```bash
# macOS:
open -a TextEdit .env
# Windows:
notepad .env
# Linux:
xdg-open .env
```
Use whichever command matches the user's platform.

Tell the user:
> I've opened your `.env` file. You need to add two keys:
>
> 1. **META_ACCESS_TOKEN** — go to https://developers.facebook.com/tools/explorer/ and generate an access token. Paste it after `META_ACCESS_TOKEN=`.
> 2. **AIRTABLE_PAT** — go to https://airtable.com/create/tokens and create a Personal Access Token. Required scopes: `data.records:write` and `schema.bases:read`. Grant access to the base you copied in Step 1. Paste it after `AIRTABLE_PAT=`.
>
> Save the file and let me know when done.

Wait for confirmation. Then verify the Meta token works:
```bash
python3 -c "from lib.meta_ads import verify_token; verify_token()"
```

### Step 3 — Create the `Competitor Ads` table
Run the setup script to create the table in the user's base:
```bash
python3 setup_table.py --base-id <BASE_ID_FROM_STEP_1>
```

This reads `schema.json` and creates the `Competitor Ads` table with all 20 fields via pyairtable. If the table already exists, it prints the existing schema and skips creation.

### Step 4 — Decide how to source competitors
Ask the user which mode they want:

> Would you like to:
> **A)** Give me a list of competitor Facebook pages (names or page IDs) directly, or
> **B)** Point me at a website URL and I'll suggest relevant competitors to scrape?

**Mode A (direct):** Ask for competitor page names or numeric page IDs, comma-separated. You'll pass them straight to `pull_ads.py`.

**Mode B (discover):** Ask for the URL. Then:
1. Fetch the URL with your `WebFetch` tool to understand what the business does
2. Run `python3 discover_competitors.py --url <URL>` — it returns a JSON list of candidate Facebook pages that advertise in related terms
3. Combine the script's results with your own knowledge of the space to propose 5–10 competitors
4. Present the list to the user and ask them to confirm which ones to scrape

### Step 5 — Ask about video transcription
Before running the scraper, ask:

> Some of your competitors' ads will be videos. Would you like me to **transcribe the audio** from those videos? This uses OpenAI Whisper locally on your machine (free, no API key needed), but adds ~10-15 seconds per video to the run time.

If yes, add `--transcribe` to the command in Step 6. Also run `pip install openai-whisper` first (requires ffmpeg installed).

If no, skip it — they can always re-run with `--transcribe` later.

### Step 6 — Pull ads and write them to Airtable
Run the scraper for the confirmed competitor pages:
```bash
python3 pull_ads.py \
  --pages "Ben Heath,Chase Dimond" \
  --countries US \
  --ads-per-page 10 \
  --sort-by impressions_high_to_low \
  --workers 4 \
  --write-to-airtable \
  --base-id <BASE_ID_FROM_STEP_1> \
  --output ads_output/ads.json
  # Add --transcribe if user opted in at Step 5
```

This does everything in one shot:
1. Calls the Meta Ad Library API for each competitor page
2. Extracts creative images/videos via parallel headless Chrome workers (~4x faster)
3. Writes all records directly to the `Competitor Ads` table via pyairtable (~20s for 166 records)
4. Saves a JSON backup to `--output`

After completion, summarize what was scraped:
- Number of ads per competitor
- How many have creatives vs missing
- Total records written

---

## File layout
```
CLAUDE.md                  # This file — runtime instructions for Claude Code
README.md                  # User-facing quickstart
.env.example               # Template env vars (placeholders only, no real keys)
requirements.txt           # Python deps
schema.json                # Airtable table schema definition
META_ADS_LIBRARY_API.md    # Self-contained API reference
setup_table.py             # Creates the Competitor Ads table via pyairtable
lib/
  meta_ads.py              # Ad Library API client, page ID resolution, retries
  creative_extractor.py    # Selenium-based snapshot media extraction (parallel)
pull_ads.py                # Main scraper CLI — scrape + write to Airtable
discover_competitors.py    # URL → candidate competitors via Ad Library search
```

## Things to remember
- **Never print the user's keys in chat.** Treat `.env` as secret — never cat/read/display META_ACCESS_TOKEN or AIRTABLE_PAT.
- **Always open `.env` for the user** — never make them search for it.
- **Creative extraction requires Chrome.** On first run, `webdriver-manager` will download the matching ChromeDriver — this takes ~10s.
- **Commercial ads do not return numeric `impressions` or `spend`.** Use `eu_total_reach` as the engagement signal (derive High/Medium/Low tiers from it). Leave `Impressions` blank if EU reach is also missing.
- **Rate limits:** Meta returns 429 with a `Retry-After` header. `meta_ads.py` handles this automatically.
- **Snapshot 500 errors:** Never request `ad_creative_bodies` with pagination — it intermittently causes 500s. The scraper uses all fields in one call (safe for ≤100 ads per page).
