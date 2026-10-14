"""ab_throttle_experiment.py — A/B TÁCH NHÂN QUẢ: nhịp độ (burst rate-limit) hay quota tích luỹ?

WHY: ngày 2026-10-13 quét toàn rổ cho phiên 12/10 chỉ ghi được ~60/100 mã mỗi lát, mỗi mã lỗi
tốn ~9s (retry). Khi mở kênh chẩn đoán (provider.last_history_error) thì lý do lộ ra là
`FETCH_ERR:RetryError` — tức vnstock đã cạn retry (throttle/timeout phía nguồn), KHÔNG phải lỗi
parser. Nhưng so sánh `throttle 2.0` (60/100) vs `throttle 0` (58/100) không phân xử được, vì
hai lần chạy ở hai BỐI CẢNH QUOTA khác nhau.

Thiết kế chống nhiễu thời gian (interleaved, cùng lát, cùng phiên):
  - Lấy CÙNG một lát mã chưa nạp, tách theo chỉ số chẵn/lẻ → 2 nhóm CÙNG độ khó dữ liệu.
  - Nhóm A (chẵn): throttle 0.0 — Nhóm B (lẻ): throttle 2.0, chạy NGAY sau A.
  - Tiêu chí phân xử (đặt trước):
      cả A và B >= 90%       -> nguyên nhân là CẠN QUOTA TÍCH LUỸ trong ngày
      B >= 90% và A ~60%     -> nguyên nhân là BURST RATE-LIMIT theo giây (phải giữ throttle)
      cả A và B ~60%         -> các mã đó là mã chết/nguồn không có bar -> danh sách DEAD_OR_UNSUPPORTED

Chạy:
  python backend/scripts/ab_throttle_experiment.py --date 2026-10-12 --start-idx 200 --count 100
READ-ONLY ngoài việc ghi OHLCV qua đúng đường backfill có guard (giống ptck_load_today.sh).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXTRA_PATHS = (
    PROJECT_ROOT,
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "backend" / "src",
    PROJECT_ROOT / "backend" / "libs" / "vnstock",
)
for _p in _EXTRA_PATHS:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pending_symbols(date: str, start_idx: int, count: int) -> list[str]:
    """Mã trong lát [start_idx, start_idx+count) CHƯA có dòng hợp lệ cho `date`."""
    from src.database.data_validity import VALID_ROW_SQL

    db = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        universe = [
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol <> 'VNINDEX' ORDER BY symbol"
            )
        ]
        done = {
            str(r[0])
            for r in con.execute(
                f"SELECT symbol FROM daily_ohlcv WHERE date = ? AND {VALID_ROW_SQL}", (date,)
            )
        }
    finally:
        con.close()
    chunk = universe[start_idx : start_idx + count]
    return [s for s in chunk if s not in done]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--start-idx", type=int, default=0)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--throttle-a", type=float, default=0.0)
    ap.add_argument("--throttle-b", type=float, default=2.0)
    args = ap.parse_args()

    from src.services.market_backfill import backfill_market_history

    pend = pending_symbols(args.date, args.start_idx, args.count)
    group_a = pend[0::2]
    group_b = pend[1::2]
    print(f"=== A/B {args.date} lát {args.start_idx}..{args.start_idx + args.count - 1} ===")
    print(f"  mã còn nợ trong lát: {len(pend)}  -> "
          f"A(chẵn)={len(group_a)} throttle={args.throttle_a}s | "
          f"B(lẻ)={len(group_b)} throttle={args.throttle_b}s")
    if not pend:
        print("  (không còn mã nợ — không có gì để so)")
        return 0

    wa, fa, _ = backfill_market_history(group_a, args.date, throttle_sec=args.throttle_a)
    wb, fb, _ = backfill_market_history(group_b, args.date, throttle_sec=args.throttle_b)
    ra = wa / len(group_a) if group_a else 0.0
    rb = wb / len(group_b) if group_b else 0.0
    print(f"  A(throttle={args.throttle_a}): written={wa} failed={fa} -> {ra:.0%}")
    print(f"  B(throttle={args.throttle_b}): written={wb} failed={fb} -> {rb:.0%}")

    if ra >= 0.9 and rb >= 0.9:
        verdict = "CẠN QUOTA TÍCH LUỸ trong ngày (cả hai nhóm đều tốt khi quota mới)"
    elif rb >= 0.9 and ra < 0.75:
        verdict = "BURST RATE-LIMIT theo giây -> GIỮ --throttle 2.0 cho mọi quét theo lô"
    elif ra < 0.75 and rb < 0.75:
        verdict = "MÃ CHẾT/NGUỒN KHÔNG CÓ BAR -> đưa vào DEAD_OR_UNSUPPORTED_SYMBOLS (throttle không cứu được)"
    else:
        verdict = "KHÔNG KẾT LUẬN ĐƯỢC (kết quả lẫn) — cần lặp lại ở bối cảnh quota khác"
    print(f"  PHÁN QUYẾT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
