"""
PRICE PROVENANCE GATE — UNIT TESTS (TDD RED/GREEN)
PTCK_VNSTOCK Quantitative Research Suite

[WHY] Báo cáo thực nghiệm FOC (Pha 1, commit 15e09c8) đã chạy trên chuỗi giá
`kbs` đã bị chứng minh là back-adjusted (giá + volume bị điều chỉnh server-side), vi phạm
Invariant #1 FAIL_CLOSED_ON_ADJUSTED_PRICE của engine cổ tức. Cần một cổng
kiểm tra độc lập, tái sử dụng được, phát hiện chuỗi giá đã điều chỉnh TRƯỚC
khi bất kỳ runner audit nào được phép chạy.

[ROOT CAUSE] Nguồn `kbs` (KB Securities IIS API) và `vci` (Vietcap gap-chart)
đều trả lịch sử giá ĐÃ điều chỉnh (multiplicative back-adjust theo cổ tức),
khóa neo về thời điểm truy vấn; các bar sau ngày GDKHQ cuối cùng giữ nguyên
giá thô (tròn bội 100 VND). Hệ quả: giá trị tuyệt đối của lịch sử không còn
nằm trên lưới tick 100 VND của HOSE/UPCoM, và khoảng rơi giá ngày GDKHQ không
còn tương ứng số cổ tức công bố.

[INVARIANT] Mọi chuỗi giá đi vào `DividendAuditEngineV2` PHẢI vượt cổng:
(1) tỷ lệ đóng cửa lệch lưới tick ≤ ngưỡng; (2) khoảng rơi ngày GDKHQ khớp
cổ tức. Vi phạm => AdjustedPriceDetectedError (fail-closed, không im lặng).
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from src.research.price_provenance_gate import (
        AdjustedPriceDetectedError,
        ExDateGapVerdict,
        ReconstructedVerdict,
        TickComplianceReport,
        assert_raw_price_series,
        exdate_gap_check,
        load_vintage_ohlcv,
        scan_symbol,
        tick_compliance,
        verify_reconstructed_prices,
    )
    from src.research.price_provenance_gate import main as gate_main
    from src.research.cash_event_registry import CASH_EVENTS, TICK_SIZES
    from src.research.raw_price_reconstruction import CashEvent
except ImportError:
    from backend.src.research.price_provenance_gate import (
        AdjustedPriceDetectedError,
        ExDateGapVerdict,
        ReconstructedVerdict,
        TickComplianceReport,
        assert_raw_price_series,
        exdate_gap_check,
        load_vintage_ohlcv,
        scan_symbol,
        tick_compliance,
        verify_reconstructed_prices,
    )
    from backend.src.research.price_provenance_gate import main as gate_main
    from backend.src.research.cash_event_registry import CASH_EVENTS, TICK_SIZES
    from backend.src.research.raw_price_reconstruction import CashEvent


# ── [WHY] Giá thô UPCoM/HOSE LUÔN nằm trên lưới 100 VND. Chuỗi tổng hợp này
#    mô phỏng đúng hợp đồng của bar thô để chứng minh cổng KHÔNG báo false
#    positive trên dữ liệu sạch (ca chống vá quá tay phía "dữ liệu dơ").
def test_raw_series_all_on_tick_passes():
    closes = [70_000.0, 70_500.0, 71_100.0, 69_900.0, 72_000.0]
    report = tick_compliance("SYNTH_RAW", closes)
    assert isinstance(report, TickComplianceReport)
    assert report.n_bars == 5
    assert report.n_off_tick == 0
    assert report.off_tick_ratio == 0.0
    assert report.is_tick_clean is True
    # Không raise khi assert
    assert_raw_price_series(report)


# ── [WHY] Ca lỗi thật đã xảy ra: KBS API trả VEA 2021-01-04 close=27542.1496
#    (phân số). Cổng PHẢI chặn chuỗi này và ném lỗi rõ ràng, không cảnh báo mờ.
def test_adjusted_series_off_tick_fails_closed():
    closes = [27_542.1496, 26_544.6472, 26_600.064, 25_935.0624, 25_602.5616]
    report = tick_compliance("SYNTH_ADJ", closes)
    assert report.is_tick_clean is False
    assert report.n_off_tick >= 4
    with pytest.raises(AdjustedPriceDetectedError):
        assert_raw_price_series(report)


# ── [WHY] Fail-closed tuyệt đối: chuỗi rỗng/không xác định KHÔNG được phép
#    "qua cổng" vì thiếu bằng chứng (Missing > Fake).
def test_empty_series_fail_closed():
    with pytest.raises(ValueError):
        tick_compliance("EMPTY", [])
    with pytest.raises(ValueError):
        tick_compliance("NONE", None)


# ── [WHY] Khoảng rơi ngày GDKHQ là bằng chứng độc lập thứ hai. Giá thô FOC
#    09/04/2021=108.000, 12/04/2021 (ex cash 20.000đ) phải rơi ~18,5%.
def test_exdate_gap_raw_drop_matches_dividend():
    verdict = exdate_gap_check(prev_close_raw=108_000.0, ex_date_close_raw=88_000.0, cash_per_share=20_000.0)
    assert isinstance(verdict, ExDateGapVerdict)
    assert verdict.is_consistent is True
    assert verdict.abs_error < 0.005


# ── [WHY] Ca chống vá: dữ liệu THẬT trong DB (điều chỉnh vintage cũ) cho
#    09/04/2021=108.030 -> 12/04/2021=103.130, chỉ rơi 4,5% trong khi cổ tức
#    20,000đ yêu cầu ~18,5%. Cổng PHẢI phát hiện mâu thuẫn này.
def test_exdate_gap_adjusted_mismatch_detected():
    verdict = exdate_gap_check(prev_close_raw=108_030.0, ex_date_close_raw=103_130.0, cash_per_share=20_000.0)
    assert verdict.is_consistent is False
    assert verdict.observed_drop_ratio < verdict.expected_drop_ratio / 2


# ── [WHY] Phép thử đầu vào: giá trị phi lý (<=0) phải raise, không im lặng
#    cho qua với tỷ số NaN/inf.
def test_exdate_gap_invalid_inputs_fail_closed():
    with pytest.raises(ValueError):
        exdate_gap_check(prev_close_raw=0.0, ex_date_close_raw=88_000.0, cash_per_share=20_000.0)
    with pytest.raises(ValueError):
        exdate_gap_check(prev_close_raw=108_000.0, ex_date_close_raw=88_000.0, cash_per_share=-1.0)


# ── [WHY] Quét symbol trên CSDL thật (fixture sqlite tạm, KHÔNG đụng DB production):
#    mô phỏng đúng cấu trúc daily_ohlcv với 1 mã thô + 1 mã điều chỉnh theo
#    từng năm, khẳng định báo cáo theo năm phân tách được hai loại.
def test_scan_symbol_flags_adjusted_by_year(tmp_path):
    db = tmp_path / "fixture.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE daily_ohlcv (
            symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, adj_close REAL, volume REAL, source TEXT, is_stale INTEGER
        )
        """
    )
    rows = []
    # RAW: 2026 toàn bộ tròn bội 100 (adj_close = close, chuỗi không điều chỉnh)
    for d, c in [("2026-03-02", 38_800.0), ("2026-03-03", 38_900.0), ("2026-03-04", 39_000.0)]:
        rows.append(("RAWSY", d, c, c, c, c, c, 100_000.0, "kbs", 0))
    # ADJ: 2021 phân số (mô phỏng KBS server-side adjustment)
    for d, c in [("2021-03-02", 27_542.1496), ("2021-03-03", 26_544.6472), ("2021-03-04", 26_600.064)]:
        rows.append(("ADJSY", d, c, c, c, c, c, 299_320.0, "kbs", 0))
    conn.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    rep_raw = scan_symbol(db, "RAWSY")
    assert rep_raw["overall"].is_tick_clean is True

    rep_adj = scan_symbol(db, "ADJSY")
    assert rep_adj["overall"].is_tick_clean is False
    assert rep_adj["by_year"][2021].is_tick_clean is False
    assert isinstance(rep_adj["first_date"], date)


