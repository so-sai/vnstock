"""
Portfolio Recommendation Engine (State-Driven Scoring v2).
NO hardcoded symbols. Purely score-driven, market-wide scan.

Architecture:
    flow_forecast + regime + RS + sector_flow + capital_displacement + rsi_regime
                                 |
                                 v
                     market-wide scan (143 symbols)
                                 |
                         composite scoring:
                           flow_alignment      x 0.30
                           regime_strength     x 0.25
                           breadth_confirmation x 0.15
                           momentum_persistence x 0.15
                           liquidity_quality   x 0.15
                                 |
                         percentile ranking
                                 |
                     ┌───────────┼───────────┐
                     v           v           v
                 NHÓM ỔN ĐỊNH  DÒNG TIỀN    CƠ HỘI
                 (top 15%)     DẪN SÓNG      THEO DÕI
                               (next 25%)    (next 30%)
"""
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.database.db_core import get_connection
from src.engine.universe import get_sector, get_universe

logger = logging.getLogger(__name__)

# Scoring weights
W_FLOW_ALIGNMENT = 0.30
W_REGIME_STRENGTH = 0.25
W_BREADTH = 0.15
W_MOMENTUM = 0.15
W_LIQUIDITY = 0.15

# Tier percentiles (dynamic - adjusted by market regime)
TIER_THRESHOLDS = {
    'TRENDING': {'core': 0.85, 'rotation': 0.60, 'opportunity': 0.30},
    'RANGING':  {'core': 0.80, 'rotation': 0.55, 'opportunity': 0.25},
    'CRISIS':   {'core': 0.90, 'rotation': 0.70, 'opportunity': 0.40},
}

# Vietnamese tier labels
TIER_LABELS = {
    'core': 'NHÓM ỔN ĐỊNH',
    'rotation': 'DÒNG TIỀN DẪN SÓNG',
    'opportunity': 'CƠ HỘI THEO DÕI',
}

# Flow projection → score mapping
FLOW_PROJECTION_SCORE = {
    'ACCELERATION': 90,
    'CONTINUATION': 70,
    'STABLE': 50,
    'DECELERATION': 30,
    'LAG': 10,
    'NEUTRAL': 40,
}


@dataclass
class Recommendation:
    symbol: str
    tier: str
    tier_vn: str
    conviction: float
    rationale: str
    sector: str = ''
    total_score: float = 0.0
    flow_alignment_score: float = 0.0
    regime_strength: float = 0.0
    breadth_score: float = 0.0
    momentum_score: float = 0.0
    liquidity_score: float = 0.0
    flow_alignment_label: str = ''
    entry_suggestion: str = ''


def _read_flow_forecast() -> dict:
    path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output" / "flow_forecast.json"
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def _read_capital_displacement() -> dict:
    path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output" / "capital_displacement.json"
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def _read_rsi_regime_report() -> dict:
    path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output" / "rsi_regime_report.json"
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def _read_rs_data() -> pd.DataFrame:
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT symbol, date, close, volume FROM daily_ohlcv "
                "WHERE date = (SELECT MAX(date) FROM daily_ohlcv) "
                "AND symbol != 'VNINDEX' AND volume > 0",
                conn
            )
        return df
    except:
        return pd.DataFrame()


def _get_regime_from_db() -> dict:
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT date, status, regime_score, breadth_pct FROM regime_history "
                "ORDER BY date DESC LIMIT 1",
                conn
            )
        if not df.empty:
            return {
                'status': df['status'].iloc[0],
                'regime_score': float(df['regime_score'].iloc[0]),
                'breadth_pct': float(df['breadth_pct'].iloc[0]),
            }
    except:
        pass
    return {'status': 'UNKNOWN', 'regime_score': 0.5, 'breadth_pct': 50}


