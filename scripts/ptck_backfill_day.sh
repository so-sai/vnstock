#!/usr/bin/env bash
# ptck_backfill_day.sh <YYYY-MM-DD> [throttle_sec] — nạp EOD cho MỘT NGÀY TƯỜNG MINH.
#
# WHY: bản cron cũ (ptck_load_today.sh) lấy ngày bằng `date` (hôm nay), nên khi Hermes
# bỏ lỡ fire (máy tắt) và chạy catch-up trễ 1 ngày, nó stamp phiên HIỆN TẠI chứ không
# nạp phiên bị nợ → mất bar vĩnh viễn (đã xảy ra thật: bar 2026-10-12 toàn thị trường,
# và foreign 12/10 mất không thể phục hồi vì nguồn foreign chỉ có real-time).
#
# --skip-done: chỉ làm mã còn thiếu/bẩn ⇒ gọi lại an toàn, không nạp lại toàn rổ.
# --throttle : nguồn vci/kbs rate-limit → vnstock RetryError → provider.history() nuốt
#              exception trả None → log chỉ ghi reason=UNKNOWN (không phân biệt được với
#              "mã không có dữ liệu"). Đo 2026-10-13: throttle=2.0 ghi tốt mã thanh khoản
#              (10/10 bluechip, 2/2 PNJ+FPT), nhưng đuôi mã nhỏ vẫn lỗi → throttle chưa đủ,
#              phải đọc log coverage trước khi tin kết quả.
#
# PHẠM VI: chỉ ghi daily_ohlcv + VNINDEX. Foreign KHÔNG nạp lại được (nguồn real-time).
#
# VỊ TRÍ SỐNG: bản Hermes cron gọi phải nằm ở <hermes>/scripts/ (tên file trần).
#   Bản trong repo này là bản gốc để version-hoá; copy sang hermes/scripts/ khi đổi.
#
# Usage: ptck_backfill_day.sh 2026-10-12 2.0

set -u
cd "D:/04. Software/opensource_contrib/PTCK_VNSTOCK" || exit 1

D="${1:-$(date +%Y-%m-%d)}"
THROTTLE="${2:-2.0}"
PY="backend/.venv/Scripts/python.exe"
LOG_DIR="${PTCK_LOG_DIR:-C:/Users/Admin/AppData/Local/Temp/ptck_run}"
mkdir -p "$LOG_DIR"
L="$LOG_DIR/backfill_${D}.log"

{
  echo "=== BACKFILL NGÀY TƯỜNG MINH $D — bắt đầu $(date '+%Y-%m-%d %H:%M:%S') — throttle=${THROTTLE}s ==="
  for i in $(seq 0 100 1500); do
    echo "--- SLICE $i $(date '+%H:%M:%S') ---"
    PTCK_BACKFILL_ATTEMPTS=2 "$PY" backend/scripts/backfill_day_batched.py \
      --date "$D" --start-idx $i --count 100 --skip-done --throttle "$THROTTLE"
  done
  # BẮT BUỘC: chỉ số không nằm trong danh sách mã của backfill_day_batched.py
  # (lọc `symbol <> 'VNINDEX'`) nên phải nạp riêng, nếu không VNINDEX đứng im vô hình.
  echo "--- INDEX $D $(date '+%H:%M:%S') ---"
  PTCK_BACKFILL_ATTEMPTS=2 "$PY" backend/scripts/load_index_today.py --date "$D"
  echo "SWEEP_DONE $D $(date '+%H:%M:%S')"
} >> "$L" 2>&1

grep -E "SWEEP_DONE|INDEX_LOAD|^→ written" "$L" | tail -6

# Cổng fail-closed: thiếu chỉ số của phiên ⇒ thoát 1 để cron báo ĐỎ.
"$PY" - "$D" <<'PY' || exit 1
import sqlite3, sys
d = sys.argv[1]
c = sqlite3.connect("file:backend/data/screener_cache.db?mode=ro", uri=True)
n = c.execute(
    "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='VNINDEX' AND date=? AND COALESCE(is_stale,0)=0",
    (d,),
).fetchone()[0]
tot = c.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date=?", (d,)).fetchone()[0]
print(f"GATE {d}: VNINDEX_rows={n}  tổng_mã={tot}")
sys.exit(0 if n else 1)
PY
