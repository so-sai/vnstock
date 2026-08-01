"""ptck_vn20_builder.py — Dynamic PTCK_VN20 Index Builder.

WHY:
  Builds a self-updating Top-20 stock index (PTCK_VN20) from internal
  financial data, replacing static external benchmark lists.

Architecture (Dual-Index System):
  - PROBE SET (HOSE30): Fixed frozenset of 30 blue-chip symbols for
    Crawler health monitoring and Silent Throttling detection.
  - ALLOCATION SET (PTCK_VN20): Dynamic top-20 rebuilt every Earnings
    Cycle (Monday 08:20) using a 4-tier pipeline.

4-Tier Pipeline:
  1. Health & Valuation Filter — exclude falsifiable / negative CFO stocks
  2. Fingerprint Compilation (LAW-005) — Structural, Behavioural, Outcome
  3. Cluster Compression (LAW-008) — max 3 symbols per sector cluster
  4. Export to SQLite Meta Table + API endpoint

WIN11 BLACK-SCREEN BUG (2026-08-01):
  This module runs headless (no GUI). It only queries SQLite and computes
  scores — no Playwright/GPU involvement. Safe from TDR timeout.
"""

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

ALPHA_DECAY = 0.15

# ── HOSE30 Benchmark Probe Set (fixed, for Crawler health monitoring) ──
# These 30 symbols are the liquidity/probe set for detecting Silent Throttling
# and Stale Data in crawler infrastructure. NOT used for capital allocation.
HOSE30_PROBE_SET = frozenset({
    'HPG', 'VCB', 'TCB', 'CTG', 'MBB', 'STB', 'VPB', 'HDB', 'SHB', 'NVB',
    'VIB', 'BID', 'FPT', 'VIC', 'VHM', 'NVL', 'MSN', 'SAB', 'VNM', 'KDH',
    'ROX', 'DIG', 'GAS', 'PLX', 'BCM', 'PNJ', 'MWG', 'SSI', 'VIX', 'REE',
})

# Sector cluster mapping for LAW-008 Cluster Compression.
# Max 3 symbols per cluster in PTCK_VN20.
SECTOR_CLUSTERS: Dict[str, List[str]] = {}

MAX_PER_CLUSTER = 3
TARGET_SIZE = 20


def _get_db_path() -> Path:
    return PROJECT_ROOT / "backend" / "vnstock_data" / "financial_facts.db"


def _get_connection():
    db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_all_symbols_with_health() -> List[Dict]:
    """Tier 1: Scan CSDL 30 quý BCTC, filter falsifiable / negative CFO."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT DISTINCT
                f.symbol,
                f.sector,
                f.industry,
                f.cfo_3y_avg,
                f.roe_3y_avg,
                f.roic_3y_avg,
                f.debt_equity_ratio,
                f.revenue_growth_3y,
                f.eps_growth_3y,
                f.market_cap,
                f.total_shares_outstanding,
                f.price,
                f.so_phien,
                f.ngay_max
            FROM financial_facts f
            WHERE f.cfo_3y_avg IS NOT NULL
              AND f.so_phien >= 20
            ORDER BY f.market_cap DESC
        """).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        symbol = row['symbol']
        cfo = row['cfo_3y_avg'] or 0.0
        roe = row['roe_3y_avg'] or 0.0
        roic = row['roic_3y_avg'] or 0.0
        de = row['debt_equity_ratio'] or 0.0
        rev_growth = row['revenue_growth_3y'] or 0.0
        eps_growth = row['eps_growth_3y'] or 0.0
        mcap = row['market_cap'] or 0.0

        # Falsifiability filter (LAW-007): exclude stocks with negative CFO cascade
        if cfo < 0 and roe < 0:
            continue

        # Exclude stocks with debt/equity > 3.0 (high leverage risk)
        if de > 3.0:
            continue

        results.append({
            'symbol': symbol,
            'sector': row['sector'] or 'UNKNOWN',
            'industry': row['industry'] or 'UNKNOWN',
            'cfo_3y_avg': round(cfo, 2),
            'roe_3y_avg': round(roe, 2),
            'roic_3y_avg': round(roic, 2),
            'debt_equity_ratio': round(de, 2),
            'revenue_growth_3y': round(rev_growth, 2),
            'eps_growth_3y': round(eps_growth, 2),
            'market_cap': round(mcap, 0),
            'total_shares': row['total_shares_outstanding'] or 0,
            'price': row['price'] or 0.0,
            'so_phien': row['so_phien'] or 0,
            'ngay_max': row['ngay_max'],
        })

    return results


