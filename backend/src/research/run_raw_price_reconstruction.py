"""
RAW PRICE RECONSTRUCTION — RUNNER CALIBRATION FOC/VEA/SMB (PHA 2B)
PTCK_VNSTOCK Quantitative Research Suite

[WHY] Chạy `raw_price_reconstruction.py` trên dữ liệu KBS thật: phục nguyên
giá thô rồi đối chiếu gap identity qua BIÊN ĐỘ PHIÊN GDKHQ (`[low_ex, high_ex]`)
cho FOC (6 sự kiện), VEA (7 sự kiện) và SMB (13 sự kiện, lưới tick 50 VND).
Đây là bằng chứng calibration trước khi cổng `price_provenance_gate
--reconstructed` phán quyết chính thức.

[ROOT CAUSE] Pha 1 (commit 15e09c8) chạy trên adjusted closes →
double-counting. Runner này chứng minh bằng chứng chứng thực/trảm trên dữ liệu
thật; danh sách event lấy từ `cash_event_registry` (single source of truth,
provenance VSDC) chứ không khai báo trùng lặp ở đây.

[INVARIANT] NON-DECISIONAL — mọi số liệu xuất ra là calibration findings,
không đưa vào checkpoint quyết định (Nghị quyết 04, Mục 9). Exit code:
0 = runner hoàn tất (report được in); certification do module/gate quyết định.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.research.cash_event_registry import CASH_EVENTS, TICK_SIZES

# [WHY] Nạp vintage qua đúng loader của cổng (một nguồn kiểm chứng duy nhất:
# cấm trộn vintage DB VCI 1,15504× vào chuỗi KBS 4dp, và mọi bar hỏng cấu trúc
# phải bị chặn ở cùng một chỗ).
from src.research.price_provenance_gate import load_vintage_ohlcv
from src.research.raw_price_reconstruction import (
    FactorSolveError,
    _segment_bad_count,
    _segment_candidates,
    reconstruct_raw_prices,
)

DATA_DIR = BACKEND / "data"


def _plateau_breakpoints(
    dates: list[date],
    closes: list[float],
    window: int = 30,
) -> list[date]:
    """Phát hiện breakpoints mức RUNNER (không thuộc core module).

    Quét window trượt, mỗi window solve candidate set; breakpoint = start
    index của window thuần đầu tiên mang plateau factor mới. Khác với core
    (fail-closed, không đoán): runner PHẢI có khả năng survey — nhưng mọi
    kết quả chỉ là calibration finding, không chứng nhận.
    """
    grid_tol = 0.1
    allowed = max(1, int(0.05 * window))
    estimates: list[tuple[int, float]] = []
    for t in range(0, len(closes) - window + 1):
        seg = closes[t : t + window]
        try:
            cands = _segment_candidates(seg, grid_tol)
        except FactorSolveError:
            continue
        good = [c for c in cands if _segment_bad_count(seg, c, grid_tol) <= allowed]
        if good:
            estimates.append((t, max(good)))
    # plateau: gộp estimates liên tiếp chênh < 1e-4 (trong cùng segment
    # neighbor-k family tách ra bằng ngưỡng này trên window 30 bars thật)
    bps: list[int] = []
    plateau_f: float | None = None
    for t, f in estimates:
        if plateau_f is None:
            plateau_f = f
        elif abs(f - plateau_f) / plateau_f > 1e-4:
            if not bps or t - bps[-1] > window:
                bps.append(t)
            plateau_f = f
    return [dates[b] for b in bps]


def run_symbol(sym: str, title: str) -> None:
    """Phục nguyên 1 symbol trên vintage KBS + in bằng chứng calibration."""
    print()
    print("=" * 72)
    print(f"{sym} — {title}")
    print("=" * 72)
    events = CASH_EVENTS[sym]
    tick = TICK_SIZES[sym]
    try:
        dates, closes, lows, highs = load_vintage_ohlcv(sym, DATA_DIR)
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAIL_CLOSED (vintage): {exc}")
        return
    try:
        report = reconstruct_raw_prices(sym, dates, closes, events=events, tick=tick, lows=lows, highs=highs)
    except FactorSolveError as exc:
        print(f"FACTOR-SOLVE-ERROR (fail-closed): {exc}")
        return

    print(f"bars={report.n_bars} off_grid={report.n_off_grid} certified={report.is_certified} lưới={tick:g} VND")
    print("\nsegments:")
    for s, e, f in report.segments:
        print(f"  {s} .. {e}: f={f:.8f}")
    print("\ngap identity (Δ_market = drop − cash, phải nằm trong biên phiên ex):")
    for ev, delta in zip(events, report.gap_deltas):
        print(f"  {ev.ex_date}: cash={ev.cash:>10,.1f} | Δ_market={delta:>+9,.1f}")
    print("\nanomalies:")
    for a in report.anomalies:
        print(f"  ! {a}")
    if not report.anomalies:
        print("  (không có)")


def run_foc() -> None:
    run_symbol("FOC", "RECONSTRUCTION vs 6 SỰ KIỆN VSDC (PHA 1)")


def run_vea() -> None:
    run_symbol("VEA", "RECONSTRUCTION vs 7 SỰ KIỆN VSDC (PHA 2)")


def run_smb() -> None:
    run_symbol("SMB", "RECONSTRUCTION vs 13 SỰ KIỆN VSDC (PHA 3, LƯỚI 50 VND)")


def run_survey() -> None:
    """Survey blind breakpoints mức runner (NON-DECISIONAL, không chứng nhận).

    [WHY] In TÓM TẮT chứ không in từng mốc: đo thực tế 40 mốc phát hiện / 7 mốc
    khai báo ⇒ nhiễu >80% (plateau trượt window không phân biệt được biến động
    giá với breakpoint). In từng dòng sẽ khiến người đọc tưởng có 33 "sự kiện
    lạ" cần xử lý — sai sự thật. Con số duy nhất đáng dùng là: detect có chứa
    đủ 7 mốc khai báo hay không.
    """
    print()
    print("=" * 72)
    print("SURVEY BREAKPOINTS (blind, runner-level) — VEA vs 7 event khai báo")
    print("=" * 72)
    dates, closes, _lows, _highs = load_vintage_ohlcv("VEA", DATA_DIR)
    detected = set(_plateau_breakpoints(dates, closes))
    declared = {e.ex_date for e in CASH_EVENTS["VEA"]}
    hits = sorted(declared & detected)
    print(f"detected={len(detected)} declared={len(declared)} trùng={len(hits)}/{len(declared)}")
    print(f"khai báo KHÔNG thấy trong survey: {[str(d) for d in sorted(declared - detected)]}")
    print("Kết luận sử dụng được: survey chỉ dùng làm DANH SÁCH ỨNG VIÊN;")
    print("nhiễu plateau >80% ⇒ cấm dùng để kết luận hay sửa registry.")


if __name__ == "__main__":
    run_foc()
    run_vea()
    run_smb()
    run_survey()
    print("\nNON-DECISIONAL — calibration findings only (NQ-04, Mục 9).")
    sys.exit(0)
