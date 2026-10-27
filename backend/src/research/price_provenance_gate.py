"""
PRICE PROVENANCE GATE (V1.0) — CỔNG PHÁT HIỆN CHUỖI GIÁ ĐÃ ĐIỀU CHỈNH
PTCK_VNSTOCK Quantitative Research Suite

Status: ACTIVE (fail-closed gate — mọi runner audit cổ tức PHẢI đi qua)

[WHY] Báo cáo thực nghiệm FOC (Pha 1, commit 15e09c8) đã bị chứng minh chạy
trên chuỗi giá `kbs` ĐÃ back-adjust server-side, vi phạm Invariant #1
FAIL_CLOSED_ON_ADJUSTED_PRICE. Khi giá lịch sử đã loại cổ tức mà engine lại
cộng thêm cổ tức tiền mặt ⇒ cổ tức bị ĐẾM HAI LẦN, giá vào lệnh sai, lát cắt
ADV20 sai (volume cũng bị scalẻ theo). Cổng này chặn tái phát bằng BẰNG CHỨNG
VẬT LÝ không thể giả mạo của giá chứng khoán Việt Nam.

[ROOT CAUSE] API IIS của KB Securities (`kbs`) và gap-chart của Vietcap (`vci`)
đều trả lịch sử giá theo kiểu back-adjust multiplicative, neo về thời điểm
truy vấn: P_adj(t) = P_raw(t) × Π(1 − D_i / P_raw,i) với i chạy trên các ngày
GDKHQ SAU t. Hệ quả quan sát được:
  1. Bar trước ngày GDKHQ cuối cùng KHÔNG còn nằm trên lưới tick 100 VND
     (HOSE/UPCoM bắt buộc bội số 100) ⇒ giá trị phân số kiểu 27542.1496.
  2. Volume cũng bị scalẻ (turnover-preserving) ⇒ 299.320 cp thay vì số tròn.
  3. Bar sau ngày GDKHQ cuối cùng là giá thô ⇒ tròn bội 100 tuyệt đối.
  4. Vintage trộn lẫn: cùng một ngày trong DB và API khác ngày cho TỶ LỆ CỐ
     ĐỊNH (FOC 09/04/2021: DB 108.030 vs API 93.526 ⇒ hệ số 1,155×) vì lịch
     sử cũ không được pull lại sau khi có cổ tức mới.

[INVARIANT] assert_raw_price_series() phải được gọi TRƯỚC khi nạp bars vào
DividendAuditEngineV2. Mọi chuỗi không vượt cổng ⇒ AdjustedPriceDetectedError
(fail-closed). Cấm thay ngưỡng để "vượt" cổng: ngưỡng mặc định 1% là dung sai
mất mát làm tròn, KHÔNG phải công tắc tắt.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Lưới tick tối thiểu của HOSE/UPCoM (VND). Giá thô LUÔN là bội số của 100.
TICK_SIZE_VND = 100.0

# Dung sai mặc định: ≤1% bar lệch lưới ⇒ có thể là làm tròn/đàm phán lẻ còn
# sót. Trên 1% ⇒ chuỗi điều chỉnh (fail-closed).
DEFAULT_MAX_OFF_TICK_RATIO = 0.01

# Dung sai kiểm tra khoảng rơi ngày GDKHQ (tỷ lệ tuyệt đối).
DEFAULT_GAP_TOLERANCE = 0.02

# Vị từ chuẩn — chỉ đọc bar hợp lệ, KHÔNG đụng is_stale (panel lịch sử).
VALID_ROW_SQL = (
    "(close IS NOT NULL AND close > 0 "
    "AND low IS NOT NULL AND low <= close "
    "AND high IS NOT NULL AND close <= high "
    "AND volume IS NOT NULL AND volume >= 0)"
)


class AdjustedPriceDetectedError(RuntimeError):
    """Chuỗi giá bị nghi là ĐÃ ĐIỀU CHỈNH — chặn trước khi audit (fail-closed)."""


@dataclass(frozen=True)
class TickComplianceReport:
    """Kết quả kiểm tra lưới tick của một chuỗi giá."""

    symbol: str
    n_bars: int
    n_off_tick: int
    off_tick_ratio: float
    is_tick_clean: bool
    first_date: object | None = None  # date | None
    last_date: object | None = None


@dataclass(frozen=True)
class ExDateGapVerdict:
    """Phán quyết đối chiếu khoảng rơi giá ngày GDKHQ với cổ tức công bố."""

    expected_drop_ratio: float
    observed_drop_ratio: float
    abs_error: float
    is_consistent: bool


def tick_compliance(
    symbol: str,
    closes: list[float] | None,
    tick_size: float = TICK_SIZE_VND,
    max_off_tick_ratio: float = DEFAULT_MAX_OFF_TICK_RATIO,
    first_date=None,
    last_date=None,
) -> TickComplianceReport:
    """Đo tỷ lệ đóng cửa KHÔNG phải bội số của lưới tick.

    [INVARIANT] Chuỗi rỗng/None ⇒ ValueError (fail-closed: không có bằng chứng
    thì không qua cổng, tuyệt đối không trả "sạch" cho dữ liệu thiếu).
    """
    if not closes:
        raise ValueError(f"FAIL_CLOSED: chuỗi giá của {symbol} rỗng — không đủ bằng chứng để phán quyết.")

    n_off = sum(1 for c in closes if abs((c / tick_size) - round(c / tick_size)) > 1e-6)
    ratio = n_off / len(closes)
    return TickComplianceReport(
        symbol=symbol,
        n_bars=len(closes),
        n_off_tick=n_off,
        off_tick_ratio=ratio,
        is_tick_clean=ratio <= max_off_tick_ratio,
        first_date=first_date,
        last_date=last_date,
    )


def assert_raw_price_series(report: TickComplianceReport) -> TickComplianceReport:
    """Chặn đứng chuỗi giá đã điều chỉnh. Ném AdjustedPriceDetectedError nếu vi phạm."""
    if not report.is_tick_clean:
        raise AdjustedPriceDetectedError(
            f"ADJUSTED_PRICE_DETECTED: {report.symbol} có {report.n_off_tick}/{report.n_bars} bar "
            f"({report.off_tick_ratio * 100:.1f}%) đóng cửa KHÔNG phải bội số 100 VND. "
            "Chuỗi này nhiều khả năng đã back-adjust server-side (cổ tức đã bị loại khỏi giá). "
            "CẤP BẮT BUỘC: lấy lại giá thô hoặc dựng lại từ corporate actions có provenance "
            "trước khi chạy bất kỳ audit cổ tức nào (Invariant #1)."
        )
    return report


def exdate_gap_check(
    prev_close_raw: float,
    ex_date_close_raw: float,
    cash_per_share: float,
    tolerance: float = DEFAULT_GAP_TOLERANCE,
) -> ExDateGapVerdict:
    """Đối chiếu khoảng rơi giá qua ngày GDKHQ với cổ tức tiền mặt công bố.

    [WHY] Bằng chứng độc lập thứ hai với lưới tick: trên giá THÔ, cổ phiếu rơi
    approximately đúng tỷ lệ D/P ngay khi hết quyền (bỏ qua biến động thị trường
    trong biên độ tolerance). Giá đã điều chỉnh thì khoảng rơi này KHÔNG còn.
    [INVARIANT] prev_close_raw <= 0 hoặc cash_per_share < 0 ⇒ ValueError.
    """
    if prev_close_raw <= 0:
        raise ValueError("FAIL_CLOSED: giá đóng cửa trước GDKHQ phải > 0.")
    if cash_per_share < 0:
        raise ValueError("FAIL_CLOSED: cổ tức tiền mặt không được âm.")

    expected = cash_per_share / prev_close_raw
    observed = 1.0 - (ex_date_close_raw / prev_close_raw)
    err = abs(observed - expected)
    return ExDateGapVerdict(
        expected_drop_ratio=expected,
        observed_drop_ratio=observed,
        abs_error=err,
        is_consistent=err <= tolerance,
    )


def scan_symbol(
    db_path: Path | str,
    symbol: str,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Quét compliance theo NĂM cho một symbol trong screener_cache.db.

    Trả về {"overall": TickComplianceReport, "by_year": {year: TickComplianceReport},
            "first_date": date, "last_date": date}. Không ghi — chỉ đọc (audit).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        query = f"SELECT date, close FROM daily_ohlcv WHERE symbol = ? AND {VALID_ROW_SQL} "
        params: list[object] = [symbol]
        if start:
            query += "AND date >= ? "
            params.append(start)
        if end:
            query += "AND date <= ? "
            params.append(end)
        query += "ORDER BY date ASC"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    closes = [float(r[1]) for r in rows]
    dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]
    overall = tick_compliance(
        symbol,
        closes,
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
    )

    by_year: dict[int, TickComplianceReport] = {}
    year_buckets: dict[int, list[float]] = {}
    for d, c in zip(dates, closes):
        year_buckets.setdefault(d.year, []).append(c)
    for y in sorted(year_buckets):
        by_year[y] = tick_compliance(f"{symbol}:{y}", year_buckets[y])

    return {
        "overall": overall,
        "by_year": by_year,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m backend.src.research.price_provenance_gate --symbols VEA,FOC"""
    args = list(sys.argv[1:] if argv is None else argv)
    symbols: list[str] = ["VEA", "FOC"]
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "screener_cache.db"

    i = 0
    while i < len(args):
        if args[i] == "--symbols" and i + 1 < len(args):
            symbols = [s.strip().upper() for s in args[i + 1].split(",") if s.strip()]
            i += 2
        elif args[i] == "--db" and i + 1 < len(args):
            db_path = Path(args[i + 1])
            i += 2
        else:
            print(f"Tham số không hợp lệ: {args[i]}")
            return 2

    if not db_path.exists():
        print(f"FAIL_CLOSED: không tìm thấy CSDL {db_path}")
        return 2

    print("=" * 80)
    print("PRICE PROVENANCE GATE — KIỂM TRA LƯỚI TICK 100 VND (NGUỒN: screener_cache.db)")
    print(f"CSDL: {db_path}")
    print("=" * 80)

    exit_code = 0
    for sym in symbols:
        rep = scan_symbol(db_path, sym)
        ov = rep["overall"]
        print(f"\n[{sym}] {rep['first_date']} → {rep['last_date']} | {ov.n_bars} phiên sạch (VALID_ROW_SQL)")
        for y, yr in rep["by_year"].items():
            status = "RAW?" if yr.is_tick_clean else "ADJUSTED(!)"
            print(f"  {y}: {yr.n_bars:3d} bar | lệch lưới: {yr.n_off_tick:3d} ({yr.off_tick_ratio * 100:5.1f}%) | {status}")
        if ov.is_tick_clean:
            print(f"  ==> PHÁN QUYẾT: PASS — chuỗi {sym} nằm trên lưới tick (không phát hiện điều chỉnh).")
        else:
            print(
                f"  ==> PHÁN QUYẾT: FAIL — chuỗi {sym} NGHI LÀ ĐÃ ĐIỀU CHỈNH "
                f"({ov.off_tick_ratio * 100:.1f}% bar lệch lưới). KHÔNG được dùng cho audit cổ tức."
            )
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
