"""backfill_day_batched.py — Backfill 1 phiên theo LÁT CẮT symbol.

WHY: chạy toàn bộ ~1.5k mã trong 1 tiến trình mất >20 phút → vượt cap foreground
600s → bị promote thành background và bị kill lúc agent đóng lượt
(`termination_source=agent_close`). Chạy theo lát 250-300 mã thì MỖI lần gọi
hoàn tất trong 1 lượt và GHI NGAY kết quả của lát đó → chết giữa đường cũng
không mất phần đã xong.

Usage:
    python backend/scripts/backfill_day_batched.py --date 2026-09-30 --start-idx 0 --count 300
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src", PROJECT_ROOT / "backend" / "libs" / "vnstock"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--start-idx", type=int, default=0)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument(
        "--skip-done",
        action="store_true",
        help="bỏ qua mã đã có dòng SẠCH (is_stale=0) cho ngày đó → gọi lại tự tiếp tục việc còn dở.",
    )
    ap.add_argument(
        "--symbols",
        default="",
        help="danh sách mã cụ thể (phẩy) — bỏ qua --start-idx/--count. Dùng cho 'nạp mục tiêu' watchlist.",
    )
    args = ap.parse_args()

    from src.database.db_core import get_connection
    from src.services.market_backfill import backfill_market_history

    if args.symbols:
        chunk = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        total = len(chunk)
        print(f"=== BACKFILL MỤC TIÊU {len(chunk)} mã — ngày {args.date} ===")
    else:
        with get_connection() as conn:
            syms = [
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol <> 'VNINDEX' ORDER BY symbol"
                )
            ]
            if args.skip_done:
                done = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT symbol FROM daily_ohlcv WHERE date = ? AND COALESCE(is_stale, 0) = 0",
                        (str(args.date),),
                    )
                }
                pending = [s for s in syms if s not in done]
                print(f"    (--skip-done: bỏ {len(syms) - len(pending)} mã đã sạch, còn {len(pending)} mã cần làm)")
                syms = pending
        total = len(syms)
        chunk = syms[args.start_idx : args.start_idx + args.count]
        if not chunk:
            print(f"LÁT RỖNG: start_idx={args.start_idx} >= tổng {total}")
            return 1
        print(f"=== BACKFILL LÁT {args.start_idx}..{args.start_idx + len(chunk) - 1} / {total} mã — ngày {args.date} ===")
        print(f"    ({chunk[0]} .. {chunk[-1]})")

    written, failed, skipped = backfill_market_history(chunk, str(args.date))
    print(f"→ written={written} failed={failed} skipped={skipped} (lát này)")
    if not args.symbols:
        print(f"→ còn lại: start_idx={args.start_idx + args.count} (nếu < {total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
