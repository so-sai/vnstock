"""
DIVIDEND TOTAL RETURN & LIQUIDITY AUDIT ENGINE (V2.1 - PRODUCTION SPEC)
PTCK_VNSTOCK Quantitative Research Suite

Tuân thủ nghiêm ngặt 3 Bất biến của PTCK:
1. Missing > Fake: Fail-closed khi chuỗi giá bị điều chỉnh trước hoặc thiếu ngày sự kiện.
2. Event-Driven True Timeline: Tách biệt ex_date, pay_date và chu kỳ thanh toán bù trừ T+2.
3. Reality Check: Phạt trượt giá (Slippage), lô chẵn 100, thanh khoản thực tế UPCoM/HOSE.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class MarketBar:
    date: datetime
    raw_close: float  # Bắt buộc là giá đóng cửa chưa điều chỉnh
    volume: float  # Khối lượng giao dịch trong phiên


@dataclass(frozen=True)
class DividendEvent:
    ex_date: datetime  # Ngày GDKHQ
    pay_date: datetime  # Ngày tiền về tài khoản thực tế
    cash_per_share: float  # Tiền mặt / 1 cp (VND)


@dataclass(frozen=True)
class EngineConfig:
    symbol: str
    initial_capital: float
    is_unadjusted_price: bool = True
    lot_size: int = 100
    tax_dividend_cash: float = 0.05  # 5% thuế cổ tức tiền mặt
    fee_trade: float = 0.0015  # 0.15% phí giao dịch
    tax_sell: float = 0.0010  # 0.10% thuế bán
    max_adv_participation: float = 0.20  # Tối đa 20% thanh khoản phiên
    base_slippage_bps: float = 0.0050  # Trượt giá 50 bps cho mã thanh khoản thấp
    non_term_bank_rate: float = 0.0020  # 0.2%/năm lãi suất không kỳ hạn cho tiền mặt chờ
    settlement_days: int = 2  # Chu kỳ T+2


def validate_dataset(bars: list[MarketBar], divs: list[DividendEvent], cfg: EngineConfig):
    if not cfg.is_unadjusted_price:
        raise ValueError("DATA_INTEGRITY_FAIL: Dữ liệu đầu vào phải là giá chưa điều chỉnh (Unadjusted).")
    if len(bars) < 2:
        raise ValueError("DATA_INTEGRITY_FAIL: Cần tối thiểu 2 điểm dữ liệu giá để xác định kỳ kiểm toán.")
    for d in divs:
        if d.pay_date < d.ex_date:
            raise ValueError(f"TIMELINE_ANOMALY: pay_date ({d.pay_date}) < ex_date ({d.ex_date}) tại {cfg.symbol}")


class DividendAuditEngine:
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg

    def _execute_order(
        self, target_cash_or_shares: float, price: float, daily_vol: float, is_buy: bool
    ) -> tuple[float, float, float]:
        """
        Thực thi lệnh theo lô 100, tính trượt giá và ước lượng số phiên khớp.
        """
        max_capacity = max(daily_vol * self.cfg.max_adv_participation, float(self.cfg.lot_size))

        if is_buy:
            exec_price = price * (1.0 + self.cfg.base_slippage_bps)
            cost_per_share = exec_price * (1.0 + self.cfg.fee_trade)
            shares = math.floor(target_cash_or_shares / cost_per_share / self.cfg.lot_size) * self.cfg.lot_size
            actual_spent = shares * cost_per_share
            days = shares / max_capacity if shares > 0 else 0.0
            return float(shares), actual_spent, days
        else:
            exec_price = price * (1.0 - self.cfg.base_slippage_bps)
            proceeds = target_cash_or_shares * exec_price * (1.0 - self.cfg.fee_trade - self.cfg.tax_sell)
            days = target_cash_or_shares / max_capacity if target_cash_or_shares > 0 else 0.0
            return proceeds, 0.0, days

    def simulate_bank_exact(
        self, start_date: datetime, end_date: datetime, rate_schedule: dict[int, float]
    ) -> tuple[float, int, int]:
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

    def run_audit(self, bars: list[MarketBar], divs: list[DividendEvent], bank_rates: dict[int, float]) -> dict:
        validate_dataset(bars, divs, self.cfg)

        bars_sorted = sorted(bars, key=lambda b: b.date)
        divs_sorted = sorted(divs, key=lambda d: d.ex_date)

        start_dt = bars_sorted[0].date
        end_dt = bars_sorted[-1].date
        total_years = max((end_dt - start_dt).days / 365.25, 1e-4)

        avg_vol = sum(b.volume for b in bars_sorted) / len(bars_sorted)

        # 1. Khởi tạo vị thế (settled ngay phiên mở đầu)
        init_shares, init_spent, entry_days = self._execute_order(
            self.cfg.initial_capital, bars_sorted[0].raw_close, avg_vol, is_buy=True
        )

        # Nhánh DRIP
        settled_shares_drip = init_shares
        cash_drip = self.cfg.initial_capital - init_spent

        # Nhánh Cash-out
        shares_static = init_shares
        cash_static = self.cfg.initial_capital - init_spent
        div_cash_spent_nominal = 0.0
        div_cash_saved_acc = 0.0

        # Quản lý sự kiện cổ tức
        div_ptr = 0
        total_div_events = len(divs_sorted)
        pending_reinvestments: list[tuple[datetime, float]] = []  # (pay_date, amount)
        unsettled_drip_shares: list[tuple[datetime, float]] = []  # (trade_date, shares)

        ex_div_count = 0
        reinvested_count = 0
        peak_nav = -math.inf
        max_drawdown = 0.0

        for idx, bar in enumerate(bars_sorted):
            prev_dt = bars_sorted[idx - 1].date if idx > 0 else start_dt - timedelta(days=1)
            days_step = (bar.date - prev_dt).days

            # Tiền mặt chờ giải ngân hưởng lãi suất không kỳ hạn
            if cash_drip > 0 and days_step > 0:
                cash_drip *= 1.0 + self.cfg.non_term_bank_rate * (days_step / 365.25)

            # A. Chốt quyền (Ex-dividend)
            while div_ptr < total_div_events and prev_dt < divs_sorted[div_ptr].ex_date <= bar.date:
                ev = divs_sorted[div_ptr]
                if ev.ex_date >= start_dt:
                    net_div = ev.cash_per_share * (1.0 - self.cfg.tax_dividend_cash)

                    # Quyền hưởng của DRIP: toàn bộ cổ phiếu sở hữu (đã về hoặc đang về T+2)
                    total_owned_sh = settled_shares_drip + sum(s for _, s in unsettled_drip_shares)
                    pending_reinvestments.append((ev.pay_date, total_owned_sh * net_div))

                    # Quyền hưởng của Cash-out
                    cash_received = shares_static * net_div
                    div_cash_spent_nominal += cash_received

                    # Mô phỏng nếu đem gửi tiết kiệm phần cổ tức này đến cuối kỳ
                    rem_yrs = max((end_dt - ev.pay_date).days / 365.25, 0.0)
                    r = bank_rates.get(ev.pay_date.year, 0.05)
                    div_cash_saved_acc += cash_received * ((1.0 + r) ** rem_yrs)

                    ex_div_count += 1
                div_ptr += 1

            # B. Giải phóng cổ phiếu T+2 về trạng thái khả dụng (settled)
            still_unsettled = []
            for td, sh in unsettled_drip_shares:
                if (bar.date - td).days >= self.cfg.settlement_days:
                    settled_shares_drip += sh
                else:
                    still_unsettled.append((td, sh))
            unsettled_drip_shares = still_unsettled

            # C. Tái đầu tư khi tiền cổ tức thực về (pay_date <= bar.date)
            still_pending = []
            for pay_dt, amt in pending_reinvestments:
                if pay_dt <= bar.date:
                    cash_drip += amt
                    new_sh, spent, _ = self._execute_order(cash_drip, bar.raw_close, bar.volume, is_buy=True)
                    if new_sh > 0:
                        unsettled_drip_shares.append((bar.date, new_sh))
                        cash_drip -= spent
                        reinvested_count += 1
                else:
                    still_pending.append((pay_dt, amt))
            pending_reinvestments = still_pending

            # D. Đo lường rủi ro (NAV & Drawdown)
            tot_shares_nav = settled_shares_drip + sum(s for _, s in unsettled_drip_shares)
            cur_nav = (tot_shares_nav * bar.raw_close) + cash_drip + sum(amt for _, amt in pending_reinvestments)

            if cur_nav > peak_nav:
                peak_nav = cur_nav
            dd = (cur_nav - peak_nav) / peak_nav if peak_nav > 0 else 0.0
            if dd < max_drawdown:
                max_drawdown = dd

        # Chốt chặn kiểm toán Lớp 3
        expected_events = sum(1 for d in divs_sorted if start_dt <= d.ex_date <= end_dt)
        if ex_div_count != expected_events:
            raise RuntimeError(f"AUDIT_CORRUPT: Rơi rụng sự kiện cổ tức! Kỳ vọng: {expected_events}, thực thi: {ex_div_count}")

        # Thanh lý cuối kỳ
        # Chỉ những cổ phiếu đã về trước T+2 (settled) mới được bán thanh lý
        sellable_drip_shares = settled_shares_drip
        locked_t2_shares = sum(s for _, s in unsettled_drip_shares)

        liquidated_drip_cash, _, exit_days_drip = self._execute_order(
            sellable_drip_shares, bars_sorted[-1].raw_close, avg_vol, is_buy=False
        )

        # Tài sản cuối kỳ DRIP: Tiền bán được + Giá trị cổ phiếu kẹt T+2 (tính theo giá đóng cửa trừ thuế/phí) + Tiền mặt
        t2_locked_val = locked_t2_shares * bars_sorted[-1].raw_close * (1.0 - self.cfg.fee_trade - self.cfg.tax_sell)
        final_drip_wealth = liquidated_drip_cash + t2_locked_val + cash_drip + sum(amt for _, amt in pending_reinvestments)

        # Thanh lý nhánh Cash-out
        static_liquidated, _, exit_days_static = self._execute_order(
            shares_static, bars_sorted[-1].raw_close, avg_vol, is_buy=False
        )
        final_static_stock_only = static_liquidated + cash_static
        final_static_with_saved_divs = final_static_stock_only + div_cash_saved_acc

        # Ngân hàng
        bank_final, full_rolls, stub_days = self.simulate_bank_exact(start_dt, end_dt, bank_rates)

        total_drip_final_shares = settled_shares_drip + locked_t2_shares

        return {
            "symbol": self.cfg.symbol,
            "period_years": total_years,
            "observations": len(bars_sorted),
            "events_matched": ex_div_count,
            "reinvest_cycles": reinvested_count,
            "bank_final_nav": bank_final,
            "bank_cagr": (bank_final / self.cfg.initial_capital) ** (1.0 / total_years) - 1.0,
            "bank_full_rolls": full_rolls,
            "bank_stub_days": stub_days,
            "drip_final_wealth": final_drip_wealth,
            "drip_cagr": (final_drip_wealth / self.cfg.initial_capital) ** (1.0 / total_years) - 1.0,
            "drip_shares_init": init_shares,
            "drip_shares_final": total_drip_final_shares,
            "drip_shares_locked_t2": locked_t2_shares,
            "max_drawdown_observed": max_drawdown,
            "entry_days_required": entry_days,
            "exit_days_required": exit_days_drip,
            "cashout_terminal_stock_val": final_static_stock_only,
            "cashout_spent_dividends": div_cash_spent_nominal,
            "cashout_wealth_if_saved": final_static_with_saved_divs,
            "cashout_cagr_if_saved": (final_static_with_saved_divs / self.cfg.initial_capital) ** (1.0 / total_years) - 1.0,
        }
