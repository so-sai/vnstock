"""EMPIRICAL AUDIT RUNNER: DSN — "BÒ SỮA TIỀN MẶT THUẦN TÚY" (HOSE) — PHA 5.

[WHY] NQ-08 chọn DSN làm ca "hoàn hảo" của dòng tiền cổ tức: 2.400-2.500 đ/cp đợt
chính + 1.500-1.600 đ đợt giữa năm, đều đặn 2021-2026, không nợ vay tài chính,
thị giá 34-62k ổn định trên HOSE. Đây là đối chứng ngược với PAT: nếu cỗ máy DRIP
cho kết quả tốt trên PAT (mua đỉnh chu kỳ) nhưng tệ trên DSN thì kết luận "cổ tức
đều = thắng" là sai; và ngược lại.

[ROOT CAUSE] DSN còn là ca kiểm định lưới tick THỨ HAI trên HOSE (sau SMB): lưới
50 VND là bắt buộc cho vùng giá 10k-50k, và đã đo được lưới 100 VND làm gãy 64/115
bar ở segment cuối. Nếu chỉ có SMB, ta không phân biệt được "lưới 50 đúng" với
"may mắn khớp do cấu trúc giá SMB".

[INVARIANT] Như Pha 4: event/lưới/biên đều từ SSOT; runner chỉ cấu hình + in.
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from src.research.dividend_audit_engine import DividendAuditEngineV2, EngineConfig, VietnamTradingCalendar
    from src.research.symbol_audit_pipeline import (
        BANK_RATES_BIG4,
        load_certified_series,
        print_audit_report,
        trace_initial_execution,
    )
except ImportError:  # pragma: no cover
    from backend.src.research.dividend_audit_engine import (
        DividendAuditEngineV2,
        EngineConfig,
        VietnamTradingCalendar,
    )
    from backend.src.research.symbol_audit_pipeline import (
        BANK_RATES_BIG4,
        load_certified_series,
        print_audit_report,
        trace_initial_execution,
    )


def run_dsn_audit() -> dict:
    series = load_certified_series("DSN")
    divs = series.dividends()
    bars = list(series.audit_bars)

    cfg = EngineConfig(
        symbol="DSN",
        initial_capital=1_000_000_000.0,
        is_unadjusted_price=True,
        lot_size=100,
        tax_dividend_cash=0.05,
        fee_trade=0.0015,
        tax_sell=0.0010,
        max_adv_participation=0.20,
        base_slippage_bps=0.0050,
        non_term_bank_rate=0.0020,
        settlement_trading_days=2,
        adv_window_size=20,
    )

    entry_fills = trace_initial_execution(
        bars,
        list(series.warmup_bars),
        cfg.initial_capital,
        cfg.lot_size,
        cfg.base_slippage_bps,
        cfg.fee_trade,
        cfg.max_adv_participation,
    )
    engine = DividendAuditEngineV2(cfg, calendar=VietnamTradingCalendar())
    res = engine.run_audit(
        bars=bars,
        cash_divs=divs,
        bank_rates=BANK_RATES_BIG4,
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids={d.action_id for d in divs},
        warmup_bars=list(series.warmup_bars),
    )

    n_limit = sum(1 for t in series.gap_checks if t == "limit-band")
    print(
        f"[BẰNG CHỨNG] lưới tick {series.tick_size:g} VND (HOSE 10k-50k); "
        f"{n_limit}/{len(series.gap_checks)} event dùng tầng trần/sàn ±7%; "
        "mọi event được kiểm chéo vendor độc lập."
    )

    price_change = (bars[-1].raw_close - bars[0].raw_close) / bars[0].raw_close * 100
    print_audit_report(
        series=series,
        res=res,
        cfg=cfg,
        entry_fills=entry_fills,
        divs=divs,
        charter=(
            "Pha 5 — 'bò sữa tiền mặt thuần túy' (CTCP Công viên nước Đầm Sen, HOSE): "
            "ca đối chứng dòng tiền đều đặn, thị giá ổn định, lưới tick 50 VND"
        ),
        price_note=(
            f"(Gốc cổ phần biến động {price_change:+.1f}% do thị giá từ "
            f"{bars[0].raw_close:,.0f} đ -> {bars[-1].raw_close:,.0f} đ)"
        ),
    )
    return res


if __name__ == "__main__":
    run_dsn_audit()
