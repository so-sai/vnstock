r"""Sửa các dòng "số 0 bịa ra" trong `capital_displacement_history` bằng SỐ ĐO THẬT từ store.

[WHY] `capital_displacement_engine` bản cũ ghi `vnindex_close = vnindex_chg = vnindex_vol_ratio = 0`
khi không lấy được VNINDEX (Ca 3, đã vá ở engine). Dòng đã ghi thì KHÔNG tự biến mất: dòng
2026-06-30 vẫn nằm trong DB với cả ba cột = 0.0, và số 0 đó không phân biệt được với một giá
trị đo được ⇒ mọi truy vết/so sánh sau này đều nhiễm.

[ROOT CAUSE] Không phải "dữ liệu thô sai" mà là "dữ liệu DẪN XUẤT sai được lưu vĩnh viễn":
nguồn thật (daily_ohlcv của VNINDEX) vẫn còn nguyên trong store ⇒ sửa được bằng cách tính lại
theo ĐÚNG định nghĩa của engine, thay vì để NULL (mất thông tin) hoặc để 0 (giữ thông tin giả).

[INVARIANT]
  1. CHỈ đụng vào dòng đang KHÔNG có số đo (`vnindex_close IS NULL OR = 0`); tuyệt đối không
     ghi đè một giá trị đã đo được ⇒ chạy lại nhiều lần là no-op (idempotent).
  2. Bar dùng để tính phải qua `is_stale = 0 AND VALID_ROW_SQL` (dòng cách ly/rác không được
     trở thành nguồn của một con số tham chiếu).
  3. Không có bar hợp lệ cho ngày đó ⇒ ghi NULL + khai báo, KHÔNG ghi 0.
  4. Mặc định DRY-RUN; chỉ ghi khi có `--apply`.

Chạy:
    python backend/scripts/repair_capital_displacement_vnindex.py            # xem trước
    python backend/scripts/repair_capital_displacement_vnindex.py --apply    # ghi
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.database.data_validity import VALID_ROW_SQL  # noqa: E402
from src.database.db_core import get_connection  # noqa: E402

BROKEN_PREDICATE = "(vnindex_close IS NULL OR vnindex_close = 0 OR vnindex_chg IS NULL OR vnindex_vol_ratio IS NULL)"
# [Ca 3c] `defensive_avg_chg` chưa từng có nguồn tính: git chỉ có 2 lần chạm chuỗi này (2e9140d
# thêm hard-code, cac5cd7 xoá) và cả 10/10 dòng trong bảng đều = 0.0 — dấu vân tay của hằng số,
# không phải số đo. Mọi giá trị 0.0 ở cột này là bịa.
DEFENSIVE_PREDICATE = "defensive_avg_chg = 0"
MA20_MIN_BARS = 5
VNINDEX = "VNINDEX"


def _bars(conn: sqlite3.Connection) -> list[tuple]:
    """Toàn bộ bar VNINDEX HỢP LỆ và TƯƠI, sắp theo ngày."""
    return conn.execute(
        f"SELECT date, close, volume FROM daily_ohlcv "
        f"WHERE symbol = ? AND is_stale = 0 AND {VALID_ROW_SQL} ORDER BY date",
        (VNINDEX,),
    ).fetchall()


def _measurements(bars: list[tuple]) -> dict:
    """{ngày: (close, chg, vol_ratio)} theo ĐÚNG định nghĩa của engine (Ca 3)."""
    out = {}
    for i, (date, close, volume) in enumerate(bars):
        chg = None
        if i > 0 and bars[i - 1][1]:
            chg = round((close - bars[i - 1][1]) / bars[i - 1][1] * 100, 2)
        vol_ratio = None
        if i >= 21:
            baseline = [b[2] for b in bars[i - 21 : i]]
            mean = sum(baseline) / len(baseline)
            if mean > 0:
                vol_ratio = round(volume / mean, 2)
        elif i >= MA20_MIN_BARS:
            baseline = [b[2] for b in bars[:i]]
            mean = sum(baseline) / len(baseline)
            if mean > 0:
                vol_ratio = round(volume / mean, 2)
        out[date] = (close, chg, vol_ratio)
    return out


def _repair_defensive(conn: sqlite3.Connection, apply: bool) -> None:
    """Đưa `defensive_avg_chg = 0` (hằng số bịa) về NULL. Idempotent, cùng luật với Ca 3."""
    rows = conn.execute(f"SELECT COUNT(*) FROM capital_displacement_history WHERE {DEFENSIVE_PREDICATE}").fetchone()[0]
    print(f"\nDòng defensive_avg_chg = 0 (số bịa, không có nguồn tính): {rows}")
    if rows == 0:
        print("Không có gì để sửa (idempotent).")
        return
    if not apply:
        return
    with conn:
        conn.execute(f"UPDATE capital_displacement_history SET defensive_avg_chg = NULL WHERE {DEFENSIVE_PREDICATE}")
    left = conn.execute(f"SELECT COUNT(*) FROM capital_displacement_history WHERE {DEFENSIVE_PREDICATE}").fetchone()[0]
    print(f"Đã ghi {rows} dòng → NULL. Còn lại {left} dòng (kỳ vọng 0).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sửa vnindex_* = 0 trong capital_displacement_history")
    ap.add_argument("--apply", action="store_true", help="ghi thay đổi (mặc định chỉ xem trước)")
    args = ap.parse_args()

    with get_connection() as conn:
        bars = _bars(conn)
        measured = _measurements(bars)
        broken = conn.execute(
            f"SELECT date, vnindex_close, vnindex_chg, vnindex_vol_ratio FROM capital_displacement_history "
            f"WHERE {BROKEN_PREDICATE} ORDER BY date"
        ).fetchall()

        print(f"Bar VNINDEX hợp lệ: {len(bars)}" + (f"  ({bars[0][0]} → {bars[-1][0]})" if bars else ""))
        print(f"Dòng thiếu số đo VNINDEX: {len(broken)}")
        if not broken:
            print("Không có dòng nào thiếu số đo VNINDEX (idempotent).")
        else:
            updates: list[tuple] = []
            print(f"\n{'ngày':<12} {'close cũ':>10} {'chg cũ':>7} {'vol cũ':>7}   →  {'close':>10} {'chg':>7} {'vol':>7}  nguồn")
            for date, c_old, g_old, v_old in broken:
                if date in measured:
                    close, chg, vol_ratio = measured[date]
                    source = "daily_ohlcv (đo lại)"
                else:
                    close, chg, vol_ratio = None, None, None
                    source = "KHÔNG có bar hợp lệ ⇒ NULL"
                updates.append((close, chg, vol_ratio, date))
                fmt = lambda x: "NULL" if x is None else f"{x:,.2f}"  # noqa: E731
                print(
                    f"{date:<12} {fmt(c_old):>10} {fmt(g_old):>7} {fmt(v_old):>7}   →  "
                    f"{fmt(close):>10} {fmt(chg):>7} {fmt(vol_ratio):>7}  {source}"
                )

            if args.apply:
                # Chốt chặn idempotent: vị từ vẫn phải đúng tại thời điểm ghi.
                with conn:
                    for close, chg, vol_ratio, date in updates:
                        conn.execute(
                            f"UPDATE capital_displacement_history "
                            f"SET vnindex_close = ?, vnindex_chg = ?, vnindex_vol_ratio = ? "
                            f"WHERE date = ? AND {BROKEN_PREDICATE}",
                            (close, chg, vol_ratio, date),
                        )
                after = conn.execute(
                    f"SELECT COUNT(*) FROM capital_displacement_history WHERE {BROKEN_PREDICATE}"
                ).fetchone()[0]
                print(f"\nĐã ghi {len(updates)} dòng. Còn lại {after} dòng thiếu số đo.")

        _repair_defensive(conn, args.apply)
        if not args.apply:
            print("\nDRY-RUN: chưa ghi gì. Thêm --apply để thực thi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
