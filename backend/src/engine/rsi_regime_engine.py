"""
RSI Regime Engine (Phase 14 — Momentum Habitat Layer).
Detects RSI range behavior over time — not point values.

Core philosophy:
    "Where is RSI living?"
    NOT "What is RSI reading?"

Architecture:
    daily_ohlcv → RSI(14) → rolling range percentiles → habitat classification
        ↓
    flow_decay_engine + capital_displacement_engine → habitat confirmation
        ↓
    decision_engine → conviction boost/penalty
"""
import sys, io, json, warnings, logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings('ignore')

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
    return root_path

PROJECT_ROOT = _hydrate_path()

backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import src.config
import numpy as np
import pandas as pd
from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

RSI_PERIOD = 14
PERCENTILE_LOW = 20
PERCENTILE_HIGH = 80
LOOKBACK_DAYS = 120
MIN_HISTORY = 30

HABITAT_LABELS = {
    'BULL_CONTROL': 'TĂNG KIỂM SOÁT',
    'BEAR_CONTROL': 'GIẢM KIỂM SOÁT',
    'WEAK_BULL': 'TĂNG YẾU',
    'WEAK_BEAR': 'GIẢM YẾU',
    'SHIFTING_UP': 'CHUYỂN SANG TĂNG',
    'SHIFTING_DOWN': 'CHUYỂN SANG GIẢM',
    'COMPRESSION': 'NÉN XUNG LỰC',
    'NEUTRAL': 'KHÔNG XU HƯỚNG',
}

TIMEFRAME_LABELS = {
    'daily': 'NGÀY',
    'weekly': 'TUẦN',
}

from src.engine.universe import get_universe

CORE_SYMBOLS = get_universe(exclude=['VNINDEX'])


