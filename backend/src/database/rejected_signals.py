"""rejected_signals.py — Nghĩa địa giả thuyết cho Anti-Survivorship Architecture.

Lưu toàn bộ tín hiệu bị Governor từ chối (Rejected Hypotheses).
Mỗi entry là một snapshot đầy đủ: state vector, lý do, prior/posterior belief.
Sau 5/10/20 phiên, EOD worker cập nhật simulated_exit để phục vụ
Counterfactual Pipeline của QuantStatsBridge.

LAW-001 (Anti-Survivorship):
  Không chỉ lưu người sống. Lưu cả nghĩa địa.

Dynamic Slippage Model:
  Slippage_simulated = max(0.001, α × (OrderVol / ADV_20) + β × (ATR_14 / Close))
  α = 0.3 (impact coefficient), β = 0.5 (volatility coefficient)
"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.database.db_core import get_connection

# Dynamic Slippage parameters
ALPHA_IMPACT = 0.3
BETA_VOLATILITY = 0.5
MIN_SLIPPAGE = 0.001  # 0.1% floor

TABLE_NAME = "rejected_signals_archive"


def ensure_table():
    """Tạo bảng nếu chưa tồn tại."""
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                rejection_reason TEXT NOT NULL,
                regime_score REAL NOT NULL,
                adx_value REAL NOT NULL,
                feature_vector_json TEXT NOT NULL,
                prior_belief REAL NOT NULL,
                posterior_belief REAL NOT NULL,
                simulated_exit_5d REAL,
                simulated_exit_10d REAL,
                simulated_exit_20d REAL
            )
        """)


def record_rejected_signal(
    ticker: str,
    signal_type: str,
    rejection_reason: str,
    regime_score: float,
    adx_value: float,
    feature_vector: Dict[str, Any],
    prior_belief: float,
    posterior_belief: float,
) -> int:
    """Ghi một tín hiệu bị từ chối vào nghĩa địa.

    Args:
        ticker: Mã cổ phiếu (VIC, VHM, ...)
        signal_type: Loại tín hiệu (BREAKOUT, ABSORPTION, ...)
        rejection_reason: Lý do từ chối (COLD_START, GOVERNOR_LOCK, ADX_HAIRCUT, ...)
        regime_score: Điểm regime tại thời điểm T
        adx_value: ADX tại thời điểm T
        feature_vector: Dict state vector đầy đủ (sẽ serialize JSON)
        prior_belief: Niềm tin Governor trước khi xét signal
        posterior_belief: Niềm tin Governor sau khi xét signal

    Returns:
        id của bản ghi mới
    """
    ensure_table()
    now = datetime.now().isoformat()
    fv_json = json.dumps(feature_vector, ensure_ascii=False, default=str)
    with get_connection() as conn:
        cur = conn.execute(
            f"INSERT INTO {TABLE_NAME} "
            "(timestamp, ticker, signal_type, rejection_reason, "
            " regime_score, adx_value, feature_vector_json, "
            " prior_belief, posterior_belief) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, ticker, signal_type, rejection_reason,
             regime_score, adx_value, fv_json,
             prior_belief, posterior_belief),
        )
        return cur.lastrowid


