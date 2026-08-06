# -*- coding: utf-8 -*-
"""
cme_fedwatch_playwright.py — CME FedWatch crawler using Playwright headless.

WHY Playwright (2026-08): CME now serves the FedWatch tool behind a WAF/CDN
that returns HTTP 403 to plain `requests`/`curl` (TLS fingerprint + JS
challenge). Playwright drives a real Chromium so the WAF sees a genuine
browser fingerprint and serves the DOM. This module is the LAST-RESORT layer:
world_sensor `_fetch_cme_fedwatch()` tries cheap `requests` first, and only
falls back here when the response is 403 / missing __NEXT_DATA__.

Design decisions (mirrors cafef_crawler._try_cafef_pw):
  1. async_playwright + Chromium headless (not `requests`) — WAF bypass.
  2. Block image/css/font/media via page.route — CME page is ~5MB of junk
     otherwise; we only need the `.cmeTable` DOM. Cuts RAM to ~180MB.
  3. `.cmeTable` selector: the FedWatch tool renders probabilities into a
     table with this class. Wait for it so JS render completes before read.
  4. `finally: await browser.close()` — no leaked Chromium processes
     (a known cause of Windows memory creep in long-running EOD cronjobs).
  5. ModuleNotFoundError rescue — Playwright is optional; EOD pipeline must
     never crash just because the module isn't installed.
  6. Sync wrapper `fetch_cme_fedwatch()` so callers (world_sensor, tests)
     don't need asyncio plumbing.

Returns (implied_rate, hike_probability, next_meeting_label, source_tag)
matching world_sensor contract; on any failure returns DEFAULT tuple.
"""

import asyncio
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# ── Fallback defaults (match world_sensor.py) ─────────────────────────
DEFAULT_FED_RATE = 5.50
DEFAULT_HIKE_PROB = 0.0
DEFAULT_MEETING = "unknown"
SOURCE_TAG = "cme_pw"  # provenance: data came from Playwright crawl

# CME FedWatch 30-Day FF tool (same URL as world_sensor).
CME_FEDWATCH_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"

# Resource types that add no DOM value for `.cmeTable` scraping.
BLOCKED_RESOURCE_GLOBS = "**/*.{png,jpg,jpeg,svg,css,woff,woff2}"

# ── Windows launch flags (mirrors cafef_crawler; see WHY there) ───────
WINDOWS_LAUNCH_FLAGS = [
    "--disable-gpu",  # Windows 11 black-screen bug (2026-08-01)
    "--no-sandbox",
    "--disable-accelerated-2d-canvas",
    "--no-first-run",
    "--disable-blink-features=AutomationControlled",
]

# Timeouts
NAVIGATION_TIMEOUT_MS = 30_000
TABLE_WAIT_TIMEOUT_MS = 20_000


def _extract_fedwatch_from_table(table_el) -> Tuple[float, float, str]:
    """Parse implied rate + hike prob from the first `.cmeTable` body row.

    CME FedWatch table layout (as of 2026-08):
      header: [Meeting] [Date] [No Change] [Increase] [Decrease]
      row   : [March 2026] [3/18/2026] [92.0%] [8.0%] [0.0%]

    We take the FIRST row (nearest meeting), read the "Increase" column as
    implied hike probability. Target rate is parsed from the first cell via
    a regex on meeting/date text (best-effort; falls back to default).

    Args:
        table_el: Playwright Locator of the `.cmeTable` table.

    Returns:
        (implied_rate, hike_probability, meeting_label)
    """
    try:
        header_cells = table_el.locator("thead tr").first.locator("th,td").all_inner_texts()
        header_idx = [h.strip().lower() for h in header_cells]

        # Find which column is the "Increase" / hike probability.
        hike_col = None
        for i, h in enumerate(header_idx):
            if h in ("increase", "increasing", "hike", "probability of increase"):
                hike_col = i
                break
        # Decrease/cut column is typically 2nd after No Change when Increase absent.
        if hike_col is None:
            for i, h in enumerate(header_idx):
                if h in ("decrease", "cut", "decreasing"):
                    hike_col = i
                    break

        first_row = table_el.locator("tbody tr").first
        if first_row.count() == 0:
            return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)

        cells = first_row.locator("td").all_inner_texts()
        if len(cells) < 3:
            return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)

        meeting_label = cells[0].strip() or DEFAULT_MEETING

        hike_prob = DEFAULT_HIKE_PROB
        if hike_col is not None and hike_col < len(cells):
            raw = cells[hike_col].replace("%", "").replace(",", ".").strip()
            try:
                hike_prob = float(raw) / 100.0
            except ValueError:
                logger.debug(f"CME hike cell not numeric: {cells[hike_col]!r}")

        # Implied target rate — best-effort regex on meeting cell text
        # (e.g. "March 2026", "3/18/2026 4.25-4.50%"). Default if absent.
        import re

        implied_rate = DEFAULT_FED_RATE
        rate_match = re.search(r"([45]\.\d{2})\s*[-–]\s*([45]\.\d{2})", " ".join(cells[:2]))
        if rate_match:
            implied_rate = float(rate_match.group(2))

        return (implied_rate, hike_prob, meeting_label)
    except Exception as e:  # noqa: BLE001 - parse failures are non-blocking, return defaults
        logger.debug(f"CME FedWatch table parse failed: {e}")
        return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)


