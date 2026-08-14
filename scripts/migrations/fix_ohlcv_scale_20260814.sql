-- =============================================================================
-- MIGRATION: fix_ohlcv_scale_20260814.sql
-- -----------------------------------------------------------------------------
-- MỤC ĐÍCH
--   Vá lỗ hổng migration 20260811: nhóm large-cap có TOÀN BỘ lịch sử
--   max(close) < 1000 bị đánh đồng nhầm với penny (điều kiện cũ
--   `MAX(close) >= 1000` KHÔNG bắt được vì chuỗi giá toàn bộ nằm ở đơn vị
--   "nghìn đồng", chưa từng vượt mốc 1000) → 8 mã UNIVERSE core-58 chưa được
--   chuẩn hóa ×1000 (VD: HPG lưu 22.05 thay vì 22.050 VND).
--
-- DANH SÁCH & BẰNG CHỨNG (Zero-Hallucination — không bịa, mỗi mã có ≥2 bằng chứng):
--   UNIVERSE (core-58) + paper_trades_log entry_price tính bằng VND:
--     HPG  entry=23,250 | SSI entry=25,15?? -> UNIVERSE+ADV=64M
--     MBB  UNIVERSE+ADV=55M | STB UNIVERSE+ADV=47M | TCB entry=33,600
--     VIB  UNIVERSE+ADV=27M | MWG UNIVERSE+ADV=14M | VNM entry=54,900
--   Bằng chứng cụ thể:
--     - paper_trades_log (VND chuẩn, nguồn hệ thống): HPG=23,250 TCB=33,600 VNM=54,900
--     - Provider KBS/VCI (2026-08-12): HPG=22.1 nghìn, TCB=31.5 nghìn → DB lưu nguyên
--       giá nguồn "nghìn đồng" nhưng LPB/IJC/BCM/FPT/VCB đã ×1000 ở đợt 1 → không nhất quán.
--     - ADV20 > 10M cp/phiên (HPG 94M, SSI 64M, MBB 55M, STB 47M, TCB 35M,
--       VIB 27M, MWG 14M, VNM 11M) — không penny nào có thanh khoản này.
--
-- KHÔNG ĐỤNG (thiếu provenance):
--   - Penny thật max<1000: QBS/HHG/MPT/DMX/... (min≈1, giá vài trăm → nghìn đồng).
--   - Các mã max<1000 không thuộc UNIVERSE, không có entry_price trong paper_trades
--     (LTG, HTP, PPE, SGH, PCG...) → cô lập, cảnh báo, không bịa.
--
-- TÁI LẬP
--   sqlite3 backend/data/screener_cache.db < scripts/migrations/fix_ohlcv_scale_20260814.sql
--   ⚠ BACKUP TRƯỚC: copy screener_cache.db sang screener_cache.db.bak_20260814_scale2 (đã có)
-- =============================================================================

BEGIN TRANSACTION;

UPDATE daily_ohlcv
SET open = open * 1000,
    high = high * 1000,
    low = low * 1000,
    close = close * 1000,
    adj_close = CASE WHEN adj_close IS NOT NULL AND adj_close > 0
                     THEN adj_close * 1000 ELSE adj_close END
WHERE symbol IN ('HPG', 'SSI', 'MBB', 'STB', 'TCB', 'VIB', 'MWG', 'VNM')
  AND close > 0;

COMMIT;
