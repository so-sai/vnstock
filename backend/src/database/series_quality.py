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
CODE_NON_NUMERIC_STATUS = "NON_NUMERIC_STATUS_SERIES"

# Biến số KINH TẾ bị đóng băng (hằng số) → khoá tự động: một hằng số không mang thông tin,
# và khi ghép nhiều chuỗi như vậy vào ma trận thiết kế thì X'X suy biến (rank-deficient).
# Đo từ store 2026-10-14: mỗi chuỗi 46 dòng, 1 giá trị duy nhất.
_FED_STUB_DETAIL = "46 dòng macro_history / 1 giá trị duy nhất — chuỗi tĩnh (stub), không có nguồn cập nhật động"

DEGENERATE_SERIES: dict[str, dict] = {
    "VGB10Y": {
        "code": CODE_DEGENERATE_CONSTANT,
        "detail": "60 dòng macro_history (56 có giá trị) / 1 giá trị duy nhất = 4.542 "
        "(2026-06-11 → 2026-10-05) — seeder World Bank không tạo được đường cong TPCP",
        "flagged_at": "2026-10-13",
        "consumers": ["engine/macro_governor.py:_score_omo", "governor/unfreeze_gates_v2.py:gate3"],
        "action": "loại khỏi phép tính; cổng coi như thiếu dữ liệu (fail-closed)",
    },
    **{
        v: {
            "code": CODE_DEGENERATE_CONSTANT,
            "detail": _FED_STUB_DETAIL,
            "flagged_at": "2026-10-14",
            "consumers": [],
            "action": "loại khỏi mọi ma trận hồi quy (chống suy biến hạng thiếu)",
        }
        for v in ("FED_UNCERTAINTY", "FOMC_DISSENT", "IMPLIED_HIKE_PROB", "QT_BALANCE_TR", "RESERVES_TR")
    },
}

# Chuỗi CỜ TRẠNG THÁI / metadata — không phải biến định lượng. Không báo động "suy biến"
# (hằng số là đúng thiết kế) nhưng vẫn phải bị loại khỏi mọi ma trận tính toán.
NON_NUMERIC_STATUS_SERIES: dict[str, dict] = {
    "VGB10Y_STATUS": {
        "code": CODE_NON_NUMERIC_STATUS,
        "detail": "60 dòng macro_history — nhãn trạng thái của VGB10Y (metadata, không phải biến số)",
        "flagged_at": "2026-10-14",
        "action": "loại khỏi ma trận định lượng; không tính là cảnh báo dữ liệu",
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


def is_excluded_from_models(variable: str) -> bool:
    """True nếu biến phải bị loại khỏi mọi ma trận tính toán (suy biến HOẶC chuỗi cờ trạng thái)."""
    return variable in DEGENERATE_SERIES or variable in NON_NUMERIC_STATUS_SERIES


def series_kind(variable: str) -> str:
    """'degenerate_constant' | 'non_numeric_status' | 'ok' — một chỗ duy nhất để phân loại."""
    if variable in DEGENERATE_SERIES:
        return "degenerate_constant"
    if variable in NON_NUMERIC_STATUS_SERIES:
        return "non_numeric_status"
    return "ok"


def filter_usable(variables) -> list:
    """Loại cả chuỗi suy biến và chuỗi cờ trạng thái khỏi danh sách tính toán."""
    return [v for v in variables if not is_excluded_from_models(v)]


def classify_constant_series(db_path: str | Path | None = None, min_bars: int = 20) -> dict[str, tuple[str, int]]:
    """{variable: (kind, n_bars)} cho mọi chuỗi hằng số phát hiện được trong store."""
    return {v: (series_kind(v), n) for v, n in detect_constant_series(db_path, min_bars).items()}


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