async def _fetch_cme_fedwatch_async() -> Tuple[float, float, str]:
    """Async core: launch headless Chromium, block junk resources, scrape `.cmeTable`."""
    try:
        from playwright.async_api import async_playwright
    except ImportError, ModuleNotFoundError:
        logger.warning("Playwright chưa cài — CME FedWatch Playwright crawl bị bỏ qua")
        return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                channel="chrome",
                args=WINDOWS_LAUNCH_FLAGS,
            )
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
            page = await ctx.new_page()

            # Block junk resources — we only need the `.cmeTable` DOM.
            await page.route(
                BLOCKED_RESOURCE_GLOBS,
                lambda route: route.abort(),
            )

            resp = await page.goto(
                CME_FEDWATCH_URL,
                timeout=NAVIGATION_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )
            if resp is not None and resp.status != 200:
                logger.warning(f"CME FedWatch PW HTTP {resp.status}")
                return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)

            # Wait for the probabilities table to render (JS SPA).
            try:
                await page.wait_for_selector(".cmeTable", timeout=TABLE_WAIT_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - no table -> non-blocking default
                logger.warning("CME FedWatch: .cmeTable not found after render wait")
                return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)

            table = page.locator(".cmeTable").first
            result = _extract_fedwatch_from_table(table)
            logger.info(f"CME FedWatch (Playwright): {result}")
            return result
    except asyncio.TimeoutError:
        logger.warning("CME FedWatch PW: navigation timeout")
        return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)
    except Exception as e:  # noqa: BLE001 - WAF/crawl failures are non-blocking
        logger.warning(f"CME FedWatch PW fetch failed: {e}")
        return (DEFAULT_FED_RATE, DEFAULT_HIKE_PROB, DEFAULT_MEETING)
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001, S110 - best-effort cleanup, browser may already be gone
                pass


def fetch_cme_fedwatch() -> Tuple[float, float, str, str]:
    """Sync entry point used by world_sensor + tests.

    Returns:
        (implied_rate, hike_probability, next_meeting_label, source_tag)
        source_tag = "cme_pw" on success, "default" on failure.
    """
    try:
        rate, prob, meeting = asyncio.run(_fetch_cme_fedwatch_async())
    except RuntimeError as e:
        # Caller already inside an event loop (e.g. some test runners) —
        # fall back to a manual run via asyncio.new_event_loop.
        logger.debug(f"asyncio.run blocked ({e}); using new event loop")
        loop = asyncio.new_event_loop()
        try:
            rate, prob, meeting = loop.run_until_complete(_fetch_cme_fedwatch_async())
        finally:
            loop.close()

    if meeting == DEFAULT_MEETING and prob == DEFAULT_HIKE_PROB:
        return (rate, prob, meeting, "default")
    return (rate, prob, meeting, SOURCE_TAG)
