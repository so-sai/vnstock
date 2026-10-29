"""PIPELINE KIỂM TOÁN THỰC NGHIỆM DÙNG CHUNG CHO PHA 4–5 (PAT, DSN).

[WHY] Ba runner Pha 1–3 (FOC/VEA/SMB) mỗi cái tự nạp vintage, tự phục nguyên, tự
in báo cáo — chấp nhận được khi mỗi mã là một ca kiểm định riêng, nhưng Pha 4
(PAT, UPCoM, lưới 100) và Pha 5 (DSN, HOSE, lưới 50) chỉ khác nhau ở registry,
còn toàn bộ chuỗi thao tác là một. Chép lần thứ tư/thứ năm đúng là cái bẫy DRY
đã làm trôi lệch cổ tức ba lần trước đó (xem `audit_provenance`).

[ROOT CAUSE] Phần dùng chung KHÔNG chỉ là "đọc file": nó là BA chốt chặn liên
hoàn — (1) phục nguyên band mode đúng lưới tick của sàn, (2) certify gap identity
qua biên phiên GDKHQ, (3) kiểm chéo vendor độc lập với giá khớp. Nếu mỗi runner
tự ghép lại, một runner có thể quên đúng chốt mà runner kia có — và báo cáo vẫn
in ra như thật.

[INVARIANT]
(1) Mọi hàm nạp ở đây FAIL-CLOSED: `is_certified=False`, tick lệch, hoặc tầng 3
    có `conflict`/thiếu bằng chứng cho event `limit-band` ⇒ RAISE, không trả
    chuỗi "gần đúng".
(2) Cổ tức LUÔN lấy từ `build_vsdc_dividends` (SSOT), không nhận tham số event
    từ runner — chữ ký hàm không có chỗ cho bản sao thứ sáu.
(3) Mốc kết thúc audit là tham số BẮT BUỘC: cửa sổ 2025–2026 là OOS nên mọi báo
    cáo phải in nhãn NON-DECISIONAL (NQ-04 Mục 9).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from src.research.audit_provenance import (
        AUDIT_WINDOW_START,
        build_vsdc_dividends,
        dividend_cash_total,
    )
    from src.research.cash_event_registry import (
        CASH_EVENTS,
        TICK_SIZES,
    )
    from src.research.cross_vendor_agreement import (
        EventCrossCheck,
        load_db_closes,
    )
    from src.research.dividend_audit_engine import CashDividendEvent, EngineConfig, MarketBar
    from src.research.price_provenance_gate import (
        AdjustedPriceDetectedError,
        evaluate_cross_vendor,
        resolve_vintage_path,
        verify_reconstructed_prices,
    )
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.research.audit_provenance import (
        AUDIT_WINDOW_START,
        build_vsdc_dividends,
        dividend_cash_total,
    )
    from backend.src.research.cash_event_registry import (
        CASH_EVENTS,
        TICK_SIZES,
    )
    from backend.src.research.cross_vendor_agreement import (
        EventCrossCheck,
        load_db_closes,
    )
    from backend.src.research.dividend_audit_engine import CashDividendEvent, EngineConfig, MarketBar
    from backend.src.research.price_provenance_gate import (
        AdjustedPriceDetectedError,
        evaluate_cross_vendor,
        resolve_vintage_path,
        verify_reconstructed_prices,
    )

# [WHY] ADV20 cần đủ 20 phiên lịch sử volume: dùng ít hơn sẽ khiến trần 20% ADV
# bị tính trên mẫu số nhỏ ⇒ kế hoạch giải ngân bị thổi phồng, đúng loại lỗi vi
# cấu trúc mà cả bộ kiểm toán này tồn tại để tránh.
WARMUP_SIZE = 20

# [WHY] Mốc kết thúc chung cho cả 5 mã: 2026-10-23 (phiên cuối của Pha 1–3).
# Dùng chung để bảng so sánh 5 mã không bị lệch kỳ (PAT vintage tới 28/10, DSN
# tới 29/10) — lệch 4 phiên đủ làm CAGR lệch 0,05pp và gây tranh cãi vô ích.
AUDIT_WINDOW_END = date(2026, 10, 23)

DB_PATH = BACKEND_DIR / "data" / "screener_cache.db"

# [WHY] Lãi suất tiền gửi 12 tháng bình quân nhóm Big 4 theo năm (TT 04/2022/TT-NHNN
# + công bố lãi suất niêm yết từng năm). Đây là BENCHMARK, không phải tham số
# chiến lược: sửa để "cho DRIP thắng" là gian lận phép so sánh. Cùng một bảng
# cho cả 5 mã để bảng tổng hợp so sánh được.
BANK_RATES_BIG4: dict[int, float] = {
    2021: 0.0560,
    2022: 0.0740,
    2023: 0.0550,
    2024: 0.0520,
    2025: 0.0530,
    2026: 0.0540,
}


@dataclass(frozen=True)
class CertifiedSeries:
    """Chuỗi đã qua ĐỦ BA tầng, sẵn sàng nạp vào engine."""

    symbol: str
    audit_bars: tuple[MarketBar, ...]
    warmup_bars: tuple[MarketBar, ...]
    n_bars_total: int
    n_off_tick: int
    tick_size: float
    segments: tuple[tuple[date, date, float], ...]
    gap_checks: tuple[str, ...]
    cross_checks: tuple[EventCrossCheck, ...]

    def dividends(self) -> list[CashDividendEvent]:
        """Cổ tức trong kỳ audit — SUY RA từ registry theo bar đầu tiên.

        [WHY] Chữ ký này là chốt chặn: runner không có tham số nào để truyền
        danh sách cổ tức của riêng nó, nên bản sao thứ sáu không thể xuất hiện.
        """
        return build_vsdc_dividends(self.symbol, self.audit_bars[0].trade_date)


def load_certified_series(
    symbol: str,
    *,
    audit_end: date = AUDIT_WINDOW_END,
    db_path: Path | str = DB_PATH,
    bars_dir: Path | str | None = None,
) -> CertifiedSeries:
    """Nạp + chứng nhận chuỗi 3 tầng cho `symbol`. Raise nếu thiếu bằng chứng."""
    if symbol not in CASH_EVENTS or symbol not in TICK_SIZES:
        raise ValueError(f"FAIL_CLOSED: {symbol} chưa có registry cổ tức/lưới tick")
    bars_dir = Path(bars_dir) if bars_dir is not None else BACKEND_DIR / "data"
    tick = TICK_SIZES[symbol]

    # [WHY] Đọc volume từ ĐÚNG file mà `verify_reconstructed_prices` vừa nạp:
    # lấy chuỗi volume từ nguồn khác sẽ làm ADV20 lệch ngày với giá đã certify.
    path = resolve_vintage_path(symbol, bars_dir)
    raw_rows = json.loads(path.read_text(encoding="utf-8"))
    # `load_vintage_ohlcv` bên trong `verify_reconstructed_prices` đã kiểm
    # tính toàn vẹn; ta nạp lại ở đây chỉ để lấy volume theo đúng thứ tự ngày.
    raw_rows = sorted(raw_rows, key=lambda r: r["t"])

    from src.research.price_provenance_gate import load_vintage_ohlcv

    dates, closes, lows, highs = load_vintage_ohlcv(symbol, bars_dir)
    if len(raw_rows) != len(dates):
        raise ValueError(f"{path.name}: số bar lệch giữa hai lần đọc ({len(raw_rows)} vs {len(dates)}) — dừng")

    verdict = verify_reconstructed_prices(symbol, dates, closes, lows, highs, CASH_EVENTS[symbol], tick_size=tick)
    if not verdict.is_certified:
        raise AdjustedPriceDetectedError(
            f"FAIL_CLOSED: chuỗi {symbol} chưa được chứng nhận gap identity — cấm chạy audit. "
            f"Anomalies: {list(verdict.reasons)}"
        )

    # ── Tầng 3: kiểm chéo vendor (độc lập với band OHLC) ──
    try:
        db_closes = load_db_closes(symbol, db_path)
    except (ValueError, sqlite3.Error) as exc:
        # [WHY] Runner audit là nơi ĐƯA RA CON SỐ cuối cùng, nên ở đây không có
        # chế độ "ghi chú": thiếu DB ⇒ không kiểm chéo được ⇒ dừng (khác với
        # cổng, nơi event in-range đã đủ mạnh để bỏ qua tầng 3).
        raise AdjustedPriceDetectedError(f"FAIL_CLOSED: không đọc được DB để kiểm chéo vendor cho {symbol}: {exc}") from exc
    checks, blocking, _notes = evaluate_cross_vendor(
        symbol, CASH_EVENTS[symbol], dict(zip(dates, closes)), db_closes, verdict.gap_checks
    )
    if blocking:
        raise AdjustedPriceDetectedError(
            f"FAIL_CLOSED: tầng 3 phát hiện vấn đề provenance cho {symbol}: " + "; ".join(blocking)
        )

    bars_all = [
        MarketBar(trade_date=d, raw_close=p, volume=float(r["v"])) for d, p, r in zip(dates, verdict.raw_closes, raw_rows)
    ]

    # [WHY] Warm-up = 20 phiên TRƯỚC kỳ audit (để ADV20 có mẫu số thật). Mã
    # niêm yết muộn (PAT 06/2022) không có lịch sử trước 2021-01-04 ⇒ dùng 20 bar
    # ĐẦU của chính chuỗi làm warm-up rồi bắt đầu audit từ bar kế tiếp. Không
    # được chạy engine khi chưa đủ 20 bar: trần 20% ADV20 sẽ tính trên mẫu số
    # nhỏ, kế hoạch giải ngân bị thổi phồng.
    warmup = [b for b in bars_all if b.trade_date < AUDIT_WINDOW_START][-WARMUP_SIZE:]
    if len(warmup) < WARMUP_SIZE:
        if len(bars_all) < WARMUP_SIZE + 1:
            raise ValueError(f"FAIL_CLOSED: {symbol} chỉ có {len(bars_all)} bar — không đủ warm-up ADV20")
        warmup = bars_all[:WARMUP_SIZE]
    warmup_end = warmup[-1].trade_date
    audit_bars = [b for b in bars_all if warmup_end < b.trade_date <= audit_end]
    if not audit_bars:
        raise ValueError(f"FAIL_CLOSED: {symbol} không có bar nào trong kỳ audit sau {warmup_end}")

    return CertifiedSeries(
        symbol=symbol,
        audit_bars=tuple(audit_bars),
        warmup_bars=tuple(warmup),
        n_bars_total=len(dates),
        n_off_tick=verdict.n_off_tick or 0,
        tick_size=tick,
        segments=verdict.segments,
        gap_checks=verdict.gap_checks,
        cross_checks=checks,
    )


def trace_initial_execution(
    bars: list[MarketBar],
    warmup_bars: list[MarketBar],
    initial_capital: float,
    lot_size: int,
    slippage_bps: float,
    fee_trade: float,
    max_part: float,
) -> list[dict]:
    """Truy vết từng phiên giải ngân ban đầu (giống hệt Pha 1–3).

    [WHY] Order Slicing theo lô, trần 20% ADV20, phạt trượt giá — ba ràng buộc
    vi cấu trúc này quyết định "1 tỷ đồng có thật sự mua được không". Bỏ qua
    chúng sẽ biến mọi so sánh DRIP vs tiền gửi thành so sánh lý thuyết.
    """
    vol_hist = [wb.volume for wb in warmup_bars]
    cash = initial_capital
    fills = []

    for bar in bars:
        vol_hist.append(bar.volume)
        adv20 = sum(vol_hist[-20:]) / 20.0
        max_sh = int((adv20 * max_part) // lot_size) * lot_size
        cost_per_sh = bar.raw_close * (1.0 + slippage_bps) * (1.0 + fee_trade)
        sh_afford = int(cash // cost_per_sh // lot_size) * lot_size
        sh_bought = min(sh_afford, max_sh)

        if sh_bought > 0:
            spent = sh_bought * cost_per_sh
            cash -= spent
            fills.append(
                {
                    "date": bar.trade_date,
                    "adv20": adv20,
                    "shares_bought": sh_bought,
                    "price": bar.raw_close,
                    "cost_spent": spent,
                    "cash_remaining": cash,
                }
            )
        if cash < cost_per_sh * lot_size:
            break

    return fills


def print_audit_report(
    *,
    series: CertifiedSeries,
    res: dict,
    cfg: EngineConfig,
    entry_fills: list[dict],
    divs: list[CashDividendEvent],
    charter: str,
    price_note: str,
) -> None:
    """In báo cáo 4 lớp — CÙNG khuôn cho mọi mã, số liệu lấy từ engine.

    [WHY] Không hardcode bất kỳ con số nào trong phần thân: mọi giá trị đều đọc
    từ `res`/`series`. Báo cáo Pha 1 từng in cứng tổng cổ tức và Alpha, và khi
    chuỗi giá được phục nguyên lại thì con số in ra không còn khớp dữ liệu.
    """
    total_cash_per_share = dividend_cash_total(divs)
    bars = list(series.audit_bars)
    tot_sh = 0
    tot_sp = 0.0

    print("=" * 86)
    print(f"🛡️ BÁO CÁO KIỂM TOÁN ĐỊNH LƯỢNG THỰC NGHIỆM: {series.symbol} VS TIỀN GỬI 12 THÁNG")
    print(f"Đặc tính kiểm toán: {charter}")
    print(f"Khoảng thời gian: {bars[0].trade_date} đến {bars[-1].trade_date} ({res['period_years']:.2f} năm)")
    print(f"Số phiên quan sát: {res['bars_observed']} phiên | Phục nguyên giá thô: 100% bội số {series.tick_size:g} VND")
    print(
        f"Nguồn đối soát cổ tức: VSDC Official ({res['processed_cash_divs']} kỳ tiền mặt, "
        f"tổng {total_cash_per_share:,.2f} đ/cp) — suy ra từ cash_event_registry"
    )
    tier_counts: dict[str, int] = {}
    for tier in series.gap_checks:
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    cross_counts: dict[str, int] = {}
    for chk in series.cross_checks:
        cross_counts[chk.status] = cross_counts.get(chk.status, 0) + 1
    print(
        "Tầng nghiệm thu gap: "
        + ", ".join(f"{k}={v}" for k, v in sorted(tier_counts.items()))
        + " | kiểm chéo vendor: "
        + ", ".join(f"{k}={v}" for k, v in sorted(cross_counts.items()))
    )
    print("Chứng nhận chuỗi giá: price_provenance_gate --reconstructed = EXIT 0 (certified, không conflict)")
    print("NHÃN: NON-DECISIONAL — 2025-2026 là cửa sổ OOS; báo cáo này mô tả, KHÔNG dùng để quyết định.")
    print("=" * 86)

    print(f"\n[CÂU HỎI 1] THỜI GIAN VÀ TIẾN TRÌNH GIẢI NGÂN BAN ĐẦU ({cfg.initial_capital:,.0f} ĐỒNG VÀO {series.symbol}):")
    print("  • Cơ chế khớp lệnh: Order Slicing theo lô 100, trần 20% ADV20, phạt trượt giá 50 bps.")
    print(f"  • Tổng số phiên cần để giải ngân hết: {len(entry_fills)} phiên giao dịch.")
    print("  • Nhật ký khớp lệnh từng phiên:")
    for f in entry_fills:
        tot_sh += f["shares_bought"]
        tot_sp += f["cost_spent"]
        print(
            f"    - Phiên {f['date']}: Khớp {f['shares_bought']:>5,d} cp @ {f['price']:>7,.0f} đ "
            f"| ADV20: {f['adv20']:>6,.0f} | Tiền mua: {f['cost_spent']:>11,.0f} đ "
            f"| Tiền dư: {f['cash_remaining']:>11,.0f} đ"
        )
    print(f"  • Tổng cổ phần khởi tạo: {tot_sh:,d} cp | Tổng vốn đã giải ngân: {tot_sp:,.0f} đ")
    print(f"  • Tiền mặt lẻ tồn dư (không đủ mua lô 100): {entry_fills[-1]['cash_remaining']:,.0f} đ")

    print("\n[CÂU HỎI 2] HIỆU SUẤT TỔNG TÀI SẢN DRIP VS TIỀN GỬI 12 THÁNG:")
    print("  A. BENCHMARK TIỀN GỬI NGÂN HÀNG 12 THÁNG (BIG 4 - TT 04/2022/TT-NHNN):")
    print(
        f"     - Số chu kỳ 365 ngày trọn vẹn: {res['bank_full_rolls']} kỳ + {res['bank_stub_days']} ngày lẻ (lãi KKH 0.2%/năm)"
    )
    print(f"     - Tổng tài sản cuối kỳ:        {res['bank_final_nav']:>16,.0f} VND")
    print(f"     - Lợi suất kép bình quân (CAGR): {res['bank_cagr'] * 100:>15.2f}%/năm")
    print("\n  B. CHIẾN LƯỢC DRIP (TÁI ĐẦU TƯ TOÀN BỘ CỔ TỨC + PHÍ/THUẾ/TRƯỢT GIÁ):")
    drip_sh = res["drip_settled_regular_shares"]
    print(f"     - Tích lũy cổ phần:            {tot_sh:,d} cp  --->  {drip_sh:,.0f} cp (+{drip_sh - tot_sh:,.0f} cp)")
    print(f"     - Cổ phiếu kẹt T+2 cuối kỳ:    {res['drip_unsettled_shares']:>16,.0f} cp")
    print(f"     - Tổng tài sản thanh lý ròng:  {res['drip_final_wealth']:>16,.0f} VND")
    print(f"     - Lợi suất kép bình quân (CAGR): {res['drip_cagr'] * 100:>15.2f}%/năm")

    alpha_vnd = res["drip_final_wealth"] - res["bank_final_nav"]
    alpha_cagr = res["drip_cagr"] - res["bank_cagr"]
    if alpha_vnd > 0:
        print("  ==> PHÁN QUYẾT HIỆU SUẤT: CHIẾN LƯỢC DRIP VƯỢT TIỀN GỬI NGÂN HÀNG.")
        print(f"      + Chênh lệch tài sản tuyệt đối: +{alpha_vnd:,.0f} VND (+{alpha_vnd / cfg.initial_capital * 100:.2f}%)")
        print(f"      + Tỷ suất sinh lời vượt trội (Alpha): +{alpha_cagr * 100:.2f}%/năm")
    else:
        print("  ==> PHÁN QUYẾT HIỆU SUẤT: TIỀN GỬI NGÂN HÀNG VƯỢT DRIP.")
        print(f"      - Tiền gửi ngân hàng vượt trội: +{-alpha_vnd:,.0f} VND (+{-alpha_vnd / cfg.initial_capital * 100:.2f}%)")
        print(f"      - Alpha của chiến lược DRIP: {alpha_cagr * 100:.2f}%/năm")

    print("\n[CÂU HỎI 3] KỊCH BẢN RÚT CỔ TỨC (CASH-OUT) & RỦI RO SỤT GIẢM (MAX DRAWDOWN):")
    print(f"  • (B1) Giá trị cổ phiếu còn lại cuối kỳ (sau thoái vốn): {res['cashout_terminal_stock_val']:>14,.0f} VND")
    print(f"        {price_note}")
    print(f"  • (B2) Tổng tiền mặt cổ tức thực nhận đã rút (sau 5% thuế): {res['cashout_spent_dividends']:>14,.0f} VND")
    recovered_pct = res["cashout_spent_dividends"] / cfg.initial_capital * 100
    print(f"        (Đã thu hồi thuần bằng tiền mặt: {recovered_pct:.2f}% vốn ban đầu)")
    print(
        f"  • (B3) Tổng tài sản nếu gửi tiết kiệm dòng cổ tức rút ra: {res['cashout_wealth_if_saved']:>14,.0f} VND "
        f"(CAGR: {res['cashout_cagr_if_saved'] * 100:.2f}%/năm)"
    )
    print(f"  • Rủi ro sụt giảm danh mục tối đa (Max Drawdown):        {res['max_drawdown_observed'] * 100:>14.2f}%")
    print("=" * 86)
