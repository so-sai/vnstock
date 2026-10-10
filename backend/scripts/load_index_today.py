"""load_index_today.py — nạp chỉ số (VNINDEX) cho một ngày. Bước bắt buộc của cron EOD.

VÌ SAO PHẢI CÓ FILE NÀY
-----------------------
`backfill_day_batched.py` — đường nạp EOD chính — lọc bỏ chỉ số ngay trong truy vấn chọn mã:

    SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol <> 'VNINDEX' ORDER BY symbol

Nên đường nạp hằng ngày **về mặt cấu trúc không bao giờ nạp được chỉ số**. Hậu quả thật
(2026-10-09): VNINDEX đứng ở 2026-10-05 suốt 4 phiên, không exception, không log — trong
khi VN30 (không bị lọc) vẫn đủ tới 09/10. Chỉ số là mỏ neo của trigger K3 và benchmark của
toàn hệ thống, nên thiếu nó là mù một mắt mà không ai biết.

DÙNG `update_vnindex()` CHỨ KHÔNG DÙNG ĐƯỜNG BACKFILL MÃ
-------------------------------------------------------
Đường backfill mã áp các guard/transform dành cho CỔ PHIẾU (quy đổi ×1000 khi close<1000,
dải biên theo sàn) lên một chỉ số — sai bản chất. `update_vnindex()` là hàm chuyên dụng,
có validator canonical theo `INDEX_LEVEL`, và đã được kiểm chứng chạy đúng cho 06→09/10.

Cách dùng:
    python backend/scripts/load_index_today.py                # hôm nay
    python backend/scripts/load_index_today.py --date 2026-10-09
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (
    PROJECT_ROOT,
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "backend" / "src",
    PROJECT_ROOT / "backend" / "libs",
    PROJECT_ROOT / "backend" / "libs" / "vnstock",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (mặc định: hôm nay)")
    ap.add_argument("--symbol", default="VNINDEX")
    args = ap.parse_args()
    target = args.date or datetime.now().strftime("%Y-%m-%d")

    from src.daily_updater import update_vnindex

    try:
        rows = update_vnindex(target)
    except Exception as e:  # noqa: BLE001 - CLI phải báo rõ và trả mã lỗi, không nuốt
        print(f"INDEX_LOAD_FAIL date={target} symbol={args.symbol} err={type(e).__name__}: {e}")
        return 1
    print(f"INDEX_LOAD_OK date={target} symbol={args.symbol} rows={rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