def _get_rsi_regime_by_symbol(symbol: str, rsi_report: dict) -> dict:
    if not rsi_report:
        return {'persistence_score': 0.5, 'stability': 0.5, 'habitat': 'NEUTRAL'}
    bull_leaders = rsi_report.get('bull_leaders', [])
    bear_leaders = rsi_report.get('bear_leaders', [])
    for entry in bull_leaders + bear_leaders:
        if entry.get('symbol') == symbol:
            return entry
    return {'persistence_score': 0.5, 'stability': 0.5, 'habitat': 'NEUTRAL'}


def _compute_flow_alignment_score(sector: str, forecast: dict) -> tuple:
    sector_forecasts = forecast.get('sector_forecasts', [])
    for sf in sector_forecasts:
        if sf.get('sector') == sector:
            proj = sf.get('projection_1d', 'NEUTRAL')
            score = FLOW_PROJECTION_SCORE.get(proj, 40)
            return (score, proj)
    return (40, 'NEUTRAL')


def _compute_regime_strength_score(regime: dict) -> float:
    status = regime.get('status', 'UNKNOWN')
    regime_score = regime.get('regime_score', 0.5)
    if status == 'TRENDING':
        return min(100, 50 + regime_score * 50)
    elif status == 'RANGING':
        return 30 + regime_score * 40
    elif status == 'CRISIS':
        return max(5, 20 - regime_score * 15)
    return 40


def _compute_breadth_score(breadth_pct: float) -> float:
    return min(100, breadth_pct * 2)


def _compute_liquidity_score(symbol: str, rs_data: pd.DataFrame) -> float:
    vol_data = rs_data[rs_data['symbol'] == symbol]
    if vol_data.empty or rs_data.empty:
        return 50
    daily_vol = float(vol_data['volume'].iloc[0])
    all_vols = rs_data['volume'].replace(0, 1)
    vol_rank = np.searchsorted(sorted(all_vols), daily_vol) / len(all_vols)
    return vol_rank * 100


def _score_symbol(symbol: str, sector: str, rs_data: pd.DataFrame,
                  forecast: dict, displacement: dict, regime: dict,
                  rsi_report: dict) -> dict:
    # 1. Flow alignment score (0-100)
    flow_score, flow_label = _compute_flow_alignment_score(sector, forecast)

    # 2. Regime strength score (0-100)
    regime_strength = _compute_regime_strength_score(regime)

    # 3. Breadth confirmation score (0-100)
    breadth_score = _compute_breadth_score(regime.get('breadth_pct', 50))

    # 4. Momentum persistence score (0-100)
    rsi_info = _get_rsi_regime_by_symbol(symbol, rsi_report)
    momentum_score = rsi_info.get('persistence_score', 0.5) * 100

    # 5. Liquidity quality score (0-100)
    liquidity_score = _compute_liquidity_score(symbol, rs_data)

    # Composite total
    total = (
        flow_score * W_FLOW_ALIGNMENT +
        regime_strength * W_REGIME_STRENGTH +
        breadth_score * W_BREADTH +
        momentum_score * W_MOMENTUM +
        liquidity_score * W_LIQUIDITY
    )

    return {
        'total_score': round(total, 1),
        'flow_alignment_score': round(flow_score, 1),
        'flow_alignment_label': flow_label,
        'regime_strength': round(regime_strength, 1),
        'breadth_score': round(breadth_score, 1),
        'momentum_score': round(momentum_score, 1),
        'liquidity_score': round(liquidity_score, 1),
    }


def _classify_tier(score_pct: float, regime_status: str) -> str:
    thresholds = TIER_THRESHOLDS.get(regime_status, TIER_THRESHOLDS['RANGING'])
    if score_pct >= thresholds['core']:
        return 'core'
    elif score_pct >= thresholds['rotation']:
        return 'rotation'
    elif score_pct >= thresholds['opportunity']:
        return 'opportunity'
    return None


