# AGENT BOOTLOADER

**Startup:** `sentinel_check.py; kit recall`. **CLI:** `ptck.py`.

## Luật bất biến
- Zero-Hallucination: không bịa số; no provenance = no trust; thiếu → đứng ngoài.
- Bằng chứng chuẩn hóa theo Regime; không đánh giá tuyệt đối.
- TDD Fail-Fast: smoke root + PYTHONPATH sạch; import relative; cấm `except: pass`.
- OHLCV `YYYY-MM-DD`; ADV_20D ≥50k; position ≤10-15% ADV_20D.
- Fusion V2: M2=.45/M1=.15/Alpha=.20/M3=.20; stop 7%/take 25%/hold 15; entry .55/exit .30.

## Quy ước vận hành
- OOS 2025-2026 đã nhiễm → cấm dùng chọn tham số; kiểm định mới qua paper-trading hoặc IS.
- CAGR = calendar-year `annualize_cagr`; PIT guard period-based only (`pit_queries.py`).
- Entity 4 khung: BANK/SECURITIES/INSURANCE/STANDARD — không đổi nhãn theo ngành; tham số đọc từ ICB.
- Mã mới sau 16:00 EOD: `cafef-crawl` → `backfill-history` → `analyze`.
