#!/usr/bin/env bash
# ptck_backfill_1210.sh — cron one-shot 07:50 Thứ Năm 15/10: nạp nốt phiên 2026-10-12.
#
# WHY tách khỏi thí nghiệm A/B: ptck_backfill_day.sh quét cả rổ sẽ ăn mất dải mã
# (--start-idx 200 --count 100) của thí nghiệm A/B và làm bẩn trạng thái quota trước 07:30.
#
# THROTTLE ĐƯỢC CHỌN TỰ ĐỘNG theo phán quyết A/B của chính buổi sáng đó (đọc log, không đoán):
#   "CẠN QUOTA TÍCH LUỸ"   -> throttle 0.0 (nhịp độ không phải vấn đề, chạy nhanh)
#   "BURST RATE-LIMIT"     -> giữ 2.0
#   "MÃ CHẾT..." / khác   -> giữ 2.0 (an toàn)
#
# GIỚI HẠN: cron script bị cắt ở 3600s. Vì backfill dùng --skip-done nên phần chưa xong
# có thể chạy tiếp bằng tay: bash ptck_backfill_day.sh 2026-10-12 2.0

set -u
LOG_DIR="${PTCK_LOG_DIR:-C:/Users/Admin/AppData/Local/Temp/ptck_run}"
L_AB="$LOG_DIR/ab_throttle_$(date +%Y-%m-%d).log"
L="$LOG_DIR/backfill_1210_cron_$(date +%Y-%m-%d).log"

TH="2.0"
if [ -f "$L_AB" ] && grep -q "CẠN QUOTA TÍCH LUỸ" "$L_AB"; then
  TH="0.0"
fi

{
  echo "=== BACKFILL 2026-10-12 (one-shot cron) — $(date '+%Y-%m-%d %H:%M:%S') — throttle=$TH ==="
  echo "    (nguồn chọn throttle: $L_AB)"
  bash /c/Users/Admin/AppData/Local/hermes/scripts/ptck_backfill_day.sh 2026-10-12 "$TH"
} >> "$L" 2>&1

grep -E "SWEEP_DONE|GATE|BACKFILL" "$L" | tail -3
