# AGENT COGNITIVE BOOTLOADER

**Startup:** `python backend/src/utils/sentinel_check.py; kit recall`. **Memory:** `.kit/local_brain.db` là truth (session chi tiết lưu đó); markdown này là chỉ mục siêu ngắn. **CLI:** luôn dùng `ptck.py`.

## Luật bất biến
- Zero-Hallucination: cấm bịa dữ liệu (`_generate_synthetic_base` luôn `[]`); No Provenance = No Trust; thiếu dữ liệu → ĐỨNG NGOÀI.
- LAW-009/010: tín hiệu vĩ mô có độ trễ theo ngành (MacroLagEngine); bằng chứng chuẩn hóa theo Regime, không đánh giá tuyệt đối.
- TDD Fail-Fast 3 tầng: smoke test từ project root + PYTHONPATH sạch; import relative; cấm `except: pass` im lặng.
- OHLCV: date luôn `YYYY-MM-DD` (write-guard db_core.py:392); scan-guard data_integrity.py; read-guard `_get_close` `<1000→×1000`.
- Thanh khoản: ADV_20D ≥ 50k cp/ngày (HNX/UPCoM), không hạ ngưỡng; position ≤10-15% ADV_20D.
- Fusion V2: M2=0.45/M1=0.15/Alpha=0.20/M3=0.20; stop 7%/take 25%/hold 15; entry 0.55/exit 0.30.

## Onboarding mã mới (post-16:00 EOD)
`cafef-crawl --symbols X --source vci` → `backfill-history --symbols X` → `analyze --symbols X` (VD DMX: HOSE, Điện Máy Xanh, phiên đầu 06/08/2026; BCTC 82 facts đã nạp, backfill chờ sau 16:00). `symbol_industry` tự đồng bộ từ VCI Listing (screener.py to_sql replace).

Chi tiết session: `kit recall`.
