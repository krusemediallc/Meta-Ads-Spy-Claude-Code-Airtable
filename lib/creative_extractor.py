"""Extract image/video URLs from Meta Ad Library snapshot URLs.

The Ad Library API never returns direct creative URLs for commercial ads — you
have to load the `ad_snapshot_url` in a real browser and read the DOM after it
finishes rendering.

Uses Selenium + headless Chrome via webdriver-manager (auto-downloads driver).
Supports parallel extraction via ThreadPoolExecutor for ~4-6x speedup.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


@dataclass
class Creative:
    url: str
    media_type: str  # "image" or "video"


# Cache the driver path so webdriver-manager only downloads once
_driver_path: str | None = None


def _get_driver_path() -> str:
    global _driver_path
    if _driver_path is None:
        _driver_path = ChromeDriverManager().install()
    return _driver_path


def create_driver(headless: bool = True) -> webdriver.Chrome:
    """Build a Chrome driver. webdriver-manager auto-downloads the right version."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    service = Service(_get_driver_path())
    return webdriver.Chrome(service=service, options=options)


def _accept_cookies(driver: webdriver.Chrome) -> None:
    """Click any cookie-banner accept button if present."""
    xpaths = [
        "//button[contains(., 'Allow')]",
        "//button[contains(., 'Accept')]",
        "//button[contains(., 'Agree')]",
        "//*[@data-cookiebanner='accept_button']",
    ]
    for xp in xpaths:
        try:
            btn = driver.find_element(By.XPATH, xp)
            if btn.is_displayed():
                btn.click()
                time.sleep(0.5)
                return
        except Exception:
            continue


def extract_creatives(driver: webdriver.Chrome, snapshot_url: str) -> list[Creative]:
    """Load a snapshot URL and return the ad's image/video URLs."""
    creatives: list[Creative] = []
    seen: set[str] = set()

    try:
        driver.get(snapshot_url)
        time.sleep(2.5)
        _accept_cookies(driver)
        time.sleep(0.5)

        # Images
        for img in driver.find_elements(By.TAG_NAME, "img"):
            try:
                src = img.get_attribute("src") or ""
                if not src or src in seen:
                    continue
                if not any(h in src for h in ("scontent", "fbcdn", "cdninstagram")):
                    continue
                w = int(img.get_attribute("naturalWidth") or 0)
                h = int(img.get_attribute("naturalHeight") or 0)
                if w and h and (w < 200 or h < 200):
                    continue
                seen.add(src)
                creatives.append(Creative(src, "image"))
            except Exception:
                continue

        # Videos
        for v in driver.find_elements(By.TAG_NAME, "video"):
            try:
                src = v.get_attribute("src") or ""
                if not src:
                    for s in v.find_elements(By.TAG_NAME, "source"):
                        s_src = s.get_attribute("src") or ""
                        if s_src:
                            src = s_src
                            break
                if src and src not in seen:
                    seen.add(src)
                    creatives.append(Creative(src, "video"))
            except Exception:
                continue

    except Exception as e:
        print(f"    snapshot error: {e}", flush=True)

    return creatives


def _worker(items: list[tuple[str, str]], headless: bool, worker_id: int, startup_delay: float = 0) -> dict[str, list[Creative]]:
    """Single-worker loop: one driver processes a chunk of (ad_id, snapshot_url) pairs."""
    if startup_delay > 0:
        time.sleep(startup_delay)
    results: dict[str, list[Creative]] = {}
    driver = create_driver(headless=headless)
    try:
        for i, (ad_id, url) in enumerate(items, 1):
            print(f"    [w{worker_id}] {i}/{len(items)} ad {ad_id}", flush=True)
            results[ad_id] = extract_creatives(driver, url)
            time.sleep(0.3)
    finally:
        driver.quit()
    return results


def extract_batch(
    snapshot_urls: list[tuple[str, str]],
    headless: bool = True,
    workers: int = 1,
) -> dict[str, list[Creative]]:
    """Extract creatives for many ads. Set workers > 1 for parallel extraction.

    snapshot_urls: list of (ad_id, snapshot_url) tuples
    returns: {ad_id: [Creative, ...]}
    """
    if workers <= 1:
        return _worker(snapshot_urls, headless, worker_id=1)

    # Split work across workers
    chunks: list[list[tuple[str, str]]] = [[] for _ in range(workers)]
    for i, item in enumerate(snapshot_urls):
        chunks[i % workers].append(item)
    chunks = [c for c in chunks if c]  # drop empties

    print(f"    extracting {len(snapshot_urls)} creatives across {len(chunks)} workers...", flush=True)
    results: dict[str, list[Creative]] = {}
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = {
            pool.submit(_worker, chunk, headless, wid + 1, startup_delay=wid * 2.0): wid
            for wid, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            results.update(future.result())

    return results