# ══ CHẾ ĐỘ --reconstructed (NQ-04, 28/10/2026) ══════════════════════════════
# [WHY] DB `daily_ohlcv` của FOC là vintage TRỘN (VCI adjusted 1.15504× + kbs):
# quét trực tiếp DB sẽ FAIL vĩnh viễn và không chứng minh được gì về chuỗi sẽ
# dùng để audit. Chế độ reconstructed phán quyết trên chuỗi ĐÃ phục nguyên từ
# MỘT vintage KBS duy nhất (full precision 4dp) — đúng thứ sẽ nạp vào engine.
FOC_VINTAGE_DIR = BACKEND / "data"
_needs_foc_vintage = pytest.mark.skipif(
    not (FOC_VINTAGE_DIR / "kbs_foc_2021_2026.json").exists(),
    reason="vintage KBS FOC không có trong workspace (chỉ có trên máy đã crawl)",
)


@_needs_foc_vintage
def test_reconstructed_foc_vintage_certifies_100pct_tick_grid():
    """[WHY] DoD NQ-04: 100% bars bội 100 VND + gap identity qua biên phiên
    GDKHQ, trên dữ liệu THẬT 2021–2026 (1452 bars, 6 cổ tức tiền mặt)."""
    dates, closes, lows, highs = load_vintage_ohlcv("FOC", FOC_VINTAGE_DIR)
    verdict = verify_reconstructed_prices(
        "FOC", dates, closes, lows, highs, CASH_EVENTS["FOC"]
    )
    assert verdict.is_certified is True, verdict.reasons
    assert verdict.n_off_tick == 0
    assert verdict.n_bars == len(dates) == len(closes)
    assert len(verdict.segments) == len(CASH_EVENTS["FOC"]) + 1
    assert len(verdict.gap_deltas) == len(CASH_EVENTS["FOC"])
    # Δ_market đo được: −1,200 … +2,200 VND (biến động phiên UPCoM), KHÔNG phải 0
    assert verdict.max_abs_delta is not None
    assert verdict.max_abs_delta < 2_500.0
    assert any(abs(d) > 100.0 for d in verdict.gap_deltas)


