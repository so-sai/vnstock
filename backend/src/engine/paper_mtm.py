"""paper_mtm.py — Mark-to-Market P&L cho Paper Trading (chuẩn kế toán VN).

Giải quyết 3 giới hạn bắt buộc để đạt tiêu chuẩn Forward Testing:

  1. REAL-TIME VALUATION: Unrealized P&L @ close T, chiết khấu phí giao dịch
     cố định + thuế TNCN chiều bán (0.1% giá trị bán).

  2. CASH FLOW ACCOUNTING: Quản lý sức mua (buying power), đảm bảo không chi
     vượt HDR_limit do Governor ấn định. Mô hình vòng đời thanh toán T+2.5
     (Unsettled Inventory) đặc thù TTCK Việt Nam.

  3. CORPORATE ACTION ADJUSTMENT: Cổ tức tiền mặt, cổ tức cổ phiếu, chia tách
     đồng bộ vào Cost Basis ngay trong đêm (ex-date) — chống báo lỗ giả tạo.

VÒNG ĐỜI THANH TOÁN T+2.5 (câu hỏi khai thác sâu):
  TTCK Việt Nam (HOSE/HNX) thanh toán T+2, tiền/CK về tài khoản ~13:00 ngày T+2
  ("T+2.5"). Hệ quả kế toán bắt buộc:
    - MUA @ T: cổ phiếu chưa về đến ~T+2 chiều → KHÔNG được bán trước T+2
      (chống "bán khống giả tạo" trên hàng chưa settle).
    - BÁN @ T: tiền chưa về đến ~T+2 chiều → sức mua KHẢ DỤNG chưa tăng ngay,
      chỉ có "sức mua ứng trước" (nếu cho phép). Mặc định: KHÔNG ứng trước
      → Governor không được cấp vốn vượt cash đã settle tại T+1.
  Ta mô hình mỗi lot có settle_date = trading_day(T, +2). Inventory chia:
    - settled_qty:   bán được, tính vào sức mua khi bán.
    - unsettled_qty: đang chờ về, KHÓA (không bán).
"""
import json
import logging
import sys
from contextlib import contextmanager
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

# --- Kế toán giao dịch TTCK Việt Nam --------------------------------------
SETTLEMENT_DAYS = 2          # T+2 (tiền/CK về ~13:00 T+2 → "T+2.5")
BUY_FEE_BPS = 15.0           # phí mua ~0.15%
SELL_FEE_BPS = 15.0          # phí bán ~0.15%
SELL_TAX_BPS = 10.0          # thuế TNCN chuyển nhượng 0.1% giá trị bán
# Ứng trước tiền bán (Cash Advance): phí ~0.04%/ngày chờ (lãi suất ứng trước CTCK).
CASH_ADVANCE_FEE_BPS_PER_DAY = 4.0   # 0.04%/ngày = 4 bps/ngày
DEFAULT_PORTFOLIO_ID = "SEL_PAPER_V1"
DEFAULT_INITIAL_CAPITAL = 1_000_000_000.0


@contextmanager
def _conn_or(conn):
    """Trả connection dùng chung (Global Transaction) hoặc mở mới.

    - conn không None → dùng chung cursor, KHÔNG tự commit (tầng cao quản lý).
    - conn None → mở connection riêng, TỰ ĐỘNG commit khi block thoát sạch
      (tương thích ngược cho test / lệnh thủ công `paper run`).
    """
    if conn is not None:
        yield conn
    else:
        with get_connection() as c:
            yield c
            c.commit()