def _generate_entry_suggestion(tier: str, conviction: float, regime_status: str) -> str:
    if tier == 'core' and conviction >= 70:
        return 'MUA khi điều chỉnh — tích lũy dần'
    if tier == 'core':
        return 'NẮM GIỮ vị thế cốt lõi'
    if tier == 'rotation' and conviction >= 60:
        return 'MUA khi có volume xác nhận'
    if tier == 'rotation':
        return 'THEO DÕI chờ volume — vào lệnh một phần'
    if tier == 'opportunity' and conviction >= 50:
        return 'LƯỚT SÓNG — stop chặt, thoát nhanh'
    return 'CHỜ — theo dõi thêm'


def generate_recommendations(target_date: Optional[str] = None) -> dict:
    today = target_date or datetime.now().strftime('%Y-%m-%d')
    print(f"\n{'='*70}")
    print("  PORTFOLIO RECOMMENDATION ENGINE v2")
    print(f"  Date: {today}")
    print(f"  Scanning: market-wide ({len(get_universe())} symbols)")
    print(f"{'='*70}")

    forecast = _read_flow_forecast()
    displacement = _read_capital_displacement()
    rs_data = _read_rs_data()
    regime = _get_regime_from_db()
    rsi_report = _read_rsi_regime_report()
    regime_status = regime.get('status', 'UNKNOWN')
    symbols = get_universe()

    scored_symbols = []
    for symbol in symbols:
        try:
            sector = get_sector(symbol)
            if sector == 'OTHER':
                continue
            scored = _score_symbol(symbol, sector, rs_data, forecast, displacement, regime, rsi_report)
            scored_symbols.append({
                'symbol': symbol,
                'sector': sector,
                **scored,
            })
        except Exception as e:
            logger.warning(f"Score failed for {symbol}: {e}")
            continue

    if not scored_symbols:
        return {'date': today, 'regime': regime, 'recommendations': {}, 'summary': {}}

    scored_symbols.sort(key=lambda x: x['total_score'], reverse=True)
    n = len(scored_symbols)
    scored_with_rank = []
    for i, s in enumerate(scored_symbols):
        pct = 1.0 - (i / n) if n > 0 else 0
        scored_with_rank.append({**s, 'score_percentile': round(pct, 4)})

    recommendations = {'core': [], 'rotation': [], 'opportunity': []}
    for s in scored_with_rank:
        tier = _classify_tier(s['score_percentile'], regime_status)
        if tier is None:
            continue
        entry = _generate_entry_suggestion(tier, s['total_score'], regime_status)
        recommendations[tier].append({
            'symbol': s['symbol'],
            'tier': tier.upper(),
            'tier_vn': TIER_LABELS[tier],
            'conviction': s['total_score'],
            'rationale': _build_rationale(s),
            'sector': s['sector'],
            'total_score': s['total_score'],
            'flow_alignment_score': s['flow_alignment_score'],
            'flow_alignment_label': s['flow_alignment_label'],
            'regime_strength': s['regime_strength'],
            'breadth_score': s['breadth_score'],
            'momentum_score': s['momentum_score'],
            'liquidity_score': s['liquidity_score'],
            'entry_suggestion': entry,
            'score_percentile': s['score_percentile'],
        })

    for tier in ['core', 'rotation', 'opportunity']:
        recommendations[tier].sort(key=lambda x: -x['conviction'])

    result = {
        'date': today,
        'regime': regime,
        'recommendations': recommendations,
        'summary': {
            'total_scanned': len(scored_symbols),
            'core_count': len(recommendations['core']),
            'rotation_count': len(recommendations['rotation']),
            'opportunity_count': len(recommendations['opportunity']),
            'top_core': [r['symbol'] for r in recommendations['core'][:3]],
            'top_rotation': [r['symbol'] for r in recommendations['rotation'][:3]],
            'top_opportunity': [r['symbol'] for r in recommendations['opportunity'][:3]],
            'scoring_weights': {
                'flow_alignment': W_FLOW_ALIGNMENT,
                'regime_strength': W_REGIME_STRENGTH,
                'breadth': W_BREADTH,
                'momentum': W_MOMENTUM,
                'liquidity': W_LIQUIDITY,
            },
        }
    }

    print(f"Regime: {regime_status} (score: {regime['regime_score']})")
    print(f"Breadth: {regime['breadth_pct']:.0f}%")
    print(f"Scanned: {len(scored_symbols)} symbols")
    print("\nRECOMMENDATIONS (3-Tier):")
    for tier_name in ['core', 'rotation', 'opportunity']:
        vn_label = TIER_LABELS[tier_name]
        print(f"\n--- {vn_label} ({tier_name.upper()}) ---")
        for r in recommendations[tier_name]:
            arrow = '🟢' if r['conviction'] >= 65 else '🟡' if r['conviction'] >= 45 else '⚪'
            print(f"  {arrow} {r['symbol']:5s} | {r['conviction']:3.0f}% | "
                  f"{r['sector']:10s} | {r['flow_alignment_label']:12s} | {r['rationale']}")
    print("\nSummary:")
    print(f"  NHÓM ỔN ĐỊNH:      {len(recommendations['core'])} symbols")
    print(f"  DÒNG TIỀN DẪN SÓNG: {len(recommendations['rotation'])} symbols")
    print(f"  CƠ HỘI THEO DÕI:    {len(recommendations['opportunity'])} symbols")
    print(f"{'='*70}")

    _store_recommendations(result)
    _export_json(result)

    return result


