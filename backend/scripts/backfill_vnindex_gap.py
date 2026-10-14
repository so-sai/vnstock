"""backfill_vnindex_gap.py — vá 41 phiên VNINDEX bị thiếu (2026-04 → 2026-09).

WHY: `daily_ohlcv` thiếu bar VNINDEX ở 41/190 phiên của cửa sổ 2026-01-01..2026-10-09
(2026-05 chỉ có 2/20 phiên). Hệ quả kép:
  1. `_vnindex_drop_3d()` trong backtest/trend_loop.py FAIL-OPEN: thiếu bar → return 0.0
     ("không giảm") ⇒ phanh khẩn cấp không thể kích hoạt ở đúng các phiên thiếu.
  2. Mọi module governor/benchmark dùng VNINDEX đều chạy trên chuỗi thủng.
Chỉ 1 mã × N phiên ⇒ vài chục request, không đụng quota cổ phiếu.

Dùng `update_vnindex(target)` (hàm chuyên dụng của hệ thống) cho từng ngày thiếu.
Ghi daily_ohlcv — đã được duyệt (GO ưu tiên 1). Fail-fast: in rõ ngày nào còn thiếu.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"D:/04. Software/opensource_contrib/PTCK_VNSTOCK")
_EXTRA_PATHS = (
    PROJECT_ROOT,
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "backend" / "src",
    PROJECT_ROOT / "backend" / "libs" / "vnstock",
)
for _p in _EXTRA_PATHS:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
W0, W1 = "2026-04-01", "2026-09-30"


def missing_vnindex_dates() -> tuple[list[str], list[str]]:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        expected = [
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT date FROM daily_ohlcv WHERE date BETWEEN ? AND ? ORDER BY date", (W0, W1)
            )
        ]
        have = {
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN ? AND ?",
                (W0, W1),
            )
        }
    finally:
        con.close()
    return [d for d in expected if d not in have], expected


def main() -> int:
    from src.daily_updater import update_vnindex

    miss, expected = missing_vnindex_dates()
    print(f"=== VNINDEX gap {W0}..{W1}: {len(miss)}/{len(expected)} phiên thiếu ===")
    if not miss:
        print("  (không thiếu — không làm gì)")
        return 0
    print(f"  thiếu: {miss[:6]}{' ...' if len(miss) > 6 else ''}")
    ok = fail = 0
    for d in miss:
        try:
            rows = update_vnindex(d)
            ok += 1 if rows else 0
            fail += 0 if rows else 1
            print(f"  {d}: rows={rows}")
        except Exception as e:  # noqa: BLE001 - 1 ngày lỗi không dừng cả lượt vá
            fail += 1
            print(f"  {d}: EXC {type(e).__name__}: {e}")
    print(f"\n=== KẾT QUẢ: ok={ok} fail={fail} ===")

    still, _ = missing_vnindex_dates()
    print(f"  còn thiếu sau khi vá: {len(still)} phiên {still[:8]}")
    return 0 if not still else 1


if __name__ == "__main__":
    raise SystemExit(main())
