"""series_quality.py — Sổ đăng ký chuỗi dữ liệu SUY BIẾN (degenerate) của store.

WHY: một chuỗi macro có thể "tồn tại" (có bar, không NULL, không stale theo nghĩa thời
gian) nhưng KHÔNG mang thông tin — ví dụ hằng số. Nếu pipeline vẫn dùng nó thì mọi
ngưỡng/trọng số tính trên chuỗi đó là số giả, không có lỗi nào được báo.
Ca thật: `VGB10Y` — 56 bar, ĐÚNG 1 giá trị 4.542 (2026-06-11 → 2026-10-05), do seeder
(World Bank) không tạo được đường cong lợi suất TPCP. Nó đang được dùng ở:
  - `engine/macro_governor.py::_score_omo` (trọng số 0.4 của điểm rủi ro OMO)
  - `governor/unfreeze_gates_v2.py` G3 (spread VGB10Y − US10Y ≥ −30bps)
Nguyên tắc: **missing > fake**. Chuỗi suy biến bị loại khỏi phép tính; cổng dựa vào nó
phải FAIL-CLOSED (coi như thiếu dữ liệu), không được coi là điều kiện đạt.

Bỏ cờ khi: có provider nạp đường cong TPCP thật (nhiều mức kỳ hạn, biến thiên > 1 giá trị)
và `detect_constant_series()` không còn bắt được biến đó nữa.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "screener_cache.db"

# ── Sổ đăng ký: mã cờ theo biến ────────────────────────────────────────
CODE_DEGENERATE_CONSTANT = "DATA_QUALITY_DEGENERATE_CONSTANT"

DEGENERATE_SERIES: dict[str, dict] = {
    "VGB10Y": {
        "code": CODE_DEGENERATE_CONSTANT,
        "detail": "60 dòng macro_history (56 có giá trị) / 1 giá trị duy nhất = 4.542 "
        "(2026-06-11 → 2026-10-05) — seeder World Bank không tạo được đường cong TPCP",
        "flagged_at": "2026-10-13",
        "consumers": ["engine/macro_governor.py:_score_omo", "governor/unfreeze_gates_v2.py:gate3"],
        "action": "loại khỏi phép tính; cổng coi như thiếu dữ liệu (fail-closed)",
    },
}


def is_degenerate(variable: str) -> bool:
    """True nếu biến nằm trong sổ đăng ký suy biến."""
    if variable in DEGENERATE_SERIES:
        logger.warning(
            "[SERIES_QUALITY] %s = %s — %s",
            variable,
            DEGENERATE_SERIES[variable]["code"],
            DEGENERATE_SERIES[variable]["detail"],
        )
        return True
    return False


def degenerate_info(variable: str) -> dict | None:
    return DEGENERATE_SERIES.get(variable)


def filter_usable(variables) -> list:
    """Loại các biến suy biến khỏi danh sách tính toán."""
    return [v for v in variables if v not in DEGENERATE_SERIES]


def detect_constant_series(
    db_path: str | Path | None = None, min_bars: int = 20, table: str = "macro_history"
) -> dict[str, int]:
    """Phát hiện (lại) các chuỗi hằng số từ chính store — để cờ này kiểm chứng được.

    Trả {variable: n_bars} cho mọi biến có >= min_bars bar mà chỉ có <= 1 giá trị
    phân biệt.
    """
    path = Path(db_path) if db_path else DB_PATH
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT variable, COUNT(*), COUNT(DISTINCT value) FROM {table} "
            "WHERE value IS NOT NULL GROUP BY variable "
            "HAVING COUNT(*) >= ? AND COUNT(DISTINCT value) <= 1",
            (min_bars,),
        ).fetchall()
    finally:
        con.close()
    return {str(r[0]): int(r[1]) for r in rows}