def _compile_fingerprints(symbols: List[Dict]) -> List[Dict]:
    """Tier 2: LAW-005 Fingerprint Compiler — compute Epistemic Score.

    Three fingerprint layers:
      1. Structural: ROE stability, Asset Turnover
      2. Behavioural: FCF Conversion (CFO/Net Income), ROIC Persistence
      3. Outcome: EPS Growth, Revenue Growth
    """
    scored = []
    for s in symbols:
        cfo = s['cfo_3y_avg']
        roe = s['roe_3y_avg']
        roic = s['roic_3y_avg']
        de = s['debt_equity_ratio']
        rev_g = s['revenue_growth_3y']
        eps_g = s['eps_growth_3y']
        mcap = s['market_cap']

        # Structural score (0-30): ROE stability + low leverage
        structural = min(30, max(0, roe * 0.5 + (1.0 - min(de, 3.0) / 3.0) * 15))

        # Behavioural score (0-30): CFO health + ROIC persistence
        behavioural = min(30, max(0, cfo * 0.3 + roic * 0.3 + (rev_g / 100.0) * 10))

        # Outcome score (0-40): EPS growth + revenue growth + market cap weight
        outcome = min(40, max(0, eps_g * 0.4 + rev_g * 0.3 + (mcap / 1e9) * 0.1))

        epistemic_score = round(structural + behavioural + outcome, 2)

        scored.append({
            **s,
            'structural_fp': round(structural, 2),
            'behavioural_fp': round(behavioural, 2),
            'outcome_fp': round(outcome, 2),
            'epistemic_score': epistemic_score,
        })

    # Sort by epistemic score descending
    scored.sort(key=lambda x: x['epistemic_score'], reverse=True)
    return scored


def _apply_cluster_compression(symbols: List[Dict]) -> List[Dict]:
    """Tier 3: LAW-008 Cluster Compression — max N symbols per sector cluster.

    Prevents Sector Clustering Trap where a booming sector dominates the index.
    Within each sector, selects the top-scored symbols up to MAX_PER_CLUSTER.
    """
    cluster_map: Dict[str, List[Dict]] = {}
    for s in symbols:
        sector = s['sector']
        cluster_map.setdefault(sector, []).append(s)

    selected = []
    for sector, members in cluster_map.items():
        # Sort by epistemic score descending within cluster
        members.sort(key=lambda x: x['epistemic_score'], reverse=True)
        # Take at most MAX_PER_CLUSTER
        selected.extend(members[:MAX_PER_CLUSTER])

    # If we have more than TARGET_SIZE, trim by score
    if len(selected) > TARGET_SIZE:
        selected.sort(key=lambda x: x['epistemic_score'], reverse=True)
        selected = selected[:TARGET_SIZE]

    return selected


