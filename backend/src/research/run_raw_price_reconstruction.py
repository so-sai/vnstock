"""
RAW PRICE RECONSTRUCTION — RUNNER CALIBRATION FOC/VEA (PHA 2B)
PTCK_VNSTOCK Quantitative Research Suite

[WHY] Chạy `raw_price_reconstruction.py` trên dữ liệu KBS thật: FOC với
6 sự kiện Phase-1 (kiểm gap identity — biên bản P2 E8 dự báo mâu thuẫn)
và VEA blind scan (plateau detection mức runner, KHÔNG thuộc core —
core fail-closed với undisclosed breakpoints).

[ROOT CAUSE] Pha 1 (commit 15e09c8) chạy trên adjusted closes →
double-counting. Runner này chứng minh bằng chứng chứng thực/trảm
trên dữ liệu thật TRƯỚC khi bất kỳ re-run engine nào được phép.

[INVARIANT] NON-DECISIONAL — mọi số liệu xuất ra là calibration findings,
không đưa vào checkpoint quyết định (Nghị quyết 04, Mục 9). Exit code:
0 = runner hoàn tất (report được in); certification do module quyết định.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.research.raw_price_reconstruction import (
    CashEvent,
    FactorSolveError,
    _segment_bad_count,
    _segment_candidates,
    reconstruct_raw_prices,
)

DATA_DIR = BACKEND / "data"
KBS_DIR = DATA_DIR if (DATA_DIR / "kbs_foc_2021_2026.json").exists() else Path(r"C:\Users\Admin\AppData\Local\Temp\opencode")

FOC_EVENTS = (  # ex_date, cash_div — VSDC verified provenance
    CashEvent(date(2021, 4, 13), 20_000.0),
    CashEvent(date(2022, 6, 10), 8_000.0),
    CashEvent(date(2023, 6, 8), 5_000.0),
    CashEvent(date(2024, 7, 18), 2_000.0),
    CashEvent(date(2025, 5, 23), 10_000.0),
    CashEvent(date(2026, 8, 10), 10_000.0),
)

VEA_EVENTS = (  # ex_date, cash_div — VSDC verified provenance (Pha 2)
    CashEvent(date(2021, 1, 4), 5_252.9),
    CashEvent(date(2021, 7, 15), 4_990.0),
    CashEvent(date(2021, 10, 12), 462.7),
    CashEvent(date(2022, 10, 28), 4_493.7),
    CashEvent(date(2023, 11, 17), 4_186.9),
    CashEvent(date(2024, 11, 19), 5_035.18),
    CashEvent(date(2025, 11, 18), 4_658.08),
)


def load_kbs(sym: str) -> tuple[list[date], list[float]]:
    rows = json.loads((KBS_DIR / f"kbs_{sym.lower()}_2021_2026.json").read_text())
    dates = [date.fromisoformat(r["t"][:10]) for r in rows]
    closes = [float(r["c"]) for r in rows]
    return dates, closes


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


def run_foc() -> None:
    print("=" * 72)
    print("FOC — RECONSTRUCTION vs 6 SỰ KIỆN PHASE-1")
    print("=" * 72)
    dates, closes = load_kbs("FOC")
    try:
        report = reconstruct_raw_prices("FOC", dates, closes, events=FOC_EVENTS)
    except FactorSolveError as e:
        print(f"FACTOR-SOLVE-ERROR (fail-closed): {e}")
        return
    print(f"bars={report.n_bars} off_grid={report.n_off_grid} certified={report.is_certified}")
    print("\nsegments:")
    for s, e, f in report.segments:
        print(f"  {s} .. {e}: f={f:.8f}")
    print("\nanomalies:")
    for a in report.anomalies:
        print(f"  ! {a}")


def run_vea() -> None:
    print()
    print("=" * 72)
    print("VEA — RECONSTRUCTION vs 7 SỰ KIỆN VSDC (PHA 2)")
    print("=" * 72)
    dates, closes = load_kbs("VEA")
    try:
        report = reconstruct_raw_prices("VEA", dates, closes, events=VEA_EVENTS)
    except FactorSolveError as e:
        print(f"FACTOR-SOLVE-ERROR (fail-closed): {e}")
        return
    print(f"bars={report.n_bars} off_grid={report.n_off_grid} certified={report.is_certified}")
    print("\nsegments:")
    for s, e, f in report.segments:
        print(f"  {s} .. {e}: f={f:.8f}")
    print("\nanomalies:")
    for a in report.anomalies:
        print(f"  ! {a}")


if __name__ == "__main__":
    run_foc()
    run_vea()
    print("\nNON-DECISIONAL — calibration findings only (NQ-04, Mục 9).")
    sys.exit(0)
