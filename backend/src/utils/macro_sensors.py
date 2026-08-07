import datetime
import logging
import sqlite3
from typing import Any

import pandas as pd
import requests
import yfinance as yf
from requests import Session
from requests.adapters import HTTPAdapter
from src.core.errors import RateLimitError
from urllib3.util.retry import Retry

logger = logging.getLogger("PTK_SYSTEM")


class RateLimitException(RateLimitError):
    """Ngoại lệ chuyên biệt cho lỗi HTTP 429 — chặn ngay lập tức mọi retry."""


class MacroSensorEngine:
    def __init__(self, db_path: str = "backend/data/screener_cache.db"):
        self.db_path = db_path
        self.timeout_standard = 30.0
        self.timeout_fast = 15.0

    def _get_resilient_session(self) -> Session:
        """Cấu hình Session với cơ chế Retry nâng cao, giải phóng luồng treo 429.

        Loại trừ 429 khỏi status_forcelist để xử lý thủ công, chống ban IP nhanh.
        respect_retry_after_header=False — vô hiệu hóa auto-sleep để tránh treo thread.
        """
        session = Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            respect_retry_after_header=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get_fresh_macro_data(self, sensor_name: str) -> tuple[pd.DataFrame | None, bool]:
        """
        Lấy dữ liệu vĩ mô tươi (is_stale = 0) từ SQLite.
        Đây là SQL bắt buộc dùng cho RegimeEngine và các tầng tiêu thụ hạ lưu.
        Trả về: (DataFrame dữ liệu, có_dữ_liệu_không)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                query = "SELECT * FROM macro_history WHERE variable = ? AND is_stale = 0 ORDER BY date DESC LIMIT 1"
                df = pd.read_sql_query(query, conn, params=[sensor_name])
                if df is not None and not df.empty:
                    return df, True
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            logger.error(f"[CRITICAL_DB_ERROR] Không thể truy vấn sensor {sensor_name}: {str(e)}")
        return None, False

    def _get_t_minus_one_fallback(self, sensor_name: str) -> tuple[pd.DataFrame | None, bool]:
        """
        Trích xuất dữ liệu lịch sử gần nhất (T-1) từ SQLite cache khi API sập.
        Trả về: (DataFrame dữ liệu, cờ stale=True)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                query = "SELECT * FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1"
                df = pd.read_sql_query(query, conn, params=[sensor_name])
                if df is not None and not df.empty:
                    logger.warning(f"[MACRO_STALE] Kích hoạt Proxy Sensor cho {sensor_name}. Sử dụng dữ liệu T-1.")
                    # Override ngày tháng thành ngày hiện tại để tránh lệch trục thời gian LOCF
                    today_str = datetime.date.today().isoformat()
                    if "date" in df.columns:
                        df["date"] = today_str
                    elif isinstance(df.index, pd.DatetimeIndex):
                        df.index = pd.DatetimeIndex([today_str] * len(df))
                    return df, True
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            logger.error(f"[CRITICAL_DB_ERROR] Không thể đọc cache dự phòng cho {sensor_name}: {str(e)}")

        return None, True

    def fetch_world_bank_data(self) -> tuple[dict[str, Any] | None, bool]:
        """Cập nhật dữ liệu từ World Bank với cơ chế chống nghẽn Timeout.

        Sử dụng Retry Adapter với exponential backoff để tránh spam API
        khi server trả về lỗi 5xx (Rate Limit / Server Error).
        """
        url = "https://api.worldbank.org/v2/country/VN/indicator/NY.GDP.MKTP.CD?format=json"
        session = self._get_resilient_session()
        try:
            response = session.get(url, timeout=self.timeout_fast)
            response.raise_for_status()
            data = response.json()
            return {"gdp_raw": data}, False
        except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
            logger.error(f"[NETWORK_TIMEOUT] World Bank API không phản hồi: {str(e)}")
            return None, True

    def fetch_yf_macro_sensor(self, ticker: str) -> tuple[pd.DataFrame | None, bool]:
        """Cập nhật cảm biến vĩ mô từ Yahoo Finance với cấu hình cô lập luồng.

        Tiêm trực tiếp session resilient (đã cấu hình Retry 5xx, loại trừ 429)
        vào yfinance. Khi phát hiện HTTP 429 Rate Limit, ném RateLimitException
        để kích hoạt gián đoạn khẩn cấp sang Proxy Sensor T-1 ngay lập tức,
        không bao giờ retry trong cùng một phiên chạy để bảo vệ IP.
        """
        try:
            session = self._get_resilient_session()
            ticker_obj = yf.Ticker(ticker, session=session)
            df = ticker_obj.history(period="5d", timeout=self.timeout_standard)

            # 429 detection via yfinance shared errors dict (no try/except needed)
            if ticker in yf.shared._ERRORS:
                err_val = str(yf.shared._ERRORS[ticker])
                if "429" in err_val or "too many requests" in err_val.lower():
                    logger.error("[RATE_LIMIT] Phát hiện HTTP 429 cho %s. Ngắt kết nối khẩn cấp, chuyển sang T-1.", ticker)
                    return self._get_t_minus_one_fallback(ticker)

            if df.empty or "Close" not in df.columns:
                logger.warning("[SENSOR_WARNING] %s: dữ liệu trả về trống hoặc thiếu cột Close.", ticker)
                return self._get_t_minus_one_fallback(ticker)

            return df, False
        except RateLimitException:
            logger.error("[RATE_LIMIT] Phát hiện HTTP 429 cho %s. Ngắt kết nối khẩn cấp, chuyển sang T-1.", ticker)
            return self._get_t_minus_one_fallback(ticker)
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            logger.error("[SENSOR_ERROR] Lỗi kết nối cảm biến %s: %s", ticker, str(e))
            return self._get_t_minus_one_fallback(ticker)
