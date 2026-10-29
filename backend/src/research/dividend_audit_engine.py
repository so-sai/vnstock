"""
DIVIDEND TOTAL RETURN & LIQUIDITY AUDIT ENGINE (V2.4 - HARDENED SPEC)
PTCK_VNSTOCK Quantitative Research Suite

Status: active — nghiệm thu 5/5 mã benchmark (FOC, VEA, SMB, PAT, DSN) ngày 29/10/2026
trên chuỗi giá THÔ đã certified bởi price_provenance_gate --reconstructed (EXIT 0).
Cửa sổ OOS 2025-2026 vẫn NON-DECISIONAL: engine mô tả, không dùng để chọn tham số.
Invariants Enforced:
1. FAIL_CLOSED_ON_ADJUSTED_PRICE: Chặn đứng giá điều chỉnh trước.
2. MANDATORY_INDEPENDENT_PROVENANCE: Bắt buộc đối soát 2 chiều với corporate actions độc lập.
3. CONSERVED_CASH_AND_EQUITY: Hạch toán 3 trạng thái vị thế (settled, unsettled, receivables).
4. ADV20_WARMUP_ORDER_SLICING: Chỉ khớp lệnh khi đủ 20 phiên volume history, max 20% ADV20.
5. DECREE_126_STOCK_DIVIDEND_TAX_DEFERRED: Thuế TNCN 5% trên mệnh giá 10k khi bán cổ phiếu thưởng.
6. VIETNAM_T2_TRADING_CALENDAR: Thanh toán T+2 theo ngày làm việc, loại trừ T7/CN/Lễ.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

# =====================================================================
# LỚP 1: DATA INTEGRITY, CALENDAR & PROVENANCE
# =====================================================================


@dataclass(frozen=True)
class MarketBar:
    trade_date: date
    raw_close: float  # BẮT BUỘC: Giá chưa điều chỉnh (VND)
    volume: float  # Khối lượng khớp lệnh thực tế


@dataclass(frozen=True)
class CashDividendEvent:
    ex_date: date  # Ngày GDKHQ
    pay_date: date  # Ngày tiền mặt về tài khoản
    cash_per_share: float  # Tiền mặt / 1 cp (VND)
    action_id: str  # Mã định danh sự kiện độc lập (VD: VSDC/FOC/DIV_CASH_2023)


@dataclass(frozen=True)
class StockDividendEvent:
    ex_date: date  # Ngày GDKHQ
    pay_date: date  # Ngày cổ phiếu thưởng về tài khoản giao dịch
    share_ratio: float  # Tỷ lệ chia (VD: 0.15 cho 15%)
    action_id: str  # Mã định danh sự kiện độc lập (VD: VSDC/FOC/DIV_STOCK_2023)


class VietnamTradingCalendar:
    """Xác định ngày làm việc và chu kỳ thanh toán T+2 theo quy định VSDC/HOSE/HNX/UPCoM."""

    def __init__(self, holidays: set[date] | None = None):
        self.holidays: set[date] = holidays if holidays is not None else set()

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:  # Thứ Bảy (5), Chủ Nhật (6)
            return False
        return d not in self.holidays

    def add_trading_days(self, start_date: date, n_days: int) -> date:
        curr = start_date
        added = 0
        while added < n_days:
            curr += timedelta(days=1)
            if self.is_trading_day(curr):
                added += 1
        return curr


@dataclass(frozen=True)
class EngineConfig:
    symbol: str
    initial_capital: float
    is_unadjusted_price: bool = True
    lot_size: int = 100
    tax_dividend_cash: float = 0.05  # 5% thuế TNCN cổ tức tiền mặt
    fee_trade: float = 0.0015  # 0.15% phí giao dịch mua/bán
    tax_sell: float = 0.0010  # 0.10% thuế chuyển nhượng chứng khoán
    par_value: float = 10_000.0  # Mệnh giá 10.000 VND theo Luật DN / NĐ 126
    tax_stock_dividend: float = 0.05  # 5% thuế TNCN cổ tức cổ phiếu tính trên mệnh giá khi bán
    max_adv_participation: float = 0.20  # Tối đa 20% ADV20 mỗi phiên
    base_slippage_bps: float = 0.0050  # Trượt giá 50 bps cho mã thanh khoản mỏng
    non_term_bank_rate: float = 0.0020  # 0.2%/năm lãi suất không kỳ hạn cho tiền mặt chờ
    settlement_trading_days: int = 2  # Chu kỳ thanh toán bù trừ T+2
    adv_window_size: int = 20  # Cửa sổ bắt buộc để tính ADV20 (20 phiên)


# =====================================================================
# LỚP 2: CASHFLOW, ORDER SLICING & POSITION LEDGER
# =====================================================================


@dataclass
class PositionLot:
    settle_date: date
    shares: float
    is_bonus: bool = False  # Đánh dấu nguồn gốc cổ tức cổ phiếu theo NĐ 126


@dataclass
class PositionLedger:
    """Quản lý chặt chẽ 3 trạng thái cổ phần: đã về, đang về, và nguồn gốc cổ tức cổ phiếu."""

    settled_regular_shares: float = 0.0
    settled_bonus_shares: float = 0.0
    unsettled_lots: list[PositionLot] = field(default_factory=list)

    @property
    def settled_shares(self) -> float:
        return self.settled_regular_shares + self.settled_bonus_shares

    @property
    def unsettled_shares(self) -> float:
        return sum(lot.shares for lot in self.unsettled_lots)

    @property
    def total_economic_shares(self) -> float:
        """Tổng quyền lợi kinh tế để tính NAV và quyền nhận cổ tức (không đếm trùng)."""
        return self.settled_shares + self.unsettled_shares

    def process_settlement(self, current_date: date):
        """Chuyển các lô T+2 sang trạng thái khả dụng (settled) khi đến hoặc qua ngày thanh toán."""
        still_unsettled = []
        for lot in self.unsettled_lots:
            if current_date >= lot.settle_date:
                if lot.is_bonus:
                    self.settled_bonus_shares += lot.shares
                else:
                    self.settled_regular_shares += lot.shares
            else:
                still_unsettled.append(lot)
        self.unsettled_lots = still_unsettled


class DividendAuditEngineV2:
    def __init__(self, cfg: EngineConfig, calendar: VietnamTradingCalendar | None = None):
        self.cfg = cfg
        self.calendar = calendar or VietnamTradingCalendar()

    def run_audit(
        self,
        bars: list[MarketBar],
        cash_divs: list[CashDividendEvent],
        bank_rates: dict[int, float],
        provenance_source: str,
        independent_action_ids: set[str],
        stock_divs: list[StockDividendEvent] | None = None,
        warmup_bars: list[MarketBar] | None = None,
    ) -> dict:
        # 1. KIỂM SOÁT ĐẦU VÀO VÀ BẢO ĐẢM TÍNH PROVENANCE (FAIL-CLOSED)
        if not self.cfg.is_unadjusted_price:
            raise ValueError("FAIL_CLOSED: Bắt buộc sử dụng giá chưa điều chỉnh (Raw Close).")
        if len(bars) < 2:
            raise ValueError("FAIL_CLOSED: Cần tối thiểu 2 phiên dữ liệu giá để xác định chu kỳ kiểm toán.")
        if not provenance_source:
            raise ValueError(
                "PROVENANCE_REQUIRED: Phải cung cấp nguồn provenance độc lập (VSDC/Sở GDCK). "
                "Không có provenance => Đứng ngoài."
            )

        stock_divs = stock_divs or []
        bars_sorted = sorted(bars, key=lambda b: b.trade_date)
        cash_divs_sorted = sorted(cash_divs, key=lambda d: d.ex_date)
        stock_divs_sorted = sorted(stock_divs, key=lambda d: d.ex_date)

        start_date = bars_sorted[0].trade_date
        end_date = bars_sorted[-1].trade_date
        total_years = max((end_date - start_date).days / 365.25, 1e-4)

        # Đối soát 2 chiều với tập Provenance độc lập (Chống đối soát vòng tròn)
        all_actions_in_period = {d.action_id for d in cash_divs_sorted if start_date <= d.ex_date <= end_date} | {
            d.action_id for d in stock_divs_sorted if start_date <= d.ex_date <= end_date
        }

        missing_events = independent_action_ids - all_actions_in_period
        extra_events = all_actions_in_period - independent_action_ids
        if missing_events or extra_events:
            raise RuntimeError(
                f"PROVENANCE_MISMATCH: Dữ liệu nạp không khớp nguồn {provenance_source} "
                f"(Missing: {missing_events}, Extra: {extra_events})"
            )

        # 2. KHỞI TẠO TÀI KHOẢN VÀ LỊCH SỬ ADV20 WARM-UP
        volume_history: list[float] = []
        if warmup_bars:
            warmup_sorted = sorted(warmup_bars, key=lambda b: b.trade_date)
            for wb in warmup_sorted:
                volume_history.append(wb.volume)

        ledger_drip = PositionLedger()
        ledger_static = PositionLedger()

        # Tiền mặt nhàn rỗi (Unallocated cash)
        cash_drip = self.cfg.initial_capital
        cash_static = self.cfg.initial_capital
        initial_deploy_target_static = self.cfg.initial_capital

        div_cash_spent_nominal = 0.0
        div_cash_saved_acc = 0.0

        # Khoản phải thu cổ tức (Accrued Dividend Receivables) bảo toàn NAV
        # (pay_date, amount)
        pending_cash_divs_drip: list[tuple[date, float]] = []
        pending_cash_divs_static: list[tuple[date, float]] = []
        # (pay_date, shares)
        pending_stock_divs_drip: list[tuple[date, float]] = []
        pending_stock_divs_static: list[tuple[date, float]] = []

        peak_nav_drip = -math.inf
        max_drawdown_drip = 0.0
        processed_cash_divs = 0
        processed_stock_divs = 0

        cash_div_idx = 0
        stock_div_idx = 0
        n_cash_divs = len(cash_divs_sorted)
        n_stock_divs = len(stock_divs_sorted)

        # 3. VÒNG LẶP DÒNG SỰ KIỆN THEO PHIÊN GIAO DỊCH
        for i, bar in enumerate(bars_sorted):
            volume_history.append(bar.volume)
            prev_date = bars_sorted[i - 1].trade_date if i > 0 else start_date - timedelta(days=1)
            days_step = (bar.trade_date - prev_date).days

            # Lãi suất tiền gửi không kỳ hạn cho tiền mặt nhàn rỗi
            if cash_drip > 0 and days_step > 0:
                cash_drip *= 1.0 + self.cfg.non_term_bank_rate * (days_step / 365.25)
            if cash_static > 0 and days_step > 0:
                cash_static *= 1.0 + self.cfg.non_term_bank_rate * (days_step / 365.25)

            # Bước A: Giải phóng cổ phiếu T+2 đã thanh toán
            ledger_drip.process_settlement(bar.trade_date)
            ledger_static.process_settlement(bar.trade_date)

            # Bước B: Xử lý tiền cổ tức & cổ tức cổ phiếu đến ngày thanh toán (pay_date <= bar.trade_date)
            # DRIP Cash Div: Tiền về tài khoản nhập vào cash_drip để tái đầu tư
            still_pending_cash_drip = []
            for pay_dt, amount in pending_cash_divs_drip:
                if bar.trade_date >= pay_dt:
                    cash_drip += amount
                else:
                    still_pending_cash_drip.append((pay_dt, amount))
            pending_cash_divs_drip = still_pending_cash_drip

            # Static Cash Div: Tiền về tài khoản rút ra ngoài (không tái đầu tư)
            still_pending_cash_static = []
            for pay_dt, amount in pending_cash_divs_static:
                if bar.trade_date >= pay_dt:
                    div_cash_spent_nominal += amount
                    rem_yrs = max((end_date - pay_dt).days / 365.25, 0.0)
                    r = bank_rates.get(pay_dt.year, 0.05)
                    div_cash_saved_acc += amount * ((1.0 + r) ** rem_yrs)
                else:
                    still_pending_cash_static.append((pay_dt, amount))
            pending_cash_divs_static = still_pending_cash_static

            # DRIP Stock Div: Cổ phiếu về tài khoản (chờ T+2 từ ngày pay_date)
            still_pending_stock_drip = []
            for pay_dt, sh in pending_stock_divs_drip:
                if bar.trade_date >= pay_dt:
                    settle_dt = self.calendar.add_trading_days(bar.trade_date, self.cfg.settlement_trading_days)
                    ledger_drip.unsettled_lots.append(PositionLot(settle_date=settle_dt, shares=sh, is_bonus=True))
                else:
                    still_pending_stock_drip.append((pay_dt, sh))
            pending_stock_divs_drip = still_pending_stock_drip

            # Static Stock Div: Cổ phiếu về tài khoản của nhánh static
            still_pending_stock_static = []
            for pay_dt, sh in pending_stock_divs_static:
                if bar.trade_date >= pay_dt:
                    settle_dt = self.calendar.add_trading_days(bar.trade_date, self.cfg.settlement_trading_days)
                    ledger_static.unsettled_lots.append(PositionLot(settle_date=settle_dt, shares=sh, is_bonus=True))
                else:
                    still_pending_stock_static.append((pay_dt, sh))
            pending_stock_divs_static = still_pending_stock_static

            # Bước C: Tính toán ADV20 chuẩn mực (FAIL-CLOSED: Không đủ 20 phiên thì cấm khớp)
            if len(volume_history) >= self.cfg.adv_window_size:
                trailing_20 = volume_history[-self.cfg.adv_window_size :]
                adv20 = sum(trailing_20) / self.cfg.adv_window_size
                max_shares_allowed = (
                    math.floor((adv20 * self.cfg.max_adv_participation) / self.cfg.lot_size) * self.cfg.lot_size
                    if adv20 > 0
                    else 0.0
                )
            else:
                adv20 = 0.0
                max_shares_allowed = 0.0

            # Bước D: Thực thi lệnh mua theo Order Slicing (DRIP và Initial Static)
            exec_buy_price = bar.raw_close * (1.0 + self.cfg.base_slippage_bps)
            cost_per_share = exec_buy_price * (1.0 + self.cfg.fee_trade)

            # DRIP: Tái đầu tư toàn bộ tiền mặt hiện có trong giới hạn thanh khoản phiên
            if cash_drip >= (cost_per_share * self.cfg.lot_size) and max_shares_allowed > 0:
                shares_affordable = math.floor(cash_drip / cost_per_share / self.cfg.lot_size) * self.cfg.lot_size
                shares_to_buy = min(shares_affordable, max_shares_allowed)
                if shares_to_buy > 0:
                    spent = shares_to_buy * cost_per_share
                    cash_drip -= spent
                    settle_dt = self.calendar.add_trading_days(bar.trade_date, self.cfg.settlement_trading_days)
                    ledger_drip.unsettled_lots.append(PositionLot(settle_date=settle_dt, shares=shares_to_buy, is_bonus=False))

            # Static: Chỉ giải ngân vốn ban đầu theo lát cắt
            can_buy_static = (
                initial_deploy_target_static > 0
                and max_shares_allowed > 0
                and cash_static >= (cost_per_share * self.cfg.lot_size)
            )
            if can_buy_static:
                funds_to_use = min(cash_static, initial_deploy_target_static)
                shares_affordable = math.floor(funds_to_use / cost_per_share / self.cfg.lot_size) * self.cfg.lot_size
                shares_to_buy = min(shares_affordable, max_shares_allowed)
                if shares_to_buy > 0:
                    spent = shares_to_buy * cost_per_share
                    cash_static -= spent
                    initial_deploy_target_static -= spent
                    settle_dt = self.calendar.add_trading_days(bar.trade_date, self.cfg.settlement_trading_days)
                    ledger_static.unsettled_lots.append(
                        PositionLot(settle_date=settle_dt, shares=shares_to_buy, is_bonus=False)
                    )

            # Bước E: Chốt quyền cổ tức tiền mặt & cổ phiếu (Ex-Dividend Entitlement)
            while cash_div_idx < n_cash_divs and prev_date < cash_divs_sorted[cash_div_idx].ex_date <= bar.trade_date:
                cev = cash_divs_sorted[cash_div_idx]
                if cev.ex_date >= start_date:
                    net_cash = cev.cash_per_share * (1.0 - self.cfg.tax_dividend_cash)
                    pending_cash_divs_drip.append((cev.pay_date, ledger_drip.total_economic_shares * net_cash))
                    pending_cash_divs_static.append((cev.pay_date, ledger_static.total_economic_shares * net_cash))
                    processed_cash_divs += 1
                cash_div_idx += 1

            while stock_div_idx < n_stock_divs and prev_date < stock_divs_sorted[stock_div_idx].ex_date <= bar.trade_date:
                sev = stock_divs_sorted[stock_div_idx]
                if sev.ex_date >= start_date:
                    bonus_shares_drip = ledger_drip.total_economic_shares * sev.share_ratio
                    bonus_shares_static = ledger_static.total_economic_shares * sev.share_ratio
                    pending_stock_divs_drip.append((sev.pay_date, bonus_shares_drip))
                    pending_stock_divs_static.append((sev.pay_date, bonus_shares_static))
                    processed_stock_divs += 1
                stock_div_idx += 1

            # Bước F: Tính toán NAV và Max Drawdown bảo toàn dòng tiền (Conserved NAV)
            stock_val_drip = ledger_drip.total_economic_shares * bar.raw_close
            pending_receivables_cash = sum(amt for _, amt in pending_cash_divs_drip)
            pending_receivables_stock = sum(sh for _, sh in pending_stock_divs_drip) * bar.raw_close

            current_nav_drip = stock_val_drip + cash_drip + pending_receivables_cash + pending_receivables_stock

            if current_nav_drip > peak_nav_drip:
                peak_nav_drip = current_nav_drip
            drawdown = (current_nav_drip - peak_nav_drip) / peak_nav_drip if peak_nav_drip > 0 else 0.0
            if drawdown < max_drawdown_drip:
                max_drawdown_drip = drawdown

        # 4. THANH LÝ CUỐI KỲ (ÁP DỤNG THUẾ NGHỊ ĐỊNH 126 CHO CỔ TỨC BẰNG CỔ PHIẾU)
        final_bar = bars_sorted[-1]
        exit_exec_price = final_bar.raw_close * (1.0 - self.cfg.base_slippage_bps)

        def compute_liquidation(ledger: PositionLedger) -> tuple[float, float, float]:
            reg_proceeds = ledger.settled_regular_shares * exit_exec_price * (1.0 - self.cfg.fee_trade - self.cfg.tax_sell)

            tax_bonus_per_share = min(exit_exec_price, self.cfg.par_value) * self.cfg.tax_stock_dividend
            bonus_proceeds = ledger.settled_bonus_shares * (
                exit_exec_price * (1.0 - self.cfg.fee_trade - self.cfg.tax_sell) - tax_bonus_per_share
            )

            liquidated_cash = reg_proceeds + bonus_proceeds

            unsettled_reg = sum(lot.shares for lot in ledger.unsettled_lots if not lot.is_bonus)
            unsettled_bon = sum(lot.shares for lot in ledger.unsettled_lots if lot.is_bonus)

            unsettled_val = unsettled_reg * exit_exec_price * (
                1.0 - self.cfg.fee_trade - self.cfg.tax_sell
            ) + unsettled_bon * (exit_exec_price * (1.0 - self.cfg.fee_trade - self.cfg.tax_sell) - tax_bonus_per_share)

            total_stock_value = liquidated_cash + unsettled_val
            return liquidated_cash, unsettled_val, total_stock_value

        liq_cash_drip, unnet_val_drip, tot_stock_drip = compute_liquidation(ledger_drip)
        receivables_end_cash_drip = sum(amt for _, amt in pending_cash_divs_drip)
        receivables_end_stock_drip = (
            sum(sh for _, sh in pending_stock_divs_drip) * exit_exec_price * (1.0 - self.cfg.fee_trade - self.cfg.tax_sell)
        )

        final_drip_wealth = tot_stock_drip + cash_drip + receivables_end_cash_drip + receivables_end_stock_drip

        liq_cash_static, unnet_val_static, tot_stock_static = compute_liquidation(ledger_static)
        receivables_end_cash_static = sum(amt for _, amt in pending_cash_divs_static)
        final_static_stock_wealth = tot_stock_static + cash_static + receivables_end_cash_static
        final_static_with_saved_divs = final_static_stock_wealth + div_cash_saved_acc

        # Benchmark tiền gửi ngân hàng 12M
        bank_final, full_rolls, stub_days = self.simulate_bank_exact(start_date, end_date, bank_rates)

        return {
            "symbol": self.cfg.symbol,
            "period_years": total_years,
            "bars_observed": len(bars_sorted),
            "processed_cash_divs": processed_cash_divs,
            "processed_stock_divs": processed_stock_divs,
            "bank_final_nav": bank_final,
            "bank_cagr": (bank_final / self.cfg.initial_capital) ** (1.0 / total_years) - 1.0,
            "bank_full_rolls": full_rolls,
            "bank_stub_days": stub_days,
            "drip_final_wealth": final_drip_wealth,
            "drip_cagr": (final_drip_wealth / self.cfg.initial_capital) ** (1.0 / total_years) - 1.0,
            "drip_settled_regular_shares": ledger_drip.settled_regular_shares,
            "drip_settled_bonus_shares": ledger_drip.settled_bonus_shares,
            "drip_unsettled_shares": ledger_drip.unsettled_shares,
            "drip_unallocated_cash": cash_drip,
            "max_drawdown_observed": max_drawdown_drip,
            "cashout_terminal_stock_val": final_static_stock_wealth,
            "cashout_spent_dividends": div_cash_spent_nominal,
            "cashout_wealth_if_saved": final_static_with_saved_divs,
            "cashout_cagr_if_saved": (final_static_with_saved_divs / self.cfg.initial_capital) ** (1.0 / total_years) - 1.0,
            "unfilled_initial_capital_static": initial_deploy_target_static,
        }

    def simulate_bank_exact(
        self,
        start_date: date,
        end_date: date,
        rate_schedule: dict[int, float],
    ) -> tuple[float, int, int]:
        """Tính lãi suất ngân hàng 12 tháng chính xác từng chu kỳ 365 ngày kèm ngày lẻ không kỳ hạn (TT 04/2022)."""
        balance = self.cfg.initial_capital
        curr = start_date
        full_cycles = 0

        while True:
            nxt = curr + timedelta(days=365)
            if nxt <= end_date:
                r = rate_schedule.get(curr.year, 0.05)
                balance *= 1.0 + r
                curr = nxt
                full_cycles += 1
            else:
                break

        stub_days = (end_date - curr).days
        if stub_days > 0:
            balance *= 1.0 + self.cfg.non_term_bank_rate * (stub_days / 365.25)

        return balance, full_cycles, stub_days
