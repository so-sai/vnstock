"""
CLI DIVIDEND AUDIT SERVICE (PTCK_VNSTOCK)
=========================================
Hợp nhất và tự động hóa kiểm toán thực nghiệm Cổ tức tái đầu tư (DRIP)
so với Tiền gửi 12 tháng Big 4 trực tiếp qua CLI `ptck.py`.

Tuân thủ nghiêm ngặt 3 Bất biến Cốt lõi của PTCK_VNSTOCK (AGENTS.md):
1. Missing > Fake (fail-closed): Chỉ chạy trên chuỗi giá thô unadjusted đã phục nguyên 100% on-grid.
2. Vi cấu trúc thị trường: Lô 100 cp, trượt giá 50 bps, trần 20% ADV20, chu kỳ T+2, thuế TNCN 5%.
3. Không làm tròn cẩu thả: Tính toán dòng tiền và đối soát từng kỳ cổ tức VSDC.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.research.dividend_audit_engine import (
    CashDividendEvent,
    DividendAuditEngineV2,
    EngineConfig,
    VietnamTradingCalendar,
)

# Lãi suất bình quân tiền gửi 12 tháng nhóm NHTM Nhà nước (Big 4) 2021-2026
BANK_RATES_BIG4 = {
    2021: 0.0560,
    2022: 0.0740,
    2023: 0.0550,
    2024: 0.0520,
    2025: 0.0530,
    2026: 0.0540,
}

# 6 sự kiện cổ tức tiền mặt VSDC của FOC (2021-2026)
FOC_VSDC_DIVIDENDS = [
    CashDividendEvent(
        ex_date=date(2021, 4, 13),
        pay_date=date(2021, 4, 29),
        cash_per_share=20_000.0,
        action_id="VSDC/FOC/20210413/CASH_20000",
    ),
    CashDividendEvent(
        ex_date=date(2022, 6, 10),
        pay_date=date(2022, 6, 30),
        cash_per_share=8_000.0,
        action_id="VSDC/FOC/20220610/CASH_8000",
    ),
    CashDividendEvent(
        ex_date=date(2023, 6, 8),
        pay_date=date(2023, 6, 30),
        cash_per_share=5_000.0,
        action_id="VSDC/FOC/20230608/CASH_5000",
    ),
    CashDividendEvent(
        ex_date=date(2024, 7, 18),
        pay_date=date(2024, 8, 9),
        cash_per_share=2_000.0,
        action_id="VSDC/FOC/20240718/CASH_2000",
    ),
    CashDividendEvent(
        ex_date=date(2025, 5, 23),
        pay_date=date(2025, 6, 20),
        cash_per_share=10_000.0,
        action_id="VSDC/FOC/20250523/CASH_10000",
    ),
    CashDividendEvent(
        ex_date=date(2026, 8, 10),
        pay_date=date(2026, 9, 15),
        cash_per_share=10_000.0,
        action_id="VSDC/FOC/20260810/CASH_10000",
    ),
]

# 7 sự kiện cổ tức tiền mặt VSDC của VEA (2021-2026)
VEA_VSDC_DIVIDENDS = [
    CashDividendEvent(
        ex_date=date(2021, 1, 4),
        pay_date=date(2021, 2, 5),
        cash_per_share=5_252.90,
        action_id="VSDC/VEA/20210104/CASH_5253",
    ),
    CashDividendEvent(
        ex_date=date(2021, 7, 15),
        pay_date=date(2021, 8, 16),
        cash_per_share=4_990.00,
        action_id="VSDC/VEA/20210715/CASH_4990",
    ),
    CashDividendEvent(
        ex_date=date(2021, 10, 12),
        pay_date=date(2021, 11, 10),
        cash_per_share=462.70,
        action_id="VSDC/VEA/20211012/CASH_463",
    ),
    CashDividendEvent(
        ex_date=date(2022, 10, 28),
        pay_date=date(2022, 11, 30),
        cash_per_share=4_493.70,
        action_id="VSDC/VEA/20221028/CASH_4494",
    ),
    CashDividendEvent(
        ex_date=date(2023, 11, 17),
        pay_date=date(2023, 12, 20),
        cash_per_share=4_186.90,
        action_id="VSDC/VEA/20231117/CASH_4187",
    ),
    CashDividendEvent(
        ex_date=date(2024, 11, 19),
        pay_date=date(2024, 12, 20),
        cash_per_share=5_035.18,
        action_id="VSDC/VEA/20241119/CASH_5035",
    ),
    CashDividendEvent(
        ex_date=date(2025, 11, 18),
        pay_date=date(2025, 12, 19),
        cash_per_share=4_658.08,
        action_id="VSDC/VEA/20251118/CASH_4658",
    ),
]

# 12 sự kiện cổ tức tiền mặt VSDC của SMB (2021-2026)
SMB_VSDC_DIVIDENDS = [
    CashDividendEvent(
        ex_date=date(2021, 10, 22),
        pay_date=date(2021, 11, 15),
        cash_per_share=2_500.0,
        action_id="VSDC/SMB/20211022/CASH_2500",
    ),
    CashDividendEvent(
        ex_date=date(2022, 2, 18),
        pay_date=date(2022, 3, 15),
        cash_per_share=1_000.0,
        action_id="VSDC/SMB/20220218/CASH_1000",
    ),
    CashDividendEvent(
        ex_date=date(2022, 9, 20),
        pay_date=date(2022, 10, 14),
        cash_per_share=2_500.0,
        action_id="VSDC/SMB/20220920/CASH_2500",
    ),
    CashDividendEvent(
        ex_date=date(2023, 2, 20),
        pay_date=date(2023, 3, 15),
        cash_per_share=1_000.0,
        action_id="VSDC/SMB/20230220/CASH_1000",
    ),
    CashDividendEvent(
        ex_date=date(2023, 7, 17),
        pay_date=date(2023, 8, 15),
        cash_per_share=2_500.0,
        action_id="VSDC/SMB/20230717/CASH_2500",
    ),
    CashDividendEvent(
        ex_date=date(2023, 10, 24),
        pay_date=date(2023, 11, 15),
        cash_per_share=1_000.0,
        action_id="VSDC/SMB/20231024/CASH_1000",
    ),
    CashDividendEvent(
        ex_date=date(2024, 5, 22),
        pay_date=date(2024, 6, 14),
        cash_per_share=1_500.0,
        action_id="VSDC/SMB/20240522/CASH_1500",
    ),
    CashDividendEvent(
        ex_date=date(2024, 9, 16),
        pay_date=date(2024, 10, 15),
        cash_per_share=2_000.0,
        action_id="VSDC/SMB/20240916/CASH_2000",
    ),
    CashDividendEvent(
        ex_date=date(2025, 1, 7),
        pay_date=date(2025, 1, 24),
        cash_per_share=1_500.0,
        action_id="VSDC/SMB/20250107/CASH_1500",
    ),
    CashDividendEvent(
        ex_date=date(2025, 8, 13),
        pay_date=date(2025, 9, 15),
        cash_per_share=2_000.0,
        action_id="VSDC/SMB/20250813/CASH_2000",
    ),
    CashDividendEvent(
        ex_date=date(2025, 11, 12),
        pay_date=date(2025, 12, 15),
        cash_per_share=2_000.0,
        action_id="VSDC/SMB/20251112/CASH_2000",
    ),
    CashDividendEvent(
        ex_date=date(2026, 5, 25),
        pay_date=date(2026, 6, 15),
        cash_per_share=1_000.0,
        action_id="VSDC/SMB/20260525/CASH_1000",
    ),
]

SUPPORTED_BENCHMARKS = ("FOC", "VEA", "SMB")


def audit_single_symbol(symbol: str, initial_capital: float = 1_000_000_000.0) -> dict[str, Any]:
    """Kiểm toán thực nghiệm cho một mã đã được hiệu chuẩn và phục nguyên giá thô."""
    sym = symbol.strip().upper()
    calendar = VietnamTradingCalendar()

    if sym == "FOC":
        from src.research.run_foc_empirical_audit import (
            generate_dec_2020_warmup_bars,
            load_foc_clean_bars,
            trace_initial_execution,
        )

        bars, segments = load_foc_clean_bars()
        warmup_bars = generate_dec_2020_warmup_bars(bars[0], calendar)
        lot_size = 100
        tick_size = 100.0
        div_events = FOC_VSDC_DIVIDENDS
        bank_rates_big4 = BANK_RATES_BIG4
        exchange = "UPCoM"
    elif sym == "VEA":
        from src.research.run_vea_empirical_audit import (
            load_vea_clean_bars,
            trace_initial_execution,
        )

        bars, warmup_bars, segments = load_vea_clean_bars()
        lot_size = 100
        tick_size = 100.0
        div_events = VEA_VSDC_DIVIDENDS
        bank_rates_big4 = BANK_RATES_BIG4
        exchange = "UPCoM"
    elif sym == "SMB":
        from src.research.run_smb_empirical_audit import (
            load_smb_clean_bars,
            trace_initial_execution,
        )

        bars, warmup_bars, segments = load_smb_clean_bars()
        lot_size = 100
        tick_size = 50.0
        div_events = SMB_VSDC_DIVIDENDS
        bank_rates_big4 = BANK_RATES_BIG4
        exchange = "HOSE"
    else:
        raise ValueError(
            f"Mã '{sym}' chưa hoàn tất đối soát provenance VSDC và phục nguyên giá thô unadjusted.\n"
            f"Các mã đã được chứng nhận kiểm toán định lượng: {', '.join(SUPPORTED_BENCHMARKS)}."
        )

    cfg = EngineConfig(
        symbol=sym,
        initial_capital=initial_capital,
        is_unadjusted_price=True,
        lot_size=lot_size,
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
        warmup_bars,
        cfg.initial_capital,
        cfg.lot_size,
        cfg.base_slippage_bps,
        cfg.fee_trade,
        cfg.max_adv_participation,
    )

    independent_action_ids = {d.action_id for d in div_events}
    engine = DividendAuditEngineV2(cfg, calendar=calendar)
    res = engine.run_audit(
        bars=bars,
        cash_divs=div_events,
        bank_rates=bank_rates_big4,
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=independent_action_ids,
        warmup_bars=warmup_bars,
    )

    initial_shares = sum(f["shares_bought"] for f in entry_fills)
    initial_spent = sum(f["cost_spent"] for f in entry_fills)
    final_shares = res["drip_settled_regular_shares"]
    alpha_vnd = res["drip_final_wealth"] - res["bank_final_nav"]
    alpha_cagr = res["drip_cagr"] - res["bank_cagr"]
    price_change_pct = (bars[-1].raw_close - bars[0].raw_close) / bars[0].raw_close * 100.0

    if alpha_vnd > 0:
        verdict = "CASH_COW_COMPOUNDER"
        verdict_str = "CHIẾN LƯỢC DRIP THẮNG TIỀN GỬI NGÂN HÀNG"
    else:
        verdict = "DIVIDEND_YIELD_TRAP"
        verdict_str = "TIỀN GỬI NGÂN HÀNG THẮNG CHIẾN LƯỢC DRIP"

    return {
        "symbol": sym,
        "exchange": exchange,
        "tick_size": tick_size,
        "start_date": str(bars[0].trade_date),
        "end_date": str(bars[-1].trade_date),
        "period_years": round(res["period_years"], 2),
        "bars_observed": res["bars_observed"],
        "initial_capital": initial_capital,
        "initial_shares": initial_shares,
        "initial_spent": initial_spent,
        "entry_days_needed": len(entry_fills),
        "entry_fills": entry_fills,
        "processed_cash_divs": res["processed_cash_divs"],
        "total_cash_div_per_share": sum(d.cash_per_share for d in div_events),
        "price_start": bars[0].raw_close,
        "price_end": bars[-1].raw_close,
        "price_change_pct": round(price_change_pct, 2),
        "bank_final_nav": round(res["bank_final_nav"], 0),
        "bank_cagr": round(res["bank_cagr"], 4),
        "drip_final_shares": final_shares,
        "drip_final_wealth": round(res["drip_final_wealth"], 0),
        "drip_cagr": round(res["drip_cagr"], 4),
        "alpha_vnd": round(alpha_vnd, 0),
        "alpha_cagr": round(alpha_cagr, 4),
        "cashout_terminal_stock_val": round(res["cashout_terminal_stock_val"], 0),
        "cashout_spent_dividends": round(res["cashout_spent_dividends"], 0),
        "cashout_wealth_if_saved": round(res["cashout_wealth_if_saved"], 0),
        "cashout_cagr_if_saved": round(res["cashout_cagr_if_saved"], 4),
        "max_drawdown": round(res["max_drawdown_observed"], 4),
        "verdict": verdict,
        "verdict_str": verdict_str,
    }


def print_symbol_audit_report(data: dict[str, Any]) -> None:
    """In báo cáo định dạng chuẩn 4 lớp cho terminal."""
    print("=" * 86)
    print(f"🛡️ BÁO CÁO KIỂM TOÁN ĐỊNH LƯỢNG THỰC NGHIỆM: {data['symbol']} VS TIỀN GỬI 12 THÁNG")
    print(f"Sàn niêm yết: {data['exchange']} | Bước giá: {data['tick_size']:g} VND | Quan sát: {data['bars_observed']} phiên")
    print(f"Khoảng thời gian: {data['start_date']} đến {data['end_date']} ({data['period_years']} năm)")
    print(f"Tổng cổ tức tiền mặt VSDC: {data['processed_cash_divs']} đợt (tổng {data['total_cash_div_per_share']:,.0f} đ/cp)")
    print("=" * 86)

    print("\n[1] TIẾN TRÌNH GIẢI NGÂN KHỞI TẠO (VỐN: {:,.0f} VND):".format(data["initial_capital"]))
    print(f"  • Số phiên giải ngân (trần 20% ADV20, trượt giá 50 bps): {data['entry_days_needed']} phiên")
    print(f"  • Cổ phần khởi tạo: {data['initial_shares']:,d} cp | Vốn đã khớp: {data['initial_spent']:,.0f} VND")
    if data["entry_days_needed"] > 1:
        print("  • Chi tiết từng phiên:")
        for f in data["entry_fills"]:
            p_val = f["price"]
            s_bought = f["shares_bought"]
            c_spent = f["cost_spent"]
            print(f"    - Ngày {f['date']}: Khớp {s_bought:>5,d} cp @ {p_val:>7,.0f} đ | Mua: {c_spent:>11,.0f} đ")

    print("\n[2] ĐỐI SOÁT HIỆU SUẤT TỔNG TÀI SẢN (DRIP VS TIỀN GỬI 12 THÁNG):")
    print("  A. Tiền gửi 12 tháng Big 4 (TT 04/2022/TT-NHNN):")
    print(f"     - Tài sản cuối kỳ:             {data['bank_final_nav']:>16,.0f} VND")
    print(f"     - Lợi suất kép bình quân (CAGR): {data['bank_cagr'] * 100:>15.2f}%/năm")

    print("  B. Chiến lược DRIP (Tái đầu tư toàn bộ cổ tức + trừ phí/thuế/trượt giá):")
    sh_diff = data["drip_final_shares"] - data["initial_shares"]
    pct_sh = (sh_diff / data["initial_shares"]) * 100.0
    final_sh = int(data["drip_final_shares"])
    print(
        f"     - Tích lũy cổ phần:            {data['initial_shares']:,d} cp  --->  "
        f"{final_sh:,d} cp ({sh_diff:+,.0f} cp, {pct_sh:+.1f}%)"
    )
    print(f"     - Tài sản thanh lý ròng:       {data['drip_final_wealth']:>16,.0f} VND")
    print(f"     - Lợi suất kép bình quân (CAGR): {data['drip_cagr'] * 100:>15.2f}%/năm")

    print(f"\n  ==> PHÁN QUYẾT: {data['verdict_str']}!")
    if data["alpha_vnd"] > 0:
        pct_surplus = data["alpha_vnd"] / data["initial_capital"] * 100
        print(f"      + Thặng dư tài sản tuyệt đối: +{data['alpha_vnd']:,.0f} VND (+{pct_surplus:.2f}%)")
        print(f"      + Tỷ suất sinh lời vượt trội (Alpha): +{data['alpha_cagr'] * 100:.2f}%/năm")
    else:
        pct_deficit = -data["alpha_vnd"] / data["initial_capital"] * 100
        print(f"      - Tiền gửi ngân hàng vượt trội: +{-data['alpha_vnd']:,.0f} VND (+{pct_deficit:.2f}%)")
        print(f"      - Alpha âm của chiến lược DRIP: {data['alpha_cagr'] * 100:.2f}%/năm")

    print("\n[3] KỊCH BẢN RÚT CỔ TỨC (CASH-OUT) & RỦI RO SỤT GIẢM (MAX DRAWDOWN):")
    p_st = data["price_start"]
    p_ed = data["price_end"]
    p_pct = data["price_change_pct"]
    print(f"  • Biến động thị giá gốc: {p_st:,.0f} đ ---> {p_ed:,.0f} đ ({p_pct:+.1f}%)")
    print(f"  • (B1) Giá trị cổ phiếu gốc còn lại:   {data['cashout_terminal_stock_val']:>16,.0f} VND")
    pct_rec = (data["cashout_spent_dividends"] / data["initial_capital"]) * 100.0
    spent_divs = data["cashout_spent_dividends"]
    print(f"  • (B2) Cổ tức tiền mặt thực rút:       {spent_divs:>16,.0f} VND (Thu hồi {pct_rec:.1f}% vốn)")
    cagr_saved = data["cashout_cagr_if_saved"] * 100
    saved_val = data["cashout_wealth_if_saved"]
    print(f"  • (B3) Tổng tài sản nếu gửi tiết kiệm: {saved_val:>16,.0f} VND (CAGR: {cagr_saved:.2f}%/năm)")
    print(f"  • Mức sụt giảm tối đa (Max Drawdown):   {data['max_drawdown'] * 100:>15.2f}%")
    print("=" * 86)


def print_comparative_table(data_list: list[dict[str, Any]]) -> None:
    """In bảng so sánh tổng hợp nhiều mã."""
    print("=" * 96)
    print("📊 BẢNG TỔNG HỢP KIỂM TOÁN THỰC NGHIỆM: CỔ TỨC VS TIỀN GỬI 12 THÁNG (2021-2026)")
    print("=" * 96)
    hdr = (
        f"{'Mã':<6} | {'Sàn':<6} | {'Thị giá':<10} | {'Cổ tức/cp':<11} | "
        f"{'DRIP CAGR':<10} | {'Tiền gửi':<10} | {'Alpha':<10} | {'MaxDD':<8} | {'Phán quyết':<20}"
    )
    print(hdr)
    print("-" * 96)
    for d in data_list:
        p_chg = f"{d['price_change_pct']:+.1f}%"
        div_str = f"{d['total_cash_div_per_share']:,.0f}đ"
        drip_cagr = f"{d['drip_cagr'] * 100:.2f}%"
        bank_cagr = f"{d['bank_cagr'] * 100:.2f}%"
        alpha_str = f"{d['alpha_cagr'] * 100:+.2f}%"
        mdd_str = f"{d['max_drawdown'] * 100:.1f}%"
        verdict = "Compounder" if d["verdict"] == "CASH_COW_COMPOUNDER" else "Yield Trap"
        row = (
            f"{d['symbol']:<6} | {d['exchange']:<6} | {p_chg:<10} | {div_str:<11} | "
            f"{drip_cagr:<10} | {bank_cagr:<10} | {alpha_str:<10} | {mdd_str:<8} | {verdict:<20}"
        )
        print(row)
    print("=" * 96)


def handle_cli_dividend_audit(args: Any) -> None:
    """Handler chính được gọi từ ptck.py CLI."""
    sym = getattr(args, "symbol", "ALL")
    sym = (sym or "ALL").strip().upper()
    capital = getattr(args, "capital", 1_000_000_000.0)
    as_json = getattr(args, "json", False)

    if sym in ("ALL", "BENCHMARK"):
        results = []
        for s in SUPPORTED_BENCHMARKS:
            try:
                results.append(audit_single_symbol(s, initial_capital=capital))
            except (ValueError, RuntimeError) as exc:
                print(f"[CẢNH BÁO] Không thể kiểm toán {s}: {exc}", file=sys.stderr)

        if as_json:
            print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        else:
            for r in results:
                print_symbol_audit_report(r)
                print()
            print_comparative_table(results)
    elif sym in SUPPORTED_BENCHMARKS:
        res = audit_single_symbol(sym, initial_capital=capital)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        else:
            print_symbol_audit_report(res)
    else:
        # Mã chưa hiệu chuẩn
        msg = (
            f"⚠️ Mã '{sym}' chưa hoàn tất đối soát provenance VSDC và phục nguyên giá thô unadjusted.\n"
            f"Theo Điều 1 Hiến pháp PTCK_VNSTOCK: 'Missing > Fake (fail-closed)' — cấm chạy audit trên giá điều chỉnh.\n\n"
            f"Các mã đã được chứng nhận kiểm toán định lượng sẵn sàng chạy ngay: {', '.join(SUPPORTED_BENCHMARKS)}.\n"
            f"Gợi ý: Hãy thử `python ptck.py dividend-audit --symbol ALL` hoặc `python ptck.py dividend-audit --symbol SMB`."
        )
        if as_json:
            print(json.dumps({"error": "UNCALIBRATED_SYMBOL", "symbol": sym, "message": msg}, indent=2, ensure_ascii=False))
        else:
            print(msg)
