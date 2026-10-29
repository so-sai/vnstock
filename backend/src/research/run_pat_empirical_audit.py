"""EMPIRICAL AUDIT RUNNER: PAT — "SIÊU CỔ TỨC CHU KỲ HÀNG HÓA" (UPCoM) — PHA 4.

[WHY] NQ-08 chọn PAT để khảo nghiệm điều kiện SỤP ĐỔ của DRIP: một doanh nghiệp
phốt pho vàng chi 10.000-10.655 đ/cp mỗi đợt khi giá hàng hóa ở đỉnh (2022-2023),
rồi hạ dần khi chu kỳ hạ nhiệt. DRIP mua cổ phiếu bằng cổ tức ở đúng vùng giá cao
sẽ bị "mua đỉnh" — đây là ca đối chứng với VEA/SMB (cash-cow đều đặn).

[ROOT CAUSE] Trước runner này PAT chỉ tồn tại như một cái tên trong nghị quyết:
registry chưa có event, `audit_provenance` chưa có pay_date, chuỗi KBS 2022-2026
chưa được phục nguyên. Không có ba thứ đó thì mọi con số về PAT sẽ là bịa.

[INVARIANT] Cổ tức, lưới tick, biên trần/sàn, mốc kết thúc đều lấy từ SSOT +
pipeline dùng chung (`symbol_audit_pipeline`). Runner này chỉ còn: cấu hình engine
và in báo cáo — không được khai báo lại event.
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


def run_pat_audit() -> dict:
    series = load_certified_series("PAT")
    divs = series.dividends()
    bars = list(series.audit_bars)

    cfg = EngineConfig(
        symbol="PAT",
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

    # [WHY] Cảnh báo bằng chứng phải nằm NGAY TRONG báo cáo: 4/9 event của PAT
    # được nghiệm thu bằng tầng trần/sàn ±15% (phiên gap), không phải band OHLC.
    n_limit = sum(1 for t in series.gap_checks if t == "limit-band")
    print(
        f"[BẰNG CHỨNG] {n_limit}/{len(series.gap_checks)} event dùng tầng trần/sàn ±15% UPCoM; "
        "mọi event đều được kiểm chéo vendor độc lập (xem bảng ở cổng --reconstructed)."
    )

    price_change = (bars[-1].raw_close - bars[0].raw_close) / bars[0].raw_close * 100
    print_audit_report(
        series=series,
        res=res,
        cfg=cfg,
        entry_fills=entry_fills,
        divs=divs,
        charter=(
            "Pha 4 — 'siêu cổ tức chu kỳ hàng hóa' (CTCP Phốt pho Vàng Việt Trì, UPCoM): "
            "thử thách DRIP khi mua lại cổ phiếu ở vùng đỉnh chu kỳ lợi nhuận"
        ),
        price_note=(
            f"(Gốc cổ phần biến động {price_change:+.1f}% do thị giá từ "
            f"{bars[0].raw_close:,.0f} đ -> {bars[-1].raw_close:,.0f} đ)"
        ),
    )
    return res


if __name__ == "__main__":
    run_pat_audit()
