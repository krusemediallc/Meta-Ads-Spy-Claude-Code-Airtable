"""Extract image/video URLs from Meta Ad Library snapshot URLs.

Uses Selenium + headless Chrome with:
- Parallel workers via ThreadPoolExecutor
- Smart DOM waits (no fixed sleeps) for faster extraction
- Driver pooling across all ads in one run
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


@dataclass
class Creative:
    url: str
    media_type: str  # "image" or "video"


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
    # Speed tweaks: disable images-initially-off to still get URLs, but skip other heavy resources
    options.add_argument("--blink-settings=imagesEnabled=true")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    service = Service(_get_driver_path())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(20)
    return driver


# JS to check if the DOM has a real creative element loaded.
# Runs faster than Python-side find_elements in a tight wait loop.
_WAIT_JS = """
return (function() {
  var vids = document.querySelectorAll('video');
  for (var i = 0; i < vids.length; i++) {
    var src = vids[i].src || (vids[i].querySelector('source') && vids[i].querySelector('source').src);
    if (src) return true;
  }
  var imgs = document.querySelectorAll('img');
  for (var i = 0; i < imgs.length; i++) {
    var s = imgs[i].src || '';
    if ((s.indexOf('scontent') !== -1 || s.indexOf('fbcdn') !== -1 || s.indexOf('cdninstagram') !== -1)
        && imgs[i].naturalWidth >= 200) {
      return true;
    }
  }
  return false;
})();
"""


def extract_creatives(driver: webdriver.Chrome, snapshot_url: str) -> list[Creative]:
    """Load a snapshot URL and return the ad's image/video URLs.

    Uses a smart WebDriverWait that returns as soon as a creative appears
    in the DOM (usually ~0.5-1.5s), instead of a fixed multi-second sleep.
    """
    creatives: list[Creative] = []
    seen: set[str] = set()

    try:
        driver.get(snapshot_url)
    except Exception as e:
        print(f"    load error: {e}", flush=True)
        return creatives

    # Smart wait: poll the DOM until a real creative element appears (max 6s)
    try:
        WebDriverWait(driver, 6, poll_frequency=0.2).until(
            lambda d: d.execute_script(_WAIT_JS)
        )
    except TimeoutException:
        # Fallback — give it a brief grace period and keep going
        time.sleep(0.5)

    try:
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
        print(f"    DOM parse error: {e}", flush=True)

    return creatives


def _worker(items: list[tuple[str, str]], headless: bool, worker_id: int, startup_delay: float = 0) -> dict[str, list[Creative]]:
    """Single-worker loop: one driver processes a chunk of (ad_id, snapshot_url) pairs."""
    if startup_delay > 0:
        time.sleep(startup_delay)
    results: dict[str, list[Creative]] = {}
    driver = create_driver(headless=headless)
    try:
        for i, (ad_id, url) in enumerate(items, 1):
            if i % 10 == 0 or i == 1 or i == len(items):
                print(f"    [w{worker_id}] {i}/{len(items)} ad {ad_id}", flush=True)
            results[ad_id] = extract_creatives(driver, url)
    finally:
        driver.quit()
    return results


def extract_batch(
    snapshot_urls: list[tuple[str, str]],
    headless: bool = True,
    workers: int = 1,
) -> dict[str, list[Creative]]:
    """Extract creatives for many ads in parallel.

    Drivers are created ONCE per worker and reused across the whole batch —
    no per-page tear-down / rebuild overhead.

    snapshot_urls: list of (ad_id, snapshot_url) tuples
    returns: {ad_id: [Creative, ...]}
    """
    if not snapshot_urls:
        return {}
    if workers <= 1:
        return _worker(snapshot_urls, headless, worker_id=1)

    # Split work across workers (round-robin so slow pages don't all land on one worker)
    chunks: list[list[tuple[str, str]]] = [[] for _ in range(workers)]
    for i, item in enumerate(snapshot_urls):
        chunks[i % workers].append(item)
    chunks = [c for c in chunks if c]

    print(f"    extracting {len(snapshot_urls)} creatives across {len(chunks)} workers...", flush=True)
    results: dict[str, list[Creative]] = {}
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = {
            pool.submit(_worker, chunk, headless, wid + 1, startup_delay=wid * 1.0): wid
            for wid, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            results.update(future.result())

    return results
