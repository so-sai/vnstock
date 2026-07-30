"""
world_sensor.py — P0.5 World Layer: Fed Policy State (5D Latent).

Three signal blocks:

1. Fed Target Rate & FOMC Decision (CME FedWatch 30-Day FF):
   - Current target rate, next-meeting implied probability of hike/hold/cut.
   
2. FOMC Dissent Tracker:
   - Number of dissenting votes at last meeting (dissent = hawkish/dovish divergence).
   - Higher dissent → higher FED_UNCERTAINTY score.

3. QT / Balance Sheet Impulse:
   - Change in Fed total assets (weekly H.4.1 release).
   - Negative change = QT drain → GLOBAL_LIQUIDITY contraction.

Architecture:
  WorldSensor polls upstream data sources and caches results.
  Output dict feeds into CausalGraph edges (8 World→Vietnam edges).
  This is a stateless sensor — fresh fetch on each call, cache within TTL.

# ===================================================================
# ADR #8 — WHY World Layer (P0.5) is separate from P0?
# ===================================================================
# Fed policy is a SIGNAL GENERATOR, not a deterministic Vietnam macro
# driver. The 5D latent state quantifies "what the Fed is doing" —
# it must pass through TRANSMISSION edges (DXY, US10Y, Global Risk,
# Global Liquidity) before affecting Vietnam. This prevents the
# logical fallacy "Fed hawk → Vietnam market bad" by forcing every
# Fed signal through measurable intermediate variables.
# ===================================================================
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ===================================================================
# WORLD SENSOR DATA LINKS & API ENDPOINTS REGISTRY
# ===================================================================
# WHY registry format: Centralize every upstream URL in one place so
# that data-source breakage (URL changes, API deprecations) is visible
# at a glance. Each endpoint has a documented fallback strategy.

# 1. CME FEDWATCH TOOL (30-day Fed Funds Futures Probability)
#    Used for: implied Fed rate, next meeting hike/cut probabilities
#    Fallback: hardcoded DEFAULT_FED_RATE if scrape fails
CME_FEDWATCH_URL = (
    "https://www.cmegroup.com/markets/interest-rates/"
    "cme-fedwatch-tool.html"
)

# 2. FEDERAL RESERVE BOARD (FOMC Statements & Dissent Voting)
#    Used for: dissenting vote count at last FOMC meeting
#    Fallback: DEFAULT_DISSENT (0) if scrape fails
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_STATEMENT_BASE_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary"
FOMC_HISTORY_URL = "https://www.federalreserve.gov/monetarypolicy/fomc_historical.htm"

# 3. ST. LOUIS FRED API (Federal Reserve Economic Data - JSON Endpoints)
#    WHY FRED over yFinance for US macro? FRED is the canonical source for
#    US economic data (Fed balance sheet, reserves, yields). yFinance is
#    a backup when FRED API key is unavailable or rate-limited.
#    Fallback chain: FRED API → yFinance tickers → CACHE → defaults
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_MAP = {
    "FED_ASSETS": "WALCL",        # Total Assets of Federal Reserve (QT/QE)
    "RESERVES": "WRESBAL",        # Reserve Balances with Federal Reserve Banks
    "US10Y": "DGS10",             # 10-Year Treasury Constant Maturity Rate
    "USD_INDEX": "DTWEXBGS",      # Nominal Broad U.S. Dollar Index
}

# 4. YAHOO FINANCE (Backup Realtime Tickers)
#    WHY yFinance fallback? FRED data is published with 1-day lag (business
#    days). For same-day EOD runs, yFinance provides real-time/close prices.
#    These are NOT the primary source — they validate/correct FRED stale data.
YFINANCE_WORLD_TICKERS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "BRENT": "BZ=F",
}

# ── Defaults (when all upstream sources unavailable) ──────────────────

DEFAULT_FED_RATE = 5.50
DEFAULT_DISSENT = 0
DEFAULT_FRED_VALUE = 0.0
CACHE_TTL_SECONDS = 3600  # 1 hour
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "world_cache"
CACHE_FILE = CACHE_DIR / "fed_policy_cache.json"

# ── FRED API key (optional, from env) ─────────────────────────────────

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")


# ── Helpers ─────────────────────────────────────────────────────────────


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _read_cache() -> Optional[dict]:
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            age = time.time() - data.get("cached_at", 0)
            if age < CACHE_TTL_SECONDS:
                return data
    except Exception:
        pass
    return None


def _write_cache(data: dict):
    try:
        _ensure_cache_dir()
        data["cached_at"] = time.time()
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"World cache write failed: {e}")


# ── Block 1: CME FedWatch (30-Day FF Futures) ─────────────────────────


def _fetch_cme_fedwatch() -> tuple[float, float, str]:
    """Parse CME FedWatch 30-Day FF probability for next meeting.

    Returns:
        (implied_rate, probability_of_hike, next_meeting_label)
        On failure: (default, 0.0, "unknown")
    """
    try:
        headers = _browser_headers()
        resp = requests.get(CME_FEDWATCH_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # WHY __NEXT_DATA__ scraping instead of CME API? CME does not expose
        # a public REST API for FedWatch. The __NEXT_DATA__ script tag contains
        # the full page state as JSON. This is the same technique used by
        # professional quant shops and is more reliable than HTML table parsing.
        # If CME changes their frontend framework, the cache TTL (1h) gives
        # us time to detect breakage without blocking the EOD pipeline.
        import re
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>'
            r"(.*?)</script>",
            html, re.DOTALL,
        )
        if not match:
            logger.warning("CME FedWatch: __NEXT_DATA__ script not found")
            return (DEFAULT_FED_RATE, 0.0, "unknown")

        payload = json.loads(match.group(1))
        props = payload.get("props", {})
        page_props = props.get("pageProps", {})
        data = (
            page_props.get("initialState")
            or page_props.get("dehydratedState")
            or page_props.get("__NEXT_DATA__")
            or {}
        )
        implied_rate = DEFAULT_FED_RATE
        hike_prob = 0.0
        meeting_label = "unknown"

        try:
            contracts = (
                data.get("quotes", {}).get("quotes", [])
            ) or (
                data.get("contracts", [])
            ) or []
            if not contracts:
                products = data.get("products", [])
                for p in products:
                    if "30 Day Federal Funds" in p.get("name", ""):
                        contracts = p.get("contracts", [])
                        break

            if contracts:
                sorted_cts = sorted(
                    contracts, key=lambda c: c.get("expiration", "ZZZZ")
                )
                nearest = sorted_cts[0]
                implied_rate = float(nearest.get("last", implied_rate))
                hike_prob = float(nearest.get("probability", 0.0))
                meeting_label = nearest.get("tradeDate", "unknown")
        except Exception as inner:
            logger.debug(f"CME contract parse: {inner}")

        return (implied_rate, hike_prob, meeting_label)

    except Exception as e:
        logger.warning(f"CME FedWatch fetch failed: {e}")
        return (DEFAULT_FED_RATE, 0.0, "unknown")


def _browser_headers() -> dict:
    """Standard browser-like headers for HTML scraping endpoints."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }


