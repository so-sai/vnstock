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

# ── Data source URLs ────────────────────────────────────────────────────

CME_FEDWATCH_URL = (
    "https://www.cmegroup.com/markets/interest-rates/"
    "cme-fedwatch-tool.html"
)
FOMC_HISTORY_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomc_historical.htm"
)
FRED_BALANCE_SHEET_API = (
    "https://api.stlouisfed.org/fred/series/observations"
    "?series_id=WALCL&sort_order=desc&limit=2"
    "&file_type=json&api_key="
)

# ── Defaults (when upstream unavailable) ────────────────────────────────

DEFAULT_FED_RATE = 5.50
DEFAULT_DISSENT = 0
DEFAULT_QT_BALANCE = 0.0
CACHE_TTL_SECONDS = 3600  # 1 hour
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "world_cache"
CACHE_FILE = CACHE_DIR / "fed_policy_cache.json"

# ── FRED API key (optional, from env) ──────────────────────────────────

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
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"World cache write failed: {e}")


def _fetch_cme_fedwatch() -> tuple[float, float, str]:
    """Parse CME FedWatch 30-Day FF probability for next meeting.

    Returns:
        (implied_rate, probability_of_hike, next_meeting_label)
        On failure: (default, 0.0, "unknown")
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        resp = requests.get(CME_FEDWATCH_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # CME embeds JSON in a script tag with id="__NEXT_DATA__"
        import re
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>'
            r'(.*?)</script>',
            html, re.DOTALL,
        )
        if not match:
            logger.warning("CME FedWatch: __NEXT_DATA__ script not found")
            return (DEFAULT_FED_RATE, 0.0, "unknown")

        payload = json.loads(match.group(1))
        # Navigate to the pricing data — this path may shift if CME changes their
        # frontend structure. The key is to find `contractDetails` or `quotes`.
        props = payload.get("props", {})
        page_props = props.get("pageProps", {})
        # CME typically nests pricing under `pageProps.initialState` or similar
        # Fallback chain
        data = (
            page_props.get("initialState")
            or page_props.get("dehydratedState")
            or page_props.get("__NEXT_DATA__")
            or {}
        )
        # Extract the 30-day FF rate from the contract with the nearest expiry.
        implied_rate = DEFAULT_FED_RATE
        hike_prob = 0.0
        meeting_label = "unknown"

        # Attempt to find contracts in the data tree
        try:
            contracts = (
                data.get("quotes", {})
                .get("quotes", [])
            ) or (
                data.get("contracts", [])
            ) or []
            if not contracts:
                # Alternative: walk through product page
                products = data.get("products", [])
                for p in products:
                    if "30 Day Federal Funds" in p.get("name", ""):
                        contracts = p.get("contracts", [])
                        break

            if contracts:
                # Sort by expiration, pick nearest
                sorted_cts = sorted(contracts, key=lambda c: c.get("expiration", "ZZZZ"))
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


def _fetch_fomc_dissent() -> int:
    """Scrape FOMC historical page for dissent count at last meeting.

    Returns:
        Number of dissenting votes (0 if unavailable).
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
        }
        resp = requests.get(FOMC_HISTORY_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # Look for the most recent meeting row and count "No" votes
        import re
        # FOMC historical table rows: <tr><td>Date</td><td>Action</td><td>Votes</td>...
        # Dissent = "No" votes typically listed alongside the decision.
        rows = re.findall(
            r'<tr[^>]*>.*?<td[^>]*>(.*?)</td>.*?</tr>',
            html, re.DOTALL,
        )
        dissent_count = 0
        for row in rows[:10]:  # check last 10 meetings
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 4:
                vote_cell = cells[3] if len(cells) > 3 else ""
                dissent_count = vote_cell.lower().count("no")
                if dissent_count > 0:
                    break

        return dissent_count

    except Exception as e:
        logger.warning(f"FOMC dissent fetch failed: {e}")
        return DEFAULT_DISSENT


def _fetch_fred_balance_sheet() -> float:
    """Fetch Fed total assets from FRED API (WALCL series).

    Returns:
        Latest total assets in trillions USD. 0.0 if unavailable.
    """
    if not FRED_API_KEY:
        logger.debug("No FRED_API_KEY set — skipping balance sheet fetch")
        return 0.0

    try:
        url = FRED_BALANCE_SHEET_API + FRED_API_KEY
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations", [])
        if len(obs) >= 1:
            latest = float(obs[0].get("value", 0))
            return round(latest / 1_000_000_000, 2)  # millions → trillions
        return 0.0
    except Exception as e:
        logger.warning(f"FRED balance sheet fetch failed: {e}")
        return DEFAULT_QT_BALANCE


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
        #   "fed_uncertainty": 0.05,
        #   "next_meeting": "2026-09-17",
        #   "timestamp": "...",
        # }
    """

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache

    def fetch(self, force_refresh: bool = False) -> dict:
        """Fetch full Fed policy state.

        Args:
            force_refresh: bypass cache and fetch fresh data.

        Returns:
            dict with keys:
                fed_target_rate   : float — current Fed funds rate
                fomc_dissent      : int — dissenting votes at last meeting
                qt_balance_tr     : float — total assets in trillions
                implied_hike_prob : float — next meeting hike probability
                next_meeting      : str — next FOMC meeting label
                fed_uncertainty   : float — composite uncertainty [0, 1]
                timestamp         : str — ISO UTC
        """
        if self.use_cache and not force_refresh:
            cached = _read_cache()
            if cached is not None:
                return self._enrich(cached)

        # Fresh fetch
        fed_rate, hike_prob, meeting = _fetch_cme_fedwatch()
        dissent = _fetch_fomc_dissent()
        qt_balance = _fetch_fred_balance_sheet()

        # WHY: fed_uncertainty = dissent/(max_dissent+1) + QT_surprise_factor.
        #      Higher dissent → more uncertainty about future policy path.
        #      This is NOT a directional signal — it measures DISPERSION, not level.
        max_historical_dissent = 4  # max dissenting votes in modern FOMC
        dissent_factor = dissent / (max_historical_dissent + 1)

        # Uncertainty: combine dissent and QT surprise (if QT is large negative)
        qt_surprise = max(0.0, (8.0 - qt_balance) / 8.0) if qt_balance > 0 else 0.0
        fed_uncertainty = round(min(1.0, dissent_factor * 0.6 + qt_surprise * 0.4), 4)

        raw = {
            "fed_target_rate": fed_rate,
            "fomc_dissent": dissent,
            "qt_balance_tr": qt_balance,
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
        raw.setdefault("qt_balance_tr", DEFAULT_QT_BALANCE)
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
            "qt_balance_tr": DEFAULT_QT_BALANCE,
            "implied_hike_prob": 0.0,
            "next_meeting": "unknown",
            "fed_uncertainty": 0.0,
        })
