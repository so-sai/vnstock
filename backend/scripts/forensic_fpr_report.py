"""forensic_fpr_report.py — False Positive Rate measurement for ForensicEngine on VN30.

Measures the rate at which ForensicEngine flags clean blue-chip (VN30) symbols
as fraudulent, ensuring the Hard VETO mechanism will not accidentally block
legitimate asset-expansion companies.

Methodology
-----------
1. Universe: VN30 members present in financial_facts.db.
2. Ground-truth proxy: VN30 = presumed-clean large-cap basket
   (no VN30 member has been convicted of financial fraud at index composition).
   Any flag on VN30 = candidate false positive.
3. Synthetic-period exclusion: periods where cross-symbol RECEIVABLES std dev
   is zero (identical values across unrelated symbols) are demo/sample fallback
   rows from `_get_sample_standard` and are excluded from the clean measurement.
4. Flags measured: Hard_Violation (absolute VETO), M_Score_Flag (soft warning),
   Risk_Score > 0.5 (aggressive penalty).
5. FPR = flagged / evaluated at each threshold.
6. Two views reported: latest-period (what Governor cache serves) and full
   point-in-time history (all periods, all symbols).
"""

import sqlite3
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.financial.financial_facts import FINANCIAL_DB_PATH
from src.financial.forensic_engine import ForensicEngine

