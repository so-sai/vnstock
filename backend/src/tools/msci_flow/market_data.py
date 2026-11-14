"""Lớp đọc dữ liệu thị trường, tỷ giá và sức khỏe nội tại từ DB nội bộ (read-only).

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Giá đóng cửa gần nhất, thanh khoản trung bình 20 phiên (ADV20), tỷ giá USD/VND
  và tình trạng sức khỏe tài chính nội tại BẮT BUỘC phải lấy từ DB hệ thống thay
  vì điền tay trong manifest để đảm bảo provenance và kiểm soát độ tươi dữ liệu.
ROOT CAUSE:
  Đọc trực tiếp từ file JSON tự khai báo dễ dẫn đến sai lệch giá, gộp nhầm chỉ số
  (`INDEX_SYMBOLS`) hoặc bỏ qua dòng bị đánh dấu `is_stale = 1` / hỏng OHLCV.
INVARIANT:
  1. Chỉ mở DB ở chế độ `mode=ro` (URI read-only), tuyệt đối không tạo hay sửa DB.
  2. Luôn áp dụng vị từ `VALID_ROW_SQL AND STOCK_ONLY_SQL AND COALESCE(is_stale, 0) = 0`
     khi đọc `daily_ohlcv`; nếu < 20 phiên hợp lệ -> `adv20_shares = None`.
  3. Thiếu DB tài chính hoặc thiếu lịch sử `NET_INCOME` -> trả `None` + cờ thiếu
     dữ liệu (`MISSING_FUNDAMENTAL_HEALTH`), cấm tự điền 0.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from src.database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL
from src.financial.company_health_v2 import CompanyHealthV2

from .models import MIN_ADV20_SESSIONS, SymbolMarketSnapshot

BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SCREENER_DB = BACKEND_DIR / "data" / "screener_cache.db"
DEFAULT_FINANCIAL_DB = BACKEND_DIR / "data" / "financial_facts.db"

# [WHY]: Hằng số chỉ báo tỷ giá trong macro_history / macro_history_v2 của screener_cache.db;
# sửa sai tên biến sẽ làm FX trả None.
FX_VARIABLE_NAME = "USD_VND"

# [WHY]: Số thập phân làm tròn tối đa không đổi đơn vị; đặt rõ biên tối thiểu cho tỷ giá dương.
ZERO_FX = 0.0


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve()
    return sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def load_fx_vnd_per_usd(
    screener_db_path: str | Path = DEFAULT_SCREENER_DB,
) -> tuple[float | None, str | None, str | None]:
    """Đọc tỷ giá `USD_VND` mới nhất từ `macro_history_v2` (fallback `macro_history`).

    Trả về `(rate, as_of_date, source_label)`. Thiếu bảng/giá trị -> `(None, None, None)`.
    """
    db_file = Path(screener_db_path)
    if not db_file.exists():
        return None, None, None

    with contextlib.closing(_connect_readonly(db_file)) as conn:
        tables = _existing_tables(conn)
        if "macro_history_v2" in tables:
            row = conn.execute(
                """
                SELECT value, date, COALESCE(source, 'macro_history_v2')
                FROM macro_history_v2
                WHERE variable = ? AND value IS NOT NULL AND value > ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (FX_VARIABLE_NAME, ZERO_FX),
            ).fetchone()
            if row is not None:
                return float(row[0]), str(row[1]), f"macro_history_v2:{row[2]}"

        if "macro_history" in tables:
            row = conn.execute(
                """
                SELECT value, date, COALESCE(source, 'macro_history')
                FROM macro_history
                WHERE variable = ?
                  AND value IS NOT NULL
                  AND value > ?
                  AND COALESCE(is_stale, 0) = 0
                ORDER BY date DESC
                LIMIT 1
                """,
                (FX_VARIABLE_NAME, ZERO_FX),
            ).fetchone()
            if row is not None:
                return float(row[0]), str(row[1]), f"macro_history:{row[2]}"

    return None, None, None