def _build_rationale(scored: dict) -> str:
    parts = []
    fa = scored['flow_alignment_label']
    if fa in ('ACCELERATION', 'CONTINUATION'):
        parts.append(f"dòng tiền {fa}")
    elif fa in ('DECELERATION', 'LAG'):
        parts.append(f"dòng tiền {fa}")
    if scored['momentum_score'] >= 70:
        parts.append('đà tăng bền')
    if scored['liquidity_score'] >= 70:
        parts.append('thanh khoản tốt')
    if scored['regime_strength'] >= 70:
        parts.append('regime ủng hộ')
    return ' — '.join(parts) if parts else 'theo dõi'


def _store_recommendations(result: dict):
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_recommendations (
                    date TEXT PRIMARY KEY,
                    regime_status TEXT,
                    regime_score REAL,
                    breadth_pct REAL,
                    total_scanned INTEGER DEFAULT 0,
                    core_count INTEGER DEFAULT 0,
                    rotation_count INTEGER DEFAULT 0,
                    opportunity_count INTEGER DEFAULT 0,
                    top_core TEXT DEFAULT '',
                    top_rotation TEXT DEFAULT '',
                    top_opportunity TEXT DEFAULT '',
                    recommendations_json TEXT DEFAULT '{}'
                )
            """)
            try:
                conn.execute("ALTER TABLE portfolio_recommendations ADD COLUMN total_scanned INTEGER DEFAULT 0")
            except:
                pass
            recs = result.get('recommendations', {})
            summary = result.get('summary', {})
            row = {
                'date': result['date'],
                'regime_status': result.get('regime', {}).get('status', ''),
                'regime_score': result.get('regime', {}).get('regime_score', 0),
                'breadth_pct': result.get('regime', {}).get('breadth_pct', 0),
                'total_scanned': summary.get('total_scanned', 0),
                'core_count': summary.get('core_count', 0),
                'rotation_count': summary.get('rotation_count', 0),
                'opportunity_count': summary.get('opportunity_count', 0),
                'top_core': ','.join(summary.get('top_core', [])),
                'top_rotation': ','.join(summary.get('top_rotation', [])),
                'top_opportunity': ','.join(summary.get('top_opportunity', [])),
                'recommendations_json': json.dumps(recs, ensure_ascii=False),
            }
            cols = ', '.join(row.keys())
            vals = ', '.join(['?'] * len(row))
            conn.execute(f"INSERT OR REPLACE INTO portfolio_recommendations ({cols}) VALUES ({vals})",
                         list(row.values()))
            conn.commit()
    except Exception as e:
        logger.warning(f"Store recommendations error: {e}")


def _export_json(result: dict):
    out_path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output" / "portfolio_recommendations.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Saved to: {out_path}")


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    generate_recommendations()
