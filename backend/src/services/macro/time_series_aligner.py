"""
time_series_aligner.py — Module 1: Causal-Preserving Time Alignment.

Architecture: PTD Layer 0 (Data Infrastructure)
Alignment rule: US(t-1) close → VN(t) close
  US close 21:00 UTC day t-1  →  VN close 08:00 UTC day t
  Lead-lag ≈ 11h (causal, information flows US → VN)

Pairwise deletion for holidays:
  If US holiday → drop row entirely from correlation window.
  No forward-fill for PTD (stale_flag only for governor display).
"""

import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
logger = logging.getLogger(__name__)


# ── Holiday Calendars ──────────────────────────────────────────────────

# NYSE: observed date (Mon-Fri), shifted from fixed if falls on weekend
_NYSE_RULES = {
    "new_year": (1, 1),
    "mlk_day": (1, "third_mon"),
    "presidents_day": (2, "third_mon"),
    "memorial_day": (5, "last_mon"),
    "juneteenth": (6, 19),
    "independence_day": (7, 4),
    "labor_day": (9, "first_mon"),
    "thanksgiving": (11, "fourth_thu"),
    "christmas": (12, 25),
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth occurrence of weekday (0=Mon) in month. n=1 → first, n=-1 → last."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    last_day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed_holiday(d: date) -> date:
    """If d falls on Sat → Fri before; Sun → Mon after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _generate_nyse_holidays(start_year: int, end_year: int) -> set[date]:
    """Generate NYSE holiday dates for year range."""
    holidays = set()
    for y in range(start_year, end_year + 1):
        raw = []
        raw.append(date(y, 1, 1))  # NY
        raw.append(_nth_weekday(y, 1, 0, 3))  # MLK
        raw.append(_nth_weekday(y, 2, 0, 3))  # Presidents
        raw.append(_nth_weekday(y, 5, 0, -1))  # Memorial
        raw.append(date(y, 6, 19))  # Juneteenth
        raw.append(date(y, 7, 4))  # Independence
        raw.append(_nth_weekday(y, 9, 0, 1))  # Labor
        raw.append(_nth_weekday(y, 11, 3, 4))  # Thanksgiving
        raw.append(date(y, 12, 25))  # Xmas
        holidays.update(_observed_holiday(d) for d in raw)
    return holidays


def _load_vn_holidays() -> set[date]:
    """Load VN holidays from config json."""
    holidays = set()
    cal_path = PROJECT_ROOT / "backend" / "src" / "config" / "weekend_holidays.json"
    if not cal_path.exists():
        cal_path = Path(__file__).resolve().parent.parent.parent / "config" / "weekend_holidays.json"
    if cal_path.exists():
        try:
            with open(cal_path) as f:
                data = json.load(f)
            for h in data.get("holidays", []):
                holidays.add(date.fromisoformat(h))
        except Exception as e:
            logger.warning(f"Cannot load VN holiday calendar: {e}")
    return holidays


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


# ── US Session Calendar ───────────────────────────────────────────────

class USSessionCalendar:
    """NYSE trading days: Mon-Fri excluding holidays, shifted for observance."""

    def __init__(self, start_year: int = 2015, end_year: Optional[int] = None):
        self.end_year = end_year or date.today().year + 1
        self._holidays = _generate_nyse_holidays(start_year, self.end_year)

    def is_trading_day(self, d: date) -> bool:
        return not _is_weekend(d) and d not in self._holidays

    def prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d


# ── VN Session Calendar ───────────────────────────────────────────────

class VNSessionCalendar:
    """HOSE trading days: Mon-Fri excluding VN holidays + weekends."""

    def __init__(self, start_year: int = 2015, end_year: Optional[int] = None):
        self.end_year = end_year or date.today().year + 1
        self._holidays = _load_vn_holidays()

    def is_trading_day(self, d: date) -> bool:
        return not _is_weekend(d) and d not in self._holidays


# ── Business Date Tagging ─────────────────────────────────────────────

def _tag_business_date_us(df: pd.DataFrame) -> pd.DataFrame:
    """Tag US market data with business_date (trading day)."""
    dat = df.copy()
    parsed = pd.to_datetime(dat["date"])
    return dat.assign(
        _date=parsed.dt.date,
        business_date=parsed.dt.date,
        market="US",
        utc_offset_hours=-4,
        session_end_utc=parsed + pd.Timedelta(hours=20),
    ).drop(columns=["_date"])


def _tag_business_date_vn(df: pd.DataFrame) -> pd.DataFrame:
    """Tag VN market data with business_date (trading day)."""
    dat = df.copy()
    parsed = pd.to_datetime(dat["date"])
    return dat.assign(
        _date=parsed.dt.date,
        business_date=parsed.dt.date,
        market="VN",
        utc_offset_hours=7,
        session_end_utc=parsed + pd.Timedelta(hours=8),
    ).drop(columns=["_date"])


# ── Main Aligner ──────────────────────────────────────────────────────

class TimeSeriesAligner:
    """
    Causal-preserving cross-market time alignment.

    Architecture:
      Input: US daily close (day t-1), VN daily close (day t)
      Output: Aligned DataFrame keyed by VN business_date
      Holiday: Pairwise deletion (no forward-fill for correlation)

    Usage:
        aligner = TimeSeriesAligner()
        aligned = aligner.align(us_prices, vn_prices)
        corr_window = aligner.get_correlation_window(aligned, window=90)
    """

    def __init__(self):
        self.us_cal = USSessionCalendar()
        self.vn_cal = VNSessionCalendar()

    def align(self, us_data: pd.DataFrame, vn_data: pd.DataFrame,
               futures_data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Align US(t-1) close → VN(t) close with optional futures fallback.

        When US holiday creates gap, ES futures proxy fills the missing US
        close if `futures_data` is provided. Marked as futures_proxy=True.

        Parameters
        ----------
        us_data : DataFrame
            Columns: ['date', 'value'] — daily US close
        vn_data : DataFrame
            Columns: ['date', 'value'] — daily VN close
        futures_data : DataFrame, optional
            Columns: ['date', 'value'] — daily ES futures close
            Used as fallback when US cash market is on holiday.

        Returns
        -------
        DataFrame with columns:
            business_date  : VN trading date
            us_close       : US price from prev trading day (or futures proxy)
            vn_close       : VN price from this trading day
            us_source_date : actual US date used
            lead_lag_hours : hours between the two closes
            stale_flag     : True if US data was forward-filled (holiday)
            futures_proxy  : True if US data came from futures fallback
        """
        us = _tag_business_date_us(us_data).rename(columns={"value": "us_close"})
        vn = _tag_business_date_vn(vn_data).rename(columns={"value": "vn_close"})

        # Prepare futures fallback data
        futures_lookup = {}
        if futures_data is not None:
            fd = futures_data.assign(
                _date=pd.to_datetime(futures_data["date"]).dt.date
            )
            for _, r in fd.iterrows():
                futures_lookup[r["_date"]] = r["value"]

        us = us.assign(
            us_business_date=us["business_date"],
            us_source_date=us["date"],
            vn_business_date=us["business_date"].apply(self._next_vn_trading_day),
        )

        us = us.dropna(subset=["vn_business_date"])

        # Merge US data with VN data
        aligned = vn[["business_date", "vn_close", "session_end_utc"]].merge(
            us[["vn_business_date", "us_close", "us_source_date", "session_end_utc"]],
            left_on="business_date",
            right_on="vn_business_date",
            how="inner",
            suffixes=("_vn", "_us"),
        )

        aligned = aligned.rename(columns={
            "session_end_utc_vn": "vn_close_utc",
            "session_end_utc_us": "us_close_utc",
        })

        # Tag futures proxy rows and fill US close with futures data
        proxy_flags = []
        for _, r in aligned.iterrows():
            src_d = pd.to_datetime(r["us_source_date"]).date() if isinstance(r["us_source_date"], str) else r["us_source_date"]
            if isinstance(src_d, str):
                src_d = pd.to_datetime(src_d).date()
            is_holiday = not self.us_cal.is_trading_day(src_d)
            proxy_flags.append(is_holiday and futures_data is not None)

        aligned = aligned.assign(
            lead_lag_hours=(
                pd.to_datetime(aligned["vn_close_utc"])
                - pd.to_datetime(aligned["us_close_utc"])
            ).dt.total_seconds() / 3600,
            stale_flag=False,
            futures_proxy=proxy_flags,
        )

        # Fill US close from futures for holiday rows
        if futures_data is not None:
            for idx, r in aligned.iterrows():
                if r["futures_proxy"]:
                    src_d = pd.to_datetime(r["us_source_date"]).date()
                    if src_d in futures_lookup and futures_lookup[src_d] is not None:
                        aligned.loc[idx, "us_close"] = futures_lookup[src_d]

        aligned = aligned.sort_values("business_date").reset_index(drop=True)

        result = aligned[[
            "business_date", "us_close", "vn_close",
            "us_source_date", "lead_lag_hours", "stale_flag", "futures_proxy"
        ]].copy()
        result = result.assign(business_date=pd.to_datetime(result["business_date"]))
        return result

    def align_multi_asset(self, asset_data: dict[str, pd.DataFrame],
                           vn_data: pd.DataFrame) -> pd.DataFrame:
        """
        Align multiple US-traded assets with VN.

        Parameters
        ----------
        asset_data : dict of {asset_name: DataFrame with ['date','value']}
        vn_data : DataFrame with ['date','value']

        Returns
        -------
        Wide-format DataFrame: business_date | vn_close | asset1 | asset2 | ...
        """
        base = None
        for name, df in asset_data.items():
            aligned = self.align(df, vn_data)
            aligned = aligned.rename(columns={"us_close": name}).drop(
                columns=["us_source_date", "lead_lag_hours", "stale_flag", "vn_close"],
                errors="ignore"
            )
            if base is None:
                base = aligned
            else:
                base = base.merge(aligned, on="business_date", how="inner")
        # Add vn_close back
        vn_aligned = self.align(list(asset_data.values())[0], vn_data) if asset_data else None
        if vn_aligned is not None:
            vn_part = vn_aligned[["business_date", "vn_close"]].copy()
            base = base.merge(vn_part, on="business_date", how="left")
        return base

    def get_correlation_window(self, aligned: pd.DataFrame,
                                window: int = 90) -> pd.DataFrame:
        """
        Extract pairwise-deletion window for correlation computation.

        Drops rows where either market was on holiday AND no futures proxy.
        Rows with futures_proxy=True are kept (continuous computation).
        Rows with stale_flag=True (no proxy available) are dropped.

        Returns clean DataFrame with no missing values.
        """
        clean = aligned.dropna(subset=["us_close", "vn_close"])
        # Drop only if stale AND no futures proxy
        if "futures_proxy" in clean.columns:
            clean = clean[~clean["stale_flag"] | clean["futures_proxy"]]
        else:
            # Fallback: check trading day status
            clean = clean[clean.apply(
                lambda r: self.us_cal.is_trading_day(
                    pd.to_datetime(r["us_source_date"]).date()
                ), axis=1
            )]
        return clean.tail(window).reset_index(drop=True)

    def _next_vn_trading_day(self, d) -> Optional[date]:
        """Find next VN trading day after US close (t-1 close → t open)."""
        if isinstance(d, str):
            d = date.fromisoformat(d)
        candidate = d
        for _ in range(5):
            candidate += timedelta(days=1)
            if self.vn_cal.is_trading_day(candidate):
                return candidate
        return None

    def compute_correlation_matrix(self, aligned: pd.DataFrame,
                                    assets: list[str],
                                    window: int = 90,
                                    method: str = "spearman") -> np.ndarray:
        """
        Compute rolling correlation matrix for aligned multi-asset data.

        Parameters
        ----------
        aligned : DataFrame from align_multi_asset
        assets : list of column names to include
        window : rolling window in trading days
        method : 'pearson' or 'spearman'

        Returns
        -------
        Correlation matrix (n_assets × n_assets)
        """
        window_data = aligned[assets].dropna().tail(window)
        n = len(window_data)
        if n < window * 0.5:
            logger.warning(f"Correlation window too sparse: {n}/{window} rows")
            return np.zeros((len(assets), len(assets)))
        return window_data.corr(method=method).values

    def compute_eigenvalues(self, corr_matrix: np.ndarray) -> dict:
        """
        Compute eigenvalue spectrum of correlation matrix.

        Returns
        -------
        Dict with:
            eigenvalues: sorted descending
            eigenvector_max: eigenvector for largest eigenvalue
            lambda_max: largest eigenvalue
            lambda_ratio: λ_max / λ_min
            spectral_entropy: entropy of normalized eigenvalues
            effective_rank: number of components explaining 95% variance
        """
        eigenvalues, eigenvectors = np.linalg.eigh(corr_matrix)
        eigenvalues = np.sort(eigenvalues)[::-1]
        idx_max = np.argmax(np.abs(eigenvalues))
        lambda_max = float(eigenvalues[idx_max])
        lambda_min = float(eigenvalues[-1])

        if len(eigenvalues) > 1:
            lambda_ratio = lambda_max / max(lambda_min, 1e-10)
        else:
            lambda_ratio = 1.0

        normalized = eigenvalues / max(np.sum(eigenvalues), 1e-10)
        spectral_entropy = -np.sum(normalized * np.log(normalized + 1e-10))
        cumsum = np.cumsum(eigenvalues)
        cumsum /= max(cumsum[-1], 1e-10)
        effective_rank = int(np.sum(cumsum < 0.95)) + 1

        max_eigvec = eigenvectors[:, -1]
        max_eigvec = max_eigvec / np.linalg.norm(max_eigvec)

        return {
            "eigenvalues": eigenvalues.tolist(),
            "eigenvector_max": max_eigvec.tolist(),
            "lambda_max": round(lambda_max, 4),
            "lambda_min": round(lambda_min, 4),
            "lambda_ratio": round(lambda_ratio, 4),
            "spectral_entropy": round(spectral_entropy, 4),
            "effective_rank": effective_rank,
        }

    def eigenvalue_spread(self, corr_matrix: np.ndarray) -> dict:
        """
        Quick summary for governor: systemic stress level.

        Rules (from architectural design):
          λ_max > 0.6 + ratio > 5.0 → systemic component dominant
          λ_max < 0.3 + ratio < 2.0 → market dispersion, no dominant factor
          spectral_entropy < 0.5 → very concentrated (single factor)
          spectral_entropy > 0.8 → highly distributed (no dominant factor)
        """
        ev = self.compute_eigenvalues(corr_matrix)
        n = len(ev["eigenvalues"])

        if ev["lambda_max"] > 0.6 and ev["lambda_ratio"] > 5.0:
            stress = "SYSTEMIC"
        elif ev["lambda_max"] > 0.4 or ev["lambda_ratio"] > 3.0:
            stress = "ELEVATED"
        elif ev["spectral_entropy"] > 0.8:
            stress = "DISPERSED"
        else:
            stress = "NORMAL"

        return {
            "lambda_max": ev["lambda_max"],
            "lambda_ratio": ev["lambda_ratio"],
            "spectral_entropy": ev["spectral_entropy"],
            "effective_rank": ev["effective_rank"],
            "stress_level": stress,
            "n_assets": n,
        }
