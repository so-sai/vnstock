"""Phát hiện mã có LỆCH THANG GIÁ chưa xử lý được ⇒ loại khỏi tập xếp hạng RS (§4.1).

BỐI CẢNH (2026-10-09)
---------------------
Sau khi rescale có kiểm soát (4.633 dòng vci ×1000) và cách ly 1.482 dòng, còn **56 mã**
giữ bước nhảy thang giá > 5× giữa hai phiên sạch liền kề. Nguyên nhân không còn là "vci
khác kbs" nữa mà là **lệch thang NGAY TRONG một nguồn**: HHG có `kbs 1,10` (nghìn đồng,
2026-04-07) rồi `kbs 700–800` (đồng, từ 2026-06) ⇒ `r126 = 800/1,10 − 1 ≈ +66.566%`,
một momentum GIẢ đưa HHG lên RS 99 — đầu bảng toàn thị trường.

VÌ SAO **LOẠI KHỎI XẾP HẠNG** CHỨ KHÔNG CÁCH LY TOÀN BỘ LỊCH SỬ
-------------------------------------------------------------
Bước nhảy chỉ nằm ở **một điểm nối**. Phần lịch sử trước và sau điểm nối đều tự nhất
quán (hệ số không đổi ⇒ triệt tiêu trong lợi suất). Cách ly toàn bộ mã sẽ xoá **50.794
dòng**, phần lớn là dữ liệu ĐÚNG — và theo nguyên tắc "Quarantine > Delete" thì việc đó
phải có lý do chính đáng, không phải hệ quả của một dòng SQL tiện tay.

Nên: dữ liệu **giữ nguyên**, chỉ **loại mã khỏi tập xếp hạng RS** (N_rank) cho tới khi
có dữ liệu sạch. Danh sách này ĐƯỢC SINH RA và ghi thành file để truy vết, không phải
một hằng số ai đó gõ tay.

Tiêu chí phát hiện (đúng tiêu chí đã dùng để đo suốt phiên):
    |ln(close[i]/close[i-1])| cho hai phiên SẠCH liền kề > ln(5)
    (đo trên cửa sổ 252 phiên gần nhất; một điểm nối vượt ngưỡng là đủ)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.database.data_validity import VALID_ROW_SQL  # noqa: E402

DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
OUT_PATH = PROJECT_ROOT / "backend" / "reports" / "audit_archive" / "scale_ambiguous_symbols.json"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

JUMP_RATIO = 5.0  # > 5× giữa hai phiên sạch liền kề
WINDOW_START = "2025-10-01"  # cửa sổ ~252 phiên gần nhất


def detect(
    db: Path, *, symbols: list[str] | None = None, lookback: int | None = None
) -> dict:
    """Quét tìm điểm nối lệch thang.

    symbols : chỉ quét các mã này (để verify một watchlist, vd top-10 RS).
    lookback: chỉ xét `lookback` phiên SẠCH gần nhất của mỗi mã (mặc định theo WINDOW_START).
    """
    con = sqlite3.connect(str(db))
    sym_filter = ""
    params: list[object] = []
    if symbols:
        sym_filter = " AND symbol IN (" + ",".join("?" * len(symbols)) + ")"
        params.extend(s.upper() for s in symbols)

    if lookback:
        # ROW_NUMBER để lấy đúng N phiên sạch gần nhất của TỪNG mã.
        sql = (
            "SELECT symbol, date, close, source FROM ("
            "  SELECT symbol, date, close, source,"
            "         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn"
            f"  FROM daily_ohlcv WHERE is_stale=0 AND {VALID_ROW_SQL}{sym_filter}"
            ") WHERE rn <= ? ORDER BY symbol, date"
        )
        rows = con.execute(sql, [*params, lookback]).fetchall()
        window_desc = f"{lookback} phiên sạch gần nhất mỗi mã"
    else:
        rows = con.execute(
            f"SELECT symbol, date, close, source FROM daily_ohlcv "
            f"WHERE is_stale=0 AND {VALID_ROW_SQL}{sym_filter} AND date >= ? ORDER BY symbol, date",
            [*params, WINDOW_START],
        ).fetchall()
        window_desc = f"cửa sổ từ {WINDOW_START}"
    con.close()

    seq: dict[str, list[tuple[str, float, str]]] = {}
    for s, d, c, src in rows:
        seq.setdefault(s, []).append((d, float(c), src))

    flagged: dict[str, dict] = {}
    for sym, arr in seq.items():
        for (d1, c1, s1), (d2, c2, s2) in zip(arr, arr[1:]):
            if c1 <= 0 or c2 <= 0:
                continue
            ratio = max(c1 / c2, c2 / c1)
            if ratio > JUMP_RATIO:
                flagged[sym] = {
                    "date_before": d1,
                    "close_before": c1,
                    "source_before": s1,
                    "date_after": d2,
                    "close_after": c2,
                    "source_after": s2,
                    "ratio": round(ratio, 1),
                }
                break
    return {
        "generated_at": datetime.now(VN_TZ).isoformat(),
        "criteria": (
            f"hai phiên SẠCH liền kề lệch > {JUMP_RATIO}× trong {window_desc}; "
            "dòng is_stale=0 và hợp lệ"
        ),
        "scanned_symbols": len(seq),
        "why": (
            "Lệch thang giá chưa xử lý được (có thể nằm TRONG một nguồn, vd kbs nghìn đồng → "
            "kbs đồng) ⇒ mã KHÔNG được tham gia tập xếp hạng RS (§4.1) cho tới khi có dữ liệu sạch. "
            "Dữ liệu KHÔNG bị xoá: lịch sử hai bên điểm nối vẫn tự nhất quán."
        ),
        "symbols": dict(sorted(flagged.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Sinh danh sách mã lệch thang (loại khỏi N_rank)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--symbols", nargs="*", default=None, help="chỉ quét các mã này")
    ap.add_argument("--lookback", type=int, default=None, help="chỉ xét N phiên sạch gần nhất")
    ap.add_argument("--write", action="store_true", help="ghi file JSON (mặc định chỉ in)")
    a = ap.parse_args()
    data = detect(a.db, symbols=a.symbols, lookback=a.lookback)
    n = len(data["symbols"])
    total = data["scanned_symbols"]
    print(f"quét {total} mã · lệch thang > {JUMP_RATIO}× : {n}")
    for sym, info in list(data["symbols"].items())[:8]:
        print(
            f"   {sym:<6} {info['date_before']} {info['close_before']:>12,.2f} [{info['source_before']}]"
            f"  →  {info['date_after']} {info['close_after']:>12,.2f} [{info['source_after']}]"
            f"   tỷ lệ {info['ratio']:,.0f}"
        )
    if n > 8:
        print(f"   … và {n - 8} mã khác")
    if n == 0 and a.symbols:
        print("   ✅ 0 hit — không mã nào trong danh sách này có điểm nối lệch thang")
    if a.write:
        OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ đã ghi {OUT_PATH}")
    elif not a.symbols:
        print("\n(chưa ghi — thêm --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
