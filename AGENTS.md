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
