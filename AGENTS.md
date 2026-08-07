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

## Kiến trúc 2 tầng entity (2026-08-07)
- **TẦNG 1 Khung Kế toán** (`entity_type` trong `entity_registry`): BANK / SECURITIES / INSURANCE / STANDARD. Chỉ 4 khung BCTC VAS khác biệt. HPG/FPT/MWG/VHM = STANDARD là ĐÚNG — không bao giờ đổi nhãn ngành sản xuất/BĐS/bán lẻ.
- **TẦNG 2 Tham số Ngành** (ICB policy rules trong `vn20_quant_filter.py`): ngưỡng đặc thù trong nhóm STANDARD (Thép GM≥15%, BĐS D/E...) đọc từ `icb_name2/3/4` (screener_cache.db `symbol_industry`), KHÔNG đổi cấu trúc BCTC.
- Sync registry: `python backend/scripts/entity_registry_audit.py [--apply]` — quét ICB → map (BANK='ngân hàng', SECURITIES='chứng khoán', INSURANCE='bảo hiểm'; CHÚ Ý 'môi giới' đơn lẻ lẫn BĐS → phải khớp 'chứng khoán'). Đã apply 1964 mã: 29 BANK + 45 SECURITIES + 14 INSURANCE + 1876 STANDARD.
- **LƯU Ý**: SECURITIES/INSURANCE đã có khung ratios riêng (2026-08-07): engine `compute_period_ratios` phân nhánh 4 nhóm — BANK (NIM/LDR/NPL/CASA), SECURITIES = **additive** (standard ratios + margin/FVTPL/HTM/AFS theo TT334), INSURANCE (Loss/Expense/Combined Ratio, dự phòng nghiệp vụ theo TT135), STANDARD. Ratio đặc thù chỉ xuất hiện khi nguồn cung cấp metric (không bịa). `vn20_quant_filter` vẫn chỉ phân nhánh BANK vs non-bank — SECURITIES/INSURANCE chạy ngưỡng như STANDARD.
- NIM trong health_ratios là QUARTERLY — `tier1_buffett_quality` annualize x4 trước ngưỡng 1.8% (vn20_quant_filter.py:230).

Chi tiết session: `kit recall`.