@_needs_foc_vintage
def test_reconstructed_rejects_wrong_cash_on_real_vintage():
    """[WHY] Ca chống vá quá tay: nới gate cho Δ_market KHÔNG được biến thành
    "cổ tức nào cũng qua". Khai 2023-06-08 = 1,000đ (thực 5,000đ) ⇒ tham chiếu
    rơi ra ngoài biên phiên ex ⇒ certified=False, nêu đúng ngày sai."""
    dates, closes, lows, highs = load_vintage_ohlcv("FOC", FOC_VINTAGE_DIR)
    wrong = tuple(
        CashEvent(ex_date=e.ex_date, cash=1_000.0) if e.ex_date == date(2023, 6, 8) else e
        for e in CASH_EVENTS["FOC"]
    )
    verdict = verify_reconstructed_prices("FOC", dates, closes, lows, highs, wrong)
    assert verdict.is_certified is False
    assert any("2023-06-08" in r for r in verdict.reasons)


@_needs_foc_vintage
def test_reconstructed_foc_all_events_use_strict_band():
    """[WHY] Chống trôi chuẩn: FOC tuyệt đối KHÔNG được hưởng tầng trần/sàn
    (không có event nào trong allow-list) — 6/6 phải là "in-range"."""
    dates, closes, lows, highs = load_vintage_ohlcv("FOC", FOC_VINTAGE_DIR)
    verdict = verify_reconstructed_prices(
        "FOC", dates, closes, lows, highs, CASH_EVENTS["FOC"]
    )
    assert verdict.gap_checks == ("in-range",) * len(CASH_EVENTS["FOC"])


