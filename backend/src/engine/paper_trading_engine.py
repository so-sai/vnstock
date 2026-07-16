"""paper_trading_engine.py — Phân hệ Giả lập Thời gian thực (Paper Trading)

Mục tiêu (Production Readiness Gate):
  Chạy Cronjob EOD 16:00 hàng ngày, sinh lệnh từ tín hiệu SEL/Macro/Absorption,
  KHÔNG đẩy lên sàn — chỉ ghi vào paper_trades_log. Kéo dài 30-45 phiên để tự
  chứng minh tính ổn định của W1/AQ/HDR trước khi nạp vốn thực.

Đối chiếu Live vs Backtest (trả lời câu hỏi khai thác sâu):
  1. LATENCY giả lập: decision(close T) → order → fill(open T+1). Mô hình
     base_latency + jitter theo giờ đặt lệnh, ghi latency_ms.
  2. SLIPPAGE giả lập: chênh lệch giữa giá quyết định (backtest ideal, close T)
     và giá khớp thực tế (realistic, open T+1 + market impact theo size/ADV).
  3. REJECTION RATE: mô phỏng API Vietcap từ chối (rate limit, price band ±7%
     HOSE, thanh khoản thấp). Ghi is_rejected + reject_reason.
  4. RECONCILIATION: lưu song song backtest_fill_price vs paper_fill_price,
     tính tracking_error_bps để đo phân kỳ Live-vs-Theory.

Nguyên tắc offline: engine CHỈ đọc daily_ohlcv/macro cục bộ, KHÔNG gọi API.
"""
import hashlib
import json
import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


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
import src.config  # noqa: E402
from src.database.db_core import get_connection  # noqa: E402

logger = logging.getLogger("PTCK_SYSTEM")

# --- Constants: Simulation Parameters -----------------------------------------
# HOSE price band (biên độ dao động) — dùng cho rejection khi giá vượt band.
HOSE_PRICE_BAND = 0.07
# Latency model (mô phỏng độ trễ end-to-end decision→fill, đơn vị ms).
BASE_LATENCY_MS = 180.0        # median round-trip API Vietcap giả định
LATENCY_JITTER_MS = 120.0      # dao động ngẫu nhiên (network/queue)
# Market impact: slippage ~ eta * sqrt(order_notional / ADV_notional).
IMPACT_ETA = 0.10              # hệ số tác động thị trường (10 bps @ 1x ADV)
# Base rejection probability (rate limit / transient API errors).
BASE_REJECT_PROB = 0.02
# Fixed transaction cost (phí giao dịch + thuế bán, xấp xỉ), bps.
FEE_BPS = 15.0                 # ~0.15% mua; bán cộng thêm thuế 0.1%
SELL_TAX_BPS = 10.0

PAPER_PORTFOLIO_ID = "SEL_PAPER_V1"


