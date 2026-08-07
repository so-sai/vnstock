"""rejected_signals.py — Kho lưu tín hiệu bị từ chối + Evidence Ledger.

LAW-001 (Anti-Survivorship):
  Lưu cả tín hiệu bị từ chối để phân tích counterfactual.

Evidence Ledger (thay thế Rolling Window):
  Mỗi rejected signal lưu information_gain = abs(simulated_return) × prior_belief.
  IG tích lũy không bao giờ mất — chỉ có trọng số freshness decay.
  Hệ thống học từ tổng Information Gain, không từ số lượng reject.

Evidence Expiry:
  Mỗi signal có evaluation_horizon (7d/20d/60d/120d). Sau horizon,
  record tự động closed, không còn ảnh hưởng calibration.
"""

import json
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.database.db_core import get_connection

# Dynamic Slippage parameters
ALPHA_IMPACT = 0.3
BETA_VOLATILITY = 0.5
MIN_SLIPPAGE = 0.001

# Default evaluation horizon (phiên giao dịch)
DEFAULT_HORIZON = 60

TABLE_NAME = "rejected_signals_archive"


def ensure_table():
    """Tạo bảng nếu chưa tồn tại. Tự động migrate nếu thiếu cột mới."""
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
    # Migrate schema: thêm cột mới nếu chưa có
    for col, col_type in [
        ("evaluation_horizon", "TEXT DEFAULT '60d'"),
        ("valid_until", "TEXT"),
        ("information_gain", "REAL"),
        ("surprise", "REAL"),
        ("cumulative_ig", "REAL"),
        ("status", "TEXT DEFAULT 'ACTIVE'"),
        ("accepted_alternative", "TEXT"),
        ("alternative_return_5d", "REAL"),
        ("alternative_return_10d", "REAL"),
        ("alternative_return_20d", "REAL"),
    ]:
        try:
            with get_connection() as conn:
                conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {col} {col_type}")
        except Exception:  # noqa: BLE001, S110 - cố ý bắt rộng & bỏ qua phụ (fallback/phòng thủ)
            pass


RISK_FREE_RATE = 0.05  # 5%/năm — dùng cho CASH alternative


def record_rejected_signal(
    ticker: str,
    signal_type: str,
    rejection_reason: str,
    regime_score: float,
    adx_value: float,
    feature_vector: dict[str, Any],
    prior_belief: float,
    posterior_belief: float,
    evaluation_horizon: str = "60d",
    accepted_alternative: str | None = None,
) -> int:
    """Ghi một tín hiệu bị từ chối vào Evidence Ledger.

    Evidence Ledger:
      - information_gain ban đầu = 0 (sẽ update khi có simulated_exit)
      - valid_until = now + evaluation_horizon (mặc định 60 phiên)
      - status = 'ACTIVE' → 'CLOSED' sau khi hết hạn

    Args:
        ticker: Mã cổ phiếu (VIC, VHM, ...)
        signal_type: Loại tín hiệu (BREAKOUT, ABSORPTION, MACRO_ORDER)
        rejection_reason: Lý do từ chối (GOVERNOR_LOCK, ADX_HAIRCUT, ...)
        regime_score: Điểm regime tại thời điểm T
        adx_value: ADX tại thời điểm T
        feature_vector: Dict state vector đầy đủ
        prior_belief: Niềm tin Governor trước khi xét signal [0, 1]
        posterior_belief: Niềm tin Governor sau khi xét signal [0, 1]
        evaluation_horizon: 7d, 20d, 60d, 120d

    Returns:
        id của bản ghi mới
    """
    ensure_table()
    now = datetime.now()
    fv_json = json.dumps(feature_vector, ensure_ascii=False, default=str)

    # Parse horizon (e.g., "60d" → 60 days)
    try:
        horizon_days = int(evaluation_horizon.replace("d", ""))
    except ValueError, AttributeError:
        horizon_days = DEFAULT_HORIZON
    valid_until = (now + timedelta(days=horizon_days)).isoformat()

    with get_connection() as conn:
        cur = conn.execute(
            f"INSERT INTO {TABLE_NAME} "
            "(timestamp, ticker, signal_type, rejection_reason, "
            " regime_score, adx_value, feature_vector_json, "
            " prior_belief, posterior_belief, "
            " evaluation_horizon, valid_until, information_gain, surprise, status, "
            " accepted_alternative) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'ACTIVE', ?)",
            (
                now.isoformat(),
                ticker,
                signal_type,
                rejection_reason,
                regime_score,
                adx_value,
                fv_json,
                prior_belief,
                posterior_belief,
                evaluation_horizon,
                valid_until,
                accepted_alternative,
            ),
        )
        return cur.lastrowid