def test_reconstructed_smb_certifies_with_exactly_one_declared_limit_event():
    """[WHY] NQ-07: SMB 2023-02-20 là phiên gap-up (2 bước giá) — được nghiệm
    thu bằng tầng trần/sàn ±7%, và CHỈ event đó (12 event còn lại phải in-range).
    Nếu con số này khác, phạm vi nới lỏng đã trôi."""
    smb_dir = BACKEND / "data"
    if not (smb_dir / "kbs_smb_2021_2026.json").exists():
        pytest.skip("vintage KBS SMB không có trong workspace")
    dates, closes, lows, highs = load_vintage_ohlcv("SMB", smb_dir)
    verdict = verify_reconstructed_prices(
        "SMB", dates, closes, lows, highs, CASH_EVENTS["SMB"], tick_size=TICK_SIZES["SMB"]
    )
    assert verdict.is_certified is True, verdict.reasons
    assert verdict.n_off_tick == 0
    assert verdict.gap_checks.count("limit-band") == 1
    assert verdict.gap_checks.count("in-range") == len(CASH_EVENTS["SMB"]) - 1
    idx = [e.ex_date for e in CASH_EVENTS["SMB"]].index(date(2023, 2, 20))
    assert verdict.gap_checks[idx] == "limit-band"
    assert abs(verdict.gap_deltas[idx] - (-350.0)) < 1.0


def test_gate_cli_reconstructed_smb_exit_zero():
    """[WHY] NQ-07 yêu cầu SMB về EXIT 0 — đo trên đường CLI thật."""
    if not (BACKEND / "data" / "kbs_smb_2021_2026.json").exists():
        pytest.skip("vintage KBS SMB không có trong workspace")
    assert gate_main(["--symbols", "SMB", "--reconstructed"]) == 0


def test_gate_cli_reconstructed_foc_exit_zero():
    """[WHY] DoD cuối cùng của Hội đồng: `price_provenance_gate.py` cho FOC
    phải đạt EXIT CODE 0 — đo trên đường CLI thật, không chỉ hàm nội bộ."""
    assert gate_main(["--symbols", "FOC", "--reconstructed"]) == 0


def test_gate_cli_reconstructed_unknown_symbol_fails_closed():
    """[WHY] Không có event provenance ⇒ KHÔNG được lặng lẽ bỏ qua symbol rồi
    trả exit 0 (Missing > Fake)."""
    assert gate_main(["--symbols", "KHONGCO", "--reconstructed"]) == 2


def test_gate_cli_reconstructed_missing_vintage_fails_closed(tmp_path):
    """[WHY] Thiếu file vintage ⇒ không có bằng chứng ⇒ exit 2, không phán
    quyết PASS dựa trên chuỗi rỗng."""
    assert gate_main(["--symbols", "FOC", "--reconstructed", "--bars-dir", str(tmp_path)]) == 2