def get_rejected_signals(
    limit: int = 100,
    reason_filter: Optional[str] = None,
    days_back: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Truy vấn rejected signals.

    Args:
        limit: Số bản ghi tối đa
        reason_filter: Lọc theo rejection_reason (None = tất cả)
        days_back: Chỉ lấy N ngày gần đây (None = tất cả)

    Returns:
        List[dict] với các keys:
        id, timestamp, ticker, signal_type, rejection_reason,
        regime_score, adx_value, feature_vector (deserialized),
        prior_belief, posterior_belief,
        simulated_exit_5d, simulated_exit_10d, simulated_exit_20d
    """
    ensure_table()
    where_clauses = []
    params = []
    if reason_filter:
        where_clauses.append("rejection_reason = ?")
        params.append(reason_filter)
    if days_back is not None:
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
        where_clauses.append("timestamp >= ?")
        params.append(cutoff)
    where = ""
    if where_clauses:
        where = "WHERE " + " AND ".join(where_clauses)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM {TABLE_NAME} {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["feature_vector"] = json.loads(d.pop("feature_vector_json", "{}"))
        except (json.JSONDecodeError, ValueError):
            d["feature_vector"] = {}
        result.append(d)
    return result


def count_by_reason(days_back: Optional[int] = 30) -> Dict[str, int]:
    """Đếm số lượng rejected signals theo rejection_reason."""
    ensure_table()
    where = ""
    params = []
    if days_back is not None:
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
        where = "WHERE timestamp >= ?"
        params.append(cutoff)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT rejection_reason, COUNT(*) as cnt "
            f"FROM {TABLE_NAME} {where} "
            f"GROUP BY rejection_reason ORDER BY cnt DESC",
            params,
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_counterfactual_returns(window_days: int = 30) -> np.ndarray:
    """Lấy simulated returns từ rejected signals để đưa vào QuantStatsBridge.

    Trả về numpy array các returns đã simulated từ các rejected signals.
    Ưu tiên simulated_exit_20d > 10d > 5d.
    """
    ensure_table()
    cutoff = (datetime.now() - timedelta(days=window_days * 2)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT simulated_exit_5d, simulated_exit_10d, simulated_exit_20d "
            f"FROM {TABLE_NAME} WHERE timestamp >= ? ORDER BY id DESC",
            (cutoff,),
        ).fetchall()
    returns = []
    for r in rows:
        val = r[2] if r[2] is not None else (r[1] if r[1] is not None else r[0])
        if val is not None:
            # simulated_exit là % change, convert về decimal return
            ret = float(val) / 100.0
            # Cap ±100% để tránh outlier phá hỏng metrics
            returns.append(max(-1.0, min(ret, 1.0)))
    if not returns:
        return np.array([])
    return np.array(returns[-window_days:], dtype=np.float64)


def update_simulated_exit(record_id: int, horizon_days: int, exit_return_pct: float):
    """Cập nhật simulated exit return cho một rejected signal.

    Args:
        record_id: id trong rejected_signals_archive
        horizon_days: 5, 10, hoặc 20
        exit_return_pct: % thay đổi từ entry đến exit (VD: -2.5 = -2.5%)
    """
    ensure_table()
    col = f"simulated_exit_{horizon_days}d"
    with get_connection() as conn:
        conn.execute(
            f"UPDATE {TABLE_NAME} SET {col} = ? WHERE id = ?",
            (exit_return_pct, record_id),
        )


def _compute_adv_20(symbol: str) -> float:
    """Average Daily Value (VND) 20 phiên — thanh khoản trung bình."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT close, volume FROM daily_ohlcv "
            "WHERE symbol = ? ORDER BY date DESC LIMIT 20",
            (symbol,),
        ).fetchall()
    if len(rows) < 5:
        return 0.0
    adv = np.mean([float(r[0]) * float(r[1]) for r in rows if r[0] and r[1]])
    return float(adv)