def get_rejected_signals(
    limit: int = 100,
    reason_filter: str | None = None,
    days_back: int | None = None,
) -> list[dict[str, Any]]:
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
        except json.JSONDecodeError, ValueError:
            d["feature_vector"] = {}
        result.append(d)
    return result


def count_by_reason(days_back: int | None = 30) -> dict[str, int]:
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
            f"SELECT rejection_reason, COUNT(*) as cnt FROM {TABLE_NAME} {where} GROUP BY rejection_reason ORDER BY cnt DESC",
            params,
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_counterfactual_returns(
    window_days: int = 30,
    weighted: bool = True,
) -> np.ndarray:
    """Lấy simulated returns từ Evidence Ledger (chỉ ACTIVE records).

    Evidence Ledger thay thế Rolling Window:
      - Chỉ lấy records có valid_until >= now (chưa hết hạn)
      - status = 'ACTIVE' (chưa closed)
      - Trả về returns array, ưu tiên simulated_exit_20d > 10d > 5d
      - weighted=True: lặp lại mỗi return theo trọng số information_gain
        để Governor học từ IG, không từ số lượng reject

    Returns:
        numpy array daily return (decimal)
    """
    ensure_table()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT simulated_exit_5d, simulated_exit_10d, simulated_exit_20d, "
            f"  information_gain "
            f"FROM {TABLE_NAME} "
            f"WHERE valid_until >= ? AND status = 'ACTIVE' "
            f"ORDER BY id DESC LIMIT ?",
            (now, window_days * 2),
        ).fetchall()
    returns = []
    for r in rows:
        val = r[2] if r[2] is not None else (r[1] if r[1] is not None else r[0])
        if val is not None:
            ret = max(-1.0, min(float(val) / 100.0, 1.0))
            if weighted and r[3] and r[3] > 0:
                w = int(max(1, min(r[3] * 10, 10)))
                returns.extend([ret] * w)
            else:
                returns.append(ret)
    if not returns:
        return np.array([])
    arr = np.array(returns[-window_days:], dtype=np.float64)
    return arr


def fetch_doc_returns(
    window_days: int = 30,
) -> list[dict[str, float]]:
    """Lấy cặp (alternative_return, simulated_return) để tính DOC_Index.

    DOC = Decision Opportunity Cost — đo độ lệch giữa lợi nhuận
    của mã được chọn và mã bị từ chối.

    Returns:
        List[Dict]: mỗi entry có rejected_return, alternative_return
    """
    ensure_table()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT simulated_exit_5d, simulated_exit_10d, simulated_exit_20d, "
            f"  alternative_return_5d, alternative_return_10d, alternative_return_20d "
            f"FROM {TABLE_NAME} "
            f"WHERE valid_until >= ? AND status = 'ACTIVE' "
            f"AND accepted_alternative IS NOT NULL "
            f"ORDER BY id DESC LIMIT ?",
            (now, window_days * 2),
        ).fetchall()
    pairs = []
    for r in rows:
        # Ưu tiên 20d > 10d > 5d
        sim = r[2] if r[2] is not None else (r[1] if r[1] is not None else r[0])
        alt = r[5] if r[5] is not None else (r[4] if r[4] is not None else r[3])
        if sim is not None and alt is not None:
            pairs.append(
                {
                    "rejected_return": float(sim) / 100.0,
                    "alternative_return": float(alt) / 100.0,
                }
            )
    return pairs[-window_days:]


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
            "SELECT close, volume FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 20",
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
            "SELECT high, low, close FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 15",
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


def _compute_information_gain(
    simulated_return_pct: float,
    prior_belief: float,
) -> tuple[float, float]:
    """Evidence Ledger: information_gain + surprise.

    Công thức:
      surprise = abs(simulated_return_pct / 100.0)  — mức độ sai lầm
      information_gain = surprise × prior_belief     — trọng số bởi độ tự tin

    Nếu Governor tự tin (prior=0.9) nhưng sai to (surprise=0.5):
      IG = 0.45 → Governor buộc phải revision mạnh

    Nếu Governor không chắc (prior=0.2) và sai (surprise=0.3):
      IG = 0.06 → không đáng kể, Governor đã biết nó không chắc
    """
    surprise = min(1.0, abs(simulated_return_pct) / 100.0)
    ig = round(surprise * max(0.0, min(prior_belief, 1.0)), 6)
    return (ig, round(surprise, 6))