def test_load_vintage_rejects_inverted_bar(tmp_path):
    """[WHY] low > high trong file vintage = dữ liệu hỏng; dùng làm biên phiên
    ex sẽ cho phán quyết vô nghĩa ⇒ chặn ngay khi nạp (fail-closed)."""
    bad = tmp_path / "kbs_foc_2021_2026.json"
    bad.write_text(
        '[{"t": "2021-04-12 07:00", "o": 100000, "h": 90000, "l": 100000, "c": 95000, "v": 10}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_vintage_ohlcv("FOC", tmp_path)


# ══ TẦNG 3 — KIỂM CHÉO VENDOR (V1.2, 29/10/2026) ════════════════════════════
# [WHY] PAT có 4/9 event là phiên gap nên phải nghiệm thu bằng tầng trần/sàn
# (yếu). Nếu tầng 3 cũng không chạy được cho những event đó, cổng sẽ PASS bằng
# đúng loại bằng chứng yếu nhất — phải chặn. Test dưới đây khoá chính sách đó.
def _synth_vendor_series(ex: date, n: int = 40, factor: float = 1.0, base: float = 50_000.0):
    """Chuỗi ngày giao dịch giả + hệ số điều chỉnh trước ex (1.0 = không điều chỉnh)."""
    from datetime import timedelta

    kbs, db = {}, {}
    d = ex - timedelta(days=n // 2)
    i = 0
    while len(db) < n:
        if d.weekday() < 5:
            price = base + 100.0 * i
            db[d] = price
            kbs[d] = price * (factor if d < ex else 1.0)
            i += 1
        d += timedelta(days=1)
    return kbs, db


def test_cross_vendor_conflict_blocks_certification():
    """[WHY] Vendor kia điều chỉnh 5.000 đ trong khi registry khai 1.000 đ ⇒
    phải CHẶN, kể cả khi band OHLC của tầng 2 đã PASS (mâu thuẫn provenance
    không được phép bị tầng yếu hơn che khuất)."""
    from src.research.price_provenance_gate import evaluate_cross_vendor

    ex = date(2024, 3, 15)
    pre = 50_000.0 + 100.0 * 19
    kbs, db = _synth_vendor_series(ex, factor=(pre - 5_000.0) / pre)
    checks, blocking, _ = evaluate_cross_vendor(
        "TST", [(ex, 1_000.0)], kbs, db, gap_checks=("in-range",)
    )
    assert checks[0].status == "conflict"
    assert blocking and "CONFLICT" in blocking[0]


def test_cross_vendor_missing_evidence_blocks_only_limit_band_events():
    """[WHY] Chính sách 2 mức: thiếu dữ liệu tầng 3 cho event `limit-band` ⇒
    CHẶN (bằng chứng yếu cần xác nhận độc lập); cho event `in-range` ⇒ chỉ ghi
    chú (band OHLC đã đủ mạnh). Nếu hai ca này cùng số phận thì hoặc cổng quá
    dễ, hoặc quá khắt khe — cả hai đều sai."""
    from src.research.price_provenance_gate import evaluate_cross_vendor

    ex = date(2030, 1, 10)  # ngoài mọi chuỗi ⇒ chắc chắn insufficient-data
    kbs, db = _synth_vendor_series(date(2024, 3, 15), n=20)
    _, blocking_limit, _ = evaluate_cross_vendor("TST", [(ex, 1_000.0)], kbs, db, ("limit-band",))
    _, blocking_band, notes_band = evaluate_cross_vendor("TST", [(ex, 1_000.0)], kbs, db, ("in-range",))
    assert blocking_limit and "THIẾU BẰNG CHỨNG" in blocking_limit[0]
    assert not blocking_band
    assert any("insufficient-data" in n or "KHÔNG kết luận" in n for n in notes_band)


@pytest.mark.parametrize("sym", ["PAT", "DSN"])
def test_gate_cli_reconstructed_phase4_5_exit_zero(sym):
    """[WHY] DoD Pha 4/5: cổng phải đạt EXIT 0 trên chuỗi certified của PAT
    (UPCoM, lưới 100, 4 event gap có tầng 3 xác nhận) và DSN (HOSE, lưới 50)."""
    if not (BACKEND / "data" / f"kbs_{sym.lower()}_20").parent.exists():
        pytest.skip("thiếu thư mục vintage")
    if not any((BACKEND / "data").glob(f"kbs_{sym.lower()}_*.json")):
        pytest.skip(f"vintage KBS {sym} không có trong workspace")
    assert gate_main(["--symbols", sym, "--reconstructed"]) == 0


def test_pat_limit_band_scope_is_exactly_four_events():
    """[WHY] Chống trôi allow-list: PAT được nghiệm thu trần/sàn đúng 4/9 event
    (2022-08-04, 2024-04-12, 2025-04-24, 2026-09-15). Danh sách dài hơn ⇒ ai đó
    đã nới tầng yếu; ngắn hơn ⇒ event gap bị trả về FAIL oan."""
    from src.research.cash_event_registry import LIMIT_BAND_EVENTS

    assert set(LIMIT_BAND_EVENTS["PAT"]) == {
        date(2022, 8, 4),
        date(2024, 4, 12),
        date(2025, 4, 24),
        date(2026, 9, 15),
    }
    assert set(LIMIT_BAND_EVENTS["DSN"]) == {date(2026, 5, 18)}