# ── Block 2: FOMC Dissent ────────────────────────────────────────────


def _fetch_fomc_dissent() -> int:
    """Scrape FOMC historical page for dissent count at last meeting.

    WHY scrape dissent instead of tracking FOMC statements?
    The FOMC historical page tabulates ALL meetings in one table,
    making dissent extraction a single request. Parsing individual
    statements would require 6+ requests per year.
    FOMC_HISTORY_URL is the canonical Fed source.

    Returns:
        Number of dissenting votes (0 if unavailable).
    """
    try:
        headers = _browser_headers()
        resp = requests.get(FOMC_HISTORY_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text

        import re
        rows = re.findall(
            r"<tr[^>]*>.*?<td[^>]*>(.*?)</td>.*?</tr>",
            html, re.DOTALL,
        )
        dissent_count = 0
        for row in rows[:10]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 4:
                vote_cell = cells[3] if len(cells) > 3 else ""
                dissent_count = vote_cell.lower().count("no")
                if dissent_count > 0:
                    break

        return dissent_count

    except Exception as e:
        logger.warning(f"FOMC dissent fetch failed: {e}")
        return DEFAULT_DISSENT


# ── Block 3: FRED API (multi-series) ──────────────────────────────────


def _fetch_fred_series(series_id: str) -> Optional[float]:
    """Fetch the latest observation for a FRED series.

    WHY use FRED API instead of scraping FRED pages?
    FRED provides a first-class JSON API (api.stlouisfed.org).
    No HTML parsing needed — faster, more stable, and returns
    structured data. Requires FRED_API_KEY env var (free tier).

    Args:
        series_id: FRED series ID (e.g. 'WALCL', 'DGS10').

    Returns:
        Float value, or None if unavailable.
    """
    if not FRED_API_KEY:
        return None

    try:
        url = (
            f"{FRED_BASE_URL}?series_id={series_id}"
            f"&sort_order=desc&limit=1&file_type=json&api_key={FRED_API_KEY}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations", [])
        if obs:
            val = obs[0].get("value", ".")
            if val and val != ".":
                return float(val)
        return None
    except Exception as e:
        logger.warning(f"FRED series {series_id} fetch failed: {e}")
        return None


def _fetch_fred_all() -> dict:
    """Fetch ALL configured FRED series in parallel.

    Returns:
        dict with keys matching FRED_SERIES_MAP, values are floats or None.
        Keys: 'WALCL', 'WRESBAL', 'DGS10', 'DTWEXBGS'
    """
    import concurrent.futures

    results: dict[str, Optional[float]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(_fetch_fred_series, sid): name
            for name, sid in FRED_SERIES_MAP.items()
        }
        for future in concurrent.futures.as_completed(future_map):
            name = future_map[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = None
    return results


def _scale_fred_asset(value: Optional[float]) -> float:
    """Scale WALCL (millions) → trillions. Returns 0.0 on None."""
    if value is None:
        return DEFAULT_FRED_VALUE
    return round(value / 1_000_000_000, 2)


def _scale_fred_reserves(value: Optional[float]) -> float:
    """Scale WRESBAL (billions) → trillions. Returns 0.0 on None."""
    if value is None:
        return DEFAULT_FRED_VALUE
    return round(value / 1_000_000_000, 2)


# ── Block 4: Yahoo Finance Fallback ───────────────────────────────────


def _fetch_yfinance_fallback() -> dict:
    """Fetch DXY, US10Y, BRENT from Yahoo Finance as FRED backup.

    WHY yFinance as fallback? FRED publishes with 1 business day lag.
    For same-day EOD runs, yFinance provides T data where FRED shows T-1.
    This is a SECONDARY source — FRED values override when available.

    Returns:
        dict with keys 'DXY', 'US10Y', 'BRENT' (floats), or 0.0 on failure.
    """
    results: dict[str, float] = {}
    try:
        import yfinance as yf

        for name, ticker in YFINANCE_WORLD_TICKERS.items():
            try:
                df = yf.download(ticker, period="2d", interval="1d", progress=False)
                if df is not None and not df.empty and "Close" in df.columns:
                    val = float(df["Close"].values[-1].item())
                    results[name] = val if not (val != val) else 0.0
                else:
                    results[name] = 0.0
            except Exception:
                results[name] = 0.0
    except ImportError:
        logger.debug("yfinance not installed — skipping Yahoo fallback")
        for name in YFINANCE_WORLD_TICKERS:
            results[name] = 0.0
    except Exception as e:
        logger.warning(f"yFinance bulk fetch failed: {e}")
        for name in YFINANCE_WORLD_TICKERS:
            results[name] = 0.0
    return results


# ── WorldSensor ─────────────────────────────────────────────────────────


class WorldSensor:
    """P0.5 World Layer: Fed Policy State (5D Latent).

    Usage:
        sensor = WorldSensor()
        state = sensor.fetch()
        # state = {
        #   "fed_target_rate": 5.50,
        #   "fomc_dissent": 0,
        #   "qt_balance_tr": 7.23,
        #   "reserves_tr": 3.10,
        #   "us10y_yield": 4.20,
        #   "usd_index": 120.5,
        #   "brent_oil": 85.0,
        #   "fed_uncertainty": 0.05,
        #   "next_meeting": "2026-09-17",
        #   "implied_hike_prob": 0.35,
        #   "timestamp": "...",
        # }
    """

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache

    def fetch(self, force_refresh: bool = False) -> dict:
        """Fetch full Fed policy state.

        WHY 1h TTL cache: Fed policy changes infrequently (FOMC every 6 weeks,
        balance sheet weekly). Polling upstream every call wastes bandwidth and
        risks rate-limiting. 1h is short enough for same-day EOD runs.

        WHY multi-source fallback chain:
          FRED (canonical, T-1 lag) → yFinance (real-time, backup) → CACHE → defaults.
          Each layer fills gaps left by the layer above. FRED values always
          override yFinance when both are available (FRED is authoritative).

        Args:
            force_refresh: bypass cache and fetch fresh data.

        Returns:
            dict with keys:
                fed_target_rate   : float — current Fed funds rate
                fomc_dissent      : int — dissenting votes at last meeting
                qt_balance_tr     : float — Fed total assets in trillions
                reserves_tr       : float — Reserve balances in trillions
                us10y_yield       : float — US 10Y Treasury yield %
                usd_index         : float — Nominal Broad USD Index
                brent_oil         : float — Brent crude price
                implied_hike_prob : float — next meeting hike probability
                next_meeting      : str — next FOMC meeting label
                fed_uncertainty   : float — composite uncertainty [0, 1]
                timestamp         : str — ISO UTC
        """
        if self.use_cache and not force_refresh:
            cached = _read_cache()
            if cached is not None:
                return self._enrich(cached)

        # ── Layer 1: CME FedWatch (scraped) & FOMC dissent ──────────
        fed_rate, hike_prob, meeting = _fetch_cme_fedwatch()
        dissent = _fetch_fomc_dissent()

        # ── Layer 2: FRED API (canonical US macro data) ─────────────
        fred = _fetch_fred_all()
        qt_balance = _scale_fred_asset(fred.get("FED_ASSETS"))
        reserves = _scale_fred_reserves(fred.get("RESERVES"))
        us10y = fred.get("US10Y")
        usd_idx = fred.get("USD_INDEX")

        # ── Layer 3: yFinance fallback (fills FRED gaps) ────────────
        yf_data = _fetch_yfinance_fallback()
        # WHY FRED overrides yFinance: FRED is the authoritative source.
        # yFinance only fills in when FRED returns None (stale/no key).
        if us10y is None:
            us10y = yf_data.get("US10Y")
        if usd_idx is None:
            usd_idx = yf_data.get("DXY")
        # Brent is not available via FRED free tier — always use yFinance
        brent = yf_data.get("BRENT", 0.0)

        # ── Composite uncertainty ───────────────────────────────────
        # WHY: fed_uncertainty = dissent/(max_dissent+1) + QT_surprise_factor.
        #      Higher dissent → more uncertainty about future policy path.
        #      This is NOT a directional signal — it measures DISPERSION, not level.
        max_historical_dissent = 4
        dissent_factor = dissent / (max_historical_dissent + 1)
        qt_surprise = max(0.0, (8.0 - qt_balance) / 8.0) if qt_balance > 0 else 0.0
        fed_uncertainty = round(
            min(1.0, dissent_factor * 0.6 + qt_surprise * 0.4), 4
        )

        raw = {
            "fed_target_rate": fed_rate,
            "fomc_dissent": dissent,
            "qt_balance_tr": qt_balance,
            "reserves_tr": reserves,
            "us10y_yield": round(us10y, 4) if us10y is not None else DEFAULT_FRED_VALUE,
            "usd_index": round(usd_idx, 2) if usd_idx is not None else DEFAULT_FRED_VALUE,
            "brent_oil": round(brent, 2),
            "implied_hike_prob": hike_prob,
            "next_meeting": meeting,
            "fed_uncertainty": fed_uncertainty,
        }

        _write_cache(raw)
        return self._enrich(raw)

    @staticmethod
    def _enrich(raw: dict) -> dict:
        """Add timestamp and ensure field presence."""
        raw["timestamp"] = datetime.now(timezone.utc).isoformat()
        raw.setdefault("fed_target_rate", DEFAULT_FED_RATE)
        raw.setdefault("fomc_dissent", DEFAULT_DISSENT)
        raw.setdefault("qt_balance_tr", DEFAULT_FRED_VALUE)
        raw.setdefault("reserves_tr", DEFAULT_FRED_VALUE)
        raw.setdefault("us10y_yield", DEFAULT_FRED_VALUE)
        raw.setdefault("usd_index", DEFAULT_FRED_VALUE)
        raw.setdefault("brent_oil", DEFAULT_FRED_VALUE)
        raw.setdefault("implied_hike_prob", 0.0)
        raw.setdefault("next_meeting", "unknown")
        raw.setdefault("fed_uncertainty", 0.0)
        return raw

    def status(self) -> dict:
        """Return latest cached state or fallback defaults."""
        cached = _read_cache()
        if cached:
            return self._enrich(cached)
        return self._enrich({
            "fed_target_rate": DEFAULT_FED_RATE,
            "fomc_dissent": DEFAULT_DISSENT,
            "qt_balance_tr": DEFAULT_FRED_VALUE,
            "reserves_tr": DEFAULT_FRED_VALUE,
            "us10y_yield": DEFAULT_FRED_VALUE,
            "usd_index": DEFAULT_FRED_VALUE,
            "brent_oil": DEFAULT_FRED_VALUE,
            "implied_hike_prob": 0.0,
            "next_meeting": "unknown",
            "fed_uncertainty": 0.0,
        })