def _compute_atr_14(symbol: str) -> float:
    """Average True Range 14 phiên — biến động trung bình."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT high, low, close FROM daily_ohlcv "
            "WHERE symbol = ? ORDER BY date DESC LIMIT 15",
            (symbol,),
        ).fetchall()
    if len(rows) < 3:
        return 0.0
    df = pd.DataFrame(rows[::-1], columns=["high", "low", "close"])
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["prev_close"] = df["close"].shift(1)
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["prev_close"]),
            abs(df["low"] - df["prev_close"]),
        ),
    )
    atr = df["tr"].iloc[1:].mean()  # bỏ dòng đầu (NaN prev_close)
    return float(atr) if not np.isnan(atr) else 0.0


def _compute_dynamic_slippage(
    symbol: str,
    order_volume: float = 100_000_000,  # 100tr VND mặc định
) -> float:
    """Dynamic Slippage Model.

    Công thức:
      Slippage = max(MIN_SLIPPAGE, α × (OrderVol / ADV_20) + β × (ATR_14 / Close))

    Trả về hệ số slippage (1.0 = 0% slippage, 1.01 = 1% phụ phí).
    """
    adv = _compute_adv_20(symbol)
    atr = _compute_atr_14(symbol)

    # Lấy close price mới nhất
    with get_connection() as conn:
        row = conn.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    close_price = float(row[0]) if row and row[0] else 0.0

    if adv <= 0 or close_price <= 0:
        return 1.0 + MIN_SLIPPAGE  # fallback về 0.1%

    vol_ratio = order_volume / adv
    vol_component = ALPHA_IMPACT * vol_ratio

    atr_ratio = atr / close_price if atr > 0 else 0.0
    atr_component = BETA_VOLATILITY * atr_ratio

    slippage = max(MIN_SLIPPAGE, vol_component + atr_component)
    return 1.0 + slippage


def run_eod_update(order_volume: float = 100_000_000):
    """Chạy cuối mỗi phiên: cập nhật simulated_exit cho các signal còn thiếu.

    Args:
        order_volume: Khối lượng giao dịch giả định (VND) để tính slippage động.
                      Mặc định 100 triệu — tương đương 1 lệnh retail nhỏ.

    Sử dụng Dynamic Slippage Model thay vì hằng số 0.1% để trừng phạt
    các cổ phiếu thiếu thanh khoản bị Governor từ chối.
    """
    ensure_table()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, timestamp, ticker, feature_vector_json "
            f"FROM {TABLE_NAME} WHERE simulated_exit_5d IS NULL "
            f"ORDER BY id ASC"
        ).fetchall()

    if not rows:
        return 0

    with get_connection() as conn:
        ohlcv_rows = conn.execute(
            "SELECT symbol, date, adj_close FROM daily_ohlcv "
            "WHERE date >= (SELECT MIN(date) FROM daily_ohlcv) "
            "ORDER BY date"
        ).fetchall()

    price_map: Dict[str, Dict[str, float]] = {}
    for r in ohlcv_rows:
        sym = r[0]
        if sym not in price_map:
            price_map[sym] = {}
        price_map[sym][r[1]] = float(r[2])

    now = datetime.now().date()
    updated = 0
    for r in rows:
        record_id = r[0]
        ts_str = r[1]
        ticker = r[2]
        try:
            entry_date = datetime.fromisoformat(ts_str).date()
        except (ValueError, TypeError):
            entry_date = now

        ticker_prices = price_map.get(ticker, {})
        if not ticker_prices:
            continue

        entry_close = None
        sorted_dates = sorted(ticker_prices.keys())
        for d in sorted_dates:
            if d >= entry_date.isoformat():
                entry_close = ticker_prices[d]
                break
        if entry_close is None or entry_close == 0:
            continue

        # Dynamic slippage cho ticker này
        slippage_factor = _compute_dynamic_slippage(ticker, order_volume)

        with get_connection() as conn:
            for horizon in [5, 10, 20]:
                col = f"simulated_exit_{horizon}d"
                existing = conn.execute(
                    f"SELECT {col} FROM {TABLE_NAME} WHERE id = ?",
                    (record_id,)
                ).fetchone()[0]
                if existing is not None:
                    continue

                exit_date = None
                for d in sorted_dates:
                    if d > entry_date.isoformat():
                        days_diff = (datetime.fromisoformat(d).date() - entry_date).days
                        if days_diff >= horizon:
                            exit_date = d
                            break

                if exit_date and exit_date in ticker_prices:
                    exit_close = ticker_prices[exit_date]
                    exit_pct = ((exit_close / entry_close) - 1.0) * 100 * slippage_factor
                    conn.execute(
                        f"UPDATE {TABLE_NAME} SET {col} = ? WHERE id = ?",
                        (round(exit_pct, 4), record_id),
                    )
                    updated += 1

    return updated
