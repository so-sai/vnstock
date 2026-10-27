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
        TickComplianceReport,
        assert_raw_price_series,
        exdate_gap_check,
        scan_symbol,
        tick_compliance,
    )
except ImportError:
    from backend.src.research.price_provenance_gate import (
        AdjustedPriceDetectedError,
        ExDateGapVerdict,
        TickComplianceReport,
        assert_raw_price_series,
        exdate_gap_check,
        scan_symbol,
        tick_compliance,
    )


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