def _export_to_meta_table(vn20: List[Dict]) -> None:
    """Tier 4: Export PTCK_VN20 to SQLite Meta Table."""
    conn = _get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS ptck_vn20_index")
        conn.execute("""
            CREATE TABLE ptck_vn20_index (
                symbol TEXT PRIMARY KEY,
                sector TEXT,
                industry TEXT,
                epistemic_score REAL,
                structural_fp REAL,
                behavioural_fp REAL,
                outcome_fp REAL,
                market_cap REAL,
                price REAL,
                so_phien INTEGER,
                cfo_3y_avg REAL,
                roe_3y_avg REAL,
                roic_3y_avg REAL,
                debt_equity_ratio REAL,
                revenue_growth_3y REAL,
                eps_growth_3y REAL,
                built_at TEXT,
                source TEXT DEFAULT 'PTCK_VN20_DYNAMIC'
            )
        """)
        built_at = datetime.now().isoformat()
        for s in vn20:
            conn.execute("""
                INSERT INTO ptck_vn20_index
                (symbol, sector, industry, epistemic_score, structural_fp,
                 behavioural_fp, outcome_fp, market_cap, price, so_phien,
                 cfo_3y_avg, roe_3y_avg, roic_3y_avg, debt_equity_ratio,
                 revenue_growth_3y, eps_growth_3y, built_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s['symbol'], s['sector'], s['industry'], s['epistemic_score'],
                s['structural_fp'], s['behavioural_fp'], s['outcome_fp'],
                s['market_cap'], s['price'], s['so_phien'],
                s['cfo_3y_avg'], s['roe_3y_avg'], s['roic_3y_avg'],
                s['debt_equity_ratio'], s['revenue_growth_3y'], s['eps_growth_3y'],
                built_at, 'PTCK_VN20_DYNAMIC',
            ))
        conn.commit()
    finally:
        conn.close()


def _export_to_api_cache(vn20: List[Dict]) -> None:
    """Export PTCK_VN20 to a JSON cache file for the API endpoint."""
    cache_dir = PROJECT_ROOT / "backend" / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "ptck_vn20.json"

    import json
    payload = {
        'index': 'PTCK_VN20',
        'built_at': datetime.now().isoformat(),
        'count': len(vn20),
        'symbols': [
            {
                'symbol': s['symbol'],
                'sector': s['sector'],
                'epistemic_score': s['epistemic_score'],
                'market_cap': s['market_cap'],
                'price': s['price'],
            }
            for s in vn20
        ],
    }
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_vn20(dry_run: bool = False) -> Dict:
    """Build PTCK_VN20 dynamic index via 4-tier pipeline.

    Args:
        dry_run: If True, only print what would be done without writing to DB.

    Returns:
        Dict with build statistics.
    """
    print("\n" + "=" * 70)
    print("  ═══ PTCK_VN20 Dynamic Index Builder ═══")
    print("=" * 70)

    # Tier 1: Health & Valuation Filter
    print("\n  [Tier 1] Health & Valuation Filter...")
    candidates = _fetch_all_symbols_with_health()
    print(f"    Found {len(candidates)} candidates after filtering.")

    if not candidates:
        print("    ✅ No candidates found. Aborting.")
        return {'total': 0, 'built': 0, 'dry_run': dry_run}

    # Tier 2: Fingerprint Compilation (LAW-005)
    print("  [Tier 2] LAW-005 Fingerprint Compilation...")
    scored = _compile_fingerprints(candidates)
    print(f"    Scored {len(scored)} symbols.")

    # Tier 3: Cluster Compression (LAW-008)
    print("  [Tier 3] LAW-008 Cluster Compression (max 3/sector)...")
    vn20 = _apply_cluster_compression(scored)
    print(f"    Selected {len(vn20)} symbols for PTCK_VN20.")

    # Tier 4: Export
    if dry_run:
        print("  [Tier 4] DRY RUN — would export to Meta Table + API cache.")
        for s in vn20:
            print(f"    {s['symbol']:<6s} score={s['epistemic_score']:>6.2f}  "
                  f"sector={s['sector']:<20s}  mcap={s['market_cap']:>15,.0f}")
    else:
        print("  [Tier 4] Exporting to SQLite Meta Table + API cache...")
        _export_to_meta_table(vn20)
        _export_to_api_cache(vn20)
        print("    ✅ Export complete.")

    # Summary
    print("\n  ═══ PTCK_VN20 BUILD SUMMARY ═══")
    print(f"    Total candidates:  {len(candidates)}")
    print(f"    After scoring:     {len(scored)}")
    print(f"    After compression: {len(vn20)}")
    print(f"    Target size:       {TARGET_SIZE}")
    print(f"    Dry run:           {dry_run}")

    # Show sector distribution
    sector_dist: Dict[str, int] = {}
    for s in vn20:
        sector_dist[s['sector']] = sector_dist.get(s['sector'], 0) + 1
    print(f"    Sector distribution: {dict(sorted(sector_dist.items(), key=lambda x: -x[1]))}")

    print("=" * 70)

    return {
        'total': len(candidates),
        'scored': len(scored),
        'built': len(vn20),
        'dry_run': dry_run,
        'built_at': datetime.now().isoformat(),
    }


def get_vn20_symbols() -> List[str]:
    """Read PTCK_VN20 symbols from the Meta Table."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol FROM ptck_vn20_index ORDER BY epistemic_score DESC"
        ).fetchall()
        return [r['symbol'] for r in rows]
    finally:
        conn.close()


def get_vn20_api_data() -> Dict:
    """Read PTCK_VN20 data from the API cache file."""
    import json
    cache_file = PROJECT_ROOT / "backend" / "data" / "ptck_vn20.json"
    if not cache_file.exists():
        return {'index': 'PTCK_VN20', 'count': 0, 'symbols': []}
    with open(cache_file, 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PTCK_VN20 Dynamic Index Builder")
    parser.add_argument("--build", action="store_true", help="Build PTCK_VN20 index")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--list", action="store_true", help="List current PTCK_VN20 symbols")
    args = parser.parse_args()

    if args.list:
        symbols = get_vn20_symbols()
        print(f"PTCK_VN20 symbols ({len(symbols)}): {', '.join(symbols)}")
    elif args.build or args.dry_run:
        build_vn20(dry_run=args.dry_run)
    else:
        parser.print_help()