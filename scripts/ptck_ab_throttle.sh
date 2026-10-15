#!/usr/bin/env bash
# ptck_ab_throttle.sh — cron one-shot: THÍ NGHIỆM A/B nhịp độ vs quota (Thứ Năm 15/10 07:30).
#
# WHY: ngày 13/10 quét toàn rổ cho phiên 12/10 chỉ ghi ~60/100 mã mỗi lát, reason=FETCH_ERR:RetryError
# (vnstock cạn retry). So sánh throttle 2.0 vs 0 ở HAI BỐI CẢNH QUOTA khác nhau nên không phân xử được.
# Script này chạy cùng một lát mã chưa nạp, tách chẵn/lẻ, A=throttle 0.0 rồi B=throttle 2.0 liền nhau
# khi quota ngày mới còn sạch; kết quả + phán quyết ghi ra log để đọc trước phiên.
#
# Chạy (cron): script trần trong hermes/scripts/. Log: <tmp>/ptck_run/ab_throttle_<date>.log

set -u
cd "D:/04. Software/opensource_contrib/PTCK_VNSTOCK" || exit 1

LOG_DIR="${PTCK_LOG_DIR:-C:/Users/Admin/AppData/Local/Temp/ptck_run}"
mkdir -p "$LOG_DIR"
L="$LOG_DIR/ab_throttle_$(date +%Y-%m-%d).log"

# CHỐT CHẶN IDEMPOTENT: hôm nay đã có phán quyết thì thoát ngay (job chạy recurring nên có thể
# được gọi lại sau catch-up). Không chạy lại để không làm bẩn kết quả "đọc một lần".
if [ -f "$L" ] && grep -q "PHÁN QUYẾT" "$L"; then
  echo "A/B hôm nay đã có phán quyết — bỏ qua (idempotent guard). Log: $L"
  exit 0
fi

{
  echo "=== A/B THROTTLE vs QUOTA — $(date '+%Y-%m-%d %H:%M:%S') ==="
  backend/.venv/Scripts/python.exe backend/scripts/ab_throttle_experiment.py \
    --date 2026-10-12 --start-idx 200 --count 100
  echo "AB_EXIT=$?"
} >> "$L" 2>&1

# Tóm tắt ra stdout (nằm trong run history của cron) + phán quyết
grep -E "mã còn nợ|A\(throttle|B\(throttle|PHÁN QUYẾT|AB_EXIT" "$L" | tail -6
