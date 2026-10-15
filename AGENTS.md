# AGENTS
Boot: `sentinel_check.py; kit recall`. CLI: `ptck.py`.
- Zero-Hallucination: no provenance=no trust; thiếu→đứng ngoài.
- Evidence theo Regime; cấm tuyệt đối hóa.
- TDD fail-fast: smoke root, PYTHONPATH sạch, import relative, cấm `except: pass`.
- OHLCV `YYYY-MM-DD`; ADV_20D≥50k; pos≤10-15% ADV.
- FusionV2 M2.45/M1.15/A.20/M3.20; stop7/take25/hold15; entry.55/exit.30.
- OOS25-26 nhiễm→cấm tune; valid mới=paper/IS.
- CAGR=calendar-year; PIT period-based only.
- Entity BANK/SEC/INS/STD; params từ ICB.
- Mã mới sau 16:00: cafef-crawl→backfill→analyze.
- PANEL/`is_stale`: CẤM dùng `is_stale=0` làm bộ lọc panel lịch sử (median 619/1514 dòng/phiên bị gắn `is_stale=1` → rụng ~40% mẫu, `balanced_panel` suy biến còn 8 mã). Mọi truy vấn OHLCV hợp lệ đi qua `data_validity.VALID_ROW_SQL`; `is_stale` chỉ để kiểm toán độ tươi EOD trong ngày.
- Coverage fail-fast: chuỗi Index/Target phải qua `panel_quality.assert_series_coverage` (VN30 thiếu 86/95 phiên = coverage 9,5%); phép toán toàn thị trường qua `assert_market_breadth` (median dòng HỢP LỆ chỉ ~865/phiên → KHÔNG dùng ngưỡng cứng 1000 mã, dùng `min_value_ratio`). Thiếu bar ⇒ DỪNG, cấm `fillna(0)`. Alpha/factor đa mã khởi tạo mẫu qua `balanced_panel(min_presence=0.98)`.
- Chỉ số ≠ cổ phiếu: dùng `data_validity.STOCK_ONLY_SQL` / `INDEX_SYMBOLS` (VNINDEX, VN30, HNXINDEX, HNX30, UPCOMINDEX, UPINDEX). KL VNINDEX một phiên (643,9M cp) lớn hơn cả rổ cổ phiếu ⇒ `SUM(volume)` quên lọc sai ~2×.
- Phiên bóng ma: cron chạy trong ngày nghỉ đọc bảng giá ĐÓNG BĂNG và đóng dấu ngày (đã xảy ra: 2026-09-01 = bản sao khít 2026-08-28, 1.515 dòng OHLCV + 1.515 dòng foreign). Chặn bằng `panel_quality.NON_TRADING_SESSIONS` + `detect_duplicate_sessions()`; dòng rác phải quarantine khỏi store.
- Kiểm toán độ phủ: mất dòng theo **≥5 cơ chế KHÁC NHAU** (nguồn không có bar / bị cổng biên độ chặn / tự sửa thang giá / loại vì lệch thang / lỗi dữ liệu tất định / mã treo bị bỏ qua) — CẤM gộp thành một con số "mã thiếu". Mỗi lượt sweep in khối  (, log-only, KHÔNG phải cổng); cộng dồn qua các lát bằng .
- Cron: ticker là **in-process của Hermes Desktop** — app tắt qua giờ là job **one-shot bị retire, không chạy, không log** (đã xảy ra 15/10 với 2 job 07:30/07:50). Mọi job bắt buộc chạy phải đặt **recurring** + **chốt chặn idempotent** trong script.