VN30_SYMBOLS = sorted(
    {
        "ACB",
        "BCM",
        "BID",
        "CTG",
        "DGC",
        "FPT",
        "GAS",
        "HDB",
        "HPG",
        "LPB",
        "MBB",
        "MSN",
        "MWG",
        "PNJ",
        "SAB",
        "SSI",
        "STB",
        "TCB",
        "VCB",
        "VHM",
        "VIB",
        "VIC",
        "VJC",
        "VNM",
        "VPB",
        "VRE",
    }
)


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or str(FINANCIAL_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _detect_synthetic_periods(frame: pd.DataFrame) -> pd.Series:
    """Return boolean mask: True for rows belonging to synthetic/demo periods.

    Synthetic periods are identified by near-zero cross-symbol variance in
    RECEIVABLES at the same (period, fiscal_year, fiscal_quarter). When all
    symbols report identical RECEIVABLES at a given period, the data comes
    from the demo fallback (`_get_sample_standard`) and is not real crawl data.
    """
    if frame.empty or "RECEIVABLES" not in frame.columns:
        return pd.Series(False, index=frame.index)

    grp_cols = ["period", "fiscal_year", "fiscal_quarter"]
    recv = frame.groupby(grp_cols)["RECEIVABLES"]
    cv = recv.std(ddof=0) / recv.mean().replace(0, np.nan)
    cv = cv.fillna(0.0)
    synthetic_periods = set(cv[cv < 1e-6].index.tolist())

    mask = frame.set_index(grp_cols).index.isin(synthetic_periods)
    return pd.Series(mask, index=frame.index)


def measure_fpr(
    db_path: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    exclude_synthetic: bool = True,
) -> Dict[str, Any]:
    """Run ForensicEngine on the target basket and compute FPR metrics.

    Parameters
    ----------
    db_path : str | None
        Path to financial_facts.db. Defaults to FINANCIAL_DB_PATH.
    symbols : list[str] | None
        Basket to evaluate. Defaults to VN30_SYMBOLS.
    exclude_synthetic : bool
        Drop periods where cross-symbol RECEIVABLES variance is zero
        (demo fallback rows). Default True for clean measurement.

    Returns
    -------
    dict with keys:
      - latest_period: per-symbol latest-period FPR snapshot
      - full_history: all-period FPR breakdown
      - synthetic_excluded: count of rows removed by synthetic filter
      - thresholds: FPR at each flag level
    """
    syms = symbols or VN30_SYMBOLS
    conn = _connect(db_path)
    try:
        frame = ForensicEngine().run(conn)
    finally:
        conn.close()

    if frame.empty:
        return {
            "latest_period": {},
            "full_history": {},
            "synthetic_excluded": 0,
            "thresholds": {},
            "error": "No data in forensic engine output",
        }

    basket = frame[frame["symbol"].isin(syms)].copy()
    if basket.empty:
        return {
            "latest_period": {},
            "full_history": {},
            "synthetic_excluded": 0,
            "thresholds": {},
            "error": f"No VN30 symbols found in engine output (available: {sorted(frame['symbol'].unique())})",
        }

    # ── Identify and exclude synthetic periods ──
    synthetic_mask = _detect_synthetic_periods(basket)
    synthetic_excluded = int(synthetic_mask.sum())
    clean = basket[~synthetic_mask] if exclude_synthetic else basket

    # ── Latest-period view (what Governor cache serves) ──
    latest = clean.sort_values(["symbol", "fiscal_year", "fiscal_quarter"]).groupby("symbol").tail(1)

    def _fpr(series: pd.Series) -> Dict[str, Any]:
        n = len(series)
        if n == 0:
            return {"n": 0, "fpr_pct": 0.0, "flagged": 0}
        flagged = int(series.sum())
        return {"n": n, "flagged": flagged, "fpr_pct": round(flagged / n * 100, 2)}

    latest_hv = _fpr(latest["Hard_Violation"])
    latest_mf = _fpr(latest["M_Score_Flag"])
    latest_rs50 = _fpr(latest["Risk_Score"] > 0.5)
    latest_rs70 = _fpr(latest["Risk_Score"] > 0.7)

    # ── Full point-in-time history ──
    all_hv = _fpr(basket["Hard_Violation"])
    all_mf = _fpr(basket["M_Score_Flag"])
    all_rs50 = _fpr(basket["Risk_Score"] > 0.5)

    # ── Per-symbol latest detail ──
    symbol_details = {}
    for _, row in latest.sort_values("symbol").iterrows():
        symbol_details[row["symbol"]] = {
            "period": row["period"],
            "fiscal_year": int(row["fiscal_year"]),
            "fiscal_quarter": int(row["fiscal_quarter"]),
            "adjacent": bool(row["adjacent"]),
            "m_score_flag": bool(row["M_Score_Flag"]),
            "hard_violation": bool(row["Hard_Violation"]),
            "risk_score": round(float(row["Risk_Score"]), 3),
            "sloan_ratio": round(float(row["Sloan_Ratio"]), 4) if pd.notna(row["Sloan_Ratio"]) else None,
            "ari": round(float(row["ARI"]), 4) if pd.notna(row["ARI"]) else None,
        }

    # ── Synthetic periods removed detail ──
    synthetic_detail = {}
    if exclude_synthetic and synthetic_excluded > 0:
        syn_rows = basket[synthetic_mask]
        for sym in sorted(syn_rows["symbol"].unique()):
            periods = syn_rows[syn_rows["symbol"] == sym]["period"].tolist()
            synthetic_detail[sym] = periods

    return {
        "basket_size": len(syms),
        "symbols_with_data": int(basket["symbol"].nunique()),
        "clean_rows": len(clean),
        "synthetic_excluded": synthetic_excluded,
        "synthetic_detail": synthetic_detail,
        "latest_period": {
            "evaluated": latest_hv["n"],
            "hard_violation": latest_hv,
            "m_score_flag": latest_mf,
            "risk_score_gt_0_5": latest_rs50,
            "risk_score_gt_0_7": latest_rs70,
            "symbol_details": symbol_details,
        },
        "full_history": {
            "evaluated": all_hv["n"],
            "hard_violation": all_hv,
            "m_score_flag": all_mf,
            "risk_score_gt_0_5": all_rs50,
        },
        "thresholds": {
            "hard_violation_fpr_latest_pct": latest_hv["fpr_pct"],
            "m_score_flag_fpr_latest_pct": latest_mf["fpr_pct"],
            "hard_violation_fpr_alltime_pct": all_hv["fpr_pct"],
            "passes_hard_veto_fpr_lt_2pct": latest_hv["fpr_pct"] < 2.0,
            "passes_soft_warning_fpr_lt_10pct": latest_mf["fpr_pct"] < 10.0,
        },
    }


def print_report(report: Optional[Dict[str, Any]] = None) -> None:
    """Pretty-print the FPR report to stdout."""
    if report is None:
        report = measure_fpr()

    print("=" * 72)
    print("  PTCK — ForensicEngine False Positive Rate (FPR) Report")
    print("  Basket: VN30 (presumed-clean large-cap)")
    print("=" * 72)

    latest = report.get("latest_period", {})
    thresholds = report.get("thresholds", {})

    print(f"\n  Symbols with data : {report.get('symbols_with_data', 0)} / {report.get('basket_size', 0)}")
    print(f"  Clean rows        : {report.get('clean_rows', 0)}")
    print(f"  Synthetic excluded: {report.get('synthetic_excluded', 0)}")
    if report.get("synthetic_detail"):
        print("  Synthetic details :")
        for sym, periods in report["synthetic_detail"].items():
            print(f"    {sym}: {', '.join(periods)}")

    print("\n  ── Latest Period (Governor cache view) ──")
    hv = latest.get("hard_violation", {})
    mf = latest.get("m_score_flag", {})
    print(f"  Hard_Violation (VETO): {hv.get('flagged', 0)}/{hv.get('n', 0)} = {hv.get('fpr_pct', 0.0)}%")
    print(f"  M_Score_Flag (soft)  : {mf.get('flagged', 0)}/{mf.get('n', 0)} = {mf.get('fpr_pct', 0.0)}%")

    print("\n  ── Full Point-in-Time History ──")
    ahv = report.get("full_history", {}).get("hard_violation", {})
    amf = report.get("full_history", {}).get("m_score_flag", {})
    print(f"  Hard_Violation (VETO): {ahv.get('flagged', 0)}/{ahv.get('n', 0)} = {ahv.get('fpr_pct', 0.0)}%")
    print(f"  M_Score_Flag (soft)  : {amf.get('flagged', 0)}/{amf.get('n', 0)} = {amf.get('fpr_pct', 0.0)}%")

    print("\n  ── Threshold Gates ──")
    print(f"  FPR Hard-VETO < 2.0%  : {'PASS' if thresholds.get('passes_hard_veto_fpr_lt_2pct') else 'FAIL'}")
    print(f"  FPR Soft-Warn < 10.0% : {'PASS' if thresholds.get('passes_soft_warning_fpr_lt_10pct') else 'FAIL'}")

    print("\n  ── Per-Symbol Detail (latest period) ──")
    for sym, det in sorted(latest.get("symbol_details", {}).items()):
        flag = "⚠️  M_FLAG" if det["m_score_flag"] else "   "
        hv_flag = "🚫 HV" if det["hard_violation"] else "   "
        print(
            f"  {sym:<5} {det['period']:<8} {flag} {hv_flag}  "
            f"Risk={det['risk_score']:.3f}  Sloan={det['sloan_ratio']}  ARI={det['ari']}"
        )

    print("=" * 72)


if __name__ == "__main__":
    print_report()
