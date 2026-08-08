# AGENT BOOTLOADER

**Startup:** `sentinel_check.py; kit recall` (chi tiết trong `.kit/local_brain.db` — file này là index ngắn). **CLI:** `ptck.py`.

## Luật bất biến
- Zero-Hallucination: cấm bịa dữ liệu; no provenance = no trust; thiếu dữ liệu → đứng ngoài.
- Bằng chứng chuẩn hóa theo Regime (MacroLagEngine), không đánh giá tuyệt đối.
- TDD Fail-Fast: smoke từ project root + PYTHONPATH sạch; import relative; cấm `except: pass`.
- OHLCV date `YYYY-MM-DD`; ADV_20D ≥50k cp; position ≤10-15% ADV_20D.
- Fusion V2: M2=.45/M1=.15/Alpha=.20/M3=.20; stop 7%/take 25%/hold 15; entry .55/exit .30.

## Quy ước vận hành
- **OOS 2025-2026 ĐÃ NHIỄM** (dùng ≥6 cấu hình) — cấm dùng làm bằng chứng chọn tham số; kiểm định mới chỉ qua paper-trading hoặc train/validation trong IS.
- CAGR = calendar-year `annualize_cagr` (base vốn ban đầu); PIT guard hiện period-based only (`pit_queries.py`).
- Entity: `entity_type` 4 khung (BANK/SECURITIES/INSURANCE/STANDARD) — không đổi nhãn theo ngành kinh tế; tham số ngành đọc từ ICB, không đổi cấu trúc BCTC.
- Mã mới sau 16:00 EOD: `cafef-crawl` → `backfill-history` → `analyze`.