class PaperTradingEngine:
    """Engine giả lập thực thi lệnh + đối chiếu Live-vs-Backtest.

    Deterministic: mọi yếu tố "ngẫu nhiên" (latency, rejection) được sinh từ
    seed = hash(symbol|date|portfolio) → tái lập 100% khi audit.
    """

    def __init__(self, portfolio_id: str = PAPER_PORTFOLIO_ID,
                 offline: bool = True):
        self.portfolio_id = portfolio_id
        self.offline = offline
        self._ensure_schema()

    # ------------------------------------------------------------------ schema
    def _ensure_schema(self):
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades_log (
                    trade_id            TEXT PRIMARY KEY,
                    portfolio_id        TEXT NOT NULL,
                    decision_date       TEXT NOT NULL,   -- ngày T (tín hiệu sinh)
                    fill_date           TEXT,            -- ngày T+1 (khớp thực tế)
                    symbol              TEXT NOT NULL,
                    side                TEXT NOT NULL,   -- BUY / SELL
                    signal_source       TEXT,            -- SEL / MACRO / ABSORPTION
                    quantity            INTEGER NOT NULL,
                    -- Giá tham chiếu
                    decision_price      REAL,            -- close T (giá quyết định)
                    backtest_fill_price REAL,            -- lý thuyết: khớp @ close T
                    paper_fill_price    REAL,            -- thực tế: open T+1 + impact
                    -- Vi mô thực thi (câu hỏi khai thác sâu)
                    latency_ms          REAL,
                    slippage_bps        REAL,            -- (paper - backtest)/backtest
                    market_impact_bps   REAL,
                    is_rejected         INTEGER DEFAULT 0,
                    reject_reason       TEXT,
                    -- Đối chiếu Live vs Backtest
                    tracking_error_bps  REAL,
                    fee_bps             REAL,
                    -- Ngữ cảnh Governor tại thời điểm ra lệnh
                    hdr_at_decision     REAL,
                    w1_at_decision      REAL,
                    macro_state         TEXT,
                    notes               TEXT,
                    created_at          TEXT,
                    UNIQUE(portfolio_id, decision_date, symbol, side)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_paper_trades_date
                ON paper_trades_log(decision_date)
            """)
            # Bảng tổng hợp hiệu suất theo phiên (dùng cho báo cáo ổn định 30-45 phiên)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_performance_daily (
                    portfolio_id        TEXT NOT NULL,
                    date                TEXT NOT NULL,
                    n_orders            INTEGER,
                    n_filled            INTEGER,
                    n_rejected          INTEGER,
                    rejection_rate      REAL,
                    avg_latency_ms      REAL,
                    avg_slippage_bps    REAL,
                    avg_tracking_err_bps REAL,
                    realized_pnl_bps    REAL,
                    w1                  REAL,
                    avg_hdr             REAL,
                    macro_state         TEXT,
                    created_at          TEXT,
                    PRIMARY KEY(portfolio_id, date)
                )
            """)
            conn.commit()

    # -------------------------------------------------------- deterministic RNG
    def _rng(self, *keys) -> np.random.Generator:
        """RNG tất định từ hash(keys) → tái lập audit 100%."""
        raw = "|".join(str(k) for k in keys).encode("utf-8")
        seed = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
        return np.random.default_rng(seed)

    # ------------------------------------------------------------- price lookup
    def _get_ohlcv(self, symbol: str, date: str) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT open, high, low, close, volume FROM daily_ohlcv "
                "WHERE symbol = ? AND date = ?",
                (symbol, date)
            ).fetchone()
        if not row:
            return None
        return {"open": row[0], "high": row[1], "low": row[2],
                "close": row[3], "volume": row[4]}

    def _get_next_trading_ohlcv(self, symbol: str, decision_date: str
                                ) -> Tuple[Optional[str], Optional[Dict]]:
        """OHLCV của phiên giao dịch KẾ TIẾP sau decision_date (T+1)."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT date, open, high, low, close, volume FROM daily_ohlcv "
                "WHERE symbol = ? AND date > ? ORDER BY date ASC LIMIT 1",
                (symbol, decision_date)
            ).fetchone()
        if not row:
            return None, None
        return row[0], {"open": row[1], "high": row[2], "low": row[3],
                        "close": row[4], "volume": row[5]}

    def _get_adv_notional(self, symbol: str, date: str, lookback: int = 20
                          ) -> float:
        """Average Daily Value (thanh khoản) — dùng cho market impact model."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT close, volume FROM daily_ohlcv "
                "WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT ?",
                (symbol, date, lookback)
            ).fetchall()
        if not rows:
            return 0.0
        notionals = [float(c) * float(v) for c, v in rows if c and v]
        return float(np.mean(notionals)) if notionals else 0.0

    # --------------------------------------------------------- core simulation
    def simulate_fill(self, symbol: str, side: str, quantity: int,
                      decision_date: str, signal_source: str = "SEL",
                      hdr: Optional[float] = None, w1: Optional[float] = None,
                      macro_state: str = "UNKNOWN") -> Dict:
        """Giả lập vòng đời một lệnh: decision(T) → order → fill(T+1).

        Trả về dict đầy đủ latency/slippage/rejection/tracking_error.
        """
        rng = self._rng(self.portfolio_id, decision_date, symbol, side)

        dec_bar = self._get_ohlcv(symbol, decision_date)
        fill_date, fill_bar = self._get_next_trading_ohlcv(symbol, decision_date)

        result = {
            "trade_id": self._trade_id(decision_date, symbol, side),
            "portfolio_id": self.portfolio_id,
            "decision_date": decision_date,
            "fill_date": fill_date,
            "symbol": symbol,
            "side": side.upper(),
            "signal_source": signal_source,
            "quantity": int(quantity),
            "hdr_at_decision": hdr,
            "w1_at_decision": w1,
            "macro_state": macro_state,
            "is_rejected": 0,
            "reject_reason": None,
            "created_at": datetime.now().isoformat(),
        }

        # --- Guard: thiếu dữ liệu giá ---
        if dec_bar is None or fill_bar is None:
            result["is_rejected"] = 1
            result["reject_reason"] = "NO_PRICE_DATA"
            return result

        decision_price = float(dec_bar["close"])
        result["decision_price"] = round(decision_price, 4)

        # --- 1. LATENCY giả lập (decision → fill acknowledgement) ---
        latency = BASE_LATENCY_MS + abs(rng.normal(0, LATENCY_JITTER_MS))
        result["latency_ms"] = round(float(latency), 1)

        # --- 2. REJECTION giả lập (API Vietcap) ---
        reject_reason = self._check_rejection(
            symbol, side, quantity, decision_date, decision_price,
            fill_bar, rng
        )
        if reject_reason:
            result["is_rejected"] = 1
            result["reject_reason"] = reject_reason
            return result

        # --- 3. Backtest ideal fill: khớp ngay @ close T (không trễ, không impact)
        backtest_fill = decision_price
        result["backtest_fill_price"] = round(backtest_fill, 4)

        # --- 4. Paper realistic fill: open T+1 + market impact ---
        base_fill = float(fill_bar["open"])  # thực tế khớp phiên kế tiếp
        adv = self._get_adv_notional(symbol, decision_date)
        order_notional = quantity * base_fill
        if adv > 0:
            impact_frac = IMPACT_ETA * math.sqrt(order_notional / adv) / 100.0
        else:
            impact_frac = IMPACT_ETA / 100.0
        # BUY chịu impact dương (giá lên), SELL chịu impact âm (giá xuống)
        direction = 1.0 if side.upper() == "BUY" else -1.0
        paper_fill = base_fill * (1.0 + direction * impact_frac)
        result["paper_fill_price"] = round(paper_fill, 4)
        result["market_impact_bps"] = round(impact_frac * 10000.0, 2)

        # --- 5. Slippage: paper vs backtest ---
        slippage_frac = direction * (paper_fill - backtest_fill) / backtest_fill
        result["slippage_bps"] = round(slippage_frac * 10000.0, 2)

        # --- 6. Tracking error (Live vs Backtest divergence) ---
        tracking_err = abs(paper_fill - backtest_fill) / backtest_fill
        result["tracking_error_bps"] = round(tracking_err * 10000.0, 2)

        # --- 7. Fee ---
        fee = FEE_BPS + (SELL_TAX_BPS if side.upper() == "SELL" else 0.0)
        result["fee_bps"] = round(fee, 2)

        return result

    def _check_rejection(self, symbol, side, quantity, decision_date,
                         decision_price, fill_bar, rng) -> Optional[str]:
        """Mô phỏng cơ chế từ chối lệnh của API Vietcap."""
        # (a) Price band: nếu open T+1 vượt biên ±7% so với close T → limit up/down,
        #     lệnh thị trường có thể không khớp.
        fill_open = float(fill_bar["open"])
        move = (fill_open - decision_price) / decision_price
        if side.upper() == "BUY" and move >= HOSE_PRICE_BAND * 0.99:
            return "PRICE_BAND_LIMIT_UP"   # trần, không mua được
        if side.upper() == "SELL" and move <= -HOSE_PRICE_BAND * 0.99:
            return "PRICE_BAND_LIMIT_DOWN"  # sàn, không bán được

        # (b) Thanh khoản: order > 10% ADV → có thể bị từ chối một phần/toàn phần.
        adv = self._get_adv_notional(symbol, decision_date)
        order_notional = quantity * decision_price
        if adv > 0 and order_notional > 0.10 * adv:
            # xác suất từ chối tỉ lệ với mức vượt thanh khoản
            excess = min((order_notional / adv - 0.10) / 0.40, 1.0)
            if rng.random() < 0.5 * excess:
                return "INSUFFICIENT_LIQUIDITY"

        # (c) Transient API error / rate limit (deterministic per trade).
        if rng.random() < BASE_REJECT_PROB:
            return "API_RATE_LIMIT"

        return None

    def _trade_id(self, decision_date, symbol, side) -> str:
        raw = f"{self.portfolio_id}|{decision_date}|{symbol}|{side}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    # ---------------------------------------------------------------- persist
    def record_trade(self, fill: Dict):
        cols = [
            "trade_id", "portfolio_id", "decision_date", "fill_date", "symbol",
            "side", "signal_source", "quantity", "decision_price",
            "backtest_fill_price", "paper_fill_price", "latency_ms",
            "slippage_bps", "market_impact_bps", "is_rejected", "reject_reason",
            "tracking_error_bps", "fee_bps", "hdr_at_decision", "w1_at_decision",
            "macro_state", "notes", "created_at",
        ]
        vals = [fill.get(c) for c in cols]
        placeholders = ",".join("?" * len(cols))
        with get_connection() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO paper_trades_log ({','.join(cols)}) "
                f"VALUES ({placeholders})",
                vals
            )
            conn.commit()

    def summarize_daily(self, date: str, w1: Optional[float] = None,
                        macro_state: str = "UNKNOWN") -> Dict:
        """Tổng hợp hiệu suất phiên → paper_performance_daily."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT is_rejected, latency_ms, slippage_bps, "
                "tracking_error_bps, hdr_at_decision "
                "FROM paper_trades_log "
                "WHERE portfolio_id = ? AND decision_date = ?",
                (self.portfolio_id, date)
            ).fetchall()

        n_orders = len(rows)
        if n_orders == 0:
            return {"date": date, "n_orders": 0}

        filled = [r for r in rows if not r[0]]
        rejected = [r for r in rows if r[0]]
        n_filled = len(filled)
        n_rejected = len(rejected)

        def _avg(idx, subset):
            vals = [r[idx] for r in subset if r[idx] is not None]
            return float(np.mean(vals)) if vals else 0.0

        summary = {
            "portfolio_id": self.portfolio_id,
            "date": date,
            "n_orders": n_orders,
            "n_filled": n_filled,
            "n_rejected": n_rejected,
            "rejection_rate": round(n_rejected / n_orders, 4),
            "avg_latency_ms": round(_avg(1, filled), 1),
            "avg_slippage_bps": round(_avg(2, filled), 2),
            "avg_tracking_err_bps": round(_avg(3, filled), 2),
            "realized_pnl_bps": 0.0,  # cập nhật ở giai đoạn mark-to-market sau
            "w1": w1,
            "avg_hdr": round(_avg(4, rows), 4),
            "macro_state": macro_state,
            "created_at": datetime.now().isoformat(),
        }

        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO paper_performance_daily "
                "(portfolio_id, date, n_orders, n_filled, n_rejected, "
                "rejection_rate, avg_latency_ms, avg_slippage_bps, "
                "avg_tracking_err_bps, realized_pnl_bps, w1, avg_hdr, "
                "macro_state, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (summary["portfolio_id"], summary["date"], summary["n_orders"],
                 summary["n_filled"], summary["n_rejected"],
                 summary["rejection_rate"], summary["avg_latency_ms"],
                 summary["avg_slippage_bps"], summary["avg_tracking_err_bps"],
                 summary["realized_pnl_bps"], summary["w1"], summary["avg_hdr"],
                 summary["macro_state"], summary["created_at"])
            )
            conn.commit()
        return summary

    # --------------------------------------------------------- signal → orders
    def generate_orders_from_signals(self, decision_date: str,
                                     watchlist: Optional[List[str]] = None,
                                     capital: float = 1_000_000_000.0) -> Dict:
        """Sinh lệnh giả lập từ SEL + Macro Governor + Per-Symbol Absorption.

        Logic sizing tuân thủ HDR: HDR=1.0 → cash-only (không mua).
        """
        if watchlist is None:
            watchlist = ['FPT', 'VCB', 'HPG', 'VNM', 'TCB']

        # Governor context (offline)
        from src.engine.macro_governor import MacroGovernor
        from src.engine.structure_evolution import StructureEvolutionLayer

        try:
            sel = StructureEvolutionLayer.assess_global(
                as_of=decision_date, offline=self.offline)
            w1 = sel.get("w1")
            sel_hdr = sel.get("hdr_limit")
        except Exception as e:
            logger.warning(f"[PAPER] SEL assess failed: {e}")
            w1, sel_hdr = None, None

        try:
            macro = MacroGovernor.assess_global()
            macro_state = macro.get("state", "UNKNOWN")
            macro_hdr = macro.get("hdr_override")
        except Exception as e:
            logger.warning(f"[PAPER] Macro assess failed: {e}")
            macro_state, macro_hdr = "UNKNOWN", None

        # HDR hiệu lực = max(sel, macro) — bảo thủ nhất
        hdr_candidates = [h for h in (sel_hdr, macro_hdr) if h is not None]
        effective_hdr = max(hdr_candidates) if hdr_candidates else 0.0

        results = {"decision_date": decision_date, "orders": [],
                   "w1": w1, "hdr": effective_hdr, "macro_state": macro_state}

        # HDR=1.0 → cash-only, không sinh lệnh mua
        if effective_hdr >= 0.999:
            results["note"] = "HDR=1.0 CASH_ONLY — không sinh lệnh mua."
            self.summarize_daily(decision_date, w1=w1, macro_state=macro_state)
            return results

        # Vốn khả dụng cho equity = capital * (1 - HDR)
        deployable = capital * (1.0 - effective_hdr)
        per_symbol = deployable / max(len(watchlist), 1)

        for sym in watchlist:
            bar = self._get_ohlcv(sym, decision_date)
            if not bar or not bar["close"]:
                continue
            price = float(bar["close"])
            qty = int((per_symbol // price) // 100 * 100)  # lô 100
            if qty < 100:
                continue
            fill = self.simulate_fill(
                sym, "BUY", qty, decision_date, signal_source="SEL_MACRO",
                hdr=effective_hdr, w1=w1, macro_state=macro_state)
            self.record_trade(fill)
            results["orders"].append(fill)

        summary = self.summarize_daily(decision_date, w1=w1,
                                       macro_state=macro_state)
        results["summary"] = summary
        return results

    # ------------------------------------------------------------- reporting
    def get_stability_report(self, lookback_sessions: int = 45) -> Dict:
        """Báo cáo ổn định W1/AQ/HDR + latency/slippage/rejection qua N phiên."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT date, n_orders, n_filled, n_rejected, rejection_rate, "
                "avg_latency_ms, avg_slippage_bps, avg_tracking_err_bps, "
                "w1, avg_hdr, macro_state "
                "FROM paper_performance_daily WHERE portfolio_id = ? "
                "ORDER BY date DESC LIMIT ?",
                (self.portfolio_id, lookback_sessions)
            ).fetchall()

        if not rows:
            return {"status": "NO_DATA", "sessions": 0}

        arr = list(reversed(rows))  # chronological
        w1_series = [r[8] for r in arr if r[8] is not None]
        rej_series = [r[4] for r in arr if r[4] is not None]
        slip_series = [r[6] for r in arr if r[6] is not None]
        lat_series = [r[5] for r in arr if r[5] is not None]

        def _stats(s):
            if not s:
                return {"mean": 0, "std": 0, "min": 0, "max": 0}
            a = np.array(s, dtype=float)
            return {"mean": round(float(a.mean()), 4),
                    "std": round(float(a.std()), 4),
                    "min": round(float(a.min()), 4),
                    "max": round(float(a.max()), 4)}

        return {
            "status": "OK",
            "portfolio_id": self.portfolio_id,
            "sessions": len(arr),
            "sessions_target": 45,
            "readiness": "READY" if len(arr) >= 30 else "COLLECTING",
            "w1_stability": _stats(w1_series),
            "rejection_rate": _stats(rej_series),
            "slippage_bps": _stats(slip_series),
            "latency_ms": _stats(lat_series),
            "daily": [
                {"date": r[0], "orders": r[1], "filled": r[2],
                 "rejected": r[3], "rej_rate": r[4], "latency": r[5],
                 "slippage": r[6], "w1": r[8], "hdr": r[9], "macro": r[10]}
                for r in arr
            ],
        }

    # ----------------------------------------------------------------- static
    @staticmethod
    def run_daily(decision_date: Optional[str] = None,
                  offline: bool = True) -> Dict:
        """Entry point cho cronjob EOD."""
        if decision_date is None:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM daily_ohlcv"
                ).fetchone()
            decision_date = row[0] if row and row[0] else \
                datetime.now().strftime("%Y-%m-%d")
        engine = PaperTradingEngine(offline=offline)
        return engine.generate_orders_from_signals(decision_date)

    @staticmethod
    def print_report(result: Dict, lang: str = "vi"):
        """In báo cáo Paper Trading CLI."""
        print(f"\n{'=' * 65}")
        print(f"  PAPER TRADING ENGINE — Giả lập Thời gian thực")
        print(f"{'=' * 65}")
        if result.get("status") == "NO_DATA":
            print("  Chưa có dữ liệu paper trading. Chạy: python ptck.py paper run")
            print(f"{'=' * 65}\n")
            return

        # Nếu là kết quả run
        if "orders" in result:
            print(f"  Ngày quyết định   : {result['decision_date']}")
            print(f"  Trạng thái Vĩ mô  : {result.get('macro_state')}")
            print(f"  W1                : {result.get('w1')}")
            print(f"  HDR hiệu lực      : {result.get('hdr')}")
            if result.get("note"):
                print(f"  Ghi chú           : {result['note']}")
            print(f"\n  -- Lệnh sinh ({len(result['orders'])}) --")
            for o in result["orders"]:
                status = "REJECTED" if o.get("is_rejected") else "FILLED"
                print(f"  {o['symbol']:6s} {o['side']:4s} x{o['quantity']:>7,} "
                      f"[{status}]")
                if o.get("is_rejected"):
                    print(f"         reject: {o.get('reject_reason')}")
                else:
                    print(f"         backtest={o.get('backtest_fill_price')} "
                          f"paper={o.get('paper_fill_price')} "
                          f"slip={o.get('slippage_bps')}bps "
                          f"lat={o.get('latency_ms')}ms")
            s = result.get("summary", {})
            if s and s.get("n_orders"):
                print(f"\n  -- Tổng hợp phiên --")
                print(f"  Lệnh: {s['n_orders']}  Khớp: {s['n_filled']}  "
                      f"Từ chối: {s['n_rejected']} "
                      f"({s['rejection_rate']:.1%})")
                print(f"  Latency TB: {s['avg_latency_ms']}ms  "
                      f"Slippage TB: {s['avg_slippage_bps']}bps  "
                      f"Tracking err: {s['avg_tracking_err_bps']}bps")
            print(f"{'=' * 65}\n")
            return

        # Báo cáo stability
        print(f"  Portfolio         : {result['portfolio_id']}")
        print(f"  Phiên thu thập    : {result['sessions']} / "
              f"{result['sessions_target']}")
        print(f"  Trạng thái        : {result['readiness']}")
        print(f"\n  -- Ổn định W1 (Wasserstein) --")
        w = result["w1_stability"]
        print(f"  mean={w['mean']}  std={w['std']}  "
              f"min={w['min']}  max={w['max']}")
        print(f"\n  -- Tỷ lệ Từ chối --")
        r = result["rejection_rate"]
        print(f"  mean={r['mean']:.1%}  std={r['std']:.4f}  max={r['max']:.1%}")
        print(f"\n  -- Slippage (bps) --")
        sl = result["slippage_bps"]
        print(f"  mean={sl['mean']}  std={sl['std']}  max={sl['max']}")
        print(f"\n  -- Latency (ms) --")
        la = result["latency_ms"]
        print(f"  mean={la['mean']}  std={la['std']}  max={la['max']}")
        print(f"{'=' * 65}\n")