def _calc_rsi(closes: np.ndarray, period: int = RSI_PERIOD) -> np.ndarray:
    if len(closes) < period + 1:
        return np.full_like(closes, 50.0, dtype=float)
    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_g = np.full(len(closes), np.nan)
    avg_l = np.full(len(closes), np.nan)
    avg_g[period] = np.mean(gains[:period])
    avg_l[period] = np.mean(losses[:period])
    for i in range(period + 1, len(closes)):
        avg_g[i] = (avg_g[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_l[i] = (avg_l[i - 1] * (period - 1) + losses[i - 1]) / period
    rsi = np.full(len(closes), 50.0, dtype=float)
    mask = avg_l != 0
    rsi[mask] = 100 - (100 / (1 + avg_g[mask] / avg_l[mask]))
    return rsi


def _classify_habitat(rsi_low: float, rsi_high: float, rsi_current: float,
                       prev_low: float, prev_high: float) -> dict:
    bull_control = rsi_low >= 40 and rsi_high >= 70
    bear_control = rsi_high <= 60
    weak_bull = 35 <= rsi_low < 40 and 60 <= rsi_high < 70
    weak_bear = 25 < rsi_low < 35 and rsi_high <= 65
    compression = (rsi_high - rsi_low) < 15 and not bull_control and not bear_control
    shifting_up = rsi_low >= 35 and prev_high < 65 and rsi_high >= 65
    shifting_down = rsi_high <= 65 and prev_low > 35 and rsi_low <= 35

    if bull_control:
        habitat = 'BULL_CONTROL'
    elif bear_control:
        habitat = 'BEAR_CONTROL'
    elif shifting_up:
        habitat = 'SHIFTING_UP'
    elif shifting_down:
        habitat = 'SHIFTING_DOWN'
    elif weak_bull:
        habitat = 'WEAK_BULL'
    elif weak_bear:
        habitat = 'WEAK_BEAR'
    elif compression:
        habitat = 'COMPRESSION'
    else:
        habitat = 'NEUTRAL'

    rsi_support = rsi_low
    rsi_resistance = rsi_high
    range_width = round(rsi_high - rsi_low, 1)

    rsi_position = 0.5
    if rsi_high > rsi_low:
        rsi_position = round((rsi_current - rsi_low) / (rsi_high - rsi_low), 2)

    return {
        'habitat': habitat,
        'habitat_vn': HABITAT_LABELS.get(habitat, 'KHÔNG XĐ'),
        'rsi_support': round(rsi_support, 1),
        'rsi_resistance': round(rsi_resistance, 1),
        'range_width': range_width,
        'rsi_current': round(rsi_current, 1),
        'rsi_position_in_range': rsi_position,
    }


def analyze_rsi_regime(symbol: str, lookback_days: int = LOOKBACK_DAYS,
                       target_date: Optional[str] = None,
                       preloaded_df: Optional[pd.DataFrame] = None) -> dict:
    if preloaded_df is not None:
        sym_df = preloaded_df[preloaded_df['symbol'] == symbol].copy()
        if target_date:
            sym_df = sym_df[sym_df['date'] <= target_date]
        df = sym_df.sort_values('date', ascending=False).head(lookback_days).copy()
    else:
        with get_connection() as conn:
            date_filter = f"AND date <= '{target_date}'" if target_date else ""
            df = pd.read_sql(
                f"SELECT date, close FROM daily_ohlcv "
                f"WHERE symbol = ? {date_filter} ORDER BY date DESC LIMIT {lookback_days}",
                conn, params=(symbol,)
            )

    if df.empty or len(df) < MIN_HISTORY:
        return {
            'symbol': symbol,
            'status': 'INSUFFICIENT_DATA',
            'needed_days': MIN_HISTORY,
            'actual_days': len(df),
        }

    df = df.sort_values('date').reset_index(drop=True)
    closes = df['close'].values.astype(float)

    rsi_values = _calc_rsi(closes)
    rsi_series = rsi_values[RSI_PERIOD:]
    if len(rsi_series) < 20:
        return {'symbol': symbol, 'status': 'INSUFFICIENT_DATA'}

    current_rsi = float(rsi_series[-1])
    recent = rsi_series[-min(60, len(rsi_series)):]

    rsi_low = float(np.percentile(recent, PERCENTILE_LOW))
    rsi_high = float(np.percentile(recent, PERCENTILE_HIGH))

    prev_window = rsi_series[-min(120, len(rsi_series)):-min(60, len(rsi_series))] if len(rsi_series) >= 120 else rsi_series[:len(recent)]
    if len(prev_window) >= 10:
        prev_low = float(np.percentile(prev_window, PERCENTILE_LOW))
        prev_high = float(np.percentile(prev_window, PERCENTILE_HIGH))
    else:
        prev_low, prev_high = rsi_low, rsi_high

    habitat_result = _classify_habitat(rsi_low, rsi_high, current_rsi, prev_low, prev_high)

    rsi_weekly = _compute_weekly_rsi(symbol, lookback_days, target_date=target_date, preloaded_df=preloaded_df)
    timeframe_alignment = _check_timeframe_alignment(habitat_result['habitat'], rsi_weekly)

    stability = round(min(1.0, max(0.0, 1.0 - (habitat_result['range_width'] / 60))), 2)

    recent_series = rsi_series[-30:] if len(rsi_series) >= 30 else rsi_series
    range_breaches = 0
    low_breach_count = 0
    high_breach_count = 0
    for val in recent_series:
        if val < rsi_low:
            low_breach_count += 1
        if val > rsi_high:
            high_breach_count += 1
    range_breaches = low_breach_count + high_breach_count
    breach_rate = round(range_breaches / max(1, len(recent_series)), 3)

    momentum_decay_risk = 0.0
    if habitat_result['habitat'] in ('BULL_CONTROL', 'WEAK_BULL'):
        recent_peak = float(np.max(recent_series[-10:]))
        recent_valley = float(np.min(recent_series[-10:]))
        peak_distance = recent_peak - current_rsi
        valley_distance = current_rsi - recent_valley
        if peak_distance > 5 and valley_distance < 3:
            momentum_decay_risk = round(min(1.0, peak_distance / 20), 2)

    persistence_score = 0.5
    if current_rsi > rsi_low and current_rsi < rsi_high:
        persistence_score = round(0.5 + 0.5 * (1 - breach_rate), 2)
    elif current_rsi >= rsi_high:
        persistence_score = round(max(0.3, 1.0 - high_breach_count * 0.15), 2)
    elif current_rsi <= rsi_low:
        persistence_score = round(max(0.3, 1.0 - low_breach_count * 0.15), 2)

    result = {
        'symbol': symbol,
        'status': 'OK',
        'rsi_current': round(current_rsi, 1),
        'habitat': habitat_result['habitat'],
        'habitat_vn': habitat_result['habitat_vn'],
        'rsi_support': habitat_result['rsi_support'],
        'rsi_resistance': habitat_result['rsi_resistance'],
        'range_width': habitat_result['range_width'],
        'rsi_position_in_range': habitat_result['rsi_position_in_range'],
        'range_low_pct': PERCENTILE_LOW,
        'range_high_pct': PERCENTILE_HIGH,
        'stability': stability,
        'persistence_score': persistence_score,
        'breach_rate': breach_rate,
        'momentum_decay_risk': momentum_decay_risk,
        'timeframe_alignment': timeframe_alignment,
        'failed_shift_attempts': 0,
        'rsi_weekly': rsi_weekly,
    }

    return result


def _compute_weekly_rsi(symbol: str, lookback_days: int = 180,
                        target_date: Optional[str] = None,
                        preloaded_df: Optional[pd.DataFrame] = None) -> dict:
    if preloaded_df is not None:
        sym_df = preloaded_df[preloaded_df['symbol'] == symbol].copy()
        if target_date:
            sym_df = sym_df[sym_df['date'] <= target_date]
        df = sym_df.sort_values('date', ascending=False).head(lookback_days).copy()
    else:
        with get_connection() as conn:
            date_filter = f"AND date <= '{target_date}'" if target_date else ""
            df = pd.read_sql(
                f"SELECT date, close FROM daily_ohlcv "
                f"WHERE symbol = ? {date_filter} ORDER BY date DESC LIMIT {lookback_days}",
                conn, params=(symbol,)
            )
    if df.empty or len(df) < 30:
        return {'status': 'INSUFFICIENT_DATA'}

    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df['week'] = df['date'].dt.isocalendar().week.astype(str) + '-' + df['date'].dt.isocalendar().year.astype(str)
    weekly = df.groupby('week').agg({'close': 'last'}).reset_index()
    weekly = weekly.sort_values('week')

    if len(weekly) < 14:
        return {'status': 'INSUFFICIENT_DATA'}

    weekly_closes = weekly['close'].values.astype(float)
    weekly_rsi_values = _calc_rsi(weekly_closes)
    weekly_rsi = float(weekly_rsi_values[-1])

    weekly_recent = weekly_rsi_values[-min(40, len(weekly_rsi_values)):]
    w_low = float(np.percentile(weekly_recent, PERCENTILE_LOW))
    w_high = float(np.percentile(weekly_recent, PERCENTILE_HIGH))

    w_bull = w_low >= 40 and w_high >= 70
    w_bear = w_high <= 60

    if w_bull:
        w_habitat = 'BULL_CONTROL'
    elif w_bear:
        w_habitat = 'BEAR_CONTROL'
    elif w_low >= 35 and w_high >= 60:
        w_habitat = 'WEAK_BULL'
    elif w_low <= 35 and w_high <= 65:
        w_habitat = 'WEAK_BEAR'
    else:
        w_habitat = 'NEUTRAL'

    return {
        'rsi': round(weekly_rsi, 1),
        'support': round(w_low, 1),
        'resistance': round(w_high, 1),
        'habitat': w_habitat,
        'habitat_vn': HABITAT_LABELS.get(w_habitat, 'KHÔNG XĐ'),
        'status': 'OK',
    }


def _check_timeframe_alignment(daily_habitat: str, weekly: dict) -> dict:
    if weekly.get('status') != 'OK':
        return {'aligned': True, 'description': 'CHỈ CÓ DỮ LIỆU NGÀY', 'signal': 'NEUTRAL'}

    w_hab = weekly.get('habitat', 'NEUTRAL')

    if daily_habitat == 'BULL_CONTROL' and w_hab == 'BULL_CONTROL':
        return {'aligned': True, 'description': 'TĂNG MẠNH ĐA KHUNG', 'signal': 'STRONG_BULL'}

    if 'BULL' in daily_habitat and 'BEAR' in w_hab:
        return {'aligned': False, 'description': 'NGÀY TĂNG — TUẦN GIẢM', 'signal': 'WARNING'}

    if 'BEAR' in daily_habitat and 'BULL' in w_hab:
        return {'aligned': False, 'description': 'GIẢM NGẮN HẠN — XU HƯỚNG TUẦN CÒN TĂNG', 'signal': 'PULLBACK'}

    if 'BULL' in daily_habitat and w_hab in ('WEAK_BULL', 'NEUTRAL'):
        return {'aligned': True, 'description': 'TĂNG NGẮN TRONG XU HƯỚNG TRUNG TÍNH', 'signal': 'CAUTIOUS_BULL'}

    if 'BEAR' in daily_habitat and w_hab in ('WEAK_BEAR', 'NEUTRAL'):
        return {'aligned': True, 'description': 'GIẢM NGẮN TRONG XU HƯỚNG TRUNG TÍNH', 'signal': 'CAUTIOUS_BEAR'}

    return {'aligned': True, 'description': 'KHÔNG XUNG ĐỘT', 'signal': 'NEUTRAL'}


def scan_market_rsi_regime(symbols: list = None, target_date: Optional[str] = None,
                           preloaded_df: Optional[pd.DataFrame] = None) -> list:
    if symbols is None:
        symbols = CORE_SYMBOLS
    results = []
    for sym in symbols:
        try:
            result = analyze_rsi_regime(sym, target_date=target_date, preloaded_df=preloaded_df)
            if result.get('status') == 'OK':
                results.append(result)
        except Exception as e:
            logger.warning(f"RSI regime failed for {sym}: {e}")
    results.sort(key=lambda x: -x.get('stability', 0))
    return results


def generate_market_rsi_report(target_date: Optional[str] = None,
                                preloaded_df: Optional[pd.DataFrame] = None) -> dict:
    today = target_date or datetime.now().strftime('%Y-%m-%d')
    print(f"\n{'='*70}")
    print(f"  RSI REGIME ENGINE — MARKET SCAN")
    print(f"  Date: {today}")
    print(f"{'='*70}")

    results = scan_market_rsi_regime(target_date=target_date, preloaded_df=preloaded_df)

    habitat_counts = {}
    for r in results:
        h = r['habitat']
        habitat_counts[h] = habitat_counts.get(h, 0) + 1

    bull_count = sum(1 for r in results if 'BULL' in r['habitat'])
    bear_count = sum(1 for r in results if 'BEAR' in r['habitat'])
    total_scanned = len(results)

    print(f"\nScanned: {total_scanned} symbols")
    print(f"Bull habitats: {bull_count} | Bear habitats: {bear_count}")
    print(f"\nHabitat Distribution:")
    for h, cnt in sorted(habitat_counts.items(), key=lambda x: -x[1]):
        pct = round(cnt / total_scanned * 100, 1) if total_scanned > 0 else 0
        label = HABITAT_LABELS.get(h, h)
        print(f"  {label:20s}: {cnt:2d} ({pct:.0f}%)")

    bull_symbols = [r for r in results if 'BULL' in r['habitat']]
    bear_symbols = [r for r in results if 'BEAR' in r['habitat']]

    print(f"\n--- TOP BULL CONTROL ---")
    for r in bull_symbols[:5]:
        w = r.get('timeframe_alignment', {})
        align = '✅' if w.get('aligned') else '⚠️'
        print(f"  {r['symbol']:5s} | RSI={r['rsi_current']:5.1f} | "
              f"Range={r['rsi_support']}-{r['rsi_resistance']} | "
              f"穩={r['stability']:.2f} | {align} {w.get('signal','')}")

    print(f"\n--- TOP BEAR CONTROL ---")
    for r in bear_symbols[:5]:
        w = r.get('timeframe_alignment', {})
        align = '✅' if w.get('aligned') else '⚠️'
        print(f"  {r['symbol']:5s} | RSI={r['rsi_current']:5.1f} | "
              f"Range={r['rsi_support']}-{r['rsi_resistance']} | "
              f"穩={r['stability']:.2f} | {align} {w.get('signal','')}")

    print(f"\n--- ALIGNMENT CHECK ---")
    aligned = sum(1 for r in results if r.get('timeframe_alignment', {}).get('aligned'))
    misaligned = total_scanned - aligned
    print(f"Aligned: {aligned}/{total_scanned} | Misaligned: {misaligned}")
    for r in results:
        w = r.get('timeframe_alignment', {})
        if not w.get('aligned'):
            print(f"  ⚠️ {r['symbol']:5s}: {w.get('description','')}")

    report = {
        'date': today,
        'total_scanned': total_scanned,
        'bull_count': bull_count,
        'bear_count': bear_count,
        'habitat_distribution': habitat_counts,
        'bull_leaders': [{
            'symbol': r['symbol'],
            'rsi': r['rsi_current'],
            'range': f"{r['rsi_support']}-{r['rsi_resistance']}",
            'stability': r['stability'],
            'aligned': r.get('timeframe_alignment', {}).get('aligned', True),
        } for r in bull_symbols[:5]],
        'bear_leaders': [{
            'symbol': r['symbol'],
            'rsi': r['rsi_current'],
            'range': f"{r['rsi_support']}-{r['rsi_resistance']}",
            'stability': r['stability'],
            'aligned': r.get('timeframe_alignment', {}).get('aligned', True),
        } for r in bear_symbols[:5]],
        'alignment': {
            'aligned': aligned,
            'misaligned': misaligned,
            'warnings': [{
                'symbol': r['symbol'],
                'description': r.get('timeframe_alignment', {}).get('description', ''),
            } for r in results if not r.get('timeframe_alignment', {}).get('aligned')],
        },
    }

    _store_report(report)
    _export_report(report)

    return report


def _store_report(report: dict):
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rsi_regime_history (
                    date TEXT PRIMARY KEY,
                    total_scanned INTEGER,
                    bull_count INTEGER,
                    bear_count INTEGER,
                    aligned_count INTEGER,
                    misaligned_count INTEGER,
                    habitat_distribution TEXT,
                    report_json TEXT
                )
            """)
            row = {
                'date': report['date'],
                'total_scanned': report['total_scanned'],
                'bull_count': report['bull_count'],
                'bear_count': report['bear_count'],
                'aligned_count': report['alignment']['aligned'],
                'misaligned_count': report['alignment']['misaligned'],
                'habitat_distribution': json.dumps(report['habitat_distribution'], ensure_ascii=False),
                'report_json': json.dumps(report, ensure_ascii=False),
            }
            cols = ', '.join(row.keys())
            vals = ', '.join(['?'] * len(row))
            conn.execute(f"INSERT OR REPLACE INTO rsi_regime_history ({cols}) VALUES ({vals})",
                         list(row.values()))
            conn.commit()
    except Exception as e:
        logger.warning(f"Store RSI report error: {e}")


def _export_report(report: dict):
    out_path = src.config.DATA_DIR / "output" / "rsi_regime_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out_path}")


def run_analysis(target_symbol: Optional[str] = None) -> dict:
    if target_symbol:
        result = analyze_rsi_regime(target_symbol)
        print(f"\n{'='*70}")
        print(f"  RSI REGIME ENGINE — {target_symbol}")
        print(f"{'='*70}")
        if result.get('status') == 'OK':
            print(f"  RSI: {result['rsi_current']}")
            print(f"  Habitat: {result['habitat_vn']} ({result['habitat']})")
            print(f"  Range: {result['rsi_support']} - {result['rsi_resistance']}")
            print(f"  Stability: {result['stability']}")
            print(f"  Persistence: {result['persistence_score']}")
            print(f"  Momentum Decay Risk: {result['momentum_decay_risk']}")
            w = result.get('timeframe_alignment', {})
            print(f"  Weekly: RSI={result.get('rsi_weekly',{}).get('rsi','N/A')} "
                  f"| {result.get('rsi_weekly',{}).get('habitat_vn','')}")
            print(f"  Alignment: {w.get('signal','')} — {w.get('description','')}")
            if not w.get('aligned', True):
                print(f"  ⚠️ TIMEFRAME CONFLICT")
        else:
            print(f"  Status: {result.get('status')}")
        print(f"{'='*70}")
        return result
    else:
        return generate_market_rsi_report()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    import argparse
    parser = argparse.ArgumentParser(description="RSI Regime Engine")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol analysis")
    parser.add_argument("--scan", action="store_true", help="Market-wide scan")
    args = parser.parse_args()
    if args.symbol:
        run_analysis(args.symbol)
    else:
        run_analysis()
