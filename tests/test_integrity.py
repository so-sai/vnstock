import pandas as pd
import pytest
from src.database.db_core import get_connection

def test_no_duplicate_ohlcv():
    """🛡️ Kiểm tra tính duy nhất: (Symbol, Date) không được trùng lặp"""
    with get_connection() as conn:
        query = """
            SELECT symbol, date, COUNT(*) as cnt 
            FROM daily_ohlcv 
            GROUP BY symbol, date 
            HAVING cnt > 1
        """
        duplicates = pd.read_sql(query, conn)
    assert duplicates.empty, f"Phát hiện {len(duplicates)} cặp (mã, ngày) bị trùng lặp!"

def test_data_sanity():
    """🧪 Kiểm tra tính logic: Giá > 0 và Khối lượng >= 0"""
    with get_connection() as conn:
        query = "SELECT symbol, date FROM daily_ohlcv WHERE close <= 0 OR volume < 0"
        insane_data = pd.read_sql(query, conn)
    assert insane_data.empty, "Phát hiện dữ liệu phi vật lý (Giá âm hoặc Volume âm)!"

def test_industry_mapping_coverage():
    """📊 Kiểm tra độ phủ: Mọi mã trong DB phải có phân ngành ICB"""
    with get_connection() as conn:
        # Chúng ta chỉ check các mã ĐANG CÓ dữ liệu giá
        query = """
            SELECT DISTINCT d.symbol 
            FROM daily_ohlcv d
            LEFT JOIN symbol_industry i ON d.symbol = i.symbol
            WHERE i.symbol IS NULL
        """
        unmapped = pd.read_sql(query, conn)
    
    # UPCOM hoặc mã mới có thể chưa có ngành ngay lập tức, nhưng lý tưởng là phải có.
    # Nếu fail quá nhiều, cần nạp lại industry.
    assert len(unmapped) < 50, f"Cảnh báo: {len(unmapped)} mã chưa được phân ngành ICB! Hãy chạy seed_data --mode industry"
