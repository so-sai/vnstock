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

# --- Catch-up Execution Rule --------------------------------------------------
# Kill-switch trượt giá: nếu open_{T+k} lệch quá ngưỡng này so với giá mục tiêu
# (close_T), lệnh bù bị HỦY do tín hiệu đã biến chất (Signal Decay). Tuyệt đối
# không truy đuổi giá (chasing). Có thể override qua tham số hàm.
CATCHUP_MAX_SLIPPAGE_PCT = 3.0   # 3.0% = 0.03


class PaperTradingEngine:
    """Engine giả lập thực thi lệnh + đối chiếu Live-vs-Backtest.

    Deterministic: mọi yếu tố "ngẫu nhiên" (latency, rejection) được sinh từ
    seed = hash(symbol|date|portfolio) → tái lập 100% khi audit.
    """

    def __init__(self, portfolio_id: str = PAPER_PORTFOLIO_ID,
                 offline: bool = True,
                 initial_capital: float = 1_000_000_000.0):
        self.portfolio_id = portfolio_id
        self.offline = offline
        self.initial_capital = initial_capital
        self._ensure_schema()
        # Sổ cái Mark-to-Market (cost basis, T+2.5 settlement, corporate actions)
        from src.engine.paper_mtm import PaperMtM
        self.mtm = PaperMtM(portfolio_id=portfolio_id,
                            initial_capital=initial_capital)

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
            # ----------------------------------------------------------------
            # HÀNG ĐỢI LỆNH BÙ (Catch-up Queue) — Persistent, Idempotent
            # ----------------------------------------------------------------
            # Khi backfill một ngày lỡ T còn trong cửa sổ tín hiệu (<=3 phiên),
            # lệnh KHÔNG khớp ngay tại giá lịch sử (chống lookback execution).
            # Thay vào đó đóng gói vào hàng đợi BỀN VỮNG này với target_price =
            # close_T (giá quyết định) + ngữ cảnh Governor đóng băng tại T.
            #
            # QUAN TRỌNG (Cash consistency — trả lời câu hỏi khai thác sâu):
            #   Hàng đợi KHÔNG dùng cơ chế reservation (không trừ settled_cash,
            #   không tạo lot). Do buying_power = min(settled_cash, cap - deployed)
            #   chỉ tính trên lot ĐÃ khớp thật, một lệnh treo KHÔNG khóa sức mua.
            #   → Khi Kill-switch hủy lệnh, KHÔNG có tiền nào bị giam để "reclaim".
            #   → Không cần hàm reclaim_cash (sẽ tạo bút toán ma). Tính nhất quán
            #     tiền mặt tại T+k được bảo toàn TỰ ĐỘNG, miễn là queue được xử lý
            #     TRƯỚC khi get_buying_power(T+k) được gọi để sinh tín hiệu mới.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_catchup_queue (
                    trade_id        TEXT PRIMARY KEY,   -- idempotency key
                    portfolio_id    TEXT NOT NULL,
                    decision_date   TEXT NOT NULL,       -- ngày T (tín hiệu lỡ)
                    symbol          TEXT NOT NULL,
                    side            TEXT NOT NULL,
                    target_qty      INTEGER NOT NULL,    -- qty tính tại T (sizing gốc)
                    target_price    REAL NOT NULL,       -- close_T (giá mục tiêu)
                    signal_source   TEXT,
                    hdr_at_decision REAL,
                    w1_at_decision  REAL,
                    macro_state     TEXT,
                    status          TEXT NOT NULL DEFAULT 'PENDING',
                                    -- PENDING / FILLED / REJECTED_SIGNAL_DECAY / EXPIRED
                    recovery_date   TEXT,                -- ngày T+k thực thi
                    exec_price      REAL,                -- giá khớp thực (open_{T+k})
                    decay_pct       REAL,                -- |open_Tk - close_T|/close_T
                    filled_qty      INTEGER,
                    notes           TEXT,
                    created_at      TEXT,
                    updated_at      TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_catchup_queue_status
                ON paper_catchup_queue(portfolio_id, status)
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

    # ==================================================== CATCH-UP QUEUE
    def _enqueue_catchup_order(self, symbol: str, side: str, target_qty: int,
                               target_price: float, decision_date: str,
                               signal_source: str = "SEL_MACRO",
                               hdr: Optional[float] = None,
                               w1: Optional[float] = None,
                               macro_state: str = "UNKNOWN") -> Dict:
        """Nạp một lệnh bù vào hàng đợi bền vững (KHÔNG khớp, KHÔNG khóa tiền).

        Idempotent: dùng trade_id = hash(pf|decision_date|symbol|side) làm PK →
        chạy lại backfill không tạo bản ghi trùng. target_price = close_T.
        """
        trade_id = self._trade_id(decision_date, symbol, side)
        now = datetime.now().isoformat()
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO paper_catchup_queue "
                "(trade_id, portfolio_id, decision_date, symbol, side, target_qty, "
                "target_price, signal_source, hdr_at_decision, w1_at_decision, "
                "macro_state, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'PENDING', ?, ?)",
                (trade_id, self.portfolio_id, decision_date, symbol, side.upper(),
                 int(target_qty), float(target_price), signal_source, hdr, w1,
                 macro_state, now, now)
            )
            conn.commit()
        return {"trade_id": trade_id, "symbol": symbol, "side": side.upper(),
                "target_qty": int(target_qty), "target_price": round(target_price, 4),
                "decision_date": decision_date, "status": "QUEUED_CATCHUP"}

    def process_catchup_queue(self, recovery_date: str,
                              max_slippage_pct: float = CATCHUP_MAX_SLIPPAGE_PCT
                              ) -> Dict:
        """Xử lý hàng đợi lệnh bù tại ngày phục hồi T+k. GỌI TRƯỚC khi sinh
        tín hiệu mới của T+k (để buying_power của T+k phản ánh đúng phần vốn
        đã tiêu cho lệnh bù — bảo toàn tính nhất quán tiền mặt).

        Với mỗi lệnh PENDING:
          1. CHUYỂN VỊ GIÁ: lấy open_{T+k} (thanh khoản THỰC tại ngày phục hồi).
          2. KILL-SWITCH: Decay = |open_Tk - target_close_T| / target_close_T.
             Nếu Decay > max_slippage_pct% → REJECTED_SIGNAL_DECAY (không truy giá).
          3. SIZING RECONCILIATION: tính lại qty theo buying_power HIỆN TẠI và
             open_{T+k} (giá có thể đã tăng → qty giảm để không vượt sức mua).
          4. book_buy tại open_{T+k}. book_buy tự reject nếu vẫn thiếu tiền.

        Cash consistency: KHÔNG reclaim — queue chưa từng khóa tiền. Lệnh bị hủy
        không giải phóng gì; lệnh khớp trừ settled_cash tại book_buy → get_buying_power
        của T+k tự thấy đúng.
        """
        with get_connection() as conn:
            pending = conn.execute(
                "SELECT trade_id, decision_date, symbol, side, target_qty, "
                "target_price, hdr_at_decision FROM paper_catchup_queue "
                "WHERE portfolio_id=? AND status='PENDING' "
                "ORDER BY decision_date ASC",
                (self.portfolio_id,)
            ).fetchall()

        report = {"recovery_date": recovery_date, "processed": 0,
                  "filled": [], "rejected_decay": [], "rejected_other": []}
        if not pending:
            return report

        for (trade_id, dec_date, symbol, side, target_qty, target_price,
             hdr_at_dec) in pending:
            report["processed"] += 1
            # --- 1. CHUYỂN VỊ GIÁ: open_{T+k} = thanh khoản thực ---
            bar = self._get_ohlcv(symbol, recovery_date)
            if not bar or not bar.get("open"):
                # Không có giá mở cửa ngày phục hồi → để PENDING, thử lại lần sau
                logger.warning(f"[CATCHUP-Q] {symbol}: thiếu open @ {recovery_date} "
                               f"— giữ PENDING.")
                continue
            open_tk = float(bar["open"])

            # --- 2. KILL-SWITCH: đo phân rã tín hiệu ---
            decay = abs(open_tk - target_price) / target_price if target_price else 1.0
            decay_pct = decay * 100.0
            if decay_pct > max_slippage_pct:
                self._update_catchup_status(
                    trade_id, "REJECTED_SIGNAL_DECAY", recovery_date=recovery_date,
                    exec_price=open_tk, decay_pct=decay_pct, filled_qty=0,
                    notes=f"Decay {decay_pct:.2f}% > {max_slippage_pct}% — không truy giá.")
                report["rejected_decay"].append(
                    {"symbol": symbol, "decision_date": dec_date,
                     "target_price": round(target_price, 2),
                     "open_tk": round(open_tk, 2), "decay_pct": round(decay_pct, 2)})
                logger.warning(
                    f"[CATCHUP-Q] {symbol} {dec_date}: KILL-SWITCH — decay "
                    f"{decay_pct:.2f}% > {max_slippage_pct}% → hủy (signal decay).")
                continue

            # --- 3. SIZING RECONCILIATION: size lại theo giá THỰC + sức mua HIỆN TẠI ---
            eff_hdr = hdr_at_dec if hdr_at_dec is not None else 0.0
            bp = self.mtm.get_buying_power(recovery_date, eff_hdr)
            # qty tối đa mua được tại open_Tk (đã gồm phí mua), làm tròn lô 100
            from src.engine.paper_mtm import BUY_FEE_BPS
            unit_cost = open_tk * (1.0 + BUY_FEE_BPS / 10000.0)
            max_affordable = int((bp // unit_cost) // 100 * 100) if unit_cost > 0 else 0
            exec_qty = min(int(target_qty), max_affordable)

            if exec_qty < 100:
                self._update_catchup_status(
                    trade_id, "REJECTED_INSUFFICIENT_BP", recovery_date=recovery_date,
                    exec_price=open_tk, decay_pct=decay_pct, filled_qty=0,
                    notes=f"Sức mua {bp:,.0f} không đủ 1 lô @ {open_tk:.0f}.")
                report["rejected_other"].append(
                    {"symbol": symbol, "decision_date": dec_date,
                     "reason": "INSUFFICIENT_BP", "buying_power": round(bp, 0)})
                continue

            # --- 4. Ép khớp tại open_{T+k} qua MtM ---
            book = self.mtm.book_buy(symbol=symbol, quantity=exec_qty,
                                     fill_price=open_tk, fill_date=recovery_date,
                                     hdr_limit=eff_hdr)
            if book["status"] != "FILLED":
                self._update_catchup_status(
                    trade_id, "REJECTED_" + book["status"], recovery_date=recovery_date,
                    exec_price=open_tk, decay_pct=decay_pct, filled_qty=0,
                    notes=f"book_buy: {book['status']}")
                report["rejected_other"].append(
                    {"symbol": symbol, "decision_date": dec_date,
                     "reason": book["status"]})
                continue

            self._update_catchup_status(
                trade_id, "FILLED", recovery_date=recovery_date,
                exec_price=open_tk, decay_pct=decay_pct, filled_qty=exec_qty,
                notes=f"Transposed fill @ open_{recovery_date}={open_tk:.0f} "
                      f"(target close_{dec_date}={target_price:.0f})")
            # Ghi vào nhật ký giao dịch để audit (fill_date = recovery_date)
            self._record_catchup_trade(
                trade_id, dec_date, recovery_date, symbol, side, exec_qty,
                target_price, open_tk, decay_pct, book, hdr_at_dec)
            report["filled"].append(
                {"symbol": symbol, "decision_date": dec_date,
                 "recovery_date": recovery_date, "exec_price": round(open_tk, 2),
                 "qty": exec_qty, "decay_pct": round(decay_pct, 2),
                 "target_qty": int(target_qty)})
            logger.info(
                f"[CATCHUP-Q] {symbol} {dec_date}: FILLED @ open_{recovery_date}="
                f"{open_tk:.0f} qty={exec_qty} (decay {decay_pct:.2f}%).")

        return report

    def _update_catchup_status(self, trade_id: str, status: str,
                               recovery_date: str = None, exec_price: float = None,
                               decay_pct: float = None, filled_qty: int = None,
                               notes: str = None):
        with get_connection() as conn:
            conn.execute(
                "UPDATE paper_catchup_queue SET status=?, recovery_date=?, "
                "exec_price=?, decay_pct=?, filled_qty=?, notes=?, updated_at=? "
                "WHERE trade_id=?",
                (status, recovery_date, exec_price, decay_pct, filled_qty, notes,
                 datetime.now().isoformat(), trade_id)
            )
            conn.commit()

    def _record_catchup_trade(self, trade_id, decision_date, recovery_date,
                              symbol, side, qty, target_price, exec_price,
                              decay_pct, book, hdr):
        """Ghi lệnh bù đã khớp vào paper_trades_log (audit trail)."""
        slippage_bps = (exec_price - target_price) / target_price * 10000.0 \
            if target_price else 0.0
        fill = {
            "trade_id": trade_id, "portfolio_id": self.portfolio_id,
            "decision_date": decision_date, "fill_date": recovery_date,
            "symbol": symbol, "side": side.upper(), "signal_source": "SEL_MACRO_CATCHUP",
            "quantity": int(qty), "decision_price": round(target_price, 4),
            "backtest_fill_price": round(target_price, 4),
            "paper_fill_price": round(exec_price, 4),
            "latency_ms": None, "slippage_bps": round(slippage_bps, 2),
            "market_impact_bps": None, "is_rejected": 0, "reject_reason": None,
            "tracking_error_bps": round(abs(slippage_bps), 2), "fee_bps": FEE_BPS,
            "hdr_at_decision": hdr, "w1_at_decision": None,
            "macro_state": "CATCHUP_TRANSPOSED",
            "notes": f"Catch-up transposed fill; decay={decay_pct:.2f}%; "
                     f"cost_basis={book.get('cost_basis')}; settle={book.get('settle_date')}",
            "created_at": datetime.now().isoformat(),
        }
        self.record_trade(fill)

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
                                     capital: float = 1_000_000_000.0,
                                     mtm_only: bool = False,
                                     catchup_enqueue: bool = False) -> Dict:
        """Sinh lệnh giả lập từ SEL + Macro Governor, hạch toán qua MtM.

        Quy trình EOD (đúng thứ tự kế toán):
          1. Đầu phiên: process_settlements (tiền/CK T+2 về) + apply corporate actions.
          2. Lấy Governor context (HDR, W1, macro state).
          3. Sinh lệnh MUA, sizing theo MtM buying power (tôn trọng HDR + T+2.5).
          4. book_buy qua MtM (kiểm tra sức mua thực tế, tạo lot + settle_date).
          5. Cuối phiên: mark_to_market → equity curve + Unrealized/Realized P&L.

        Args:
          mtm_only: STALE-SIGNAL GUARD (Catch-up). True → CHỈ chạy bước 1 (settle +
            corporate action) và bước 5 (mark-to-market), BỎ QUA sinh lệnh mới.
            Dùng khi bù một ngày nợ quá cũ: tín hiệu giao dịch đã hết hiệu lực,
            nhưng dòng tiền/cổ tức/settlement của ngày đó VẪN phải được hạch toán
            để đường cong tài sản liền mạch và đúng kế toán.
          catchup_enqueue: CATCH-UP EXECUTION RULE. True → tín hiệu (W1/HDR đóng
            băng tại T) vẫn được tính, NHƯNG lệnh KHÔNG khớp tại giá lịch sử.
            Thay vào đó NẠP VÀO hàng đợi paper_catchup_queue với target_price =
            close_T. Lệnh sẽ được ép khớp tại open_{T+k} (giá thanh khoản thực) khi
            process_catchup_queue() chạy ở ngày phục hồi — chống lookback execution.
            Bước 1 (settle/CA) và bước 5 (MtM vị thế CŨ) VẪN chạy đầy đủ để giữ
            đường cong tài sản liền mạch qua vùng hổng.
        """
        if watchlist is None:
            watchlist = ['FPT', 'VCB', 'HPG', 'VNM', 'TCB']

        # --- 1. Đầu phiên: settle T+2 + áp dụng sự kiện doanh nghiệp trong đêm ---
        self.mtm.process_settlements(decision_date)
        self.mtm.apply_corporate_actions(decision_date)

        # --- STALE-SIGNAL GUARD: bù ngày cũ chỉ hạch toán, không phát lệnh mới ---
        if mtm_only:
            summary = self.summarize_daily(decision_date, w1=None,
                                           macro_state="CATCHUP_MTM_ONLY")
            mtm_res = self.mtm.mark_to_market(decision_date, hdr_limit=0.0)
            return {"decision_date": decision_date, "orders": [],
                    "mtm_only": True, "note": "Stale-signal guard: chỉ MtM/settle.",
                    "summary": summary, "mtm": mtm_res}

        # --- 2. Governor context (offline) ---
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

        # --- 3. Sức mua thực tế từ MtM (đã tôn trọng HDR + tiền đã settle) ---
        buying_power = self.mtm.get_buying_power(decision_date, effective_hdr)
        results["buying_power"] = round(buying_power, 0)

        # HDR=1.0 hoặc hết sức mua → cash-only
        if effective_hdr >= 0.999 or buying_power < 1e6:
            results["note"] = ("HDR=1.0 CASH_ONLY" if effective_hdr >= 0.999
                               else "Sức mua < 1tr — không mua thêm.")
            self.summarize_daily(decision_date, w1=w1, macro_state=macro_state)
            results["mtm"] = self.mtm.mark_to_market(decision_date, effective_hdr)
            return results

        per_symbol = buying_power / max(len(watchlist), 1)

        for sym in watchlist:
            bar = self._get_ohlcv(sym, decision_date)
            if not bar or not bar["close"]:
                continue
            price = float(bar["close"])
            qty = int((per_symbol // price) // 100 * 100)  # lô 100
            if qty < 100:
                continue

            # --- CATCH-UP: nạp lệnh vào hàng đợi, KHÔNG khớp tại giá lịch sử ---
            if catchup_enqueue:
                enq = self._enqueue_catchup_order(
                    symbol=sym, side="BUY", target_qty=qty, target_price=price,
                    decision_date=decision_date, signal_source="SEL_MACRO",
                    hdr=effective_hdr, w1=w1, macro_state=macro_state)
                results["orders"].append(enq)
                continue

            # Giả lập vi mô thực thi (latency/slippage/rejection)
            fill = self.simulate_fill(
                sym, "BUY", qty, decision_date, signal_source="SEL_MACRO",
                hdr=effective_hdr, w1=w1, macro_state=macro_state)

            # Nếu API reject → chỉ ghi log, không hạch toán MtM
            if fill.get("is_rejected"):
                self.record_trade(fill)
                results["orders"].append(fill)
                continue

            # --- 4. Hạch toán MtM: book_buy tại giá paper fill (T+1) ---
            book = self.mtm.book_buy(
                symbol=sym, quantity=qty,
                fill_price=fill["paper_fill_price"],
                fill_date=fill.get("fill_date") or decision_date,
                hdr_limit=effective_hdr)
            fill["mtm_status"] = book["status"]
            if book["status"] != "FILLED":
                # MtM từ chối (hết buying power thực) → đánh dấu reject
                fill["is_rejected"] = 1
                fill["reject_reason"] = book["status"]
            else:
                fill["settle_date"] = book["settle_date"]
                fill["cost_basis"] = book["cost_basis"]
            self.record_trade(fill)
            results["orders"].append(fill)

        summary = self.summarize_daily(decision_date, w1=w1,
                                       macro_state=macro_state)
        results["summary"] = summary
        # --- 5. Cuối phiên: mark-to-market ---
        results["mtm"] = self.mtm.mark_to_market(decision_date, effective_hdr)
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
                  offline: bool = True,
                  mtm_only: bool = False,
                  catchup_enqueue: bool = False) -> Dict:
        """Entry point cho cronjob EOD.

        Args:
          mtm_only: True → chỉ hạch toán MtM/settlement (Catch-up stale guard).
          catchup_enqueue: True → nạp lệnh vào hàng đợi bù (không khớp giá lịch sử).
        """
        if decision_date is None:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM daily_ohlcv"
                ).fetchone()
            decision_date = row[0] if row and row[0] else \
                datetime.now().strftime("%Y-%m-%d")
        engine = PaperTradingEngine(offline=offline)
        return engine.generate_orders_from_signals(
            decision_date, mtm_only=mtm_only, catchup_enqueue=catchup_enqueue)

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
            # MtM snapshot (nếu có)
            m = result.get("mtm")
            if m:
                print(f"\n  -- Mark-to-Market --")
                print(f"  Tổng vốn (Equity) : {m['total_equity']:>16,.0f}  "
                      f"({m['total_return_pct']:+.2f}%)")
                print(f"  Sức mua khả dụng  : {m['buying_power']:>16,.0f}")
                print(f"  Unrealized (net)  : {m['unrealized_pnl_net']:>16,.0f}")
                print(f"  Realized (luỹ kế) : {m['realized_pnl_cum']:>16,.0f}")
                if m.get("unsettled_qty_val"):
                    print(f"  Hàng chưa settle  : {m['unsettled_qty_val']:>16,.0f}")
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