def close_expired():
    """Đóng các record đã hết hạn (valid_until < now)."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE {TABLE_NAME} SET status = 'CLOSED' WHERE valid_until < ? AND status = 'ACTIVE'",
            (now,),
        )
        return cur.rowcount


def get_cumulative_information_gain() -> float:
    """Tổng IG tích lũy — không bao giờ mất, không decay.

    Đây là metric cốt lõi cho Structure Evolution Layer:
    Khi cumulative_ig tăng mạnh trong thời gian ngắn → cấu trúc thị trường
    đang thay đổi nhanh hơn khả năng giải thích của Governor.
    """
    ensure_table()
    with get_connection() as conn:
        row = conn.execute(f"SELECT SUM(cumulative_ig) FROM {TABLE_NAME}").fetchone()
        return float(row[0]) if row and row[0] else 0.0


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
            "SELECT symbol, date, adj_close FROM daily_ohlcv WHERE date >= (SELECT MIN(date) FROM daily_ohlcv) ORDER BY date"
        ).fetchall()

    price_map: dict[str, dict[str, float]] = {}
    for r in ohlcv_rows:
        sym = r[0]
        if sym not in price_map:
            price_map[sym] = {}
        price_map[sym][r[1]] = float(r[2])

    now = datetime.now()
    updated = 0
    for r in rows:
        record_id = r[0]
        ts_str = r[1]
        ticker = r[2]
        try:
            entry_date = datetime.fromisoformat(ts_str).date()
        except ValueError, TypeError:
            entry_date = now.date()

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

        slippage_factor = _compute_dynamic_slippage(ticker, order_volume)

        with get_connection() as conn:
            # Lấy prior_belief để tính IG
            pb_row = conn.execute(
                f"SELECT prior_belief FROM {TABLE_NAME} WHERE id = ?",
                (record_id,),
            ).fetchone()
            prior_belief = float(pb_row[0]) if pb_row and pb_row[0] else 0.5

            for horizon in [5, 10, 20]:
                col = f"simulated_exit_{horizon}d"
                existing = conn.execute(f"SELECT {col} FROM {TABLE_NAME} WHERE id = ?", (record_id,)).fetchone()[0]
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
                    exit_pct = round(exit_pct, 4)

                    # Evidence Ledger: compute IG + cumulative_ig
                    ig, surprise = _compute_information_gain(exit_pct, prior_belief)
                    # cumulative_ig = tổng IG của record này
                    conn.execute(
                        f"UPDATE {TABLE_NAME} SET {col} = ?, "
                        f"information_gain = MAX(information_gain, ?), "
                        f"surprise = MAX(surprise, ?), "
                        f"cumulative_ig = COALESCE(cumulative_ig, 0) + ? "
                        f"WHERE id = ?",
                        (exit_pct, ig, surprise, ig, record_id),
                    )
                    updated += 1

    # === DOC: Cập nhật alternative_return cho accepted_alternative ===
    with get_connection() as conn:
        alt_rows = conn.execute(
            f"SELECT id, timestamp, accepted_alternative "
            f"FROM {TABLE_NAME} WHERE accepted_alternative IS NOT NULL "
            f"AND alternative_return_5d IS NULL AND status = 'ACTIVE'"
        ).fetchall()

    for ar in alt_rows:
        record_id = ar[0]
        ts_str = ar[1]
        alt_ticker = ar[2]
        try:
            entry_date = datetime.fromisoformat(ts_str).date()
        except ValueError, TypeError:
            entry_date = now.date()

        with get_connection() as conn:
            for horizon in [5, 10, 20]:
                col = f"alternative_return_{horizon}d"
                existing = conn.execute(f"SELECT {col} FROM {TABLE_NAME} WHERE id = ?", (record_id,)).fetchone()[0]
                if existing is not None:
                    continue

                if alt_ticker == "CASH":
                    # Risk-free rate return
                    frac_year = horizon / 365.0
                    rf_return = ((1.0 + RISK_FREE_RATE) ** frac_year - 1.0) * 100
                    conn.execute(
                        f"UPDATE {TABLE_NAME} SET {col} = ? WHERE id = ?",
                        (round(rf_return, 4), record_id),
                    )
                    updated += 1
                else:
                    alt_prices = price_map.get(alt_ticker, {})
                    if not alt_prices:
                        continue
                    alt_sorted = sorted(alt_prices.keys())
                    entry_close = None
                    for d in alt_sorted:
                        if d >= entry_date.isoformat():
                            entry_close = alt_prices[d]
                            break
                    if entry_close is None or entry_close == 0:
                        continue

                    exit_date = None
                    for d in alt_sorted:
                        if d > entry_date.isoformat():
                            days_diff = (datetime.fromisoformat(d).date() - entry_date).days
                            if days_diff >= horizon:
                                exit_date = d
                                break

                    if exit_date and exit_date in alt_prices:
                        exit_close = alt_prices[exit_date]
                        # Alternative không bị slippage (không fill thật)
                        alt_pct = ((exit_close / entry_close) - 1.0) * 100
                        conn.execute(
                            f"UPDATE {TABLE_NAME} SET {col} = ? WHERE id = ?",
                            (round(alt_pct, 4), record_id),
                        )
                        updated += 1

    # Đóng các record hết hạn
    close_expired()

    return updated