def _load_fundamental_health(
    symbol: str,
    financial_db_path: str | Path = DEFAULT_FINANCIAL_DB,
) -> tuple[str | None, str | None, float | None, str | None]:
    """Đọc sức khỏe 5 cơ quan (`CompanyHealthV2`) và `NET_INCOME` mới nhất (fail-closed)."""
    fin_file = Path(financial_db_path)
    if not fin_file.exists():
        return None, None, None, None

    health_archetype: str | None = None
    latest_health_period: str | None = None
    net_income_vnd: float | None = None
    net_income_period: str | None = None

    with contextlib.closing(_connect_readonly(fin_file)) as conn:
        tables = _existing_tables(conn)
        if "financial_facts" in tables:
            ni_row = conn.execute(
                """
                SELECT period, value
                FROM financial_facts
                WHERE symbol = ?
                  AND metric = 'NET_INCOME'
                  AND value IS NOT NULL
                  AND COALESCE(is_synthetic, 0) = 0
                ORDER BY period DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if ni_row is not None:
                net_income_period = str(ni_row[0])
                net_income_vnd = float(ni_row[1])

        has_health_ratios = "health_ratios" in tables
        has_facts = "financial_facts" in tables
        has_entity_registry = "entity_registry" in tables
        ratio_exists = (
            conn.execute(
                "SELECT 1 FROM health_ratios WHERE symbol = ? LIMIT 1",
                (symbol,),
            ).fetchone()
            is not None
            if has_health_ratios
            else False
        )

    if has_health_ratios and has_facts and has_entity_registry and ratio_exists:
        engine = CompanyHealthV2()
        engine._db = str(fin_file.resolve())
        state = engine.analyze(symbol)
        if state is not None:
            health_archetype = state.archetype
            latest_health_period = state.latest_period

    return health_archetype, latest_health_period, net_income_vnd, net_income_period


def load_symbol_market_snapshot(
    symbol: str,
    *,
    screener_db_path: str | Path = DEFAULT_SCREENER_DB,
    financial_db_path: str | Path = DEFAULT_FINANCIAL_DB,
) -> SymbolMarketSnapshot:
    """Đọc ảnh chụp OHLCV (20 phiên hợp lệ) + Health V2 + NET_INCOME cho `symbol`."""
    sym = symbol.upper().strip()
    db_file = Path(screener_db_path)
    if not db_file.exists():
        return SymbolMarketSnapshot(
            symbol=sym,
            last_trade_date=None,
            close_vnd=None,
            adv20_shares=None,
            valid_session_count=0,
            market_error_code="REFUSED_MISSING_DATA",
            market_error_detail=f"Không tìm thấy DB thị trường: {db_file}",
        )

    with contextlib.closing(_connect_readonly(db_file)) as conn:
        tables = _existing_tables(conn)
        if "daily_ohlcv" not in tables:
            return SymbolMarketSnapshot(
                symbol=sym,
                last_trade_date=None,
                close_vnd=None,
                adv20_shares=None,
                valid_session_count=0,
                market_error_code="REFUSED_MISSING_DATA",
                market_error_detail="DB thiếu bảng daily_ohlcv",
            )

        rows = conn.execute(
            f"""
            SELECT date, close, volume
            FROM daily_ohlcv
            WHERE symbol = ?
              AND {STOCK_ONLY_SQL}
              AND {VALID_ROW_SQL}
              AND COALESCE(is_stale, 0) = 0
            ORDER BY date DESC
            LIMIT ?
            """,
            (sym, MIN_ADV20_SESSIONS),
        ).fetchall()

    valid_count = len(rows)
    last_date = str(rows[0][0]) if rows else None
    close_vnd = float(rows[0][1]) if rows else None
    adv20_shares = sum(float(r[2]) for r in rows) / MIN_ADV20_SESSIONS if valid_count == MIN_ADV20_SESSIONS else None

    health_archetype, latest_health_period, net_income_vnd, net_income_period = _load_fundamental_health(
        sym,
        financial_db_path=financial_db_path,
    )

    return SymbolMarketSnapshot(
        symbol=sym,
        last_trade_date=last_date,
        close_vnd=close_vnd,
        adv20_shares=adv20_shares,
        valid_session_count=valid_count,
        health_archetype=health_archetype,
        latest_health_period=latest_health_period,
        net_income_vnd=net_income_vnd,
        net_income_period=net_income_period,
    )
