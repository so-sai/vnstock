-- =============================================================================
-- MIGRATION: fix_ohlcv_scale_20260811.sql
-- -----------------------------------------------------------------------------
-- MỤC ĐÍCH
--   Sửa lỗi Scale Unit Inconsistency (đơn vị "nghìn đồng" vs "đồng") trong
--   daily_ohlcv. Nguồn cào công khai (VCI/TCBS/KBS/CafeF/Vietstock) trả đơn vị
--   không nhất quán theo thời gian → SaveDataUpsert ghi thẳng vào SQLite mà
--   không qua Unit Normalizer, làm hàng loạt mã bị gãy chuỗi thời gian x1000
--   (VD: LPB 51.8 (nghìn) → 51.800 (đồng) tại mốc 2026-08-03).
--
-- NGỮ NGHĨA QUY TẮC (Zero-Hallucination: không tự bịa dữ liệu)
--   BƯỚC 1: dòng close<100, symbol có MAX(close)>=1000
--           → chứng minh symbol đó giao dịch ở đơn vị "đồng" ≥1000, nên các
--             dòng <100 chắc chắn còn ở đơn vị "nghìn đồng" → x1000.
--   BƯỚC 2: dòng 100<=close<1000, symbol có MAX(close)>=10000
--           → vùng biên (100..1000) của cổ phiếu giá thật ≥10.000 (VNZ 500
--             = 500.000) → x1000.
--   BƯỚC 3: dòng 0<close<1 — không cổ phiếu nào giao dịch dưới 1đ
--           → chắc chắn sai đơn vị (HHG 0.8 = 800) → x1000.
--   KHÔNG đụng: 151 mã penny thật (MAX<1000) và 20 mã jump nhiều mốc
--   (FTM/LUT/HKB/VHM...) thiếu provenance — cô lập, cảnh báo, không bịa.
--
-- TÁI LẬP (Run on a fresh/corrupted DB only)
--   sqlite3 backend/data/screener_cache.db < scripts/migrations/fix_ohlcv_scale_20260811.sql
--   ⚠ BACKUP TRƯỚC: copy screener_cache.db sang screener_cache.db.bak_<date>
-- =============================================================================

BEGIN TRANSACTION;

-- BƯỚC 1: close<100, symbol có MAX(close)>=1000 → x1000
CREATE TEMP TABLE t_valid_symbols AS
    SELECT symbol FROM daily_ohlcv GROUP BY symbol HAVING MAX(close) >= 1000;

UPDATE daily_ohlcv
SET open = open * 1000,
    high = high * 1000,
    low = low * 1000,
    close = close * 1000,
    adj_close = CASE WHEN adj_close IS NOT NULL AND adj_close > 0
                     THEN adj_close * 1000 ELSE adj_close END
WHERE close < 100 AND symbol IN (SELECT symbol FROM t_valid_symbols);

-- BƯỚC 2: 100<=close<1000, symbol có MAX(close)>=10000 → x1000
CREATE TEMP TABLE t_big AS
    SELECT symbol FROM daily_ohlcv GROUP BY symbol HAVING MAX(close) >= 10000;

UPDATE daily_ohlcv
SET open = open * 1000,
    high = high * 1000,
    low = low * 1000,
    close = close * 1000,
    adj_close = CASE WHEN adj_close IS NOT NULL AND adj_close > 0
                     THEN adj_close * 1000 ELSE adj_close END
WHERE close >= 100 AND close < 1000 AND symbol IN (SELECT symbol FROM t_big);

-- BƯỚC 3: 0<close<1 → chắc chắn sai đơn vị, x1000
UPDATE daily_ohlcv
SET open = CASE WHEN open > 0 AND open < 1 THEN open * 1000 ELSE open END,
    high = CASE WHEN high > 0 AND high < 1 THEN high * 1000 ELSE high END,
    low  = CASE WHEN low  > 0 AND low  < 1 THEN low  * 1000 ELSE low  END,
    close = close * 1000,
    adj_close = CASE WHEN adj_close IS NOT NULL AND adj_close > 0
                     AND adj_close < 1 THEN adj_close * 1000 ELSE adj_close END
WHERE close > 0 AND close < 1;

COMMIT;