class PaperMtM:
    """Sổ cái Mark-to-Market cho paper trading — chuẩn kế toán VN + T+2.5."""

    def __init__(self, portfolio_id: str = DEFAULT_PORTFOLIO_ID,
                 initial_capital: float = DEFAULT_INITIAL_CAPITAL):
        self.portfolio_id = portfolio_id
        self.initial_capital = initial_capital
        self._ensure_schema()
        self._ensure_portfolio()

    # ------------------------------------------------------------------ schema
    def _ensure_schema(self):
        with get_connection() as conn:
            # Sổ trạng thái danh mục (tiền + tổng quan)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_portfolio_state (
                    portfolio_id      TEXT PRIMARY KEY,
                    initial_capital   REAL NOT NULL,
                    settled_cash      REAL NOT NULL,   -- tiền đã về (dùng được)
                    pending_cash_in   REAL DEFAULT 0,  -- tiền bán chờ về (T+2)
                    created_at        TEXT,
                    updated_at        TEXT
                )
            """)
            # Từng lot mua (theo dõi cost basis + settlement riêng lẻ)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_lots (
                    lot_id            TEXT PRIMARY KEY,
                    portfolio_id      TEXT NOT NULL,
                    symbol            TEXT NOT NULL,
                    open_date         TEXT NOT NULL,    -- ngày mua (fill T+1)
                    settle_date       TEXT NOT NULL,    -- ngày CK về (T+2)
                    quantity          INTEGER NOT NULL, -- SL còn lại của lot
                    original_qty      INTEGER NOT NULL,
                    cost_basis        REAL NOT NULL,    -- giá vốn/CP (đã gồm phí mua)
                    is_closed         INTEGER DEFAULT 0,
                    created_at        TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lots_pf_sym
                ON paper_lots(portfolio_id, symbol, is_closed)
            """)
            # Dòng tiền (audit trail)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_cash_ledger (
                    entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    portfolio_id      TEXT NOT NULL,
                    date              TEXT NOT NULL,
                    entry_type        TEXT NOT NULL,  -- BUY/SELL/DIVIDEND/FEE/TAX/SETTLE
                    symbol            TEXT,
                    amount            REAL NOT NULL,  -- +vào / -ra settled_cash
                    settle_date       TEXT,           -- khi nào ảnh hưởng settled_cash
                    settled           INTEGER DEFAULT 0,
                    notes             TEXT,
                    created_at        TEXT
                )
            """)
            # P&L đã hiện thực hóa (mỗi lần bán/khớp)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_realized_pnl (
                    entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    portfolio_id      TEXT NOT NULL,
                    date              TEXT NOT NULL,
                    symbol            TEXT NOT NULL,
                    quantity          INTEGER NOT NULL,
                    cost_basis        REAL,
                    sell_price        REAL,
                    gross_pnl         REAL,
                    fees              REAL,
                    tax               REAL,
                    net_pnl           REAL,
                    created_at        TEXT
                )
            """)
            # Sự kiện doanh nghiệp
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_corporate_actions (
                    action_id         TEXT PRIMARY KEY,
                    symbol            TEXT NOT NULL,
                    ex_date           TEXT NOT NULL,
                    action_type       TEXT NOT NULL,  -- CASH_DIV/STOCK_DIV/SPLIT
                    cash_per_share    REAL DEFAULT 0, -- CASH_DIV: đồng/CP
                    ratio             REAL DEFAULT 0, -- STOCK_DIV/SPLIT: tỉ lệ (vd 0.1=10%)
                    applied           INTEGER DEFAULT 0,
                    notes             TEXT,
                    created_at        TEXT
                )
            """)
            # Snapshot equity theo phiên (đường cong vốn)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_equity_curve (
                    portfolio_id      TEXT NOT NULL,
                    date              TEXT NOT NULL,
                    settled_cash      REAL,
                    pending_cash_in   REAL,
                    market_value      REAL,       -- giá trị CP @ close T
                    unrealized_pnl    REAL,
                    realized_pnl_cum  REAL,
                    total_equity      REAL,
                    unsettled_qty_val REAL,       -- giá trị hàng chưa settle
                    buying_power      REAL,
                    hdr_limit         REAL,
                    created_at        TEXT,
                    PRIMARY KEY(portfolio_id, date)
                )
            """)
            conn.commit()

    def _ensure_portfolio(self):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT portfolio_id FROM paper_portfolio_state WHERE portfolio_id=?",
                (self.portfolio_id,)
            ).fetchone()
            if not row:
                now = datetime.now().isoformat()
                conn.execute(
                    "INSERT INTO paper_portfolio_state "
                    "(portfolio_id, initial_capital, settled_cash, pending_cash_in, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (self.portfolio_id, self.initial_capital, self.initial_capital,
                     0.0, now, now)
                )
                conn.commit()

    # -------------------------------------------------- trading-day arithmetic
    def _next_trading_days(self, date: str, n: int) -> str:
        """Trả về ngày giao dịch thứ n SAU 'date' (dựa trên daily_ohlcv)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM daily_ohlcv WHERE date > ? "
                "ORDER BY date ASC LIMIT ?",
                (date, n)
            ).fetchall()
        if len(rows) >= n:
            return rows[n - 1][0]
        # Fallback: cộng ngày dương lịch (khi thiếu dữ liệu tương lai)
        d = datetime.strptime(date, "%Y-%m-%d")
        # xấp xỉ: +n ngày làm việc
        added = 0
        while added < n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                added += 1
        return d.strftime("%Y-%m-%d")

    # ------------------------------------------------------------ price lookup
    def _get_close(self, symbol: str, date: str) -> Optional[float]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT close FROM daily_ohlcv WHERE symbol=? AND date<=? "
                "ORDER BY date DESC LIMIT 1",
                (symbol, date)
            ).fetchone()
        return float(row[0]) if row and row[0] else None

    # ----------------------------------------------------- portfolio state I/O
    def _get_state(self) -> Dict:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT initial_capital, settled_cash, pending_cash_in "
                "FROM paper_portfolio_state WHERE portfolio_id=?",
                (self.portfolio_id,)
            ).fetchone()
        return {"initial_capital": row[0], "settled_cash": row[1],
                "pending_cash_in": row[2]}

    def _update_cash(self, settled_delta: float = 0.0,
                      pending_delta: float = 0.0, conn=None):
        # ACID: nếu có conn (Global Transaction) → execute trên cursor chung,
        # KHÔNG commit (quyền commit thuộc tầng cao). Ngược lại tự mở + commit.
        with _conn_or(conn) as __c:
            __c.execute(
                "UPDATE paper_portfolio_state SET "
                "settled_cash = settled_cash + ?, "
                "pending_cash_in = pending_cash_in + ?, updated_at=? "
                "WHERE portfolio_id=?",
                (settled_delta, pending_delta, datetime.now().isoformat(),
                 self.portfolio_id)
            )

    def _ledger(self, date, entry_type, amount, symbol=None, settle_date=None,
                settled=0, notes=None, conn=None):
        with _conn_or(conn) as __c:
            __c.execute(
                "INSERT INTO paper_cash_ledger (portfolio_id, date, entry_type, "
                "symbol, amount, settle_date, settled, notes, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (self.portfolio_id, date, entry_type, symbol, amount,
                 settle_date, settled, notes, datetime.now().isoformat())
            )

    # ============================================================ SETTLEMENT
    def process_settlements(self, date: str, conn=None):
        """Xử lý các khoản đến hạn settle TÍNH ĐẾN 'date' (đầu phiên T).

        - Tiền bán (pending_cash_in) có settle_date <= date → chuyển sang settled.
        - Lot mua có settle_date <= date → cổ phiếu 'về' (settled_qty tự động
          suy ra qua so sánh settle_date, không cần cột riêng).

        ACID: nhận conn (Global Transaction) để execute chung; không tự commit.
        """
        with _conn_or(conn) as __c:
            pending = __c.execute(
                "SELECT entry_id, amount FROM paper_cash_ledger "
                "WHERE portfolio_id=? AND entry_type='SELL_PROCEEDS' "
                "AND settled=0 AND settle_date <= ?",
                (self.portfolio_id, date)
            ).fetchall()
            total_settled_in = 0.0
            for entry_id, amount in pending:
                total_settled_in += amount
                __c.execute(
                    "UPDATE paper_cash_ledger SET settled=1 WHERE entry_id=?",
                    (entry_id,)
                )

        if total_settled_in != 0:
            self._update_cash(settled_delta=total_settled_in,
                              pending_delta=-total_settled_in, conn=conn)
            self._ledger(date, "SETTLE", total_settled_in,
                         notes="Sell proceeds settled (T+2)", conn=conn)
            logger.info(f"[MtM] Settled sell proceeds: {total_settled_in:,.0f}")

    # ================================================================== BUY
    def book_buy(self, symbol: str, quantity: int, fill_price: float,
                  fill_date: str, hdr_limit: float = 0.0, conn=None) -> Dict:
        """Hạch toán lệnh MUA đã khớp (T+1 fill).

        Kiểm tra sức mua (buying power) tôn trọng HDR_limit. Tạo lot với
        settle_date = fill_date + 2 phiên (T+2). Trừ tiền + phí ngay (settled_cash).

        ACID: nhận conn (Global Transaction) để execute chung; không tự commit.
        """
        gross = quantity * fill_price
        fee = gross * BUY_FEE_BPS / 10000.0
        total_cost = gross + fee

        # --- Sức mua tôn trọng HDR: chỉ được dùng (1 - HDR) * initial_capital ---
        bp = self.get_buying_power(fill_date, hdr_limit)
        if total_cost > bp + 1e-6:
            return {"status": "REJECTED_INSUFFICIENT_BUYING_POWER",
                    "required": round(total_cost, 0),
                    "available": round(bp, 0),
                    "hdr_limit": hdr_limit}

        settle_date = self._next_trading_days(fill_date, SETTLEMENT_DAYS)
        cost_basis = total_cost / quantity  # giá vốn đã gồm phí

        lot_id = f"{self.portfolio_id}|{symbol}|{fill_date}|{fill_price:.2f}"
        with _conn_or(conn) as __c:
            __c.execute(
                "INSERT OR REPLACE INTO paper_lots (lot_id, portfolio_id, symbol, "
                "open_date, settle_date, quantity, original_qty, cost_basis, "
                "is_closed, created_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
                (lot_id, self.portfolio_id, symbol, fill_date, settle_date,
                 quantity, quantity, cost_basis, datetime.now().isoformat())
            )

        self._update_cash(settled_delta=-total_cost, conn=conn)
        self._ledger(fill_date, "BUY", -total_cost, symbol=symbol,
                     settle_date=settle_date, settled=1,
                     notes=f"qty={quantity} px={fill_price} fee={fee:.0f}",
                     conn=conn)

        return {"status": "FILLED", "lot_id": lot_id, "symbol": symbol,
                "quantity": quantity, "cost_basis": round(cost_basis, 2),
                "settle_date": settle_date, "total_cost": round(total_cost, 0),
                "fee": round(fee, 0)}

    # ================================================================= SELL
    def book_sell(self, symbol: str, quantity: int, fill_price: float,
                   fill_date: str, conn=None) -> Dict:
        """Hạch toán lệnh BÁN (FIFO). CHỈ bán được hàng ĐÃ SETTLE (chống bán khống).

        T+2.5: tiền bán về sau 2 phiên → ghi pending_cash_in, settle_date=T+2.

        ACID: nhận conn (Global Transaction) để execute chung; không tự commit.
        """
        sellable = self.get_sellable_qty(symbol, fill_date)
        if quantity > sellable:
            return {"status": "REJECTED_UNSETTLED_INVENTORY",
                    "requested": quantity, "sellable_settled": sellable,
                    "note": "Không thể bán hàng chưa về (T+2.5). Chống bán khống giả."}

        with _conn_or(conn) as __c:
            lots = __c.execute(
                "SELECT lot_id, quantity, cost_basis FROM paper_lots "
                "WHERE portfolio_id=? AND symbol=? AND is_closed=0 "
                "AND settle_date <= ? ORDER BY open_date ASC",
                (self.portfolio_id, symbol, fill_date)
            ).fetchall()

            remaining = quantity
            total_cost_matched = 0.0
            for lot_id, lot_qty, cost_basis in lots:
                if remaining <= 0:
                    break
                take = min(remaining, lot_qty)
                total_cost_matched += take * cost_basis
                new_qty = lot_qty - take
                __c.execute(
                    "UPDATE paper_lots SET quantity=?, is_closed=? WHERE lot_id=?",
                    (new_qty, 1 if new_qty == 0 else 0, lot_id)
                )
                remaining -= take

            gross_proceeds = quantity * fill_price
            fee = gross_proceeds * SELL_FEE_BPS / 10000.0
            tax = gross_proceeds * SELL_TAX_BPS / 10000.0
            net_proceeds = gross_proceeds - fee - tax

            gross_pnl = gross_proceeds - total_cost_matched
            net_pnl = net_proceeds - total_cost_matched

            settle_date = self._next_trading_days(fill_date, SETTLEMENT_DAYS)
            self._update_cash(pending_delta=net_proceeds, conn=__c)
            self._ledger(fill_date, "SELL_PROCEEDS", net_proceeds, symbol=symbol,
                         settle_date=settle_date, settled=0,
                         notes=f"qty={quantity} px={fill_price}", conn=__c)

            __c.execute(
                "INSERT INTO paper_realized_pnl (portfolio_id, date, symbol, "
                "quantity, cost_basis, sell_price, gross_pnl, fees, tax, net_pnl, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.portfolio_id, fill_date, symbol, quantity,
                 round(total_cost_matched / quantity, 4), fill_price,
                 round(gross_pnl, 2), round(fee, 2), round(tax, 2),
                 round(net_pnl, 2), datetime.now().isoformat())
            )

        return {"status": "FILLED", "symbol": symbol, "quantity": quantity,
                "gross_pnl": round(gross_pnl, 0), "net_pnl": round(net_pnl, 0),
                "fee": round(fee, 0), "tax": round(tax, 0),
                "proceeds_settle_date": settle_date}

    # ==================================================== BUYING POWER / QTY
    def get_buying_power(self, date: str, hdr_limit: float = 0.0,
                         allow_cash_advance: bool = False) -> float:
        """Sức mua khả dụng, TÔN TRỌNG HDR_limit và T+2.5.

        Sức mua cơ sở = min(settled_cash, cap_theo_HDR - đã_triển_khai).
        HDR=1.0 → cap = 0 (cash-only). HDR=0 → cap = initial_capital.

        allow_cash_advance:
          - False (mặc định): KHÔNG dùng pending_cash_in (tiền bán chưa về)
            → chống cấp vốn vượt sức mua thực tế tại T+1.
          - True: cộng dồn pending_cash_in KHẢ DỤNG (ứng trước), nhưng trừ phí
            ứng trước ≈ 0.04%/ngày × số ngày chờ đến settle. Tối ưu vòng quay vốn
            nhưng tốn chi phí — mô phỏng nghiệp vụ ứng trước tiền bán của CTCK.
        """
        state = self._get_state()
        settled = state["settled_cash"]

        # Vốn tối đa được phép triển khai vào equity theo HDR
        equity_cap = state["initial_capital"] * (1.0 - hdr_limit)
        deployed = self._get_deployed_cost(date)
        headroom = max(equity_cap - deployed, 0.0)

        available_cash = settled
        if allow_cash_advance:
            available_cash += self._advanceable_cash(date)

        # Sức mua = min(tiền khả dụng, headroom HDR)
        return min(available_cash, headroom)

    def _advanceable_cash(self, date: str) -> float:
        """Tiền bán chưa settle có thể ứng trước, ĐÃ TRỪ phí ứng trước.

        RÀNG BUỘC KẾ TOÁN (Forward Testing integrity):
          - Trading calendar (T+2 PHIÊN) xác định settle_date của khoản bán.
          - Phí ứng trước = net × 0.04% × CALENDAR DAYS (số ngày LỊCH thực tế
            từ 'date' đến settle_date, GỒM cuối tuần/lễ). Đây là chi phí lãi
            thực tế của CTCK — tiền bị giam qua ngày nghỉ vẫn tính lãi.

        Việc dùng calendar days (thay vì trading days) chống bơm khống P&L do
        tính thiếu phí qua các ngày nghỉ lễ/cuối tuần.
        """
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT amount, settle_date FROM paper_cash_ledger "
                "WHERE portfolio_id=? AND entry_type='SELL_PROCEEDS' "
                "AND settled=0 AND settle_date > ?",
                (self.portfolio_id, date)
            ).fetchall()
        total = 0.0
        for amount, settle_date in rows:
            # Số phiên giao dịch còn lại (để biết đã đến hạn hay chưa)
            trading_days_left = self._trading_days_between(date, settle_date)
            if trading_days_left <= 0:
                # đã đến hạn (sẽ settle) → ứng full, không phí
                total += amount
                continue
            # Phí tính theo CALENDAR DAYS (gồm cuối tuần/lễ)
            calendar_days = self._calendar_days_between(date, settle_date)
            fee = (amount * CASH_ADVANCE_FEE_BPS_PER_DAY
                   * calendar_days / 10000.0)
            total += max(amount - fee, 0.0)
        return total

    @staticmethod
    def _calendar_days_between(start: str, end: str) -> int:
        """Số NGÀY LỊCH giữa start (không tính) và end (tính) — gồm cuối tuần/lễ.

        Ví dụ: bán thứ Sáu, settle thứ Ba → trading=2 phiên nhưng calendar=4 ngày.
        """
        if end <= start:
            return 0
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
        return (d1 - d0).days

    def _trading_days_between(self, start: str, end: str) -> int:
        """Số phiên giao dịch giữa start (không tính) và end (tính) — dựa daily_ohlcv."""
        if end <= start:
            return 0
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM daily_ohlcv "
                "WHERE date > ? AND date <= ?",
                (start, end)
            ).fetchone()
        return int(row[0]) if row and row[0] else 0

    def _get_deployed_cost(self, date: str) -> float:
        """Tổng giá vốn CP đang nắm giữ (chưa bán) — vốn đã triển khai."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT quantity, cost_basis FROM paper_lots "
                "WHERE portfolio_id=? AND is_closed=0 AND open_date<=?",
                (self.portfolio_id, date)
            ).fetchall()
        return sum(q * cb for q, cb in rows)

    def get_sellable_qty(self, symbol: str, date: str) -> int:
        """SL cổ phiếu ĐÃ SETTLE (settle_date <= date) — mới được bán."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM paper_lots "
                "WHERE portfolio_id=? AND symbol=? AND is_closed=0 "
                "AND settle_date <= ?",
                (self.portfolio_id, symbol, date)
            ).fetchone()
        return int(row[0]) if row else 0

    def get_unsettled_qty(self, symbol: str, date: str) -> int:
        """SL cổ phiếu CHƯA về (settle_date > date) — đang khóa."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM paper_lots "
                "WHERE portfolio_id=? AND symbol=? AND is_closed=0 "
                "AND settle_date > ?",
                (self.portfolio_id, symbol, date)
            ).fetchone()
        return int(row[0]) if row else 0

    # ==================================================== CORPORATE ACTIONS
    def register_corporate_action(self, symbol: str, ex_date: str,
                                   action_type: str, cash_per_share: float = 0.0,
                                   ratio: float = 0.0, notes: str = None,
                                   conn=None):
        """Đăng ký sự kiện doanh nghiệp. action_type: CASH_DIV/STOCK_DIV/SPLIT.

        ACID: nhận conn (Global Transaction) để execute chung; không tự commit.
        """
        action_id = f"{symbol}|{ex_date}|{action_type}"
        with _conn_or(conn) as __c:
            __c.execute(
                "INSERT OR REPLACE INTO paper_corporate_actions (action_id, symbol, "
                "ex_date, action_type, cash_per_share, ratio, applied, notes, "
                "created_at) VALUES (?,?,?,?,?,?,0,?,?)",
                (action_id, symbol, ex_date, action_type, cash_per_share, ratio,
                 notes, datetime.now().isoformat())
            )
        return {"action_id": action_id, "status": "REGISTERED"}

    def apply_corporate_actions(self, date: str, conn=None) -> List[Dict]:
        """Áp dụng CA có ex_date == date lên cost basis/quantity (chạy trong đêm).

        Chống báo lỗ giả tạo vào ngày giao dịch không hưởng quyền:
          - CASH_DIV: ghi cổ tức tiền (pending T+ theo quy định) + GIẢM cost_basis.
          - STOCK_DIV/SPLIT: TĂNG quantity + GIẢM cost_basis theo tỉ lệ (giữ tổng vốn).

        ACID: nhận conn (Global Transaction) để execute chung; không tự commit.
        """
        with _conn_or(conn) as __c:
            actions = __c.execute(
                "SELECT action_id, symbol, action_type, cash_per_share, ratio "
                "FROM paper_corporate_actions WHERE ex_date=? AND applied=0",
                (date,)
            ).fetchall()

            applied = []
            for action_id, symbol, atype, cps, ratio in actions:
                lots = __c.execute(
                    "SELECT lot_id, quantity, cost_basis FROM paper_lots "
                    "WHERE portfolio_id=? AND symbol=? AND is_closed=0",
                    (self.portfolio_id, symbol)
                ).fetchall()

                total_qty = sum(q for _, q, _ in lots)
                if total_qty == 0:
                    self._mark_ca_applied(action_id, conn=__c)
                    continue

                if atype == "CASH_DIV":
                    div_cash = cps * total_qty
                    for lot_id, q, cb in lots:
                        new_cb = max(cb - cps, 0.0)
                        __c.execute(
                            "UPDATE paper_lots SET cost_basis=? WHERE lot_id=?",
                            (new_cb, lot_id))
                    sd = self._next_trading_days(date, SETTLEMENT_DAYS)
                    self._update_cash(pending_delta=div_cash, conn=__c)
                    self._ledger(date, "SELL_PROCEEDS", div_cash, symbol=symbol,
                                 settle_date=sd, settled=0,
                                 notes=f"CASH_DIV {cps}/CP x {total_qty}", conn=__c)
                    applied.append({"symbol": symbol, "type": "CASH_DIV",
                                    "cash": div_cash})

                elif atype in ("STOCK_DIV", "SPLIT"):
                    factor = 1.0 + ratio
                    for lot_id, q, cb in lots:
                        new_q = int(q * factor)
                        new_cb = cb / factor if factor > 0 else cb
                        __c.execute(
                            "UPDATE paper_lots SET quantity=?, original_qty=?, "
                            "cost_basis=? WHERE lot_id=?",
                            (new_q, new_q, new_cb, lot_id))
                    applied.append({"symbol": symbol, "type": atype, "ratio": ratio})

                self._mark_ca_applied(action_id, conn=__c)

        if applied:
            logger.info(f"[MtM] Applied {len(applied)} corporate actions @ {date}")
        return applied

    def _mark_ca_applied(self, action_id: str, conn=None):
        with _conn_or(conn) as __c:
            __c.execute(
                "UPDATE paper_corporate_actions SET applied=1 WHERE action_id=?",
                (action_id,)
            )

    # ==================================================== VALUATION (MtM)
    def mark_to_market(self, date: str, hdr_limit: float = 0.0,
                       conn=None) -> Dict:
        """Định giá danh mục @ close T. Unrealized P&L đã chiết khấu phí+thuế bán.

        Trả về snapshot đầy đủ + ghi paper_equity_curve.

        ACID: nhận conn (Global Transaction) để execute chung; không tự commit.
        """
        state = self._get_state()
        with _conn_or(conn) as c:
            lots = c.execute(
                "SELECT symbol, quantity, cost_basis, settle_date FROM paper_lots "
                "WHERE portfolio_id=? AND is_closed=0 AND open_date<=?",
                (self.portfolio_id, date)
            ).fetchall()

            market_value = 0.0
            cost_value = 0.0
            unsettled_val = 0.0
            positions = {}
            for symbol, qty, cb, settle_date in lots:
                close = self._get_close(symbol, date)
                if close is None:
                    close = cb
                mv = qty * close
                market_value += mv
                cost_value += qty * cb
                if settle_date > date:
                    unsettled_val += mv
                p = positions.setdefault(symbol, {"qty": 0, "cost": 0.0, "mv": 0.0,
                                                  "unsettled_qty": 0})
                p["qty"] += qty
                p["cost"] += qty * cb
                p["mv"] += mv
                if settle_date > date:
                    p["unsettled_qty"] += qty

            exit_fee = market_value * (SELL_FEE_BPS + SELL_TAX_BPS) / 10000.0
            unrealized_gross = market_value - cost_value
            unrealized_net = unrealized_gross - exit_fee

            row = c.execute(
                "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized_pnl "
                "WHERE portfolio_id=? AND date<=?",
                (self.portfolio_id, date)
            ).fetchone()
            realized_cum = float(row[0]) if row else 0.0

            total_equity = state["settled_cash"] + state["pending_cash_in"] + market_value
            buying_power = self.get_buying_power(date, hdr_limit)

            snapshot = {
                "date": date,
                "portfolio_id": self.portfolio_id,
                "settled_cash": round(state["settled_cash"], 0),
                "pending_cash_in": round(state["pending_cash_in"], 0),
                "market_value": round(market_value, 0),
                "cost_value": round(cost_value, 0),
                "unrealized_pnl_gross": round(unrealized_gross, 0),
                "unrealized_pnl_net": round(unrealized_net, 0),
                "exit_fee_tax": round(exit_fee, 0),
                "realized_pnl_cum": round(realized_cum, 0),
                "total_equity": round(total_equity, 0),
                "total_return_pct": round(
                    (total_equity / state["initial_capital"] - 1) * 100, 2),
                "unsettled_qty_val": round(unsettled_val, 0),
                "buying_power": round(buying_power, 0),
                "hdr_limit": hdr_limit,
                "positions": {s: {"qty": p["qty"],
                                  "avg_cost": round(p["cost"] / p["qty"], 2) if p["qty"] else 0,
                                  "market_value": round(p["mv"], 0),
                                  "unsettled_qty": p["unsettled_qty"],
                                  "upl": round(p["mv"] - p["cost"], 0)}
                              for s, p in positions.items()},
            }

            c.execute(
                "INSERT OR REPLACE INTO paper_equity_curve (portfolio_id, date, "
                "settled_cash, pending_cash_in, market_value, unrealized_pnl, "
                "realized_pnl_cum, total_equity, unsettled_qty_val, buying_power, "
                "hdr_limit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.portfolio_id, date, state["settled_cash"],
                 state["pending_cash_in"], market_value, unrealized_net,
                 realized_cum, total_equity, unsettled_val, buying_power,
                 hdr_limit, datetime.now().isoformat())
            )

        return snapshot

    # -------------------------------------------------------------- reporting
    @staticmethod
    def print_report(snapshot: Dict, lang: str = "vi"):
        print(f"\n{'=' * 65}")
        print(f"  MARK-TO-MARKET P&L — {snapshot.get('portfolio_id','')}")
        print(f"{'=' * 65}")
        print(f"  Ngày định giá     : {snapshot['date']}")
        print(f"  Tiền đã settle    : {snapshot['settled_cash']:>18,.0f}")
        print(f"  Tiền bán chờ về   : {snapshot['pending_cash_in']:>18,.0f}")
        print(f"  Giá trị CP (MV)   : {snapshot['market_value']:>18,.0f}")
        print(f"  → chưa settle     : {snapshot['unsettled_qty_val']:>18,.0f}")
        print(f"  Tổng vốn (Equity) : {snapshot['total_equity']:>18,.0f}")
        print(f"  Lợi nhuận         : {snapshot['total_return_pct']:>17.2f}%")
        print(f"  Sức mua khả dụng  : {snapshot['buying_power']:>18,.0f}  "
              f"(HDR={snapshot['hdr_limit']})")
        print(f"\n  -- P&L --")
        print(f"  Unrealized (gross): {snapshot['unrealized_pnl_gross']:>18,.0f}")
        print(f"  Phí+thuế thoát    : {snapshot['exit_fee_tax']:>18,.0f}")
        print(f"  Unrealized (net)  : {snapshot['unrealized_pnl_net']:>18,.0f}")
        print(f"  Realized (luỹ kế) : {snapshot['realized_pnl_cum']:>18,.0f}")
        if snapshot.get("positions"):
            print(f"\n  -- Vị thế --")
            for s, p in snapshot["positions"].items():
                lock = f" [khóa {p['unsettled_qty']:,}]" if p['unsettled_qty'] else ""
                print(f"  {s:6s} x{p['qty']:>8,} @ {p['avg_cost']:>10,.0f}  "
                      f"UPL={p['upl']:>14,.0f}{lock}")
        print(f"{'=' * 65}\n")
