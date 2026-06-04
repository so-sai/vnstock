"""
Capital Flow Forecasting Engine (Phase 13 — Predictive Layer).
Transforms descriptive flow data into forward-looking flow projections.

Architecture:
    capital_displacement_engine (raw flow snapshot)
        |
        v
    capital_flow_forecasting_engine (flow vectorization + momentum + regime projection)
        |
        v
    sector flow forecast + regime transition probability
        |
        v
    decision_tensor / portfolio layer (consumption)

Core math:
    F(t)   = sector flow vector at time t
    dF/dt  = flow velocity (first difference)
    d2F/dt2 = flow acceleration (second difference)
    next_regime = argmax P(regime_{t+1} | F(t), dF/dt, breadth, liquidity)
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
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from src.database.db_core import get_connection
import logging

logger = logging.getLogger(__name__)

from src.engine.universe import SECTOR_MAP, CORE_SECTORS

BROAD_SCAN_SYMBOLS = [s for s in SECTOR_MAP.keys() if s != 'VNINDEX']

FORECAST_HORIZONS = [1, 3]


@dataclass
class FlowVector:
    date: str
    sector_shares: dict
    sector_perf: dict
    sector_velocity: dict
    sector_acceleration: dict
    rotation_velocity: float
    total_volume: int
    vnindex_close: float
    vnindex_chg: float


@dataclass
class SectorForecast:
    sector: str
    current_share: float
    current_perf: float
    velocity: float
    acceleration: float
    persistence: float
    projection_1d: str
    projection_3d: str
    confidence: str


def _fetch_ohlcv_history(lookback_days: int = 60, target_date: Optional[str] = None) -> pd.DataFrame:
    with get_connection() as conn:
        if target_date:
            df = pd.read_sql(
                f"SELECT symbol, date, close, volume FROM daily_ohlcv "
                f"WHERE date >= date('{target_date}', '-{lookback_days + 10} days') "
                f"AND date <= '{target_date}' AND volume > 0 ORDER BY date",
                conn
            )
        else:
            df = pd.read_sql(
                f"SELECT symbol, date, close, volume FROM daily_ohlcv "
                f"WHERE date >= date('now', '-{lookback_days + 10} days') "
                f"AND volume > 0 ORDER BY date",
                conn
            )
    return df


def _fetch_regime_history(lookback_days: int = 120, target_date: Optional[str] = None) -> pd.DataFrame:
    with get_connection() as conn:
        date_filter = f"WHERE date <= '{target_date}'" if target_date else ""
        df = pd.read_sql(
            f"SELECT date, regime_score, status, breadth_pct FROM regime_history "
            f"{date_filter} ORDER BY date",
            conn
        )
    return df


def _compute_sector_flow_vector(df: pd.DataFrame, target_date: str) -> FlowVector:
    all_dates = sorted(df['date'].unique())
    date_idx = all_dates.index(target_date) if target_date in all_dates else -1
    prev_date = all_dates[date_idx - 1] if date_idx > 0 else None

    df_day = df[df['date'] == target_date].copy()
    if df_day.empty:
        return None

    df_prev = df[df['date'] == prev_date].copy() if prev_date else pd.DataFrame()

    total_vol = df_day['volume'].sum() or 1

    prev_close_map = {}
    if not df_prev.empty:
        for _, row in df_prev.iterrows():
            prev_close_map[row['symbol']] = row['close']

    sector_series = df_day['symbol'].map(SECTOR_MAP).fillna('OTHER')

    sector_shares = {}
    sector_perf = {}
    for sec in CORE_SECTORS:
        sec_data = df_day[sector_series == sec]
        sec_vol = sec_data['volume'].sum()
        sector_shares[sec] = round(sec_vol / total_vol * 100, 2)

        chgs = []
        weights = []
        for _, row in sec_data.iterrows():
            prev_c = prev_close_map.get(row['symbol'])
            if prev_c and prev_c > 0 and row['close'] > 0:
                chg = (row['close'] - prev_c) / prev_c * 100
                chgs.append(chg)
                weights.append(row['volume'])
        if chgs and sum(weights) > 0:
            w_avg = sum(c * w for c, w in zip(chgs, weights)) / sum(weights)
            sector_perf[sec] = round(w_avg, 2)
        else:
            sector_perf[sec] = 0.0

    vni_day = df_day[df_day['symbol'] == 'VNINDEX']
    vni_close = float(vni_day['close'].iloc[-1]) if not vni_day.empty else 0.0
    vni_chg = 0.0
    if vni_close and not vni_day.empty:
        sym_prev = prev_close_map.get('VNINDEX')
        if sym_prev and sym_prev > 0:
            vni_chg = round((vni_close - sym_prev) / sym_prev * 100, 2)

    return FlowVector(
        date=target_date,
        sector_shares=sector_shares,
        sector_perf=sector_perf,
        sector_velocity={},
        sector_acceleration={},
        rotation_velocity=0.0,
        total_volume=total_vol,
        vnindex_close=vni_close,
        vnindex_chg=vni_chg,
    )


def _get_historical_flow_vectors(lookback_days: int = 60, target_date: Optional[str] = None) -> list:
    df = _fetch_ohlcv_history(lookback_days, target_date=target_date)
    if df.empty:
        return []

    dates = sorted(df['date'].unique(), reverse=True)
    vectors = []
    for d in dates[:lookback_days]:
        vec = _compute_sector_flow_vector(df, d)
        if vec:
            vectors.append(vec)
    return vectors


def _compute_velocity_and_acceleration(vectors: list) -> list:
    if len(vectors) < 3:
        for v in vectors:
            v.sector_velocity = {s: 0.0 for s in CORE_SECTORS}
            v.sector_acceleration = {s: 0.0 for s in CORE_SECTORS}
        return vectors

    sorted_vectors = sorted(vectors, key=lambda x: x.date)

    for i in range(1, len(sorted_vectors)):
        prev = sorted_vectors[i - 1]
        curr = sorted_vectors[i]
        for s in CORE_SECTORS:
            curr.sector_velocity[s] = round(
                curr.sector_shares.get(s, 0) - prev.sector_shares.get(s, 0), 2
            )

    for i in range(2, len(sorted_vectors)):
        prev_v = sorted_vectors[i - 1]
        curr_v = sorted_vectors[i]
        for s in CORE_SECTORS:
            curr_v.sector_acceleration[s] = round(
                curr_v.sector_velocity.get(s, 0) - prev_v.sector_velocity.get(s, 0), 3
            )

    for i in range(1, len(sorted_vectors)):
        prev_rank = {
            s: sorted(sorted_vectors[i - 1].sector_shares.items(), key=lambda x: -x[1])
            for s, _ in sorted_vectors[i - 1].sector_shares.items()
        }
        curr_rank = {
            s: idx for idx, (s, _) in enumerate(
                sorted(sorted_vectors[i].sector_shares.items(), key=lambda x: -x[1])
            )
        }
        rank_changes = []
        prev_ranked = [s for s, _ in sorted(
            sorted_vectors[i - 1].sector_shares.items(), key=lambda x: -x[1]
        )]
        for idx, s in enumerate(prev_ranked):
            curr_idx = curr_rank.get(s, len(prev_ranked))
            rank_changes.append(abs(idx - curr_idx))
        sorted_vectors[i].rotation_velocity = round(
            sum(rank_changes) / len(rank_changes), 2
        )

    return sorted_vectors


def _compute_regime_transition_matrix() -> dict:
    df = _fetch_regime_history(120)
    if df.empty or len(df) < 5:
        return {}

    states = ['TRENDING', 'RANGING', 'CRISIS']
    matrix = {s: {t: 0 for t in states} for s in states}
    counts = {s: 0 for s in states}

    for i in range(1, len(df)):
        prev_status = df['status'].iloc[i - 1]
        curr_status = df['status'].iloc[i]
        if prev_status in states and curr_status in states:
            matrix[prev_status][curr_status] += 1
            counts[prev_status] += 1

    for s in states:
        if counts[s] > 0:
            for t in states:
                matrix[s][t] = round(matrix[s][t] / counts[s], 3)

    return {
        'transition_matrix': matrix,
        'state_counts': counts,
    }


def _project_regime(regime_df: pd.DataFrame, flow_velocity: float,
                    breadth_pct: float) -> dict:
    states = ['TRENDING', 'RANGING', 'CRISIS']
    if regime_df.empty or len(regime_df) < 5:
        return {
            'projected_regime': 'RANGING',
            'probabilities': {s: 0.33 for s in states},
            'confidence': 'LOW',
        }

    current_status = regime_df['status'].iloc[-1] if 'status' in regime_df.columns else 'RANGING'
    if current_status not in states:
        current_status = 'RANGING'

    matrix_data = _compute_regime_transition_matrix()
    trans_matrix = matrix_data.get('transition_matrix', {})

    base_probs = trans_matrix.get(current_status, {s: 0.33 for s in states})
    for s in states:
        if base_probs.get(s, 0) == 0 and sum(base_probs.values()) == 0:
            base_probs[s] = 0.33

    if sum(base_probs.values()) == 0:
        base_probs = {s: 0.33 for s in states}

    adjustment = 0.0
    if len(regime_df) >= 3:
        recent = regime_df.tail(3)
        if 'breadth_pct' in recent.columns:
            breadth_trend = recent['breadth_pct'].iloc[-1] - recent['breadth_pct'].iloc[0]
            adjustment += breadth_trend * 0.005

    adjustment += flow_velocity * 0.02

    adj_probs = {}
    for s in states:
        raw = base_probs.get(s, 0)
        if s == 'TRENDING':
            raw += adjustment
        elif s == 'CRISIS':
            raw -= adjustment * 0.5
        adj_probs[s] = max(0.01, min(0.99, raw))

    total = sum(adj_probs.values())
    adj_probs = {s: round(v / total, 3) for s, v in adj_probs.items()}

    projected = max(adj_probs, key=adj_probs.get)
    max_prob = adj_probs[projected]

    confidence = 'HIGH' if max_prob > 0.55 else 'MEDIUM' if max_prob > 0.40 else 'LOW'

    return {
        'projected_regime': projected,
        'probabilities': adj_probs,
        'confidence': confidence,
    }


def _classify_sector_trajectory(velocity: float, acceleration: float,
                                 persistence: float) -> tuple:
    if acceleration > 0.5 and velocity > 0:
        return ('ACCELERATION', 'HIGH')
    if velocity > 0.3 and acceleration > -0.3:
        return ('CONTINUATION', 'HIGH')
    if abs(velocity) <= 0.3 and abs(acceleration) <= 0.3:
        return ('STABLE', 'MEDIUM')
    if velocity < -0.3 and acceleration < 0.5:
        return ('DECELERATION', 'MEDIUM')
    if velocity < -0.3 and acceleration < -0.3:
        return ('LAG', 'LOW')
    return ('NEUTRAL', 'LOW')


def _estimate_persistence(shares_history: list, sector: str) -> float:
    vals = [v.sector_shares.get(sector, 0) for v in shares_history]
    if len(vals) < 5:
        return 0.5
    recent = np.array(vals[-5:])
    if np.std(recent) < 1e-6:
        return 1.0
    autocorr = np.corrcoef(recent[:-1], recent[1:])[0, 1] if len(recent) > 2 else 0.5
    return round(max(-1.0, min(1.0, autocorr if not np.isnan(autocorr) else 0.5)), 3)


def generate_flow_forecast(target_date: Optional[str] = None,
                           lookback_days: int = 60) -> dict:
    today = target_date or datetime.now().strftime('%Y-%m-%d')
    print(f"\n{'='*70}")
    print(f"  CAPITAL FLOW FORECASTING ENGINE")
    print(f"  Forecast date: {today} | Lookback: {lookback_days}d")
    print(f"{'='*70}")

    vectors = _get_historical_flow_vectors(lookback_days, target_date=target_date)
    if not vectors:
        print("  NO DATA: Cannot compute flow vectors.")
        return {'status': 'NO_DATA', 'date': today}

    vectors = _compute_velocity_and_acceleration(vectors)
    current = vectors[-1] if vectors else None
    if not current:
        return {'status': 'NO_CURRENT_VECTOR', 'date': today}

    regime_df = _fetch_regime_history(120, target_date=target_date)

    total_flow_velocity = sum(abs(v) for v in current.sector_velocity.values()) / len(CORE_SECTORS)

    latest_breadth = float(regime_df['breadth_pct'].iloc[-1]) if not regime_df.empty else 50.0
    regime_forecast = _project_regime(regime_df, total_flow_velocity, latest_breadth)

    sector_forecasts = []
    leading = []
    lagging = []

    for s in CORE_SECTORS:
        vel = current.sector_velocity.get(s, 0.0)
        acc = current.sector_acceleration.get(s, 0.0)
        persistence = _estimate_persistence(vectors, s)
        trajectory, confidence = _classify_sector_trajectory(vel, acc, persistence)

        sf = SectorForecast(
            sector=s,
            current_share=current.sector_shares.get(s, 0.0),
            current_perf=current.sector_perf.get(s, 0.0),
            velocity=vel,
            acceleration=acc,
            persistence=persistence,
            projection_1d=trajectory,
            projection_3d=trajectory,
            confidence=confidence,
        )

        sec_data = {
            'sector': s,
            'current_share_pct': sf.current_share,
            'velocity': sf.velocity,
            'acceleration': sf.acceleration,
            'persistence': sf.persistence,
            'projection_1d': sf.projection_1d,
            'projection_3d': sf.projection_3d,
            'confidence': sf.confidence,
        }
        sector_forecasts.append(sec_data)

        if vel > 0.5 and acc > -0.3:
            leading.append(s)
        elif vel < -0.5 and acc < 0.3:
            lagging.append(s)

    result = {
        'date': today,
        'forecast_horizons': [f'{h}d' for h in FORECAST_HORIZONS],
        'regime_forecast': regime_forecast,
        'flow_momentum': {
            'total_flow_velocity': round(total_flow_velocity, 3),
            'rotation_velocity': current.rotation_velocity,
            'top_sector': max(CORE_SECTORS, key=lambda s: current.sector_shares.get(s, 0)),
            'top_sector_share': round(max(current.sector_shares.get(s, 0) for s in CORE_SECTORS), 1),
            'flow_dispersion': round(np.std([current.sector_shares.get(s, 0) for s in CORE_SECTORS]), 2),
        },
        'sector_forecasts': sector_forecasts,
        'leading_sectors': leading[:3],
        'lagging_sectors': lagging[:3],
        'projection_summary': {
            'acceleration_count': sum(1 for s in sector_forecasts if s['projection_1d'] == 'ACCELERATION'),
            'continuation_count': sum(1 for s in sector_forecasts if s['projection_1d'] == 'CONTINUATION'),
            'stable_count': sum(1 for s in sector_forecasts if s['projection_1d'] == 'STABLE'),
            'deceleration_count': sum(1 for s in sector_forecasts if s['projection_1d'] == 'DECELERATION'),
            'lag_count': sum(1 for s in sector_forecasts if s['projection_1d'] == 'LAG'),
        },
        'market_context': {
            'total_volume': current.total_volume,
            'vnindex_close': current.vnindex_close,
            'vnindex_chg': current.vnindex_chg,
        },
    }

    print(f"\nRegime Forecast: {regime_forecast['projected_regime']} "
          f"(confidence: {regime_forecast['confidence']})")
    print(f"Probabilities: {regime_forecast['probabilities']}")
    print(f"\nFlow Momentum:")
    print(f"  Velocity: {result['flow_momentum']['total_flow_velocity']}")
    print(f"  Rotation: {result['flow_momentum']['rotation_velocity']}")
    print(f"  Dispersion: {result['flow_momentum']['flow_dispersion']}")
    print(f"\nSector Projections (1d):")
    for s in sorted(sector_forecasts, key=lambda x: x['velocity'], reverse=True):
        arrow = '↑' if s['velocity'] > 0 else '↓' if s['velocity'] < 0 else '→'
        print(f"  {s['sector']:10s} {arrow} share={s['current_share_pct']:5.1f}% "
              f"vel={s['velocity']:+.1f} acc={s['acceleration']:+.2f} "
              f"→ {s['projection_1d']:14s} ({s['confidence']})")
    print(f"\nLeading: {result['leading_sectors']}")
    print(f"Lagging: {result['lagging_sectors']}")
    proj = result['projection_summary']
    print(f"\nSummary: ACC={proj['acceleration_count']} "
          f"CONT={proj['continuation_count']} "
          f"STABLE={proj['stable_count']} "
          f"DECEL={proj['deceleration_count']} "
          f"LAG={proj['lag_count']}")
    print(f"{'='*70}")

    _store_forecast(result)

    return result


def _store_forecast(result: dict):
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS flow_forecast_history (
                    date TEXT PRIMARY KEY,
                    projected_regime TEXT,
                    regime_confidence TEXT,
                    regime_prob_trending REAL,
                    regime_prob_ranging REAL,
                    regime_prob_crisis REAL,
                    flow_velocity REAL,
                    rotation_velocity REAL,
                    flow_dispersion REAL,
                    projection_summary TEXT,
                    sector_forecasts TEXT,
                    leading_sectors TEXT,
                    lagging_sectors TEXT
                )
            """)
            rf = result.get('regime_forecast', {})
            probs = rf.get('probabilities', {})
            row = {
                'date': result['date'],
                'projected_regime': rf.get('projected_regime', ''),
                'regime_confidence': rf.get('confidence', ''),
                'regime_prob_trending': probs.get('TRENDING', 0),
                'regime_prob_ranging': probs.get('RANGING', 0),
                'regime_prob_crisis': probs.get('CRISIS', 0),
                'flow_velocity': result.get('flow_momentum', {}).get('total_flow_velocity', 0),
                'rotation_velocity': result.get('flow_momentum', {}).get('rotation_velocity', 0),
                'flow_dispersion': result.get('flow_momentum', {}).get('flow_dispersion', 0),
                'projection_summary': json.dumps(result.get('projection_summary', {}), ensure_ascii=False),
                'sector_forecasts': json.dumps(result.get('sector_forecasts', []), ensure_ascii=False),
                'leading_sectors': ','.join(result.get('leading_sectors', [])),
                'lagging_sectors': ','.join(result.get('lagging_sectors', [])),
            }
            cols = ', '.join(row.keys())
            vals = ', '.join(['?'] * len(row))
            conn.execute(f"INSERT OR REPLACE INTO flow_forecast_history ({cols}) VALUES ({vals})",
                         list(row.values()))
            conn.commit()
    except Exception as e:
        logger.warning(f"Store forecast error: {e}")


def run_forecast(target_date: Optional[str] = None) -> dict:
    result = generate_flow_forecast(target_date)
    out_path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output" / "flow_forecast.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out_path}")
    return result


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    run_forecast()
